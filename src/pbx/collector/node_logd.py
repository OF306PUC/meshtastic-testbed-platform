"""
node_logd — measurement point P2.

Reads the LiLyGO's Meshtastic console as PLAIN TEXT and reconstructs, per
packet, what the node did with the frames the PBX handed it over UART1.

Why a text parser and not a client: the firmware permits a single Stream API
instance and the PBX already holds it, so a second Meshtastic client on the
node's USB is impossible. The console, however, emits the full transmit
lifecycle in text whenever nobody is speaking protobuf on that port — a passive
read that cannot contend with the PBX.

What it produces that nothing else can: the TX side of message-level PDR. The
gateway records `pkt_id` on reception; this records the same id on transmission,
so the ratio becomes a subtraction instead of an inference. It also separates
"the PBX handed it over but the node never transmitted" from "transmitted and
lost on the air", which no single vantage point could distinguish.

Scope: only packets that entered via `PACKET FROM PHONE` are tracked. The node's
own telemetry also transmits, but that traffic has a known cadence and the
gateway already measures it (CadencePdrTracker); counting it here would
double-report it. It is tallied separately as context.
"""

import re
import time
import collections

# ── Console grammar ─────────────────────────────────────────────────────────
#
# Meshtastic lines look like:
#     DEBUG | 14:31:50 1613 [Serial] Setting next retransmission in 5972 msecs:
#     INFO  | ??:??:?? 165 Tell client we have new packets 22
#
# The wall clock is `??:??:??` until the node learns the time, so it is ignored
# entirely. The second field is uptime in seconds and is ALWAYS present, which
# makes it the only usable ordering key — and a falling value is how a reboot is
# detected. The [Module] tag is optional, and the SAME message appears under
# different modules (txGood shows up under both [Serial] and [Router]), so it is
# captured for context but never matched on.
_PREFIX = re.compile(
    r"^(?:DEBUG|INFO|WARN|ERROR)\s*\|\s*"
    r"(?:\?\?:\?\?:\?\?|\d{2}:\d{2}:\d{2})\s+"
    r"(?P<uptime>\d+)\s+"
    r"(?:\[(?P<module>[A-Za-z_]+)\]\s*)?"
    r"(?P<body>.*)$"
)

# Patterns are anchored to everything they need. A truncated line therefore
# fails to match and is counted as a rejection, rather than half-matching and
# yielding a plausible-looking wrong value.
_HANDED = re.compile(
    r"^PACKET FROM PHONE \(id=0x(?P<pkt>[0-9a-f]+)\b"
    r".*?\bWantAck=(?P<want_ack>[01])\b"
    r".*?\bCh=0x(?P<channel>[0-9a-f]+)\s+Portnum=(?P<portnum>\d+)\b"
)
_TX_STARTED = re.compile(r"^Started Tx \(id=0x(?P<pkt>[0-9a-f]+)\b")
_TX_DONE    = re.compile(r"^Completed sending \(id=0x(?P<pkt>[0-9a-f]+)\b")
_AIRTIME    = re.compile(r"^Packet TX: (?P<ms>\d+)ms\b")
_ACKED      = re.compile(r"^Received a ACK for 0x(?P<pkt>[0-9a-f]+), stopping retransmissions")
_COUNTERS   = re.compile(
    r"^txGood=(?P<tx_good>\d+),txRelay=(?P<tx_relay>\d+),"
    r"rxGood=(?P<rx_good>\d+),rxBad=(?P<rx_bad>\d+)\b"
)
# On over-the-air lines `Ch=` is the channel HASH; on decoded lines it is the
# INDEX. This line is the firmware telling us the mapping between the two, and
# without it the two meanings get conflated into one useless tag.
_CHANNEL_MAP = re.compile(r"^Use channel (?P<index>\d+) \(hash 0x(?P<hash>[0-9a-f]+)\)")

# Outcomes published for a tracked packet.
SENT              = "sent"                # fire-and-forget, confirmed on air
ACKED             = "acked"               # delivery confirmed
UNACKED           = "unacked"            # asked for an ack, never got one
DROPPED_BEFORE_TX = "dropped_before_tx"   # handed over, never reached the air
LOST_TO_REBOOT    = "lost_to_reboot"      # in flight when the node restarted


class NodeLogTracker:
    """
    Assembles the per-packet transmit lifecycle from console lines.

    Emitting one record per packet rather than a stream of raw events is
    deliberate: the whole lifecycle is knowable locally, so assembling it here
    keeps the MQTT volume down and leaves the central reconciler with only the
    join it cannot do locally — this record against the gateway's reception.

    Retransmission accounting is exact rather than heuristic. `WantAck=1` frames
    are re-transmitted until acked, and each attempt emits its own
    `Started Tx`/`Completed sending` for the SAME id, so counting repeats of the
    id gives the attempt count. The `Setting next retransmission` line carries
    its id on the FOLLOWING line, which is why it is not used for this.
    """

    def __init__(self, publish, expiry_sec: float = 120.0, now=time.monotonic):
        """
        Args:
            publish:    callable(kind: str, payload: dict) -> None
            expiry_sec: how long to wait before declaring an unresolved packet
                        finished. Must exceed the firmware's retransmission
                        budget, or a packet still being retried is reported as
                        unacked while it is in fact still in progress.
            now:        monotonic clock, injectable for tests.
        """
        self.publish    = publish
        self.expiry_sec = expiry_sec
        self._now       = now

        self._inflight  = {}     # pkt_id -> record
        self._last_tx   = None   # pkt_id of the most recent Started Tx
        self._uptime    = None
        self._chan_hash = {}     # hash -> index, learned from the console

        self.lines_seen     = 0
        self.lines_parsed   = 0
        self.lines_rejected = 0
        self.tx_other       = 0  # transmissions not originated by the PBX
        self.counters       = {}

    # ── Ingestion ───────────────────────────────────────────────────────────

    def feed(self, line):
        """Consumes one console line, or None as an idle tick."""
        if line is None:
            self._expire()
            return

        # Blank lines are not content and must not count as rejections.
        # lines_rejected exists to signal that the firmware's log format drifted;
        # letting ordinary blank lines inflate it would drown that signal.
        if not line.strip():
            return

        self.lines_seen += 1
        m = _PREFIX.match(line)
        if m is None:
            self.lines_rejected += 1
            return

        uptime = int(m.group("uptime"))
        self._note_uptime(uptime)
        body = m.group("body")

        if self._dispatch(body, uptime):
            self.lines_parsed += 1
        self._expire()

    def _dispatch(self, body: str, uptime: int) -> bool:
        m = _HANDED.match(body)
        if m:
            pkt = int(m.group("pkt"), 16)
            # A repeat means the id was reused after a full lifecycle; keep the
            # first record rather than resetting its attempt count.
            self._inflight.setdefault(pkt, {
                "pkt_id":      pkt,
                "portnum":     int(m.group("portnum")),
                "channel":     int(m.group("channel"), 16),
                "want_ack":    int(m.group("want_ack")),
                "tx_attempts": 0,
                "airtime_ms":  0,
                "uptime":      uptime,
                "_seen":       self._now(),
            })
            return True

        m = _TX_STARTED.match(body)
        if m:
            pkt = int(m.group("pkt"), 16)
            self._last_tx = pkt
            if pkt not in self._inflight:
                self.tx_other += 1
            return True

        m = _AIRTIME.match(body)
        if m:
            # Attributed to the most recent Started Tx: the line carries no id,
            # and the radio transmits one packet at a time.
            rec = self._inflight.get(self._last_tx)
            if rec is not None:
                rec["airtime_ms"] += int(m.group("ms"))
            return True

        m = _TX_DONE.match(body)
        if m:
            pkt = int(m.group("pkt"), 16)
            rec = self._inflight.get(pkt)
            if rec is not None:
                rec["tx_attempts"] += 1
                rec["uptime"] = uptime
                rec["_seen"]  = self._now()
                # Nothing further will ever be logged for a frame that asked for
                # no acknowledgement, so publish now instead of holding state
                # until it expires.
                if not rec["want_ack"]:
                    self._finish(pkt, SENT)
            return True

        m = _ACKED.match(body)
        if m:
            pkt = int(m.group("pkt"), 16)
            if pkt in self._inflight:
                self._inflight[pkt]["uptime"] = uptime
                self._finish(pkt, ACKED)
            return True

        m = _COUNTERS.match(body)
        if m:
            self.counters = {k: int(v) for k, v in m.groupdict().items()}
            self.publish("counters", dict(
                self.counters,
                uptime=uptime,
                inflight=len(self._inflight),
                tx_other=self.tx_other,
                lines_seen=self.lines_seen,
                lines_parsed=self.lines_parsed,
                lines_rejected=self.lines_rejected,
            ))
            return True

        m = _CHANNEL_MAP.match(body)
        if m:
            self._chan_hash[int(m.group("hash"), 16)] = int(m.group("index"))
            return True

        return False

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def _finish(self, pkt: int, outcome: str):
        rec = self._inflight.pop(pkt, None)
        if rec is None:
            return
        rec.pop("_seen", None)
        rec["outcome"] = outcome
        self.publish("tx", rec)

    def _expire(self):
        """
        Resolves packets nothing more will be said about.

        Without this the in-flight map grows without bound, and — worse — a
        packet the node dropped before transmitting would never be reported,
        which is exactly the loss this process exists to make visible.
        """
        deadline = self._now() - self.expiry_sec
        for pkt, rec in list(self._inflight.items()):
            if rec["_seen"] > deadline:
                continue
            self._finish(pkt, UNACKED if rec["tx_attempts"] else DROPPED_BEFORE_TX)

    def _note_uptime(self, uptime: int):
        """
        A falling uptime means the node restarted. Everything in flight died
        with it and will never resolve, so it is flushed rather than left to
        expire as a false `unacked` two minutes later.
        """
        previous = self._uptime
        self._uptime = uptime
        if previous is None or uptime >= previous:
            return
        lost = len(self._inflight)
        for pkt in list(self._inflight):
            self._finish(pkt, LOST_TO_REBOOT)
        self._last_tx = None
        self.publish("event", {
            "event":          "reboot",
            "uptime":         uptime,
            "uptime_before":  previous,
            "inflight_lost":  lost,
        })

    def channel_for_hash(self, hash_byte: int):
        """Index for an over-the-air channel hash, or None if not yet learned."""
        return self._chan_hash.get(hash_byte)


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    import sys
    import json
    import argparse
    from pathlib import Path

    # Puts src/ on the path so `pbx.collector...` and `gateway...` resolve when
    # run directly. parents[2] because this file sits two levels below src/:
    # src/pbx/collector/node_logd.py.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from pbx.collector import config as params
    from pbx.collector.serial_lines import ConsoleReader
    # Shared broker client. It lives under gateway/ for historical reasons and
    # is not gateway-specific; moving it to common/ would be the tidier fix.
    from gateway.mqtt_connector import MQTTConnector

    parser = argparse.ArgumentParser(
        description="Parse the Meshtastic node console and publish TX records")
    parser.add_argument("--port", default=params.NODE_SERIAL_PORT,
                        help="Node console device. Falls back to NODE_SERIAL_PORT. "
                             "Prefer a /dev/serial/by-id/ path.")
    parser.add_argument("--site", default=params.SITE,
                        help="PBX site label (p1|p2); also the broker account.")
    args = parser.parse_args()

    if not args.port:
        parser.error("a serial port is required: pass --port or set NODE_SERIAL_PORT")

    mqtt = MQTTConnector(
        broker_address=params.BROKER_ADDRESS,
        port=params.BROKER_PORT,
        client_id=params.CLIENT_ID,
        username=params.MQTT_USERNAME,
        password=params.MQTT_PASSWORD,
    )
    mqtt.connect()
    mqtt.wait_until_connected()

    topic = params.TOPIC

    def publish(kind: str, payload: dict):
        # node_label and received_at are added here rather than inside the
        # tracker so the tracker stays a pure parser: no clock, no broker, no
        # site identity. `kind` is the tag that separates the three payload
        # shapes on one topic, the same way `source` does on .../pdr.
        body = dict(payload, kind=kind, node_label=args.site,
                    received_at=int(time.time()))
        mqtt.publish(topic, json.dumps(body))

    tracker = NodeLogTracker(publish=publish, expiry_sec=params.EXPIRY_SEC)
    reader  = ConsoleReader(port=args.port,
                            baudrate=params.NODE_BAUDRATE,
                            reconnect_delay=params.RECONNECT_DELAY_SEC,
                            read_timeout=params.READ_TIMEOUT_SEC)

    print(f"[NODE-LOGD] site={args.site} port={args.port} topic={topic}")
    print(f"[NODE-LOGD] expiry={params.EXPIRY_SEC}s  Ctrl+C to stop\n")
    try:
        for line in reader.iter_lines():
            tracker.feed(line)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n[NODE-LOGD] seen={tracker.lines_seen} "
              f"parsed={tracker.lines_parsed} rejected={tracker.lines_rejected} "
              f"inflight={len(tracker._inflight)}")
        reader.close()
        mqtt.close()


if __name__ == "__main__":
    main()
