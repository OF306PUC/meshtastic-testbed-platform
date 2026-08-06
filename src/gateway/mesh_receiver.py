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

# Proxy application frame carried in the PRIVATE_APP payload:
#
#     [fw_ver:1][src_id:4][dst_id:4][seq:2]?[content:N]
#
# Little-endian: the nRF52840 proxy (../meshtastic-ble-proxy) packs the struct
# natively.  If the firmware ever switches to network byte order, flip these to
# ">" — a wrong byte order does not raise, it just yields mirrored src/dst ids
# that look like different nodes, so verify against the firmware before trusting
# per-flow message stats.
_FRAME_HDR = struct.Struct("<BII")   # fw_ver, src_id, dst_id  -> 9 bytes
_FRAME_SEQ = struct.Struct("<H")     # optional app-level sequence counter


class CadencePdrTracker:
    """
    Packet-delivery-ratio estimator for flows with a KNOWN broadcast cadence.

    No sequence numbers involved: losses are inferred from inter-arrival gaps
    against the configured interval T.  On each on-schedule reception:

        slots  = round(dt / T)          # nominal intervals the gap spans
        missed = max(0, slots - 1)

    round(), not floor(): firmware jitter is roughly symmetric around T, so
    floor() would invent a loss on every packet that runs slightly late.

    State is per flow — a (node_label, kind) pair with kind in
    ("device", "environment", "position").  Each flow keeps a cumulative
    ratio and a rolling window whose length is derived from the configured
    window: because missed slots are pushed as 0s alongside received slots as
    1s, `maxlen = window_sec / T` slots IS a time window, with no timestamp
    bookkeeping.

    Accuracy and limitations, stated on purpose:

    * ±1 packet per gap.  The firmware defers broadcasts under channel
      congestion, so T is nominal, not exact.
    * `pdr` can never exceed 1.0 (missed >= 0 by construction).  The signal for
      a broken cadence assumption is `early_count`: a reception arriving well
      inside one nominal interval, which the model does not predict.  The usual
      cause is `position_broadcast_smart_enabled` left on, which adds
      movement-triggered position broadcasts on top of the periodic timer.
    * A powered-off node inflates `missed`.  Reboots are detected from
      deviceMetrics.uptimeSeconds by the caller, which then re-anchors the
      flow so the downtime gap is not charged as radio loss.
    * `pdr` is None until at least one gap has been observed — a single packet
      carries no delivery information, and reporting 1.0 there would be a lie.
    """

    MIN_WINDOW_SLOTS = 3    # a 1-2 slot window is noise, not a measurement

    def __init__(self):
        self._flows = {}
        self._lock  = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

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
                # the cadence model does not predict.  Counted separately and
                # given no slot, and the grid anchor is deliberately NOT moved —
                # advancing `last` here would make the next on-schedule packet
                # look early too, and the whole grid would drift.
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

        Without this the estimator is purely reception-driven, so a node that
        dies keeps its last PDR forever — precisely the survivorship bias the
        measurement exists to avoid.  `charged` records what has already been
        billed for the current gap so the eventual reception does not count it
        twice.

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

        `last` moves to `now` so sweep() stops charging for the downtime, and
        `restart` makes the next reception a grid start rather than a packet
        measured against a gap that never was.
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

    # ── Internals ─────────────────────────────────────────────────────────────

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
            "cadence_violated":  f["early"] > 0,
            "gap_s":             gap_s,
        }


class MeshReceiver:
    """
    Connects to the local Meshtastic gateway over serial, listens for telemetry,
    position and proxy-message packets from known nodes, publishes them to MQTT,
    and estimates per-flow packet delivery ratio from the known broadcast
    cadence (see CadencePdrTracker).

    Proxy text messages have the following structure:
        "[proxy_firmware_version(1 byte)][src_id(4 bytes)][dst_id(4 bytes)][content]"

    They carry NO cadence, so they are captured and published but contribute no
    PDR.  Message-level PDR needs an app-level sequence counter in the frame;
    when the proxy firmware emits one, set FRAME_HAS_SEQ = True and the parser
    picks it up.
    """

    SEEN_MAX = 200

    # Flip to True once the proxy firmware appends a 2-byte sequence counter
    # after the 9-byte header.  Until then `seq` is reported as None.
    FRAME_HAS_SEQ = False

    _APP_FIELDS = ["PRIVATE_APP", "TELEMETRY_APP", "POSITION_APP"]

    def __init__(self, mqtt: MQTTConnector, known_nodes: dict,
                 intervals: dict = None, pdr_window_sec: dict = None,
                 sweep_interval_sec: int = DEFAULT_SWEEP_INTERVAL_SEC,
                 capture_content: bool = False):
        """
        Args:
            mqtt:            publish-only MQTT connector.
            known_nodes:     {node_id: label}, e.g. {"!0b64122b": "node-1"}.
            intervals:       {label: {kind: seconds}} broadcast cadences, from
                             mesh_config.json.  A kind absent for a node means
                             that node does not broadcast it, so no PDR is
                             tracked for that flow.  Defaults to the sensing-node
                             profile for every known node.
            pdr_window_sec:  {kind: seconds} rolling-window length per flow kind.
            capture_content: publish proxy message *content*, not just metadata.
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

        The loop also drives the PDR sweep: losses must be charged while a node
        is silent, otherwise a dead node's PDR would freeze at its last value.
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
            # Cadence arithmetic runs on the monotonic clock so an NTP step on
            # the host cannot fabricate losses; received_at stays wall-clock
            # because Telegraf uses it as the InfluxDB time key.
            now_mono    = time.monotonic()

            if decoded.get("portnum") not in self._APP_FIELDS:
                return

            if decoded.get("portnum") == "PRIVATE_APP":
                payload = decoded.get("payload")
                self._handle_text_message(sender_id, label, payload, rssi, snr,
                                          hop_taken, received_at, packet.get("id"))

            if decoded.get("portnum") == "POSITION_APP":
                pos       = decoded.get("position", {})
                device_ts = pos.get("time", int(time.time()))
                self._handle_position(sender_id, label, pos, rssi, snr, hop_taken,
                                      device_ts, received_at, now_mono)

            if decoded.get("portnum") == "TELEMETRY_APP":
                telem     = decoded.get("telemetry", {})
                device_ts = telem.get("time", int(time.time()))
                self._handle_device_telemetry(sender_id, label, telem, rssi, snr,
                                              hop_taken, device_ts, received_at, now_mono)
                self._handle_env_telemetry(sender_id, label, telem, rssi, snr,
                                           hop_taken, device_ts, received_at, now_mono)

        except Exception as e:
            print(f"[MESH] ERROR processing packet: {e}")

    def _is_valid(self, sender_id: str, sender_num: int, packet_id) -> bool:
        """
        Returns False if the packet should be dropped.

        The dedup step is load-bearing for PDR: a rebroadcast counted as a
        second reception would inflate the ratio.  SEEN_MAX=200 covers roughly
        20 minutes at the testbed's cadences, far longer than the few seconds a
        rebroadcast takes to arrive.
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

    # ── PDR plumbing ──────────────────────────────────────────────────────────

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

    # ── Telemetry parsers ─────────────────────────────────────────────────────

    def _handle_text_message(
            self, node_id: str, label: str, payload: bytes, rssi: int, snr: int,
            hop: int, received_at: int, pkt_id=None):
        """
        Parses one proxy application frame and publishes its metadata.

        No PDR here: phone messages have no cadence to measure against.
        """
        frame = self._parse_proxy_frame(payload)
        if frame is None:
            n = len(payload) if payload else 0
            print(f"[MSG] Malformed frame from {label} ({node_id}): {n} B")
            self.mqtt.publish_message(label, {
                "node_id":     node_id,
                "node_label":  label,
                "malformed":   True,
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
            "src_id":      frame["src_id"],     # app-level originator (proxy/phone)
            "dst_id":      frame["dst_id"],
            "fw_ver":      frame["fw_ver"],
            "seq":         frame["seq"],
            "pkt_id":      pkt_id,              # mesh-layer id, for correlation
            "malformed":   False,
            "payload_len": len(payload),
            "content_len": frame["content_len"],
            "rssi":        rssi,
            "snr":         snr,
            "hop":         hop,
            "received_at": received_at,
        }
        if self.capture_content:
            record["content_hex"] = frame["content"].hex()

        print(f"\n[MSG] {frame['src_id']} -> {frame['dst_id']} via {label} "
              f"(fw={frame['fw_ver']}, seq={frame['seq']}, "
              f"{frame['content_len']} B, hop={hop}, rssi={rssi})")
        self.mqtt.publish_message(label, record)

    @classmethod
    def _parse_proxy_frame(cls, payload) -> dict:
        """
        Unpacks [fw_ver][src_id][dst_id][seq?][content].

        Returns None when the payload is not bytes or is shorter than the
        header — a truncated frame must be reported as a loss, not guessed at.
        """
        if not isinstance(payload, (bytes, bytearray)):
            return None
        if len(payload) < _FRAME_HDR.size:
            return None

        fw_ver, src, dst = _FRAME_HDR.unpack_from(payload, 0)
        offset, seq      = _FRAME_HDR.size, None
        if cls.FRAME_HAS_SEQ and len(payload) >= offset + _FRAME_SEQ.size:
            (seq,)  = _FRAME_SEQ.unpack_from(payload, offset)
            offset += _FRAME_SEQ.size

        return {
            "fw_ver":      fw_ver,
            "src_id":      f"!{src:08x}",   # same shape as Meshtastic node ids
            "dst_id":      f"!{dst:08x}",
            "seq":         seq,
            "content":     bytes(payload[offset:]),
            "content_len": len(payload) - offset,
        }

    def _handle_position(
            self, node_id: str, label: str, pos: dict, rssi: int, snr: int, hop: int,
            device_ts: int, received_at: int, now_mono: float = None):
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
        if now_mono is not None:
            payload.update(self._pdr_fields(label, "position", now_mono))
        print(f"\n[POS] Position update from {label} ({node_id})")
        self.mqtt.publish_position(label, payload)

    def _handle_device_telemetry(
            self, node_id: str, label: str, telem: dict, rssi: int, snr: int, hop: int,
            device_ts: int, received_at: int, now_mono: float = None):
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
            device_ts: int, received_at: int, now_mono: float = None):
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
        if now_mono is not None:
            payload.update(self._pdr_fields(label, "environment", now_mono))
        print(f"\n[ENV] Environment telemetry from {label} ({node_id})")
        self.mqtt.publish_env(label, payload)
