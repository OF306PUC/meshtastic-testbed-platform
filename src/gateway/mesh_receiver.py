import time
import collections
import threading
import meshtastic
import meshtastic.serial_interface
from pubsub import pub
from gateway.mqtt_connector import MQTTConnector


class MeshReceiver:
    """
    Connects to the local Meshtastic gateway over serial, listens for
    telemetry packets from known nodes, and publishes them to MQTT.
    """

    SEEN_MAX = 200

    _APP_FIELDS = ["TELEMETRY_APP", "POSITION_APP"]

    def __init__(self, mqtt: MQTTConnector, known_nodes: dict):
        self.mqtt        = mqtt
        self.known_nodes = known_nodes
        self.seen_ids    = collections.deque(maxlen=self.SEEN_MAX)
        self._seen_lock  = threading.Lock()
        self.iface       = None
        self.my_id       = None
        self.my_num      = None

    def connect(self, devPath: str):
        """Opens the serial connection to the Meshtastic gateway."""
        self.iface  = meshtastic.serial_interface.SerialInterface(devPath=devPath)
        me          = self.iface.getMyNodeInfo()
        self.my_id  = me["user"]["id"]
        self.my_num = me["num"]
        print(f"[MESH] Gateway node: {self.my_id} (num={self.my_num})")
        print(f"[MESH] Watching nodes: {list(self.known_nodes.keys())}\n")

    def listen(self):
        """Subscribes to incoming packets and blocks until KeyboardInterrupt."""
        pub.subscribe(self._on_receive, "meshtastic.receive")
        print("Listening... Ctrl+C to stop.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.close()

    def close(self):
        """Closes serial and MQTT connections cleanly."""
        print("\n[MESH] Shutting down...")
        if self.iface:
            self.iface.close()
        self.mqtt.close()

    # ── Packet handling ───────────────────────────────────────────────────────

    def _on_receive(self, packet, interface):
        try:
            if not packet or "decoded" not in packet:
                return

            sender_id  = packet.get("fromId")
            sender_num = packet.get("from")
            rssi       = packet.get("rxRssi")
            snr        = packet.get("rxSnr")
            hop_limit  = packet.get("hopLimit")
            hop_start  = packet.get("hopStart")
            hop_taken  = (hop_start - hop_limit
                          if hop_start is not None and hop_limit is not None
                          else None)

            if not self._is_valid(sender_id, sender_num, packet.get("id")):
                return

            decoded     = packet["decoded"]
            label       = self.known_nodes[sender_id]
            received_at = int(time.time())

            if decoded.get("portnum") not in self._APP_FIELDS:
                return

            if decoded.get("portnum") == "POSITION_APP":
                pos       = decoded.get("position", {})
                device_ts = pos.get("time", int(time.time()))
                self._handle_position(sender_id, label, pos, rssi, snr, hop_taken, device_ts, received_at)

            if decoded.get("portnum") == "TELEMETRY_APP":
                telem     = decoded.get("telemetry", {})
                device_ts = telem.get("time", int(time.time()))
                self._handle_device_telemetry(sender_id, label, telem, rssi, snr, hop_taken, device_ts, received_at)
                self._handle_env_telemetry(sender_id, label, telem, rssi, snr, hop_taken, device_ts, received_at)

        except Exception as e:
            print(f"[MESH] ERROR processing packet: {e}")

    def _is_valid(self, sender_id: str, sender_num: int, packet_id) -> bool:
        """Returns False if the packet should be dropped."""
        if sender_id == self.my_id or sender_num == self.my_num:
            return False
        if sender_id not in self.known_nodes:
            return False
        if packet_id is not None:
            with self._seen_lock:
                if packet_id in self.seen_ids:
                    return False
                self.seen_ids.append(packet_id)
        return True

    # ── Telemetry parsers ─────────────────────────────────────────────────────

    def _handle_position(
            self, node_id: str, label: str, pos: dict, rssi: int, snr: int, hop: int, device_ts: int, received_at: int):
        payload = {
            "node_id":     node_id,
            "node_label":  label,
            "rssi":        rssi, 
            "snr":         snr,
            "hop":         hop, 
            "device_ts":   device_ts,
            "received_at": received_at,
        }
        for key, val in [
            ("latitude",  pos.get("latitude")),
            ("longitude", pos.get("longitude")),
            ("altitude",  pos.get("altitude")),
        ]:
            if val is not None:  # omit absent metrics; 0.0 would be a false reading
                payload[key] = val
        print(f"\n[POS] Position update from {label} ({node_id})")
        self.mqtt.publish_position(label, payload)

    def _handle_device_telemetry(
            self, node_id: str, label: str, telem: dict, rssi: int, snr: int, hop: int, device_ts: int, received_at: int):
        device = telem.get("deviceMetrics", {})
        if not device:
            return
        payload = {
            "node_id":     node_id,
            "node_label":  label,
            "rssi":        rssi, 
            "snr":         snr,
            "hop":         hop, 
            "device_ts":   device_ts,
            "received_at": received_at,
        }
        for key, val in [
            ("battery_level",  device.get("batteryLevel")),
            ("voltage",        device.get("voltage")),
            ("channel_util",   device.get("channelUtilization")),
            ("air_util_tx",    device.get("airUtilTx")),
            ("uptime_seconds", device.get("uptimeSeconds")),
        ]:
            if val is not None:  # omit absent metrics; 0.0 would be a false reading
                payload[key] = val
        print(f"\n[DEVICE] Device telemetry from {label} ({node_id})")
        self.mqtt.publish_device(label, payload)

    def _handle_env_telemetry(
            self, node_id: str, label: str, telem: dict, rssi: int, snr: int, hop: int, device_ts: int, received_at: int):
        env = telem.get("environmentMetrics", {})
        if not env:
            return
        payload = {
            "node_id":     node_id,
            "node_label":  label,
            "rssi":        rssi, 
            "snr":         snr,
            "hop":         hop, 
            "device_ts":   device_ts,
            "received_at": received_at,
        }
        for key, val in [
            ("temperature", env.get("temperature")),
            ("humidity",    env.get("relativeHumidity")),
        ]:
            if val is not None:  # omit absent metrics; 0.0 would be a false reading
                payload[key] = val
        print(f"\n[ENV] Environment telemetry from {label} ({node_id})")
        self.mqtt.publish_env(label, payload)