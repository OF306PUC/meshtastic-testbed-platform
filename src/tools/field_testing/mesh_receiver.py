"""mesh_receiver.py  –  Field-testing CSV receiver
===================================================
Part of the field-testing toolkit (src/tools/field_testing/). Unlike the
production receiver (src/gateway/mesh_receiver.py, which publishes to MQTT),
this one logs everything to per-node CSV files and also records the gateway's
own GPS position — for offline range/walk-test analysis with plot_data.py.
"""
import csv
import os
import time
import collections
import datetime
import threading
import meshtastic
import meshtastic.serial_interface
from pubsub import pub
from typing import Callable, Optional


class MeshReceiver:
    """
    Connects to the local Meshtastic gateway over serial, listens for
    telemetry and position packets from known nodes, logs them to per-node
    CSV files (with RSSI/SNR for range evaluation), and logs the gateway's
    own GPS position through the same subscription.
    """

    SEEN_MAX    = 5

    _APP_FIELDS = ["TELEMETRY_APP", "POSITION_APP"]

    def __init__(
        self,
        known_nodes: dict,
        data_dir: str = "data",
        on_telemetry: Optional[Callable[[dict], None]] = None,
        on_env_telemetry: Optional[Callable[[dict], None]] = None,
        on_position: Optional[Callable[[dict], None]] = None,
    ):
        """
        Args:
            known_nodes:      {node_id: label} e.g. {"!0b64122b": "node-1"}
            data_dir:         directory where CSV files are written
            on_telemetry:     callback(record: dict) called on each device telemetry packet
            on_env_telemetry: callback(record: dict) called on each environment telemetry packet
            on_position:      callback(record: dict) called on each position packet
        """
        self.known_nodes      = known_nodes
        self.data_dir         = data_dir
        self.on_telemetry     = on_telemetry
        self.on_env_telemetry = on_env_telemetry
        self.on_position      = on_position

        self.seen_ids      = collections.deque(maxlen=self.SEEN_MAX)
        self.iface         = None
        self.my_id         = None
        self.my_num        = None
        self._lock         = threading.Lock()
        self._running      = False

        os.makedirs(self.data_dir, exist_ok=True)
        
        self.last_telemetry:     dict = {}
        self.last_env_telemetry: dict = {}
        self.last_position:      dict = {}

    def connect(self, devPath: str):
        """Opens the serial connection."""
        self.iface  = meshtastic.serial_interface.SerialInterface(devPath=devPath)
        me          = self.iface.getMyNodeInfo()
        self.my_id  = me["user"]["id"]
        self.my_num = me["num"]
        self._running = True
        print(f"[MESH] Gateway node : {self.my_id} (num={self.my_num})")
        print(f"[MESH] Watching     : {list(self.known_nodes.keys())}\n")

    def listen(self):
        """Subscribes to packets and blocks until KeyboardInterrupt."""
        pub.subscribe(self._on_receive, "meshtastic.receive")
        print("Listening... Ctrl+C to stop.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.close()

    def close(self):
        """Closes the serial connection cleanly."""
        print("\n[MESH] Shutting down...")
        self._running = False
        if self.iface:
            self.iface.close()

    def _on_receive(self, packet, interface):
        if not packet or "decoded" not in packet:
            return

        sender_id = packet.get("fromId")
        decoded   = packet["decoded"]

        if sender_id == self.my_id:
            if decoded.get("portnum") == "POSITION_APP":
                pos = decoded.get("position", {})
                if pos:
                    self._handle_gateway_position(pos)
            return

        if sender_id not in self.known_nodes:
            return

        print(f"[PACKET] {packet}")
        label       = self.known_nodes[sender_id]
        received_at = time.time()
        rssi        = packet.get("rxRssi")
        snr         = packet.get("rxSnr")

        print(f"[MESH] {decoded.get('portnum')}")

        if decoded.get("portnum") not in self._APP_FIELDS:
            return

        if decoded["portnum"] == "POSITION_APP":
            pos       = decoded.get("position", {})
            device_ts = pos.get("time", int(time.time()))
            self._handle_position(sender_id, label, pos, device_ts, received_at, rssi, snr)

        elif decoded["portnum"] == "TELEMETRY_APP":
            telem     = decoded.get("telemetry", {})
            device_ts = telem.get("time", int(time.time()))
            self._handle_device_telemetry(sender_id, label, telem, device_ts, received_at, rssi, snr)
            self._handle_env_telemetry(sender_id, label, telem, device_ts, received_at, rssi, snr)

    def _handle_gateway_position(self, pos: dict):
        ts_now = time.time()
        record = {
            "ts":        int(ts_now),
            "latitude":  pos.get("latitude"),
            "longitude": pos.get("longitude"),
            "altitude":  pos.get("altitude"),
        }
        self._write_csv(
            "gateway", "position", record,
            ["ts", "latitude", "longitude", "altitude"],
        )
        ts_str = datetime.datetime.fromtimestamp(ts_now).strftime("%H:%M:%S")
        print(
            f"[{ts_str}] [GW-POS] {self.my_id} "
            f"lat={record['latitude']} lon={record['longitude']} "
            f"alt={record['altitude']}m"
        )

    def _handle_position(
        self,
        node_id: str,
        label: str,
        pos: dict,
        device_ts: int,
        received_at: float,
        rssi,
        snr,
    ):
        record = {
            "device_ts":   device_ts,
            "received_at": received_at,
            "latitude":    pos.get("latitude"),
            "longitude":   pos.get("longitude"),
            "altitude":    pos.get("altitude"),
            "rssi":        rssi,
            "snr":         snr,
        }
        with self._lock:
            self.last_position = record

        self._write_csv(
            label, "position", record,
            ["device_ts", "received_at", "latitude", "longitude", "altitude", "rssi", "snr"],
        )

        ts = datetime.datetime.fromtimestamp(received_at).strftime("%H:%M:%S")
        print(
            f"[{ts}] [POS] {label} ({node_id}) "
            f"lat={record['latitude']} lon={record['longitude']} "
            f"alt={record['altitude']}m  rssi={rssi} snr={snr}"
        )

        if self.on_position:
            self.on_position(record)

    def _handle_device_telemetry(
        self,
        node_id: str,
        label: str,
        telem: dict,
        device_ts: int,
        received_at: float,
        rssi,
        snr,
    ):
        device = telem.get("deviceMetrics", {})
        if not device:
            return

        record = {
            "device_ts":      device_ts,
            "received_at":    received_at,
            "battery_level":  device.get("batteryLevel"),
            "voltage":        device.get("voltage"),
            "channel_util":   device.get("channelUtilization"),
            "air_util_tx":    device.get("airUtilTx"),
            "uptime_seconds": device.get("uptimeSeconds"),
            "rssi":           rssi,
            "snr":            snr,
        }
        with self._lock:
            self.last_telemetry = record

        self._write_csv(
            label, "telemetry", record,
            ["device_ts", "received_at", "battery_level", "voltage",
             "channel_util", "air_util_tx", "uptime_seconds", "rssi", "snr"],
        )

        def _fmt(val, spec):
            return format(val, spec) if val is not None else "N/A"

        ts = datetime.datetime.fromtimestamp(received_at).strftime("%H:%M:%S")
        print(
            f"[{ts}] [TELEM] {label} ({node_id}) "
            f"bat={_fmt(record['battery_level'], '')}% "
            f"v={_fmt(record['voltage'], '.2f')}V "
            f"ch_util={_fmt(record['channel_util'], '.1f')}% "
            f"air_tx={_fmt(record['air_util_tx'], '.1f')}% "
            f"up={_fmt(record['uptime_seconds'], '')}s  "
            f"rssi={rssi} snr={snr}"
        )

        if self.on_telemetry:
            self.on_telemetry(record)

    def _handle_env_telemetry(
        self,
        node_id: str,
        label: str,
        telem: dict,
        device_ts: int,
        received_at: float,
        rssi,
        snr,
    ):
        env = telem.get("environmentMetrics", {})
        if not env:
            return

        record = {
            "device_ts":   device_ts,
            "received_at": received_at,
            "temperature": env.get("temperature"),
            "humidity":    env.get("relativeHumidity"),
            "rssi":        rssi,
            "snr":         snr,
        }
        with self._lock:
            self.last_env_telemetry = record

        self._write_csv(
            label, "env", record,
            ["device_ts", "received_at", "temperature", "humidity", "rssi", "snr"],
        )

        def _fmt(val, spec):
            return format(val, spec) if val is not None else "N/A"

        ts = datetime.datetime.fromtimestamp(received_at).strftime("%H:%M:%S")
        print(
            f"[{ts}] [ENV] {label} ({node_id}) "
            f"temp={_fmt(record['temperature'], '.1f')}°C "
            f"hum={_fmt(record['humidity'], '.1f')}%  "
            f"rssi={rssi} snr={snr}"
        )

        if self.on_env_telemetry:
            self.on_env_telemetry(record)

    def _csv_path(self, label: str, kind: str) -> str:
        date_str = datetime.date.today().isoformat()
        return os.path.join(self.data_dir, f"{label}_{kind}_{date_str}.csv")

    def _write_csv(self, label: str, kind: str, record: dict, fieldnames: list):
        path      = self._csv_path(label, kind)
        write_hdr = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_hdr:
                writer.writeheader()
            writer.writerow({k: record.get(k) for k in fieldnames})
