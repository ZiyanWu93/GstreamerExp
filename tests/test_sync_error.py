"""Cross-stream sync_error correlator tests.

The correlator (reporting._correlate_sync_error) measures inter-stream
presentation skew as a same-host max - min over per-frame viewer arrival
wall-clocks, deriving each stream's frame index from its own normalized RTP
timestamp. These tests fix its contract WITHOUT a live pipeline: the key
property is that a common clock offset cancels exactly (so the aum/veda
clock-skew artifact never leaks into the metric).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gstexp.reporting import _correlate_sync_error, build_summary  # noqa: E402

FPS = 10
RTP_STEP = 90000 // FPS   # 9000 rtp units per frame at 10 fps


def _viewer(base_rtp: int, walls: list) -> dict:
    """A synthetic viewer result: frame k has rtp_ts = base + k*RTP_STEP and
    arrival wall walls[k]. The per-stream random base exercises the
    normalization (each stream's frames are keyed off its own first rtp_ts)."""
    samples = [[base_rtp + k * RTP_STEP, w] for k, w in enumerate(walls)]
    return {"metrics": {"frame_latency": {"samples": samples}}}


def _meta(*names):
    return [{"name": n, "priority": i, "fps": FPS} for i, n in enumerate(names)]


class TestSyncErrorCorrelator(unittest.TestCase):
    def test_constant_offset_between_two_streams(self):
        # B arrives a constant 20 ms after A on every frame -> skew == 20 ms.
        a = _viewer(1000,   [100.00 + 0.1 * k for k in range(5)])
        b = _viewer(50000,  [100.02 + 0.1 * k for k in range(5)])
        out = _correlate_sync_error(_meta("front", "rear"), [a, b])
        self.assertEqual(out["samples_count"], 5)
        self.assertAlmostEqual(out["max_ms"], 20.0, places=3)
        self.assertAlmostEqual(out["median_ms"], 20.0, places=3)
        self.assertEqual(out["worst_pair"], ["front", "rear"])  # front lo, rear hi

    def test_same_host_offset_cancels_exactly(self):
        # Adding a large COMMON offset to every wall in BOTH streams models a
        # viewer-host clock skew shared by all streams; max - min is invariant,
        # so the measured skew must not move at all.
        a = _viewer(1000,  [100.00 + 0.1 * k for k in range(5)])
        b = _viewer(50000, [100.02 + 0.1 * k for k in range(5)])
        base = _correlate_sync_error(_meta("front", "rear"), [a, b])
        SKEW = 137.0  # seconds of common host offset
        a2 = _viewer(1000,  [100.00 + SKEW + 0.1 * k for k in range(5)])
        b2 = _viewer(50000, [100.02 + SKEW + 0.1 * k for k in range(5)])
        shifted = _correlate_sync_error(_meta("front", "rear"), [a2, b2])
        self.assertEqual(base["max_ms"], shifted["max_ms"])
        self.assertEqual(base["median_ms"], shifted["median_ms"])
        self.assertEqual(base["min_ms"], shifted["min_ms"])

    def test_worst_pair_picks_the_widest_spread(self):
        # Three streams; 'left' lags 'front' by 50 ms (the widest pair),
        # 'rear' sits 10 ms after 'front'.
        front = _viewer(1000,  [10.00 + 0.1 * k for k in range(4)])
        rear  = _viewer(20000, [10.01 + 0.1 * k for k in range(4)])
        left  = _viewer(70000, [10.05 + 0.1 * k for k in range(4)])
        out = _correlate_sync_error(_meta("front", "rear", "left"), [front, rear, left])
        self.assertAlmostEqual(out["max_ms"], 50.0, places=3)
        self.assertEqual(out["worst_pair"], ["front", "left"])

    def test_single_stream_yields_no_sync_error(self):
        a = _viewer(1000, [100.0 + 0.1 * k for k in range(5)])
        self.assertEqual(_correlate_sync_error(_meta("front"), [a]), {})

    def test_missing_fps_or_samples_skips_stream(self):
        a = _viewer(1000,  [100.0 + 0.1 * k for k in range(5)])
        b = _viewer(50000, [100.0 + 0.1 * k for k in range(5)])
        # Stream b has no fps -> only one usable stream -> no sync_error.
        meta = [{"name": "front", "priority": 0, "fps": FPS},
                {"name": "rear", "priority": 1}]
        self.assertEqual(_correlate_sync_error(meta, [a, b]), {})

    def test_only_frames_present_in_two_streams_counted(self):
        # A has 5 frames, B only 3; only the 3 shared indices are scored.
        a = _viewer(1000,  [100.0 + 0.1 * k for k in range(5)])
        b = _viewer(50000, [100.0 + 0.1 * k for k in range(3)])
        out = _correlate_sync_error(_meta("front", "rear"), [a, b])
        self.assertEqual(out["samples_count"], 3)


class TestBuildSummaryIntegration(unittest.TestCase):
    def _stream(self, base_rtp, walls, sent=5):
        cam = {"metrics": {"frame_count": {"summary": {"frames": sent}}}}
        viewer = _viewer(base_rtp, walls)
        viewer["metrics"]["frame_count"] = {"summary": {"frames": len(walls)}}
        return cam, viewer

    def test_multistream_summary_has_sync_error(self):
        ca, va = self._stream(1000,  [100.00 + 0.1 * k for k in range(5)])
        cb, vb = self._stream(50000, [100.02 + 0.1 * k for k in range(5)])
        summary = build_summary(_meta("front", "rear"), [ca, cb], [va, vb])
        self.assertIn("sync_error", summary)
        self.assertEqual(summary["sync_error"]["worst_pair"], ["front", "rear"])
        self.assertEqual(len(summary["streams"]), 2)

    def test_singlestream_summary_omits_sync_error(self):
        ca, va = self._stream(1000, [100.0 + 0.1 * k for k in range(5)])
        summary = build_summary(_meta("main"), [ca], [va])
        self.assertNotIn("sync_error", summary)


if __name__ == "__main__":
    unittest.main()
