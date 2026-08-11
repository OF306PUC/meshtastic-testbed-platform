import os
import re
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from common import mesh_config
from utils import InfluxDBConnector, MQTTConnector, ALL_FIELDS
from param import (
    DB_HOST, DB_PORT, DB_USERNAME, DB_PASSWORD, DB_NAME,
    BROKER_ADDRESS, BROKER_PORT, CLIENT_ID, SUBSCRIBE_TOPIC,
    MQTT_USERNAME, MQTT_PASSWORD,
)

import param

# Valid node IDs look like !7c70da02 — reject anything else before it reaches DB or templates
NODE_ID_RE = re.compile(r'^![0-9a-f]{8}$')

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins=["http://localhost:5000"], async_mode="eventlet", logger=True, engineio_logger=True)

# ── Shared connectors ────────────────────────────────────────────────────────

db = InfluxDBConnector(
    host=DB_HOST, port=DB_PORT,
    username=DB_USERNAME, password=DB_PASSWORD,
    database=DB_NAME,
)

mqtt = MQTTConnector(BROKER_ADDRESS, BROKER_PORT, CLIENT_ID,
                     username=MQTT_USERNAME, password=MQTT_PASSWORD)
mqtt.socketio = socketio
mqtt.connect()
mqtt.loop()          # must start the network thread before subscribing
mqtt.subscribe(SUBSCRIBE_TOPIC)

# ── Pages ────────────────────────────────────────────────────────────────────

@app.route("/")
@app.route("/map")
def map_view():
    return render_template("map.html")


@app.route("/dashboard")
def dashboard():
    node_id = request.args.get("node", "")
    if not NODE_ID_RE.match(node_id):
        return render_template("map.html")
    return render_template("dashboard.html", node_id=node_id)


# ── Surveyed positions ───────────────────────────────────────────────────────

def surveyed_positions():
    """
    Yields (node_id, node_label, {"lat","lon","alt"}) for every node whose
    mesh_config.json entry records a surveyed position.

    Read through common.mesh_config rather than parsed here: nothing provisions
    that block onto a radio, so this is its only consumer and therefore the only
    place its validation runs. A malformed coordinate has to fail loudly here or
    it never fails at all.

    A missing or unreadable file is not an error — it means no surveys yet, and
    the map falls back to reported GPS.
    """
    try:
        data = mesh_config.load(param.MESH_CONFIG_PATH)
    except FileNotFoundError:
        return
    except Exception as exc:
        print(f"[CFG] {param.MESH_CONFIG_PATH} unreadable: {exc}")
        return

    for key, cfg in data["nodes_cfg"].items():
        node_id = cfg.get("id")
        if not node_id:
            continue
        try:
            pos = mesh_config.position_for(cfg)
        except ValueError as exc:
            # Loud, and skipped: a bad coordinate must not reach the map.
            print(f"[CFG] node '{key}' has an invalid position: {exc}")
            continue
        if pos is not None:
            yield node_id, f"node-{key}", pos


# ── APIs ─────────────────────────────────────────────────────────────────────

@app.route("/api/nodes")
def api_nodes():
    """
    Position per node: the surveyed coordinate when one exists, otherwise the
    latest the node reported over GPS. Each entry says which, in `source`.

    History worth keeping, because it explains the shape. This route used to
    return a hardcoded list, reasoning that stationary nodes need no query. Its
    coordinates had drifted ~2 km from anything the nodes reported, so the map
    showed positions no node had been at, and its labels were swapped relative to
    mesh_config.json — the file the gateway reads when it writes the node_label
    tag — so the API contradicted the database and a chart correlated by label
    pointed at the wrong node.

    Surveyed positions are preferred because they are measured on site and do not
    drift, and because the GPS is currently reporting one identical coordinate for
    every node (see project-overview § Known constraints). They are NOT
    provisioned onto the radios: the node keeps reporting its own fix, and the
    difference between the two is the GPS error, which is a result rather than
    noise. That is why both numbers exist and why `source` is exposed instead of
    silently blending them.
    """
    reported = {n["node_id"]: n for n in db.get_nodes_position()}
    out = []

    for node_id, label, pos in surveyed_positions():
        entry = {"node_id": node_id, "node_label": label,
                 "lat": pos["lat"], "lon": pos["lon"], "source": "surveyed"}
        gps = reported.pop(node_id, None)
        if gps is not None:
            # Kept alongside so the map can show how far the fix is off.
            entry["gps_lat"], entry["gps_lon"] = gps["lat"], gps["lon"]
        out.append(entry)

    # Nodes with no survey fall back to whatever they reported.
    for gps in reported.values():
        out.append({**gps, "source": "gps"})
    return jsonify(out)


@app.route("/api/recent/<node_id>/<field>")
def api_recent(node_id, field):
    """Return the last `limit` data points for one field on one node."""
    if not NODE_ID_RE.match(node_id):
        return jsonify({"error": "invalid node_id"}), 400
    if field not in ALL_FIELDS:
        return jsonify({"error": "unknown field"}), 400
    limit = max(1, min(request.args.get("limit", default=param.TOTAL_SAMPLES_48HRS, type=int), 5000))
    print(f"[DB] query node={node_id} field={field} limit={limit}")
    data  = db.get_recent(node_id, field, limit=limit)
    print(f"[DB] returned {len(data)} points for {field}")
    return jsonify(data)


# ── Socket.IO ─────────────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    print("[WS] client connected")

@socketio.on("disconnect")
def on_disconnect():
    print("[WS] client disconnected")

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        socketio.run(app, host="0.0.0.0", port=5000,
                     debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
                     use_reloader=False)
    except KeyboardInterrupt:
        mqtt.close()
        db.close()