from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.mahimahi_to_network_spec import _spec, _steps_from_trace
from tools.scale_network_spec import scale_spec


class TestMahimahiConverter(unittest.TestCase):

    def test_trace_bins_convert_to_rates(self):
        # 3 packets in first 100 ms, 1 packet in second 100 ms.
        steps = _steps_from_trace(
            [0, 0, 99, 199],
            bin_ms=100,
            packet_bytes=1500,
            delay_ms=20,
            min_rate_kbps=1,
            merge_equal=False,
        )
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["duration"], 0.1)
        self.assertEqual(steps[0]["rate_kbps"], 360)
        self.assertEqual(steps[1]["rate_kbps"], 120)
        self.assertEqual(steps[0]["loss"], {"model": "none"})

    def test_zero_packet_bin_uses_min_rate(self):
        steps = _steps_from_trace(
            [0, 299],
            bin_ms=100,
            packet_bytes=1500,
            delay_ms=20,
            min_rate_kbps=1,
            merge_equal=False,
        )
        self.assertEqual([s["rate_kbps"] for s in steps], [120, 1, 120])

    def test_spec_shape_matches_network_schema(self):
        steps = _steps_from_trace(
            [0, 0, 99, 100],
            bin_ms=100,
            packet_bytes=1500,
            delay_ms=20,
            min_rate_kbps=1,
            merge_equal=False,
        )
        doc = _spec("fixture", "trace", steps,
                    bin_ms=100, packet_bytes=1500, delay_ms=20)
        self.assertEqual(doc["name"], "fixture")
        self.assertIn("camera_steps", doc)
        self.assertEqual(doc["viewer_steps"], [])

    def test_network_spec_scaler_preserves_shape(self):
        doc = {
            "name": "fixture",
            "description": "source",
            "camera_steps": [
                {
                    "duration": 0.1,
                    "rate_kbps": 9000,
                    "delay_ms": 20,
                    "loss": {"model": "none"},
                    "label": "a",
                },
                {
                    "duration": 0.1,
                    "rate_kbps": 1,
                    "delay_ms": 20,
                    "loss": {"model": "none"},
                    "label": "b",
                },
            ],
            "viewer_steps": [],
        }
        scaled = scale_spec(doc, name="fixture-x0p33", scale=0.33,
                            min_rate_kbps=1)
        self.assertEqual(scaled["name"], "fixture-x0p33")
        self.assertEqual([s["duration"] for s in scaled["camera_steps"]],
                         [0.1, 0.1])
        self.assertEqual([s["rate_kbps"] for s in scaled["camera_steps"]],
                         [2970, 1])
        self.assertEqual(scaled["camera_steps"][0]["delay_ms"], 20)
        self.assertEqual(scaled["camera_steps"][0]["loss"], {"model": "none"})


if __name__ == "__main__":
    unittest.main()
