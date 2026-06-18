"""Glass-to-glass (capture->render) correlator tests.

reporting._correlate_capture_render joins the camera's `encoder_in`
stage_latency sample with the viewer's `render_in` sample by RTP timestamp,
skew-corrected, to produce the full software capture->render latency. These
fix its contract without a live pipeline: it is a superset of the wire-to-wire
frame_latency, a common clock offset cancels, and it is absent unless
stage_latency is enabled on both roles.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gstexp.reporting import (  # noqa: E402
    _correlate_capture_render, _correlate_latency,
)

BASE = 1234567          # arbitrary RTP offset (RFC 3550 random start)
STEP = 9000             # 90 kHz / 10 fps


def _cam(stage_walls: dict) -> dict:
    """stage_walls: {stage_name: [wall_s per frame]}. Frame k -> rtp BASE+k*STEP."""
    samples = []
    for stage, walls in stage_walls.items():
        samples += [[stage, BASE + k * STEP, w] for k, w in enumerate(walls)]
    return {"metrics": {"stage_latency": {"samples": samples}}}


class TestCaptureRenderLatency(unittest.TestCase):

    def test_basic_join_no_skew(self):
        cam = _cam({"encoder_in":  [100.0, 100.1, 100.2],
                    "encoder_out": [100.005, 100.105, 100.205]})   # ignored stage
        view = _cam({"render_in": [100.04, 100.14, 100.24],
                     "wire_in":   [100.02, 100.12, 100.22]})        # ignored stage
        r = _correlate_capture_render(cam, view)
        self.assertEqual(r["samples_count"], 3)
        self.assertEqual(r["median_ms"], 40.0)
        self.assertEqual((r["min_ms"], r["max_ms"]), (40.0, 40.0))

    def test_common_clock_offset_cancels(self):
        # Camera clock runs +5.0 s ahead; viewer +5.0 s too. With both skews
        # set to 5.0, the offset cancels and the latency is the true 40 ms.
        cam = _cam({"encoder_in": [105.0, 105.1]})
        view = _cam({"render_in": [105.04, 105.14]})
        r = _correlate_capture_render(cam, view, camera_skew=5.0, viewer_skew=5.0)
        self.assertEqual(r["samples_count"], 2)
        self.assertEqual(r["median_ms"], 40.0)

    def test_only_matching_rtp_ts_join(self):
        # camera frames 0,1,2 ; viewer frames 1,2,3 -> only 1,2 overlap.
        cam = {"metrics": {"stage_latency": {"samples": [
            ["encoder_in", BASE + 0 * STEP, 100.0],
            ["encoder_in", BASE + 1 * STEP, 100.1],
            ["encoder_in", BASE + 2 * STEP, 100.2],
        ]}}}
        view = {"metrics": {"stage_latency": {"samples": [
            ["render_in", BASE + 1 * STEP, 100.14],
            ["render_in", BASE + 2 * STEP, 100.24],
            ["render_in", BASE + 3 * STEP, 100.34],
        ]}}}
        r = _correlate_capture_render(cam, view)
        self.assertEqual(r["samples_count"], 2)

    def test_absent_without_stage_latency(self):
        # Only frame_latency present -> no encoder_in/render_in -> empty.
        cam = {"metrics": {"frame_latency": {"samples": [[BASE, 100.0]]}}}
        view = {"metrics": {"frame_latency": {"samples": [[BASE, 100.04]]}}}
        self.assertEqual(_correlate_capture_render(cam, view), {})
        self.assertEqual(_correlate_capture_render({}, {}), {})

    def test_payloader_offset_bridge(self):
        # encoder_in is keyed in PTS-derived RTP space; render_in in the
        # wire/header space, which the payloader offsets by a constant. The
        # camera's pay_out (header) + pay_out_pts (PTS) samples expose that
        # offset; the correlator must translate the capture keys across it.
        OFF = 7_000_000
        cam = {"metrics": {"stage_latency": {"samples":
            [["encoder_in", BASE + k * STEP, 100.0 + k * 0.1] for k in range(3)]
            + [["pay_out", (BASE + k * STEP + OFF) & 0xFFFFFFFF, 100.0 + k * 0.1 + 0.002] for k in range(3)]
            + [["pay_out_pts", BASE + k * STEP, 100.0 + k * 0.1 + 0.002] for k in range(3)]
        }}}
        view = {"metrics": {"stage_latency": {"samples":
            [["render_in", (BASE + k * STEP + OFF) & 0xFFFFFFFF, 100.0 + k * 0.1 + 0.040] for k in range(3)]
        }}}
        r = _correlate_capture_render(cam, view)
        self.assertEqual(r["samples_count"], 3)   # bridged, not zero
        self.assertEqual(r["median_ms"], 40.0)

    def test_superset_of_wire_to_wire(self):
        # g2g spans encoder_in -> render_in; wire-to-wire spans egress -> ingress,
        # which lies strictly inside it, so g2g latency >= wire latency.
        cam = {"metrics": {
            "stage_latency": {"samples": [["encoder_in", BASE, 100.000]]},
            "frame_latency": {"samples": [[BASE, 100.010]]},     # egress (wire_out)
        }}
        view = {"metrics": {
            "stage_latency": {"samples": [["render_in", BASE, 100.050]]},
            "frame_latency": {"samples": [[BASE, 100.030]]},     # ingress (wire_in)
        }}
        g2g = _correlate_capture_render(cam, view)
        wire = _correlate_latency(cam, view)
        self.assertEqual(g2g["median_ms"], 50.0)   # 100.050 - 100.000
        self.assertEqual(wire["median_ms"], 20.0)  # 100.030 - 100.010
        self.assertGreaterEqual(g2g["median_ms"], wire["median_ms"])


if __name__ == "__main__":
    unittest.main()
