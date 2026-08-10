"""
Tests for the node_logd console parser (ADR-0002, measurement point P2).

Most cases run against docs/log-parsing.txt, a real capture from the LiLyGO's
Meshtastic console, so the grammar is pinned to observed output rather than to
lines invented to match the regexes.

Two properties of that capture matter for how it is used here:

* It is FOUR captures from TWO devices, concatenated under `[section]` headers.
  Feeding it as one stream is wrong — uptimes jump backwards between sections
  and the same packet id appears on both the sender and the relay that
  rebroadcast it. Tests therefore feed one section at a time.
* Lines were copied out of the VS Code Serial Monitor with the mouse and are
  truncated at ~188 characters. Only `Started Tx` and `Completed sending` are
  affected, and only past the `id=` field they are needed for, so the capture is
  still usable — and the truncated lines double as a rejection test.

Run:
    .venv/bin/python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collector.node_logd import (  # noqa: E402
    NodeLogTracker, ACKED, SENT, UNACKED, DROPPED_BEFORE_TX, LOST_TO_REBOOT,
)

_CAPTURE = Path(__file__).resolve().parents[1] / "docs" / "log-parsing.txt"


def _section(name: str):
    """Returns the lines of one `[section]` of the capture."""
    lines, collecting = [], False
    for raw in _CAPTURE.read_text(errors="replace").splitlines():
        if raw.startswith("["):
            collecting = raw.strip() == f"[{name}]"
            continue
        if collecting:
            lines.append(raw)
    return lines


class Collected:
    """Captures what the tracker publishes, per kind."""

    def __init__(self):
        self.tx, self.counters, self.events = [], [], []

    def __call__(self, kind, payload):
        {"tx": self.tx, "counters": self.counters,
         "event": self.events}[kind].append(payload)

    def tx_for(self, pkt_id):
        return next((r for r in self.tx if r["pkt_id"] == pkt_id), None)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


# ---------------------------------------------------------------------------
# Against the real capture
# ---------------------------------------------------------------------------

class TestRealCaptureTextMessage(unittest.TestCase):
    """`[meshtastic-sender portnum=1]` — a broadcast text frame, acked."""

    def setUp(self):
        self.out  = Collected()
        self.trk  = NodeLogTracker(publish=self.out)
        for line in _section("meshtastic-sender portnum=1"):
            self.trk.feed(line)

    def test_the_phone_frame_is_tracked_end_to_end(self):
        rec = self.out.tx_for(0x3A5F05E6)
        self.assertIsNotNone(rec, "the PACKET FROM PHONE frame was not tracked")
        self.assertEqual(rec["outcome"], ACKED)
        self.assertEqual(rec["tx_attempts"], 1)
        self.assertEqual(rec["want_ack"], 1)
        self.assertEqual(rec["portnum"], 1)
        self.assertEqual(rec["channel"], 1)

    def test_node_originated_traffic_is_counted_but_not_tracked(self):
        """
        The node's own telemetry also transmits. It has a known cadence and the
        gateway already measures it, so counting it here would double-report it.
        """
        self.assertIsNone(self.out.tx_for(0xB185A79D))
        self.assertGreaterEqual(self.trk.tx_other, 1)

    def test_airtime_is_attributed_to_the_transmitting_packet(self):
        rec = self.out.tx_for(0x3A5F05E6)
        self.assertGreater(rec["airtime_ms"], 0)

    def test_counters_are_published(self):
        self.assertTrue(self.out.counters, "no txGood line was parsed")
        last = self.out.counters[-1]
        for key in ("tx_good", "tx_relay", "rx_good", "rx_bad"):
            self.assertIn(key, last)

    def test_most_of_the_console_is_ignored_without_being_rejected(self):
        """
        The capture holds ~85 distinct line shapes and only seven matter. What
        the parser must not do is count the other seventy-eight as rejections —
        rejections are reserved for lines that looked parseable and were not,
        which is the signal that the firmware's format drifted.
        """
        self.assertGreater(self.trk.lines_seen, self.trk.lines_parsed)
        self.assertEqual(self.trk.lines_rejected, 0)


class TestRealCapturePrivateApp(unittest.TestCase):
    """`[meshtastic-sender portnum=256]` — the proxy's routed carrier."""

    def setUp(self):
        self.out = Collected()
        self.trk = NodeLogTracker(publish=self.out)
        for line in _section("meshtastic-sender portnum=256"):
            self.trk.feed(line)

    def test_private_app_frame_is_tracked_with_its_channel(self):
        rec = self.out.tx_for(0x19F326BA)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["outcome"], ACKED)
        self.assertEqual(rec["portnum"], 256)
        # The capture was recorded while the phone app still built PRIVATE_APP
        # on channel 0; it has since been fixed to channel 1. This pins that the
        # parser reports whatever the console said, which is the only thing a
        # frozen fixture can prove — the live check for the fix is
        # `SELECT DISTINCT channel FROM proxy_message WHERE portnum='PRIVATE_APP'`
        # against InfluxDB, not this test.
        self.assertEqual(rec["channel"], 0)


class TestChannelHashMapping(unittest.TestCase):
    """
    `Ch=` is the channel HASH on over-the-air lines and the INDEX on decoded
    ones. Conflating them would produce a meaningless tag.

    The mapping is only ever logged by a RECEIVING node — it has to resolve the
    hash to pick a key — so a sender's console never carries it. A collector
    sitting on a proxy node therefore learns the table only from traffic that
    node receives, and must treat an unknown hash as unknown rather than
    guessing zero.
    """

    def setUp(self):
        self.out = Collected()
        self.trk = NodeLogTracker(publish=self.out)
        for line in _section("meshtastic-receiver portnum=1"):
            self.trk.feed(line)

    def test_mapping_is_learned_from_received_traffic(self):
        self.assertEqual(self.trk.channel_for_hash(0xEC), 0)

    def test_an_unseen_hash_stays_unknown(self):
        self.assertIsNone(self.trk.channel_for_hash(0x99))


# ---------------------------------------------------------------------------
# Lifecycle edges, driven synthetically
# ---------------------------------------------------------------------------

def _handed(pkt, uptime=100, want_ack=1, portnum=256, ch=0):
    return (f"DEBUG | 14:00:00 {uptime} [Serial] PACKET FROM PHONE "
            f"(id=0x{pkt:08x} fr=0xbb8106c4 to=0xffffffff, transport = 0, "
            f"WantAck={want_ack}, HopLim=1 Ch=0x{ch:x} Portnum={portnum} hopStart=1)")


def _started(pkt, uptime=100):
    return (f"DEBUG | 14:00:00 {uptime} [RadioIf] Started Tx "
            f"(id=0x{pkt:08x} fr=0xbb8106c4 to=0xffffffff, transport = 0")


def _done(pkt, uptime=100):
    return (f"DEBUG | 14:00:00 {uptime} [RadioIf] Completed sending "
            f"(id=0x{pkt:08x} fr=0xbb8106c4 to=0xffffffff, transport = 0")


class TestLifecycleOutcomes(unittest.TestCase):

    def setUp(self):
        self.out   = Collected()
        self.clock = FakeClock()
        self.trk   = NodeLogTracker(publish=self.out, expiry_sec=60,
                                    now=self.clock)

    def test_fire_and_forget_publishes_as_soon_as_it_is_on_air(self):
        """No ack will ever be logged for WantAck=0, so holding state is waste."""
        self.trk.feed(_handed(0xAAAA1111, want_ack=0))
        self.trk.feed(_started(0xAAAA1111))
        self.trk.feed(_done(0xAAAA1111))

        rec = self.out.tx_for(0xAAAA1111)
        self.assertEqual(rec["outcome"], SENT)
        self.assertEqual(rec["tx_attempts"], 1)

    def test_retransmissions_are_counted_per_attempt(self):
        """
        Each retry re-emits Started Tx and Completed sending for the SAME id.
        Counting repeats is exact; counting Completed sending events across all
        ids would overcount, and the `Setting next retransmission` line cannot
        be used because it carries its id on the following line.
        """
        self.trk.feed(_handed(0xBBBB2222))
        for _ in range(3):
            self.trk.feed(_started(0xBBBB2222))
            self.trk.feed(_done(0xBBBB2222))
        self.trk.feed("DEBUG | 14:00:05 105 [Router] Received a ACK for "
                      "0xbbbb2222, stopping retransmissions")

        rec = self.out.tx_for(0xBBBB2222)
        self.assertEqual(rec["tx_attempts"], 3)
        self.assertEqual(rec["outcome"], ACKED)

    def test_handed_over_but_never_transmitted_is_its_own_outcome(self):
        """
        This is the distinction no single vantage point could make before:
        the proxy handed the frame over and the node never put it on the air, so
        the loss belongs to the UART handoff and not to the radio.
        """
        self.trk.feed(_handed(0xCCCC3333))
        self.clock.advance(61)
        self.trk.feed(None)                     # idle tick drives expiry

        rec = self.out.tx_for(0xCCCC3333)
        self.assertEqual(rec["outcome"], DROPPED_BEFORE_TX)
        self.assertEqual(rec["tx_attempts"], 0)

    def test_transmitted_but_never_acked_expires_as_unacked(self):
        self.trk.feed(_handed(0xDDDD4444))
        self.trk.feed(_started(0xDDDD4444))
        self.trk.feed(_done(0xDDDD4444))
        self.clock.advance(61)
        self.trk.feed(None)

        rec = self.out.tx_for(0xDDDD4444)
        self.assertEqual(rec["outcome"], UNACKED)
        self.assertEqual(rec["tx_attempts"], 1)

    def test_expiry_happens_on_an_idle_tick_not_only_on_new_lines(self):
        """
        A node that goes quiet right after a transmission would otherwise hold
        the packet forever, and the in-flight map would only ever grow.
        """
        self.trk.feed(_handed(0xEEEE5555))
        self.clock.advance(61)
        self.assertFalse(self.out.tx)
        self.trk.feed(None)
        self.assertTrue(self.out.tx)

    def test_reboot_flushes_everything_in_flight(self):
        """
        A falling uptime means the node restarted; in-flight packets died with
        it. Flushing them beats letting them expire as false `unacked` later.
        """
        self.trk.feed(_handed(0xFFFF6666, uptime=500))
        self.trk.feed(_started(0xFFFF6666, uptime=500))
        self.trk.feed(_handed(0xFFFF7777, uptime=12))     # uptime went backwards

        self.assertEqual(self.out.tx_for(0xFFFF6666)["outcome"], LOST_TO_REBOOT)
        self.assertEqual(len(self.out.events), 1)
        self.assertEqual(self.out.events[0]["event"], "reboot")
        self.assertEqual(self.out.events[0]["inflight_lost"], 1)


class TestMalformedInput(unittest.TestCase):
    """The stream carries truncated lines and post-overrun garbage."""

    def setUp(self):
        self.out = Collected()
        self.trk = NodeLogTracker(publish=self.out)

    def test_a_truncated_handed_line_is_rejected_not_half_parsed(self):
        """
        Cutting the line before Portnum leaves a plausible prefix. Accepting it
        would invent a packet with no portnum and no channel; the whole point of
        anchoring the pattern is that this fails instead.
        """
        full = _handed(0x12345678, portnum=256, ch=1)
        cut  = full[:full.index("Ch=0x")]
        self.trk.feed(cut)

        self.assertFalse(self.out.tx)
        self.assertEqual(self.trk.lines_parsed, 0)

    def test_garbage_is_counted_as_a_rejection(self):
        self.trk.feed("\x00\xff not a log line at all")
        self.assertEqual(self.trk.lines_rejected, 1)

    def test_a_line_with_no_module_tag_still_parses_its_prefix(self):
        """Some lines carry no [Module]; the prefix must not require one."""
        self.trk.feed("INFO  | ??:??:?? 165 Tell client we have new packets 22")
        self.assertEqual(self.trk.lines_rejected, 0)

    def test_ack_for_an_untracked_packet_is_ignored(self):
        """Relays ack packets we never originated; they are not ours to report."""
        self.trk.feed("DEBUG | 14:00:00 100 [Router] Received a ACK for "
                      "0xdeadbeef, stopping retransmissions")
        self.assertFalse(self.out.tx)


if __name__ == "__main__":
    unittest.main()
