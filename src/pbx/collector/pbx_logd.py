"""
pbx_logd — measurement point P1 of ADR-0002.

Reads the nRF52840's Zephyr console over the J-Link VCOM and turns it into
counters and events. This is the vantage point the mesh cannot provide: what
happened between the phone and the node, on the BLE and UART sides, where the
gateway sees nothing at all.

Grammar, taken from a real capture rather than from the firmware's format
strings:

    [HH:MM:SS.mmm,uuu] <lvl> module: [function: ]message

Three properties of that stream shaped this parser, and all three were only
visible in captured output:

1. **Never anchor on a non-ASCII character.** The firmware writes `→` and `—`
   in its messages, and the serial stream corrupts multi-byte sequences: one
   capture held 43 replacement characters against 69 intact ones, and the same
   line appeared in three different mangled forms. A pattern containing `→`
   loses a fifth of its matches, silently.
2. **Hexdump rows carry no prefix.** `LOG_HEXDUMP_INF` emits continuation lines
   of raw hex. They are expected output, not damage, so they are skipped without
   counting as rejections — `lines_rejected` has to stay meaningful as a
   format-drift alarm.
3. **`fromnum` is a per-phone sequence number, and it resets.** Gaps of +1 are
   consecutive deliveries; +2 is a miss; a decrease is a new session, not a loss.
   Charging a session restart as loss would invent failures on every reconnect.

The firmware emits no cumulative counters of its own, so unlike the Meshtastic
side there is nothing self-healing to lean on: a log line Zephyr drops is a
permanently undercounted event. The counters here are therefore published
alongside `lines_seen`, so the undercount is at least visible even though it is
not correctable.

Message content is never published. `CONFIG_MESHTASTIC_ROUTE_TRACE` prints
message text on the console, and the ROUTE pattern below deliberately stops
before the `content=` field.
"""

import re
import time
import collections

# ── Console grammar ─────────────────────────────────────────────────────────
#
# The timestamp is uptime since boot, not a wall clock, which makes it the only
# usable ordering key and — when it goes backwards — the reboot signal. Hours are
# `\d+` rather than `\d{2}`: a proxy left running for days prints past 99 hours.
# The [function:] segment appears on <dbg> lines only, so it is folded into the
# body and matched there when a pattern needs it.
_PREFIX = re.compile(
    r"^\[(?P<hh>\d+):(?P<mm>\d{2}):(?P<ss>\d{2})\.(?P<ms>\d{3}),\d{3}\]\s+"
    r"<(?P<level>\w+)>\s+(?P<module>\w+):\s+(?P<body>.*)$"
)

# A hexdump continuation row: leading blanks, hex pairs, then the ASCII column.
# Recognised so it can be skipped deliberately rather than counted as garbage.
_HEXDUMP_ROW = re.compile(r"^\s+([0-9a-f]{2}[ ]+)+\s*\|")

# Zephyr's boot banners, printed before the log subsystem starts and therefore
# without a prefix. Not noise: they are the most reliable reboot marker there is.
# Detecting a restart from the uptime going backwards requires having already
# seen a line from the previous boot, so a collector that attaches after a reset
# never learns one happened. The banner says so outright.
_BOOT_BANNER = re.compile(r"^\*\*\* (?:Booting|Using) \S+")

# The firmware writes the country code as a LITERAL in its format string
# (`LOG_INF("proxy_id [CL phone]: +56%" PRIu32 ...)`), so it is not part of the
# identifier. Anchoring it here keeps the published phone id equal to the value
# in the ROUTE header — capturing it as part of the number made the two
# renderings of the same phone impossible to join.
_CC = r"\+56"

# ── Whitelisted lines ───────────────────────────────────────────────────────
# Every pattern is pure ASCII and anchored to the fields it needs. A line that
# does not fully match is left alone rather than half-parsed.

# Uplink, phone -> node. `passthrough` carries app traffic; heartbeat and
# want_config are the session's own chatter and are counted separately so they
# do not inflate a delivery denominator.
_UP_DATA      = re.compile(r"^proto_decode_toradio: ToRadio variant=\d+, portnum=(?P<portnum>\d+)")
_UP_HEARTBEAT = re.compile(r"^proto_decode_toradio: ToRadio heartbeat")
_UP_WANTCFG   = re.compile(r"^proto_decode_toradio: ToRadio want_config_id=(?P<nonce>\d+)")

# Downlink, node -> phone.
_DN_DATA = re.compile(
    r"^proto_decode_fromradio: packet from=0x(?P<src>[0-9a-f]+) to=0x(?P<dst>[0-9a-f]+) "
    r"portnum=(?P<portnum>\d+) payload=(?P<bytes>\d+) B")
_DN_META = re.compile(r"^proto_decode_fromradio: variant=\d+ \(config/meta\)")
_DN_ENCR = re.compile(r"^proto_decode_fromradio: packet from=0x[0-9a-f]+ to=0x[0-9a-f]+ \(encrypted\)")

# Per-phone delivery sequence. The only counter the firmware already maintains.
_FROMNUM = re.compile(rf"^FROMNUM notification to={_CC}(?P<phone>\d+) fromnum=(?P<seq>\d+)")

# BLE session. `Connected [slot N]` is followed by an em-dash and the active
# count; the pattern stops at the bracket so a corrupted dash cannot break it.
_REGISTER  = re.compile(rf"^proxy_id \[CL phone\]: {_CC}(?P<phone>\d+)\. Register for slot (?P<slot>\d+)")
_CONNECT   = re.compile(r"^Connected \[slot (?P<slot>\d+)\]")
_DISCONN   = re.compile(r"^Disconnected \[slot (?P<slot>\d+)\] \(reason 0x(?P<reason>[0-9a-f]+)\)")
_NO_SLOT   = re.compile(r"^No free connection slot")

# Per-frame routing identities. Stops before `content=` on purpose: that field
# holds real message text and must never leave this process.
#
# These records exist to answer "who talked to whom", not to be counted: their
# InfluxDB time key is `received_at` at one-second resolution, and their tag set
# is (kind, direction, src_id, dst_id), so two frames in the same second between
# the same pair land on the same point and one overwrites the other. The totals
# live in route_up/route_dn, which cannot collide. Measured: fourteen frames
# published within one second arrived as two points.
_ROUTE = re.compile(
    r"^ROUTE (?P<dir>UP|DN)\s+hdr=\[(?P<ver>[0-9a-f]+)\]"
    r"\[0x(?P<src>[0-9a-f]+)\]\[0x(?P<dst>[0-9a-f]+)\]")

# Loss and fault lines. NOT PRESENT in any capture, and there will not be one:
# the testbed does not reach congestion on demand. These are therefore the one
# part of this parser that observed data can never confirm, so
#
#     a zero count here means "never matched", which is indistinguishable
#     from "never happened".
#
# Any analysis leaning on these being zero is leaning on a transcription. The
# narrow risk that remains — a format string typed wrong, so the pattern would
# not fire even during real congestion — is closed by
# tests/test_pbx_logd.py::TestLossPatternsAgainstFirmwareSource, which extracts
# the LOG_WRN strings from ../meshpbx/src, renders them and feeds them here. That
# catches a reworded message; it cannot catch one the firmware never emits.
_TX_DROP      = re.compile(r"^TX queue full")
_RX_OVERRUN   = re.compile(r"^RX overrun: (?P<bytes>\d+) byte")
_RX_RESYNC    = re.compile(r"(?:Bad frame length .* resyncing|RX frame stalled mid-frame)")
_PHONE_Q_FULL = re.compile(r"^FromRadio queue full for conn")
_BAD_HEADER   = re.compile(r"^PROXY_PORTNUM: bad header")
_NODE_REBOOT  = re.compile(r"^node rebooted")
_SESS_REFETCH = re.compile(r"session presumed dead")

# Portnum of the PBX's own routed carrier, per the wire contract.
PBX_PORTNUM = 256

# Every counter this parser can emit, published on every dump whether it fired or
# not. A field that appears only once it is non-zero is awkward to query and
# indistinguishable from a parser that never looked for it; a stable schema means
# "0" says "looked, found none".
_COUNTER_KEYS = (
    "up_data", "up_pbx", "up_heartbeat", "up_want_config",
    "dn_data", "dn_pbx", "dn_meta", "dn_encrypted", "dn_payload_bytes",
    "route_up", "route_dn",
    "registrations", "ble_connect", "ble_disconnect", "ble_rejected",
    "fromnum_missed", "phone_session_resets",
    "tx_dropped", "phone_queue_dropped", "broadcast_fallback",
    "rx_overrun_bytes", "rx_resync",
    "node_reboots", "pbx_reboots", "session_refetch",
    "boot_banners",
)


class PbxLogTracker:
    """
    Turns nRF52840 console lines into cumulative counters and discrete events.

    Deliberately holds almost no state: unlike the node-side parser there is no
    per-packet lifecycle to assemble here, because the proxy's log does not carry
    an identifier that ties its lines together. What it does carry is per-phone
    delivery sequences, and those are the one thing worth tracking across lines.
    """

    def __init__(self, publish, counters_every: float = 30.0, now=time.monotonic):
        """
        Args:
            publish:        callable(kind: str, payload: dict) -> None
            counters_every: seconds between counter publications. Matched to the
                            gateway's PDR sweep interval so both sides of a
                            measurement land on the same grid.
            now:            monotonic clock, injectable for tests.
        """
        self.publish        = publish
        self.counters_every = counters_every
        self._now           = now
        self._next_counters = now() + counters_every

        self.counters = collections.Counter()
        self.phones   = {}      # phone -> {seq, delivered, gaps, missed, resets}
        self.slots    = {}      # slot  -> phone, from NODE_REG
        self.uptime_s = None

        self.lines_seen     = 0
        self.lines_parsed   = 0
        self.lines_rejected = 0
        self.lines_hexdump  = 0

    # ── Ingestion ───────────────────────────────────────────────────────────

    def feed(self, line):
        """Consumes one console line, or None as an idle tick."""
        if line is None:
            self._maybe_publish_counters()
            return
        if not line.strip():
            return

        m = _PREFIX.match(line)
        if m is None:
            # Hexdump rows and boot banners are expected output with no prefix;
            # anything else is drift or damage.
            if _HEXDUMP_ROW.match(line):
                self.lines_hexdump += 1
            elif _BOOT_BANNER.match(line):
                self._note_boot(line)
            else:
                self.lines_rejected += 1
            return

        self.lines_seen += 1
        self._note_uptime(
            int(m.group("hh")) * 3600 + int(m.group("mm")) * 60 + int(m.group("ss")))

        if self._dispatch(m.group("body")):
            self.lines_parsed += 1
        self._maybe_publish_counters()

    def _dispatch(self, body: str) -> bool:
        c = self.counters

        # ── Uplink ──────────────────────────────────────────────────────────
        m = _UP_DATA.match(body)
        if m:
            c["up_data"] += 1
            if int(m.group("portnum")) == PBX_PORTNUM:
                c["up_pbx"] += 1
            return True
        if _UP_HEARTBEAT.match(body):
            c["up_heartbeat"] += 1
            return True
        if _UP_WANTCFG.match(body):
            c["up_want_config"] += 1
            return True

        # ── Downlink ────────────────────────────────────────────────────────
        m = _DN_DATA.match(body)
        if m:
            c["dn_data"] += 1
            c["dn_payload_bytes"] += int(m.group("bytes"))
            if int(m.group("portnum")) == PBX_PORTNUM:
                c["dn_pbx"] += 1
            return True
        if _DN_ENCR.match(body):
            # Could not be inspected, so it is broadcast to every phone. Counted
            # apart: it is neither a routed delivery nor a failure.
            c["dn_encrypted"] += 1
            return True
        if _DN_META.match(body):
            c["dn_meta"] += 1
            return True

        # ── Per-phone delivery sequence ─────────────────────────────────────
        m = _FROMNUM.match(body)
        if m:
            self._note_fromnum(m.group("phone"), int(m.group("seq")))
            return True

        # ── BLE session ─────────────────────────────────────────────────────
        m = _REGISTER.match(body)
        if m:
            phone, slot = m.group("phone"), int(m.group("slot"))
            c["registrations"] += 1
            self.slots[slot] = phone
            self._phone(phone)          # so it appears even before any delivery
            self.publish("event", {"event": "register", "phone": phone,
                                   "slot": slot, "uptime": self.uptime_s})
            return True
        m = _CONNECT.match(body)
        if m:
            c["ble_connect"] += 1
            self.publish("event", {"event": "connect",
                                   "slot": int(m.group("slot")),
                                   "uptime": self.uptime_s})
            return True
        m = _DISCONN.match(body)
        if m:
            slot = int(m.group("slot"))
            c["ble_disconnect"] += 1
            # A phone's fromnum restarts on the next session, so forget the
            # mapping rather than attributing the next sequence to a stale slot.
            phone = self.slots.pop(slot, None)
            self.publish("event", {"event": "disconnect", "slot": slot,
                                   "phone": phone,
                                   "reason": int(m.group("reason"), 16),
                                   "uptime": self.uptime_s})
            return True
        if _NO_SLOT.match(body):
            c["ble_rejected"] += 1
            self.publish("event", {"event": "slots_exhausted",
                                   "uptime": self.uptime_s})
            return True

        # ── Per-frame identities ────────────────────────────────────────────
        m = _ROUTE.match(body)
        if m:
            direction = "up" if m.group("dir") == "UP" else "dn"
            c[f"route_{direction}"] += 1
            self.publish("frame", {
                "direction": direction,
                "fw_ver":    int(m.group("ver"), 16),
                # Big-endian, matching the wire contract and the firmware's own
                # rendering; see memory pbx-frame-wire-format.
                "src_id":    str(int(m.group("src"), 16)),
                "dst_id":    str(int(m.group("dst"), 16)),
                "uptime":    self.uptime_s,
            })
            return True

        # ── Losses and faults (unverified against captured data) ─────────────
        for pattern, key in ((_TX_DROP, "tx_dropped"),
                             (_PHONE_Q_FULL, "phone_queue_dropped"),
                             (_BAD_HEADER, "broadcast_fallback"),
                             (_NODE_REBOOT, "node_reboots"),
                             (_SESS_REFETCH, "session_refetch")):
            if pattern.search(body):
                c[key] += 1
                self.publish("event", {"event": key, "uptime": self.uptime_s})
                return True
        m = _RX_OVERRUN.match(body)
        if m:
            c["rx_overrun_bytes"] += int(m.group("bytes"))
            return True
        if _RX_RESYNC.search(body):
            c["rx_resync"] += 1
            return True

        return False

    # ── Per-phone sequence ──────────────────────────────────────────────────

    def _phone(self, phone: str) -> dict:
        return self.phones.setdefault(phone, {
            "seq": None, "delivered": 0, "missed": 0, "gaps": 0, "resets": 0,
        })

    def _note_fromnum(self, phone: str, seq: int):
        """
        Tracks one phone's delivery sequence.

        `fromnum` counts packets made available to that phone, so consecutive
        values mean nothing was lost between them. A step of more than one means
        a notification did not reach the log — either it was never emitted or
        Zephyr dropped the line, and this parser cannot tell those apart, which
        is exactly why the gap is reported rather than silently smoothed.

        A DECREASE is a new BLE session, not a loss. Observed in the reference
        capture: one phone ran 43 upward then restarted at 18. Charging that as
        25 missed packets would manufacture a failure out of a reconnect.
        """
        st = self._phone(phone)
        prev = st["seq"]
        st["seq"] = seq

        if prev is None:
            st["delivered"] += 1
            return
        if seq < prev:
            st["resets"] += 1
            self.counters["phone_session_resets"] += 1
            self.publish("event", {"event": "fromnum_reset", "phone": phone,
                                   "seq_before": prev, "seq_after": seq,
                                   "uptime": self.uptime_s})
            st["delivered"] += 1
            return

        step = seq - prev
        st["delivered"] += 1
        if step > 1:
            st["gaps"] += 1
            st["missed"] += step - 1
            self.counters["fromnum_missed"] += step - 1

    # ── Reboot detection ────────────────────────────────────────────────────

    def _note_boot(self, banner: str):
        """
        Handles a Zephyr boot banner: an unambiguous restart, independent of
        whether any line from the previous boot was ever seen.

        Two banners are printed per boot ("Booting nRF Connect SDK" then "Using
        Zephyr OS"), so the pair is collapsed into one reset by forgetting state
        only when there is state to forget.
        """
        self.counters["boot_banners"] += 1
        had = len(self.phones) or len(self.slots) or self.uptime_s is not None
        if not had:
            return
        self.counters["pbx_reboots"] += 1
        phones, slots = len(self.phones), len(self.slots)
        self.phones.clear()
        self.slots.clear()
        self.uptime_s = None
        self.publish("event", {"event": "pbx_boot", "banner": banner[:80],
                               "phones_forgotten": phones,
                               "slots_forgotten": slots})

    def _note_uptime(self, uptime_s: int):
        """
        A falling uptime means the nRF52840 restarted. Its counters restart with
        it, so the per-phone sequences and slot map are dropped: keeping them
        would compare a fresh session against a dead one's numbers.
        """
        prev = self.uptime_s
        self.uptime_s = uptime_s
        if prev is None or uptime_s >= prev:
            return
        self.counters["pbx_reboots"] += 1
        phones, slots = len(self.phones), len(self.slots)
        self.phones.clear()
        self.slots.clear()
        self.publish("event", {"event": "pbx_reboot", "uptime": uptime_s,
                               "uptime_before": prev, "phones_forgotten": phones,
                               "slots_forgotten": slots})

    # ── Publication ─────────────────────────────────────────────────────────

    def _maybe_publish_counters(self):
        if self._now() < self._next_counters:
            return
        self._next_counters = self._now() + self.counters_every
        self.publish_counters()

    def publish_counters(self):
        """
        Emits the cumulative counters plus the parser's own health.

        lines_seen/parsed/rejected travel with the counters on purpose. The
        firmware keeps no running totals, so a log line Zephyr drops is an event
        lost for good; these three at least make the shortfall visible, and a
        rising lines_rejected is the only warning that the log format has moved.
        """
        base = {k: 0 for k in _COUNTER_KEYS}
        base.update(self.counters)
        self.publish("counters", dict(
            base,
            uptime=self.uptime_s,
            phones_known=len(self.phones),
            slots_active=len(self.slots),
            lines_seen=self.lines_seen,
            lines_parsed=self.lines_parsed,
            lines_rejected=self.lines_rejected,
            lines_hexdump=self.lines_hexdump,
        ))
        for phone, st in sorted(self.phones.items()):
            self.publish("phone", {
                "phone":     phone,
                "fromnum":   st["seq"],
                "delivered": st["delivered"],
                "missed":    st["missed"],
                "gaps":      st["gaps"],
                "resets":    st["resets"],
                "uptime":    self.uptime_s,
            })


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    import sys
    import json
    import argparse
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from pbx.collector import config as params
    from pbx.collector.serial_lines import ConsoleReader
    from gateway.mqtt_connector import MQTTConnector

    parser = argparse.ArgumentParser(
        description="Parse the nRF52840 PBX console and publish counters")
    parser.add_argument("--port", default="",
                        help="nRF52840 VCOM. Prefer a /dev/serial/by-id/ path.")
    parser.add_argument("--site", default=params.SITE,
                        help="PBX site label (p1|p2); also the broker account.")
    args = parser.parse_args()

    port = args.port or params.PBX_SERIAL_PORT
    if not port:
        parser.error("a serial port is required: pass --port or set PBX_SERIAL_PORT")

    mqtt = MQTTConnector(
        broker_address=params.BROKER_ADDRESS,
        port=params.BROKER_PORT,
        client_id=f"{params.CLIENT_ID}-pbx",
        username=params.MQTT_USERNAME,
        password=params.MQTT_PASSWORD,
    )
    mqtt.connect()
    mqtt.wait_until_connected()
    topic = params.TOPIC

    def publish(kind: str, payload: dict):
        body = dict(payload, kind=kind, node_label=args.site,
                    received_at=int(time.time()))
        mqtt.publish(topic, json.dumps(body))

    tracker = PbxLogTracker(publish=publish,
                            counters_every=params.PBX_COUNTERS_EVERY_SEC)
    reader  = ConsoleReader(port=port,
                            baudrate=params.NODE_BAUDRATE,
                            reconnect_delay=params.RECONNECT_DELAY_SEC,
                            read_timeout=params.READ_TIMEOUT_SEC)

    print(f"[PBX-LOGD] site={args.site} port={port} topic={topic}\n")
    try:
        for line in reader.iter_lines():
            tracker.feed(line)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n[PBX-LOGD] seen={tracker.lines_seen} parsed={tracker.lines_parsed} "
              f"rejected={tracker.lines_rejected} hexdump={tracker.lines_hexdump}")
        reader.close()
        mqtt.close()


if __name__ == "__main__":
    main()
