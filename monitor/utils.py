import re
import json
import threading
import paho.mqtt.client as mqtt
from influxdb import InfluxDBClient

import param

_NODE_ID_RE = re.compile(r'^![0-9a-f]{8}$')

# Fields stored in the mqtt_consumer measurement
POSITION_FIELDS    = ["latitude", "longitude", "altitude"]
ENVIRONMENT_FIELDS = ["temperature", "humidity"]
DEVICE_FIELDS      = ["battery_level", "voltage", "channel_util", "air_util_tx", "uptime_seconds"]
RX_METRICS_FIELDS  = ["rssi", "snr", "hop"]
ALL_FIELDS         = POSITION_FIELDS + ENVIRONMENT_FIELDS + DEVICE_FIELDS + RX_METRICS_FIELDS

TOPIC_FIELDS = {
    "environment": ENVIRONMENT_FIELDS,
    "position":    POSITION_FIELDS,
    "device":      DEVICE_FIELDS,
    "rx_metrics":  RX_METRICS_FIELDS,
}


class InfluxDBConnector:
    def __init__(self, host, port, username, password, database,
                 measurement="mqtt_consumer"):
        self.measurement = measurement
        self.database    = database
        print(f"[DB] connecting to {host}:{port} db={database}")
        try:
            self.client = InfluxDBClient(
                host=host, port=port,
                username=username, password=password,
                database=database,
            )
            if database:
                self.client.switch_database(database)
            dbs = [d["name"] for d in self.client.get_list_database()]
            if database in dbs:
                print(f"[DB] connected OK — database '{database}' found")
            else:
                print(f"[DB] WARNING: database '{database}' NOT found — available: {dbs}")
        except Exception as e:
            print(f"[DB] connection error: {e}")
            self.client = None

    def _query(self, q):
        if not self.client:
            print("[DB] no client, skipping query")
            return None
        try:
            return self.client.query(q)
        except Exception as e:
            print(f"[DB] query error: {e}")
            return None

    @staticmethod
    def _escape(v):
        return str(v).replace("\\", "\\\\").replace("'", "\\'")

    @staticmethod
    def _points(result):
        if not result:
            return []
        try:
            return list(result.get_points())
        except Exception:
            return []

    def get_nodes_position(self):
        q = (f'SELECT LAST("latitude") AS lat, LAST("longitude") AS lon '
             f'FROM "{self.measurement}" GROUP BY "node_id","node_label"')
        result = self._query(q)
        if not result:
            return []
        out = []
        for serie in result.raw.get("series", []):
            tags   = serie.get("tags", {})
            values = serie.get("values")
            if not values:
                continue
            row = dict(zip(serie["columns"], values[0]))
            if row.get("lat") is not None and row.get("lon") is not None:
                out.append({
                    "node_id":    tags.get("node_id"),
                    "node_label": tags.get("node_label"),
                    "lat": row["lat"],
                    "lon": row["lon"],
                })
        return out

    def get_recent(self, node_id, field, limit=param.TOTAL_SAMPLES_48HRS):
        """
        Return the last `limit` data points for one field on one node,
        ordered ascending by time.
        """
        if not _NODE_ID_RE.match(node_id):
            print(f"[DB] rejected invalid node_id '{node_id}'")
            return []
        if field not in ALL_FIELDS:
            print(f"[DB] unknown field '{field}'")
            return []
        q = (f'SELECT "{field}" '
             f'FROM "{self.measurement}" '
             f'WHERE "node_id"=\'{node_id}\' '
             f'ORDER BY time DESC LIMIT {min(int(limit), 5000)}')
        return list(reversed(self._points(self._query(q))))

    def close(self):
        if self.client:
            self.client.close()


class MQTTConnector:
    def __init__(self, broker_address, port=1883, client_id="",
                 username="", password=""):
        self.broker_address = broker_address
        self.port           = port
        self.client_id      = client_id
        self.socketio       = None
        self._subscriptions = []   # remember topics so we can re-subscribe on reconnect

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id)
        # Empty username means anonymous, which the broker refuses once
        # allow_anonymous is off. rc=5 in _on_connect is a bad credential.
        if username:
            self.client.username_pw_set(username, password)
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message    = self._on_message

    def _on_connect(self, client, userdata, flags, rc, properties):
        if rc == 0:
            print(f"[MQTT] connected to {self.broker_address}:{self.port}")
            # Re-subscribe on every connect (handles broker restarts)
            for topic in self._subscriptions:
                client.subscribe(topic)
                print(f"[MQTT] (re)subscribed → {topic}")
        else:
            print(f"[MQTT] connection refused rc={rc} — retrying in 5 s")
            threading.Timer(5, self.connect).start()

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        print(f"[MQTT] disconnected rc={rc} — paho will auto-reconnect")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception as e:
            print(f"[MQTT] bad JSON on {msg.topic}: {e}")
            return

        payload["_topic"] = msg.topic
        parts = msg.topic.split("/")
        payload["_type"] = parts[-1] if len(parts) >= 3 else "unknown"

        print(f"[MQTT] {msg.topic} | "
              f"node={payload.get('node_id')} "
              f"type={payload['_type']} "
              f"device_ts={payload.get('device_ts')} "
              f"received_at={payload.get('received_at')}")

        if self.socketio:
            self.socketio.emit("mqtt_message", payload)
        else:
            print("[MQTT] WARNING: socketio not attached, message not forwarded to browser")

    def connect(self):
        try:
            print(f"[MQTT] connecting to {self.broker_address}:{self.port} "
                  f"client_id={self.client_id}")
            self.client.connect(self.broker_address, self.port, keepalive=60)
        except Exception as e:
            print(f"[MQTT] connect failed: {e} — retrying in 5 s")
            threading.Timer(5, self.connect).start()

    def subscribe(self, topic):
        self._subscriptions.append(topic)
        self.client.subscribe(topic)
        print(f"[MQTT] subscribed → {topic}")

    def loop(self):
        self.client.loop_start()
        print("[MQTT] network loop started")

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()
        print("[MQTT] disconnected")