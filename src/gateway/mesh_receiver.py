import time
import struct
import collections
import threading
import meshtastic
import meshtastic.serial_interface
from pubsub import pub
from common.mesh_config import (
    FLOW_KINDS, DEFAULT_INTERVALS, DEFAULT_PDR_WINDOW_SEC, DEFAULT_SWEEP_INTERVAL_SEC,
)
from gateway.mqtt_connector import MQTTConnector

# PBX application frames, carried inside MeshPacket.decoded.payload.
#
#     portnum=PRIVATE_APP (256)     [VERSION:1][SRC_ID:4][DST_ID:4][content:N]
#     portnum=TEXT_MESSAGE_APP (1)  [VERSION:1][SRC_ID:4][content:N]
#
# PRIVATE_APP is the PBX's routed unicast carrier: DST_ID names which phone
# behind the target node should receive it, so the PBX can deliver to a single
# BLE connection.  TEXT_MESSAGE_APP carries no destination — the PBX broadcasts
# it to every connection — so its header is 4 bytes shorter.
#
# A proxy_id is a 4-byte BIG-ENDIAN uint32: the phone's national number without
# country code, which the PBX logs as "+56<uint32>".  It is NOT a Meshtastic
# node id and must not be rendered like one; it is published in decimal so it
# matches the PBX log and what the phone writes to NODE_REG.

_FRAME_HDR_UNICAST   = struct.Struct(">BII")   # version, src_id, dst_id -> 9 B
_FRAME_HDR_BROADCAST = struct.Struct(">BI")    # version, src_id         -> 5 B
_FRAME_VERSION       = 0x01                    # PROXY_VERSION

_FRAME_HDRS = {
    "PRIVATE_APP":      _FRAME_HDR_UNICAST,
    "TEXT_MESSAGE_APP": _FRAME_HDR_BROADCAST,
}


class CadencePdrTracker:
    """
    Packet-delivery-ratio estimator for flows with a KNOWN broadcast cadence.

    No sequence numbers involved: losses are inferred from inter-arrival gaps
    against the configured interval T.  On each on-schedule reception:

        slots  = round(dt / T)          # nominal intervals the gap spans
        missed = max(0, slots - 1)

    State is per flow — a (node_label, kind) pair with kind in
    ("device", "environment", "position"). 

    Accuracy and limitations, stated on purpose:

    * ±1 packet per gap.  The firmware defers broadcasts under channel
      congestion, so T is nominal, not exact.
    * A powered-off node inflates `missed`.  Reboots are detected from
      deviceMetrics.uptimeSeconds by the caller, which then re-anchors the
      flow so the downtime gap is not charged as radio loss.
    * `pdr` is None until at least one gap has been observed — a single packet
      carries no delivery information, and reporting 1.0 there would be a lie.
    """

    MIN_WINDOW_SLOTS = 3   

    def __init__(self):
        self._flows = {}
        self._lock  = threading.Lock()

    # Public API:
    def observe(self, flow: tuple, interval: int, window_sec: int, now: float) -> dict:
        """
        Records one reception on `flow`.  `now` MUST come from time.monotonic():
        wall-clock deltas would turn an NTP step on the host into phantom losses
        across every flow at once.

        Returns a snapshot dict ready to merge into the outgoing MQTT payload.
        """
        with self._lock:
            f = self._flows.get(flow)
            if f is None:
                f = self._flows[flow] = self._new_flow(interval, window_sec, now)
                return self._snapshot(f, gap_s=None, missed_now=0)

            dt = now - f["last"]

            if f["restart"]:
                # First packet after a reboot: it starts a new cadence grid, so
                # it is scored as neither early nor late whatever dt says.
                f["restart"] = False
                f["rx"]     += 1
                f["last"]    = now
                f["charged"] = 0
                f["recent"].append(1)
                return self._snapshot(f, gap_s=round(dt, 1), missed_now=0)

            slots = round(dt / f["interval"])

            if slots == 0:
                # Arrived inside a single nominal interval: an extra broadcast
                # the cadence model does not predict.  
                f["early"] += 1
                return self._snapshot(f, gap_s=round(dt, 1), missed_now=0)

            missed_now   = max(0, slots - 1 - f["charged"])
            f["rx"]     += 1
            f["missed"] += missed_now
            f["last"]    = now
            f["charged"] = 0
            f["recent"].extend([0] * missed_now)
            f["recent"].append(1)
            return self._snapshot(f, gap_s=round(dt, 1), missed_now=missed_now)

    def sweep(self, now: float) -> list:
        """
        Charges provisional losses for flows that have gone quiet.

        Returns [(flow, snapshot)] for the flows that changed.
        """
        changed = []
        with self._lock:
            for flow, f in self._flows.items():
                due = max(0, round((now - f["last"]) / f["interval"]) - 1)
                if due > f["charged"]:
                    delta        = due - f["charged"]
                    f["missed"] += delta
                    f["charged"] = due
                    f["recent"].extend([0] * delta)
                    changed.append((flow, self._snapshot(
                        f, gap_s=round(now - f["last"], 1), missed_now=delta)))
        return changed

    def reanchor(self, flow: tuple, now: float):
        """
        Refunds losses charged during a gap that was NOT a radio loss (the node
        was down) and restarts the cadence grid at `now`.
        """
        with self._lock:
            f = self._flows.get(flow)
            if f is None:
                return
            f["missed"] -= f["charged"]
            for _ in range(f["charged"]):
                if f["recent"] and f["recent"][-1] == 0:
                    f["recent"].pop()      # sweep() appended these at the tail
            f["charged"] = 0
            f["last"]    = now
            f["restart"] = True

    # Internals:
    def _new_flow(self, interval: int, window_sec: int, now: float) -> dict:
        slots = max(self.MIN_WINDOW_SLOTS, round(window_sec / interval))
        return {
            "interval":     interval,
            "window_slots": slots,
            "last":         now,
            "rx":           1,    # on-schedule receptions
            "missed":       0,    # inferred losses
            "early":        0,    # off-cadence extra receptions
            "charged":      0,    # losses sweep() already billed for this gap
            "restart":      False,  # next reception starts a new grid (post-reboot)
            "recent":       collections.deque([1], maxlen=slots),
        }

    @staticmethod
    def _snapshot(f: dict, gap_s, missed_now: int) -> dict:
        slots  = f["rx"] + f["missed"]
        window = f["recent"]
        return {
            "pdr":               round(f["rx"] / slots, 4) if slots >= 2 else None,
            "pdr_window":        round(sum(window) / len(window), 4) if window else None,
            "pdr_window_slots":  f["window_slots"],
            "pdr_window_filled": len(window),
            "rx_count":          f["rx"],
            "missed_est":        f["missed"],
            "missed_now":        missed_now,
            "early_count":       f["early"],
            "cadence_violated":  int(f["early"] > 0),
            "gap_s":             gap_s,
        }


class MeshReceiver:
    """
    Connects to the local Meshtastic gateway over serial, listens for telemetry,
    position and PBX-message packets from known nodes, publishes them to MQTT,
    and estimates per-flow packet delivery ratio from the known broadcast
    cadence (see CadencePdrTracker).
    """

    SEEN_MAX = 50

    _APP_FIELDS = ["PRIVATE_APP", "TEXT_MESSAGE_APP", "TELEMETRY_APP", "POSITION_APP"]

    def __init__(self, mqtt: MQTTConnector, known_nodes: dict,
                 intervals: dict = None, pdr_window_sec: dict = None,
                 sweep_interval_sec: int = DEFAULT_SWEEP_INTERVAL_SEC,
                 capture_content: bool = False):
        """
        Args:
            mqtt:            publish-only MQTT connector.
            known_nodes:     {node_id: label}, e.g. {"!0b64122b": "node-1"}.
            intervals:       {label: {kind: seconds}} broadcast cadences, from
                             mesh_config.json.
            pdr_window_sec:  {kind: seconds} rolling-window length per flow kind.
            capture_content: publish PBX message *content*, not just metadata.
                             Off by default: these are real phone messages, and
                             the MQTT stream is persisted into InfluxDB.
        """
        self.mqtt        = mqtt
        self.known_nodes = known_nodes
        self.intervals   = intervals if intervals is not None else {
            label: dict(DEFAULT_INTERVALS) for label in known_nodes.values()
        }
        self.pdr_window_sec = pdr_window_sec or {
            kind: DEFAULT_PDR_WINDOW_SEC for kind in FLOW_KINDS
        }
        self.sweep_interval_sec = sweep_interval_sec
        self.capture_content    = capture_content

        self.seen_ids    = collections.deque(maxlen=self.SEEN_MAX)
        self._seen_lock  = threading.Lock()
        self._pdr        = CadencePdrTracker()
        self._uptime     = {}          # node_id -> last uptimeSeconds seen
        self._uptime_lock = threading.Lock()
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
        print(f"[MESH] Watching nodes: {list(self.known_nodes.keys())}")
        print(f"[MESH] PDR cadences: {self.intervals}")
        print(f"[MESH] PDR windows (s): {self.pdr_window_sec}\n")

    def listen(self):
        """
        Subscribes to incoming packets and blocks until KeyboardInterrupt.
        """
        pub.subscribe(self._on_receive, "meshtastic.receive")
        print("Listening... Ctrl+C to stop.\n")
        next_sweep = time.monotonic() + self.sweep_interval_sec
        try:
            while True:
                time.sleep(1)
                now = time.monotonic()
                if now >= next_sweep:
                    next_sweep = now + self.sweep_interval_sec
                    self._run_sweep(now)
        except KeyboardInterrupt:
            self.close()

    def close(self):
        """Closes serial and MQTT connections cleanly."""
        print("\n[MESH] Shutting down...")
        if self.iface:
            self.iface.close()
        self.mqtt.close()

    # Packet handling:
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
            # Default to 0 explicitly:
            channel    = packet.get("channel", 0)

            if not self._is_valid(sender_id, sender_num, packet.get("id")):
                return

            decoded     = packet["decoded"]
            label       = self.known_nodes[sender_id]
            received_at = int(time.time())
            # Cadence arithmetic runs on the monotonic clock so an NTP step on
            # the host cannot fabricate losses; received_at stays wall-clock
            # because Telegraf uses it as the InfluxDB time key.
            now_mono    = time.monotonic()

            portnum = decoded.get("portnum")
            if portnum not in self._APP_FIELDS:
                return

            if portnum in _FRAME_HDRS:
                payload = decoded.get("payload")
                self._handle_text_message(sender_id, label, payload, portnum,
                                          rssi, snr, hop_taken, channel,
                                          received_at, packet.get("id"))

            if portnum == "POSITION_APP":
                pos       = decoded.get("position", {})
                device_ts = pos.get("time", int(time.time()))
                self._handle_position(sender_id, label, pos, rssi, snr, hop_taken,
                                      channel, device_ts, received_at, now_mono)

            if portnum == "TELEMETRY_APP":
                telem     = decoded.get("telemetry", {})
                device_ts = telem.get("time", int(time.time()))
                self._handle_device_telemetry(sender_id, label, telem, rssi, snr,
                                              hop_taken, channel, device_ts,
                                              received_at, now_mono)
                self._handle_env_telemetry(sender_id, label, telem, rssi, snr,
                                           hop_taken, channel, device_ts,
                                           received_at, now_mono)

        except Exception as e:
            print(f"[MESH] ERROR processing packet: {e}")

    def _is_valid(self, sender_id: str, sender_num: int, packet_id) -> bool:
        """
        Returns False if the packet should be dropped.
        """
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

    # PDR plumbing:
    def _pdr_fields(self, label: str, kind: str, now_mono: float) -> dict:
        """
        Records a reception and returns the PDR fields for the payload.

        Returns {} when the node does not declare a cadence for this flow —
        measuring against an interval the node was never configured with would
        manufacture losses.
        """
        interval = self.intervals.get(label, {}).get(kind)
        if not interval:
            return {}
        window = self.pdr_window_sec.get(kind, DEFAULT_PDR_WINDOW_SEC)
        return self._pdr.observe((label, kind), interval, window, now_mono)

    def _note_uptime(self, node_id: str, label: str, uptime, now_mono: float):
        """
        Detects a node reboot from a decreasing uptimeSeconds and re-anchors all
        of that node's flows.

        uptimeSeconds only rides on device telemetry, but the downtime affected
        every flow, so the reboot is applied node-wide.
        """
        if uptime is None:
            return
        with self._uptime_lock:
            previous = self._uptime.get(node_id)
            self._uptime[node_id] = uptime
        if previous is None or uptime >= previous:
            return
        print(f"[PDR] {label} rebooted (uptime {previous}s -> {uptime}s); "
              f"discarding the downtime gap")
        for kind in self.intervals.get(label, {}):
            self._pdr.reanchor((label, kind), now_mono)

    def _run_sweep(self, now_mono: float):
        """Publishes the losses charged to flows that have gone quiet."""
        received_at = int(time.time())
        for (label, kind), snapshot in self._pdr.sweep(now_mono):
            payload = {
                "node_label":  label,
                "flow":        kind,
                "source":      "sweep",   # inferred while silent, no packet behind it
                "received_at": received_at,
            }
            payload.update(snapshot)
            print(f"[PDR] {label}/{kind} silent {snapshot['gap_s']}s "
                  f"(+{snapshot['missed_now']} missed, pdr={snapshot['pdr']})")
            self.mqtt.publish_pdr(label, payload)

    # Telemetry parsers:
    def _handle_text_message(
            self, node_id: str, label: str, payload: bytes, portnum: str,
            rssi: int, snr: int, hop: int, channel: int, received_at: int,
            pkt_id=None):
        """
        Parses one PBX application frame and publishes its metadata.
        """
        frame = self._parse_pbx_frame(payload, portnum)
        if frame is None:
            n = len(payload) if payload else 0
            print(f"[MSG] Malformed {portnum} frame from {label} ({node_id}): {n} B")
            self.mqtt.publish_message(label, {
                "node_id":     node_id,
                "node_label":  label,
                "portnum":     portnum,
                "channel":     channel,
                # 0/1, not a bool — Telegraf drops boolean fields (see _snapshot).
                # This one matters most: it is the only trace a frame was bad.
                "malformed":   1,
                "payload_len": n,
                "pkt_id":      pkt_id,
                "rssi":        rssi,
                "snr":         snr,
                "hop":         hop,
                "received_at": received_at,
            })
            return

        record = {
            "node_id":     node_id,             # mesh node we heard it FROM (relay)
            "node_label":  label,
            "portnum":     portnum,             # PRIVATE_APP routed / TEXT_MESSAGE_APP broadcast
            "channel":     channel,             # 0=telemetry, 1=messaging (see below)
            "src_id":      frame["src_id"],     # app-level originator (phone)
            "dst_id":      frame["dst_id"],     # None on broadcast frames
            "fw_ver":      frame["fw_ver"],
            "pkt_id":      pkt_id,              # mesh-layer id, for correlation
            "malformed":   0,
            "payload_len": len(payload),
            "content_len": frame["content_len"],
            "rssi":        rssi,
            "snr":         snr,
            "hop":         hop,
            "received_at": received_at,
        }
        if self.capture_content:
            record["content_hex"] = frame["content"].hex()

        print(f"\n[MSG] {frame['src_id']} -> {frame['dst_id'] or 'broadcast'} "
              f"via {label} (fw={frame['fw_ver']}, {frame['content_len']} B, "
              f"hop={hop}, rssi={rssi})")
        self.mqtt.publish_message(label, record)

    @classmethod
    def _parse_pbx_frame(cls, payload, portnum: str) -> dict:
        """
        Unpacks a PBX frame using the header layout its portnum implies
        (see _FRAME_HDRS).  Broadcast frames carry no destination, so "dst_id"
        comes back None rather than a fabricated value.
        """
        hdr = _FRAME_HDRS.get(portnum)
        if hdr is None:
            return None
        if not isinstance(payload, (bytes, bytearray)):
            return None
        if len(payload) < hdr.size:
            return None

        fields = hdr.unpack_from(payload, 0)
        fw_ver = fields[0]
        if fw_ver != _FRAME_VERSION:
            return None

        src    = fields[1]
        dst    = fields[2] if len(fields) > 2 else None
        offset = hdr.size
        return {
            "fw_ver":      fw_ver,
            "src_id":      str(src),
            "dst_id":      str(dst) if dst is not None else None,
            "content":     bytes(payload[offset:]),
            "content_len": len(payload) - offset,
        }

    def _handle_position(
            self, node_id: str, label: str, pos: dict, rssi: int, snr: int, hop: int,
            channel: int, device_ts: int, received_at: int, now_mono: float = None):
        payload = {
            "node_id":     node_id,
            "node_label":  label,
            "rssi":        rssi,
            "snr":         snr,
            "hop":         hop,
            "channel":     channel,
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
        if now_mono is not None:
            payload.update(self._pdr_fields(label, "position", now_mono))
        print(f"\n[POS] Position update from {label} ({node_id})")
        self.mqtt.publish_position(label, payload)

    def _handle_device_telemetry(
            self, node_id: str, label: str, telem: dict, rssi: int, snr: int, hop: int,
            channel: int, device_ts: int, received_at: int, now_mono: float = None):
        device = telem.get("deviceMetrics", {})
        if not device:
            return
        # Reboot check first: it must re-anchor the flows BEFORE this reception
        # is scored, otherwise the downtime gap is charged as radio loss.
        if now_mono is not None:
            self._note_uptime(node_id, label, device.get("uptimeSeconds"), now_mono)
        payload = {
            "node_id":     node_id,
            "node_label":  label,
            "rssi":        rssi,
            "snr":         snr,
            "hop":         hop,
            "channel":     channel,
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
        if now_mono is not None:
            payload.update(self._pdr_fields(label, "device", now_mono))
        print(f"\n[DEVICE] Device telemetry from {label} ({node_id})")
        self.mqtt.publish_device(label, payload)

    def _handle_env_telemetry(
            self, node_id: str, label: str, telem: dict, rssi: int, snr: int, hop: int,
            channel: int, device_ts: int, received_at: int, now_mono: float = None):
        env = telem.get("environmentMetrics", {})
        if not env:
            return
        payload = {
            "node_id":     node_id,
            "node_label":  label,
            "rssi":        rssi,
            "snr":         snr,
            "hop":         hop,
            "channel":     channel,
            "device_ts":   device_ts,
            "received_at": received_at,
        }
        for key, val in [
            ("temperature", env.get("temperature")),
            ("humidity",    env.get("relativeHumidity")),
        ]:
            if val is not None:  # omit absent metrics; 0.0 would be a false reading
                payload[key] = val
        if now_mono is not None:
            payload.update(self._pdr_fields(label, "environment", now_mono))
        print(f"\n[ENV] Environment telemetry from {label} ({node_id})")
        self.mqtt.publish_env(label, payload)
