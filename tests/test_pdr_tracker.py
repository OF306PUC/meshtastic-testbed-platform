"""
Unit tests for CadencePdrTracker (src/gateway/mesh_receiver.py).

The tracker infers packet loss from inter-arrival gaps against a known
broadcast cadence, so every test drives it with explicit monotonic timestamps
instead of real time.

Run:
    .venv/bin/python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

# Put src/ on the path so package-absolute imports inside src/ work.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.mesh_receiver import CadencePdrTracker  # noqa: E402


T      = 120      # device/environment cadence used across these tests [s]
WINDOW = 3600     # rolling window [s]
FLOW   = ("node-1", "device")


def _tracker():
    return CadencePdrTracker()


class TestOnScheduleReceptions(unittest.TestCase):
    """No losses: consecutive packets one nominal interval apart."""

    def test_first_reception_reports_no_ratio(self):
        """
        A single packet carries no delivery information.

        Reporting 1.0 here would claim a perfect link from one observation, so
        pdr stays None until at least one gap has been measured.
        """
        snap = _tracker().observe(FLOW, T, WINDOW, now=0.0)
        self.assertIsNone(snap["pdr"])
        self.assertEqual(snap["rx_count"], 1)
        self.assertEqual(snap["missed_est"], 0)

    def test_two_consecutive_receptions_are_perfect(self):
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        snap = tr.observe(FLOW, T, WINDOW, now=float(T))

        self.assertEqual(snap["pdr"], 1.0)
        self.assertEqual(snap["rx_count"], 2)
        self.assertEqual(snap["missed_est"], 0)
        self.assertEqual(snap["gap_s"], float(T))

    def test_jitter_within_half_an_interval_is_not_a_loss(self):
        """
        round(), not floor(): a packet 30 % late is still that interval's packet.

        With floor() every late packet would fabricate a loss, and firmware
        jitter would read as a permanently degraded link.
        """
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        snap = tr.observe(FLOW, T, WINDOW, now=T * 1.3)

        self.assertEqual(snap["missed_est"], 0)
        self.assertEqual(snap["pdr"], 1.0)

    def test_gap_past_one_and_a_half_intervals_counts_a_loss(self):
        """The documented ±1-packet boundary: 1.6 T rounds to two slots."""
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        snap = tr.observe(FLOW, T, WINDOW, now=T * 1.6)

        self.assertEqual(snap["missed_est"], 1)
        self.assertEqual(snap["pdr"], round(2 / 3, 4))   # 2 received of 3 slots


class TestLossAccounting(unittest.TestCase):
    """Gaps spanning several intervals."""

    def test_three_interval_gap_charges_two_losses(self):
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        snap = tr.observe(FLOW, T, WINDOW, now=float(3 * T))

        self.assertEqual(snap["rx_count"], 2)
        self.assertEqual(snap["missed_est"], 2)
        self.assertEqual(snap["missed_now"], 2)
        self.assertEqual(snap["pdr"], 0.5)     # 2 received of 4 slots

    def test_pdr_never_exceeds_one(self):
        """
        missed >= 0 by construction, so the ratio is bounded above by 1.

        This is why `early_count`, not pdr > 1, is the signal for a broken
        cadence assumption.
        """
        tr = _tracker()
        now = 0.0
        for _ in range(20):
            snap = tr.observe(FLOW, T, WINDOW, now=now)
            now += T * 0.9          # every packet slightly early
            if snap["pdr"] is not None:
                self.assertLessEqual(snap["pdr"], 1.0)


class TestOffCadenceReceptions(unittest.TestCase):
    """Extra broadcasts the cadence model does not predict."""

    def test_early_reception_is_flagged_not_counted(self):
        """
        A packet arriving well inside one interval (smart position broadcast,
        typically) is counted as `early`, given no slot, and flags the flow.
        """
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        snap = tr.observe(FLOW, T, WINDOW, now=T * 0.2)

        self.assertEqual(snap["early_count"], 1)
        self.assertTrue(snap["cadence_violated"])
        self.assertEqual(snap["rx_count"], 1, "an early packet must not take a slot")
        self.assertEqual(snap["missed_est"], 0)

    def test_early_reception_does_not_move_the_grid_anchor(self):
        """
        The anchor stays on the last on-schedule packet.

        If an early arrival advanced it, the next on-time packet would measure
        only a fraction of T, look early too, and the grid would drift until
        every reception was classified off-cadence.
        """
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        tr.observe(FLOW, T, WINDOW, now=T * 0.2)      # early, anchor stays at 0
        snap = tr.observe(FLOW, T, WINDOW, now=float(T))

        self.assertEqual(snap["rx_count"], 2)
        self.assertEqual(snap["missed_est"], 0)
        self.assertEqual(snap["early_count"], 1)

    def test_clean_flow_is_not_flagged(self):
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        snap = tr.observe(FLOW, T, WINDOW, now=float(T))
        self.assertFalse(snap["cadence_violated"])
        self.assertEqual(snap["early_count"], 0)


class TestSweep(unittest.TestCase):
    """Silence detection — the only way a dead node becomes visible."""

    def test_sweep_charges_losses_while_silent(self):
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        changed = tr.sweep(now=T * 3.1)      # ~3 intervals of silence

        self.assertEqual(len(changed), 1)
        flow, snap = changed[0]
        self.assertEqual(flow, FLOW)
        self.assertEqual(snap["missed_est"], 2)
        self.assertEqual(snap["missed_now"], 2)
        self.assertEqual(snap["pdr"], round(1 / 3, 4))

    def test_sweep_is_idle_before_a_full_interval_elapses(self):
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        self.assertEqual(tr.sweep(now=T * 0.9), [])

    def test_sweep_does_not_double_charge_when_the_packet_arrives(self):
        """
        The decisive property: a loss billed by sweep must not be billed again by
        the reception that ends the gap.

        Same gap, with and without an intervening sweep, must yield the same
        cumulative loss count.
        """
        with_sweep = _tracker()
        with_sweep.observe(FLOW, T, WINDOW, now=0.0)
        with_sweep.sweep(now=290.0)                              # charges 1
        swept = with_sweep.observe(FLOW, T, WINDOW, now=float(3 * T))

        without = _tracker()
        without.observe(FLOW, T, WINDOW, now=0.0)
        plain = without.observe(FLOW, T, WINDOW, now=float(3 * T))

        self.assertEqual(swept["missed_est"], plain["missed_est"])
        self.assertEqual(swept["pdr"], plain["pdr"])
        self.assertEqual(swept["missed_est"], 2)

    def test_sweep_keeps_charging_as_silence_grows(self):
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        tr.sweep(now=T * 3.1)
        changed = tr.sweep(now=T * 5.1)

        self.assertEqual(len(changed), 1)
        _, snap = changed[0]
        self.assertEqual(snap["missed_est"], 4)
        self.assertEqual(snap["missed_now"], 2, "only the new losses are reported")

    def test_sweep_reports_nothing_for_unknown_flows(self):
        self.assertEqual(_tracker().sweep(now=9999.0), [])


class TestReanchor(unittest.TestCase):
    """Node downtime must not be charged as radio loss."""

    def test_reanchor_refunds_swept_losses(self):
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        tr.sweep(now=T * 10.1)                    # node was off: 9 losses billed
        tr.reanchor(FLOW, now=T * 10.1)
        snap = tr.observe(FLOW, T, WINDOW, now=T * 11.1)

        self.assertEqual(snap["missed_est"], 0,
                         "downtime charged by sweep must be refunded on reboot")
        self.assertEqual(snap["rx_count"], 2)
        self.assertEqual(snap["pdr"], 1.0)

    def test_reanchor_restarts_the_grid(self):
        """After a reboot the next packet is measured from the reboot, not before."""
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        tr.reanchor(FLOW, now=1000.0)
        snap = tr.observe(FLOW, T, WINDOW, now=1000.0 + T)

        self.assertEqual(snap["missed_est"], 0)
        self.assertEqual(snap["gap_s"], float(T))

    def test_reception_at_the_reanchor_instant_is_not_early(self):
        """
        REGRESSION: a reboot is detected from the very packet that reports the
        new uptime, so reanchor and the reception share a timestamp. With dt=0
        the grid check would classify it as an off-cadence extra packet and
        flag the flow; the restart flag makes it a clean grid start instead.
        """
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        tr.reanchor(FLOW, now=500.0)
        snap = tr.observe(FLOW, T, WINDOW, now=500.0)

        self.assertEqual(snap["early_count"], 0)
        self.assertFalse(snap["cadence_violated"])
        self.assertEqual(snap["rx_count"], 2)
        self.assertEqual(snap["missed_est"], 0)

    def test_reanchor_stops_sweep_charging_for_downtime(self):
        """
        reanchor also moves the anchor, otherwise the next sweep would
        immediately re-bill the downtime it just refunded.
        """
        tr = _tracker()
        tr.observe(FLOW, T, WINDOW, now=0.0)
        tr.sweep(now=T * 10.1)
        tr.reanchor(FLOW, now=T * 10.1)

        self.assertEqual(tr.sweep(now=T * 10.5), [],
                         "downtime must not be re-charged after a reboot")

    def test_reanchor_on_unknown_flow_is_a_noop(self):
        _tracker().reanchor(("node-9", "device"), now=0.0)   # must not raise


class TestRollingWindow(unittest.TestCase):
    """Window length is derived from window_sec / interval."""

    def test_window_slots_derived_from_cadence(self):
        tr = _tracker()
        snap = tr.observe(("node-1", "device"), 120, 3600, now=0.0)
        self.assertEqual(snap["pdr_window_slots"], 30)

    def test_slow_cadence_yields_a_thin_window(self):
        """
        600 s position packets over a 1 h window are only 6 samples.

        Exposed rather than hidden: pdr_window_slots is how a consumer knows the
        denominator is coarse (16.7 % resolution) and should widen the window.
        """
        tr = _tracker()
        snap = tr.observe(("node-1", "position"), 600, 3600, now=0.0)
        self.assertEqual(snap["pdr_window_slots"], 6)

    def test_window_has_a_floor(self):
        """A 1-2 slot window is noise, so it is clamped to MIN_WINDOW_SLOTS."""
        tr = _tracker()
        snap = tr.observe(("node-p1", "position"), 1800, 3600, now=0.0)
        self.assertEqual(snap["pdr_window_slots"], CadencePdrTracker.MIN_WINDOW_SLOTS)

    def test_window_forgets_old_losses(self):
        """
        The rolling figure recovers after a bad patch while the cumulative one
        keeps the history — that difference is the point of having both.
        """
        tr = _tracker()
        window_sec = 3 * T                 # 3-slot window
        now = 0.0
        tr.observe(FLOW, T, window_sec, now=now)
        now += 3 * T
        tr.observe(FLOW, T, window_sec, now=now)     # 2 losses land in the window
        for _ in range(3):                            # 3 clean receptions
            now += T
            snap = tr.observe(FLOW, T, window_sec, now=now)

        self.assertEqual(snap["pdr_window"], 1.0, "window must forget the old losses")
        self.assertLess(snap["pdr"], 1.0, "cumulative pdr must remember them")

    def test_window_filled_tracks_partial_windows(self):
        tr = _tracker()
        snap = tr.observe(FLOW, T, WINDOW, now=0.0)
        self.assertEqual(snap["pdr_window_filled"], 1)
        snap = tr.observe(FLOW, T, WINDOW, now=float(T))
        self.assertEqual(snap["pdr_window_filled"], 2)


class TestFlowIsolation(unittest.TestCase):
    """Flows are independent: (node, kind) pairs never share state."""

    def test_kinds_of_the_same_node_are_separate(self):
        tr = _tracker()
        tr.observe(("node-1", "device"), 120, WINDOW, now=0.0)
        tr.observe(("node-1", "device"), 120, WINDOW, now=120.0)
        snap = tr.observe(("node-1", "position"), 600, WINDOW, now=120.0)

        self.assertEqual(snap["rx_count"], 1, "position flow starts fresh")
        self.assertIsNone(snap["pdr"])

    def test_nodes_are_separate(self):
        tr = _tracker()
        tr.observe(("node-1", "device"), 120, WINDOW, now=0.0)
        snap = tr.observe(("node-2", "device"), 120, WINDOW, now=0.0)
        self.assertEqual(snap["rx_count"], 1)


if __name__ == "__main__":
    unittest.main()
