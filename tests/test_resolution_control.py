"""ResolutionController tests — pure logic, no GStreamer. Feed synthetic
rate traces and assert the tier/dimension sequence + hysteresis behavior."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gstexp.resolution_control import ResolutionController  # noqa: E402


def _tier(h, r):
    return types.SimpleNamespace(height=h, min_rate_kbps=r)


def _ladder(up=4.0, down=0.5, mi=2.0, alpha=1.0,
            tiers=((1024, 1000), (512, 400), (384, 0))):
    return types.SimpleNamespace(
        tiers=[_tier(h, r) for h, r in tiers],
        hysteresis_up_hold_s=up, hysteresis_down_hold_s=down,
        min_switch_interval_s=mi, ewma_alpha=alpha)


class TestResolutionController(unittest.TestCase):

    def test_starts_at_top_tier(self):
        c = ResolutionController(_ladder(), 1280, 1024)
        self.assertEqual(c.idx, 0)
        self.assertEqual(c.current_dims(), (1280, 1024))

    def test_high_rate_never_switches(self):
        c = ResolutionController(_ladder(), 1280, 1024)
        for t in range(0, 20):
            self.assertIsNone(c.update(3000, float(t)))
        self.assertEqual(c.idx, 0)

    def test_downshift_is_fast_and_direct(self):
        # rate 300 clears only the floor-0 tier → jump 1024 → 384 directly,
        # after the (small) down hold AND the min switch interval.
        c = ResolutionController(_ladder(), 1280, 1024)
        c.update(300, 0.0)
        self.assertIsNone(c.update(300, 0.4))            # hold 0.4 < 0.5
        self.assertIsNone(c.update(300, 1.0))            # interval 1.0 < 2.0
        self.assertEqual(c.update(300, 2.1), (480, 384))  # both satisfied
        self.assertEqual(c.idx, 2)

    def test_intermediate_tier(self):
        c = ResolutionController(_ladder(), 1280, 1024)
        c.update(500, 0.0)
        self.assertEqual(c.update(500, 2.5), (640, 512))  # 500 clears 400, not 1000
        self.assertEqual(c.idx, 1)

    def test_upshift_is_slow(self):
        c = ResolutionController(_ladder(up=4.0, down=0.5, mi=2.0), 1280, 1024)
        c.update(300, 0.0)
        c.update(300, 2.1)
        self.assertEqual(c.idx, 2)
        c.update(1200, 3.0)                              # up candidate starts
        self.assertIsNone(c.update(1200, 5.0))           # held 2.0 < up_hold 4.0
        self.assertEqual(c.update(1200, 7.2), (1280, 1024))  # held 4.2 ≥ 4.0
        self.assertEqual(c.idx, 0)

    def test_no_flap_at_threshold(self):
        c = ResolutionController(_ladder(up=4.0, down=0.5, mi=2.0), 1280, 1024)
        c.update(500, 0.0)
        c.update(500, 2.5)
        self.assertEqual(c.idx, 1)
        switches = 0
        for i in range(20):                              # oscillate 350/450 around the 400 floor
            if c.update(350 if i % 2 else 450, 2.7 + 0.2 * i) is not None:
                switches += 1
        self.assertEqual(switches, 0)                    # candidate timer resets each flip
        self.assertEqual(c.idx, 1)

    def test_min_switch_interval_blocks_rapid_second_switch(self):
        c = ResolutionController(_ladder(up=4.0, down=0.0001, mi=2.0), 1280, 1024)
        c.update(500, 0.0)
        self.assertEqual(c.update(500, 2.1), (640, 512))  # last_switch = 2.1
        self.assertIsNone(c.update(100, 2.2))             # 0.1 < min_interval 2.0
        self.assertEqual(c.update(100, 4.2), (480, 384))  # 2.1 ≥ 2.0

    def test_ewma_smooths_a_single_spike(self):
        c = ResolutionController(_ladder(alpha=0.3), 1280, 1024)
        for t in range(0, 10):
            c.update(3000, float(t))
        # one dip to 100: ewma = 0.3*100 + 0.7*3000 = 2130, still above 1000.
        self.assertIsNone(c.update(100, 10.0))
        self.assertEqual(c.idx, 0)

    def test_even_width_derivation(self):
        c = ResolutionController(_ladder(), 1280, 1024)
        self.assertEqual(c.dims(0), (1280, 1024))
        self.assertEqual(c.dims(1), (640, 512))
        self.assertEqual(c.dims(2), (480, 384))
        for i in range(3):
            w, h = c.dims(i)
            self.assertEqual((w % 2, h % 2), (0, 0))


if __name__ == "__main__":
    unittest.main()
