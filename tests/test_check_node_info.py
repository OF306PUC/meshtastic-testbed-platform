"""Tests for the position-quantization detector in src/tools/check_node_info.py.

The detector is what tells us whether position_precision actually took effect,
so it has to fire on the coordinates we really observed and stay quiet on
full-precision ones.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools.check_node_info import trailing_zero_bits  # noqa: E402


class TestTrailingZeroBits(unittest.TestCase):
    # The exact coordinates every node reported while position_precision was at
    # its default — byte-identical across three nodes hundreds of metres apart.
    OBSERVED_LAT = -33.4757888
    OBSERVED_LON = -70.5953792

    def test_detects_the_real_quantized_coordinates(self):
        """Both observed coordinates are exact multiples of 2**18."""
        self.assertEqual(trailing_zero_bits(self.OBSERVED_LAT), 18)
        self.assertEqual(trailing_zero_bits(self.OBSERVED_LON), 18)

    def test_implied_precision_is_14(self):
        tz = min(trailing_zero_bits(self.OBSERVED_LAT),
                 trailing_zero_bits(self.OBSERVED_LON))
        self.assertEqual(32 - tz, 14)

    def test_surveyed_positions_are_not_flagged(self):
        """The hand-surveyed coordinates in mesh_config.json must stay quiet.

        They are full precision by construction; if the detector fired on them
        it would report a fault on every healthy node.
        """
        for lat, lon in [(-33.4969028, -70.6101277778),
                         (-33.4971361, -70.6075444443),
                         (-33.5002389, -70.6119222222),
                         (-33.4999417, -70.614008333)]:
            self.assertLess(min(trailing_zero_bits(lat), trailing_zero_bits(lon)), 8,
                            f"surveyed position {lat},{lon} wrongly flagged")

    def test_no_fix_is_not_quantization(self):
        """0.0 means 'no GPS fix'; every bit is zero but nothing was masked."""
        self.assertEqual(trailing_zero_bits(0.0), 0)

    def test_masking_a_full_precision_value_is_detected(self):
        """Apply the firmware's own mask and confirm the detector sees it."""
        for precision in (12, 14, 16, 24):
            shift = 32 - precision
            units = round(abs(-33.4969028) * 1e7)
            masked = (units >> shift) << shift
            self.assertGreaterEqual(trailing_zero_bits(masked / 1e7), shift)


if __name__ == "__main__":
    unittest.main()
