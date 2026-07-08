import os
import re
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from utils import InfluxDBConnector, MQTTConnector, ALL_FIELDS
from param import (
    DB_HOST, DB_PORT, DB_USERNAME, DB_PASSWORD, DB_NAME,
    BROKER_ADDRESS, BROKER_PORT, CLIENT_ID, SUBSCRIBE_TOPIC,
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

mqtt = MQTTConnector(BROKER_ADDRESS, BROKER_PORT, CLIENT_ID)
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


# ── Hardcoded node positions (fixed installations) ────────────────────────────
# Update these if nodes are physically relocated.

NODES = [
    {"node_id": "!7c70da02", "node_label": "node-1", "lat": -33.49695, "lon": -70.61025},  
    {"node_id": "!0b64122b", "node_label": "node-2", "lat": -33.49729, "lon": -70.60736},
    {"node_id": "!32fe0d4e", "node_label": "node-3", "lat": -33.49783, "lon": -70.61518},
]

# ── APIs ─────────────────────────────────────────────────────────────────────

@app.route("/api/nodes")
def api_nodes():
    """Return fixed node positions. No DB query needed — nodes are stationary."""
    return jsonify(NODES)


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