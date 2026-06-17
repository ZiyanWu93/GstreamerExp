"""CameraSource.build() subgraph tests.

These need gi/Gst (to construct elements), so the whole module skips when
GStreamer isn't importable — e.g. the controller box, which has no GStreamer.
On a GStreamer host (the testbed) they assert the conversion subgraph shape
for each mode without any camera attached.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    from gstexp.pipeline_config import CameraSource
    _GST = True
except Exception:                                # gi/Gst/typelib unavailable
    _GST = False


@unittest.skipUnless(_GST, "GStreamer/gi not available on this host")
class TestCameraSourceBuild(unittest.TestCase):

    def test_fake_pattern_bayer_inserts_bayer2rgb(self):
        if Gst.ElementFactory.find("bayer2rgb") is None:
            self.skipTest("bayer2rgb element missing (needs gst-plugins-bad)")
        p = Gst.Pipeline.new("t")
        tail = CameraSource(mode="fake", pixel_format="bayer_rggb").build(p, 30)
        self.assertEqual(tail.get_factory().get_name(), "videorate")
        for name in ("src_testsrc", "src_native_caps", "src_bayer2rgb",
                     "src_convert", "src_scale", "src_rate"):
            self.assertIsNotNone(p.get_by_name(name), name)

    def test_fake_pattern_mono_no_bayer2rgb(self):
        p = Gst.Pipeline.new("t")
        CameraSource(mode="fake", pixel_format="mono8").build(p, 30)
        self.assertIsNone(p.get_by_name("src_bayer2rgb"))
        self.assertIsNotNone(p.get_by_name("src_convert"))
        # synthetic pattern is non-live + capped; no clocksync (only fake_clip paces)
        self.assertIsNone(p.get_by_name("src_clocksync"))

    def test_fake_clip_uses_filesrc_and_paces(self):
        p = Gst.Pipeline.new("t")
        CameraSource(mode="fake", pixel_format="mono8",
                     fake_clip="/tmp/x.mp4").build(p, 0)
        for n in ("src_filesrc", "src_decode", "src_convert", "src_clocksync"):
            self.assertIsNotNone(p.get_by_name(n), n)
        self.assertIsNone(p.get_by_name("src_bayer2rgb"))   # clip isn't raw bayer

    def test_real_mode_is_explicit_hardware_seam(self):
        with self.assertRaises(NotImplementedError):
            CameraSource(mode="real", pixel_format="bayer_rggb").build(
                Gst.Pipeline.new("t"), 0)

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            CameraSource(mode="nope", pixel_format="mono8").build(
                Gst.Pipeline.new("t"), 0)


if __name__ == "__main__":
    unittest.main()
