"""
Tests for the nRF52840 (PBX) console parser — ADR-0002 measurement point P1.

Most cases run against tests/fixtures/pbx-console.txt, a sanitised real session:
two phones register, exchange messages both ways, and disconnect. Phone numbers
were remapped consistently so the fromnum sequences and ROUTE header ids still
line up; message content and hexdump bytes are redacted. The raw capture stays
gitignored under captures/ because it carries real numbers and real message text.

What the fixture does NOT contain is any congestion event — no TX queue full, no
RX overrun, no phone queue full. That session simply had none, so those patterns
are exercised synthetically below and remain unconfirmed against observed output.
A zero count for them in production means "never seen", not "verified working".

Run:
    .venv/bin/python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pbx.collector.pbx_logd import PbxLogTracker, _PREFIX  # noqa: E402

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pbx-console.txt"


def _fixture_lines():
    return [l for l in _FIXTURE.read_text().splitlines() if not l.startswith("#")]


class Collected:
    def __init__(self):
        self.by_kind = {}

    def __call__(self, kind, payload):
        self.by_kind.setdefault(kind, []).append(payload)

    def events(self, name):
        return [e for e in self.by_kind.get("event", []) if e.get("event") == name]

    @property
    def counters(self):
        return self.by_kind.get("counters", [])


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def _run_fixture(clock=None):
    out = Collected()
    trk = PbxLogTracker(publish=out, counters_every=1e9,
                        now=clock or (lambda: 0.0))
    for line in _fixture_lines():
        trk.feed(line)
    trk.publish_counters()
    return trk, out


# ---------------------------------------------------------------------------
# Against the sanitised real session
# ---------------------------------------------------------------------------

class TestRealSession(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.trk, cls.out = _run_fixture()
        cls.c = cls.out.counters[-1]

    def test_the_grammar_covers_the_session(self):
        """
        Every prefixed line must parse. A rejection here means the Zephyr log
        format moved, which is the one thing lines_rejected exists to announce.
        """
        self.assertEqual(self.trk.lines_rejected, 0)
        self.assertGreater(self.trk.lines_seen, 350)

    def test_hexdump_rows_are_skipped_not_rejected(self):
        """
        LOG_HEXDUMP emits continuation rows with no prefix. They are expected
        output; counting them as rejections would keep the drift alarm ringing
        permanently and make it useless.
        """
        self.assertGreater(self.trk.lines_hexdump, 0)
        self.assertEqual(self.trk.lines_rejected, 0)

    def test_both_directions_are_counted(self):
        self.assertGreater(self.c["up_data"], 0)
        self.assertGreater(self.c["dn_data"], 0)
        self.assertGreater(self.c["dn_payload_bytes"], 0)

    def test_session_chatter_is_separated_from_app_traffic(self):
        """
        Heartbeats and want_config are the session talking to itself. Folding
        them into up_data would inflate any delivery denominator built on it.
        """
        self.assertGreater(self.c["up_heartbeat"], 0)
        self.assertGreater(self.c["up_want_config"], 0)
        self.assertNotEqual(self.c["up_data"], self.c["up_heartbeat"])

    def test_both_phones_registered(self):
        regs = self.out.events("register")
        self.assertGreaterEqual(len(regs), 2)
        self.assertEqual(len({r["phone"] for r in regs}), 2)

    def test_ble_connects_and_disconnects_are_tracked(self):
        self.assertGreater(self.c["ble_connect"], 0)
        self.assertGreater(self.c["ble_disconnect"], 0)

    def test_per_phone_delivery_is_reported(self):
        phones = self.out.by_kind["phone"]
        self.assertEqual(len(phones), 2)
        for p in phones:
            self.assertGreater(p["delivered"], 0)
            self.assertIsNotNone(p["fromnum"])

    def test_fromnum_gaps_are_reported_not_smoothed(self):
        """
        The session contains steps of +2, meaning a notification never reached
        the log. Whether it was never emitted or Zephyr dropped the line is
        indistinguishable from here, which is precisely why the gap is surfaced
        instead of being interpolated away.
        """
        self.assertGreater(sum(p["gaps"] for p in self.out.by_kind["phone"]), 0)
        self.assertGreater(self.c["fromnum_missed"], 0)

    def test_a_falling_fromnum_is_a_session_reset_not_a_loss(self):
        """
        One phone in the real session ran up to 43 then restarted at 18.
        Charging that as 25 missed packets would invent a failure out of a
        reconnect — the same class of error the cadence estimator avoids with
        reanchor().
        """
        self.assertGreater(self.c["phone_session_resets"], 0)
        resets = self.out.events("fromnum_reset")
        self.assertTrue(resets)
        for r in resets:
            self.assertLess(r["seq_after"], r["seq_before"])

    def test_route_frames_carry_identities_and_never_content(self):
        frames = self.out.by_kind["frame"]
        self.assertTrue(frames)
        self.assertEqual({f["direction"] for f in frames}, {"up", "dn"})
        for f in frames:
            self.assertEqual(f["fw_ver"], 1)
            self.assertTrue(f["src_id"].isdigit())
            self.assertTrue(f["dst_id"].isdigit())
            self.assertNotIn("content", f)
            self.assertNotIn("content_hex", f)

    def test_route_ids_match_the_registered_phones(self):
        """
        The ROUTE header ids and the FROMNUM phone numbers are the same values
        seen through two different renderings. If they disagree, the big-endian
        reading of the 4-byte id is wrong somewhere — the mistake that took a
        whole session to find once already.
        """
        registered = {r["phone"] for r in self.out.events("register")}
        seen = {f["src_id"] for f in self.out.by_kind["frame"]}
        seen |= {f["dst_id"] for f in self.out.by_kind["frame"]}
        self.assertTrue(seen & registered,
                        f"ROUTE ids {seen} match no registered phone {registered}")

    def test_no_congestion_was_observed_in_this_session(self):
        """
        Documents the fixture's blind spot rather than asserting correctness: the
        loss patterns below are unexercised by real data, so this test is here to
        fail loudly the day a capture with congestion replaces this one — at
        which point the synthetic cases become verified.
        """
        for key in ("tx_dropped", "phone_queue_dropped", "rx_overrun_bytes"):
            self.assertEqual(self.c[key], 0,
                             f"'{key}' fired — update this test and the fixture "
                             f"docstring, the loss paths are now observed")


# ---------------------------------------------------------------------------
# Synthetic: the paths the real session never exercised
# ---------------------------------------------------------------------------

def _line(uptime_s, level, module, body):
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    return f"[{h:02d}:{m:02d}:{s:02d}.000,000] <{level}> {module}: {body}"


class TestLossPaths(unittest.TestCase):
    """Written against the firmware's format strings, not observed output."""

    def setUp(self):
        self.out = Collected()
        self.trk = PbxLogTracker(publish=self.out, counters_every=1e9,
                                 now=lambda: 0.0)

    def _counters(self):
        self.trk.publish_counters()
        return self.out.counters[-1]

    def test_uart_tx_queue_full_is_counted(self):
        self.trk.feed(_line(10, "wrn", "main",
                            "TX queue full — ToRadio from conn 0x2000abcd dropped"))
        self.assertEqual(self._counters()["tx_dropped"], 1)

    def test_the_em_dash_in_that_line_is_not_load_bearing(self):
        """
        The firmware writes an em-dash there and the serial stream corrupts
        multi-byte characters, so the pattern must match with it mangled too.
        """
        self.trk.feed(_line(11, "wrn", "main",
                            "TX queue full �� ToRadio from conn 0x1 dropped"))
        self.assertEqual(self._counters()["tx_dropped"], 1)

    def test_rx_overrun_accumulates_bytes_not_occurrences(self):
        self.trk.feed(_line(12, "wrn", "uart_meshtastic",
                            "RX overrun: 64 byte(s) dropped (ring full)"))
        self.trk.feed(_line(13, "wrn", "uart_meshtastic",
                            "RX overrun: 16 byte(s) dropped (ring full)"))
        self.assertEqual(self._counters()["rx_overrun_bytes"], 80)

    def test_phone_queue_full_is_a_downlink_loss(self):
        self.trk.feed(_line(14, "wrn", "ble_gatt",
                            "FromRadio queue full for conn 0x2000f00d (8 deep) "
                            "— packet dropped, re-ringing FROMNUM"))
        self.assertEqual(self._counters()["phone_queue_dropped"], 1)

    def test_resync_variants_both_count(self):
        self.trk.feed(_line(15, "wrn", "uart_meshtastic",
                            "Bad frame length 9999 — resyncing"))
        self.trk.feed(_line(16, "wrn", "uart_meshtastic",
                            "RX frame stalled mid-frame (state=3, 4/40 B) — resyncing"))
        self.assertEqual(self._counters()["rx_resync"], 2)

    def test_slots_exhausted_is_reported(self):
        self.trk.feed(_line(17, "err", "ble_gatt", "No free connection slot — rejecting"))
        self.assertEqual(self._counters()["ble_rejected"], 1)
        self.assertTrue(self.out.events("slots_exhausted"))


class TestLossPatternsAgainstFirmwareSource(unittest.TestCase):
    """
    Checks the loss patterns against the firmware's own format strings.

    A congestion capture is not going to exist — the testbed does not reach those
    conditions on demand — so these patterns can never be confirmed against
    observed output. That leaves one narrow risk worth closing: a format string
    transcribed wrong, giving a pattern that would never fire even during real
    congestion, and a permanent zero indistinguishable from "no losses".

    So instead of asserting against lines typed from memory, this reads the
    LOG_WRN strings out of ../meshpbx/src, renders them with sample arguments and
    feeds the result to the parser. It catches a reworded message. It cannot catch
    a message the firmware never emits, which stays an accepted caveat.

    Skipped when the firmware repo is not checked out alongside, since it is a
    sibling repository and not a dependency.
    """

    FIRMWARE = Path(__file__).resolve().parents[2] / "meshpbx" / "src"

    # message fragment the firmware must still contain -> counter it feeds
    EXPECTED = {
        "TX queue full":            "tx_dropped",
        "RX overrun":               "rx_overrun_bytes",
        "FromRadio queue full":     "phone_queue_dropped",
        "PROXY_PORTNUM: bad header": "broadcast_fallback",
        "resyncing":                "rx_resync",
        "node rebooted":            "node_reboots",
        "session presumed dead":    "session_refetch",
    }

    @classmethod
    def setUpClass(cls):
        if not cls.FIRMWARE.is_dir():
            raise unittest.SkipTest(f"firmware not checked out at {cls.FIRMWARE}")
        cls.sources = "\n".join(
            p.read_text(errors="replace") for p in cls.FIRMWARE.glob("*.c"))

    def test_every_pattern_still_matches_a_string_in_the_firmware(self):
        """
        A pattern whose message no longer exists in the source is dead: it will
        never fire, and its counter will read zero forever while looking healthy.
        """
        for fragment, counter in self.EXPECTED.items():
            with self.subTest(counter=counter):
                self.assertIn(fragment, self.sources,
                              f"'{fragment}' is gone from the firmware — the "
                              f"'{counter}' counter is now dead code")

    def test_rendered_firmware_lines_reach_their_counters(self):
        """
        Takes each LOG_WRN/LOG_ERR format string that carries one of the tracked
        fragments, substitutes printf specifiers with sample values, wraps it in a
        Zephyr prefix and checks the counter moves. This is the closest thing to
        observed data that is available.
        """
        import re as _re
        fmt_re = _re.compile(r'LOG_(?:WRN|ERR|INF)\("((?:[^"\\]|\\.)*)"')
        spec_re = _re.compile(r"%[-+ #0-9.]*(?:\"\s*PRI[a-z]\d*\s*\")?[hlLzjt]*[diouxXeEfgGcsp]")

        rendered = 0
        for raw in fmt_re.findall(self.sources):
            if not any(f in raw for f in self.EXPECTED):
                continue
            body = raw.replace('\\"', '"')
            # PRIu32-style splices leave a stray quote pair; drop those first.
            body = _re.sub(r'"\s*PRI[a-z]\d*\s*"', "", body)
            body = spec_re.sub(lambda m: "0x1" if m.group(0).endswith("p") else "7", body)
            body = body.replace("%", "7")          # anything the regex missed

            out = Collected()
            trk = PbxLogTracker(publish=out, counters_every=1e9, now=lambda: 0.0)
            trk.feed(f"[00:00:07.000,000] <wrn> firmware: {body}")
            trk.publish_counters()
            counters = out.counters[-1]

            expected = next(c for f, c in self.EXPECTED.items() if f in raw)
            self.assertGreater(
                counters[expected], 0,
                f"firmware line did not reach '{expected}':\n  {body!r}")
            self.assertEqual(
                trk.lines_rejected, 0,
                f"firmware line was rejected outright:\n  {body!r}")
            rendered += 1

        self.assertGreaterEqual(
            rendered, len(self.EXPECTED),
            f"only {rendered} firmware lines exercised; expected at least "
            f"{len(self.EXPECTED)} — the extraction regex may have missed some")


class TestRebootAndDrift(unittest.TestCase):

    def setUp(self):
        self.out = Collected()
        self.trk = PbxLogTracker(publish=self.out, counters_every=1e9,
                                 now=lambda: 0.0)

    def test_a_falling_uptime_clears_per_phone_state(self):
        """
        The proxy's own counters restart on reboot, so keeping a phone's
        pre-reboot sequence would compare a fresh session against a dead one.
        """
        self.trk.feed(_line(5000, "inf", "ble_gatt",
                            "proxy_id [CL phone]: +56700000001. Register for slot 0"))
        self.trk.feed(_line(5001, "inf", "ble_gatt",
                            "FROMNUM notification to=+56700000001 fromnum=9"))
        self.assertEqual(len(self.trk.phones), 1)

        self.trk.feed(_line(3, "inf", "main", "=== Meshtastic BLE Proxy starting ==="))
        self.assertEqual(len(self.trk.phones), 0)
        self.assertEqual(len(self.trk.slots), 0)
        reboots = self.out.events("pbx_reboot")
        self.assertEqual(len(reboots), 1)
        self.assertEqual(reboots[0]["phones_forgotten"], 1)

    def test_uptime_past_99_hours_still_parses(self):
        """Zephyr does not wrap the hour field; a proxy left running does."""
        self.assertIsNotNone(
            _PREFIX.match("[123:45:06.000,000] <inf> main: still alive"))

    def test_garbage_is_rejected_and_counted(self):
        self.trk.feed("\x00\xff not a log line")
        self.assertEqual(self.trk.lines_rejected, 1)

    def test_an_unmatched_but_wellformed_line_is_not_a_rejection(self):
        """
        Most of this console is lines the whitelist ignores on purpose. Only a
        line whose PREFIX fails counts as drift; otherwise the alarm would fire
        on every ordinary message.
        """
        self.trk.feed(_line(20, "inf", "ble_gatt", "Advertising as 'Meshtastic_0306'"))
        self.assertEqual(self.trk.lines_rejected, 0)
        self.assertEqual(self.trk.lines_seen, 1)
        self.assertEqual(self.trk.lines_parsed, 0)


class TestCounterCadence(unittest.TestCase):

    def test_counters_publish_on_an_idle_tick(self):
        """
        A quiet console must still report. The nRF52840 emits a handful of lines
        a minute when idle, so a purely line-driven publisher would go silent for
        long stretches and look indistinguishable from a dead collector.
        """
        out, clock = Collected(), FakeClock()
        trk = PbxLogTracker(publish=out, counters_every=30, now=clock)
        self.assertFalse(out.counters)
        clock.advance(31)
        trk.feed(None)
        self.assertEqual(len(out.counters), 1)


if __name__ == "__main__":
    unittest.main()
