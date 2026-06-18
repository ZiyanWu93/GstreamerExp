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
        if Gst.ElementFactory.find("clocksync") is None:
            self.skipTest("clocksync element missing (fake_clip paces with it)")
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


@unittest.skipUnless(_GST, "GStreamer/gi not available on this host")
class TestCcNotifyFeedsAdapter(unittest.TestCase):
    """The two CC notify callbacks report rate in different units (SCReAM
    current-max-bitrate in kbps, GCC estimated-bitrate in bps); both must feed
    the ResolutionController the SAME kbps and set the encoder to the SAME bps.
    Locks the GCC/SCReAM parity the adaptive controller relies on without a
    live run (the only adaptive config exercises SCReAM)."""

    class _StubEnc:
        def __init__(self): self.bps = None
        def set_property(self, k, v):
            if k == "target-bitrate":
                self.bps = v

    class _StubCC:
        def __init__(self, prop, val): self._p = {prop: val}
        def get_property(self, k): return self._p[k]

    class _RecAdapter:
        def __init__(self): self.rates = []
        def on_rate(self, r): self.rates.append(r)

    def _cbs(self):
        from gstexp.camera import _on_bitrate_notify, _on_gcc_bitrate_notify
        from gstexp.pipeline_config import Vp8Codec
        return _on_bitrate_notify, _on_gcc_bitrate_notify, Vp8Codec()

    def test_scream_and_gcc_feed_same_kbps_and_bps(self):
        scream_cb, gcc_cb, codec = self._cbs()
        R = 1234  # the same physical rate, in kbps

        a_s, e_s = self._RecAdapter(), self._StubEnc()
        scream_cb(self._StubCC("current-max-bitrate", R), None, e_s, codec, a_s)
        a_g, e_g = self._RecAdapter(), self._StubEnc()
        gcc_cb(self._StubCC("estimated-bitrate", R * 1000), None, e_g, codec, a_g)

        self.assertEqual(a_s.rates, [float(R)])      # SCReAM kbps -> kbps
        self.assertEqual(a_g.rates, [float(R)])      # GCC bps/1000 -> kbps
        self.assertEqual(e_s.bps, R * 1000)          # encoder target identical
        self.assertEqual(e_g.bps, R * 1000)

    def test_nonpositive_rate_does_not_feed_adapter(self):
        scream_cb, gcc_cb, codec = self._cbs()
        a_s, e_s = self._RecAdapter(), self._StubEnc()
        scream_cb(self._StubCC("current-max-bitrate", 0), None, e_s, codec, a_s)
        a_g, e_g = self._RecAdapter(), self._StubEnc()
        gcc_cb(self._StubCC("estimated-bitrate", 0), None, e_g, codec, a_g)
        self.assertEqual(a_s.rates, [])
        self.assertEqual(a_g.rates, [])

    def test_adapter_absent_is_tolerated(self):
        # Non-adaptive configs pass adapter=None; the notify must still set the
        # encoder bitrate and not raise.
        scream_cb, gcc_cb, codec = self._cbs()
        e = self._StubEnc()
        scream_cb(self._StubCC("current-max-bitrate", 800), None, e, codec, None)
        self.assertEqual(e.bps, 800 * 1000)


@unittest.skipUnless(_GST, "GStreamer/gi not available on this host")
class TestResolutionAdapterApply(unittest.TestCase):
    """Regression for the committed-but-dropped-switch bug. on_rate() advances
    the controller's tier inside update() *before* it inspects _pending, so a
    decision made while a switch is in flight is dropped from scheduling. The
    fix: _apply_switch applies the controller's CURRENT tier (current_dims()),
    not a tier captured at schedule time — so the encoded resolution always
    converges to the controller's idx and the two can never permanently
    disagree. These tests pin that invariant without timing or a main loop."""

    class _RecCaps:
        def __init__(self): self.caps_str = None
        def set_property(self, k, v):
            if k == "caps":
                self.caps_str = v.to_string()

    class _RecPad:
        def __init__(self): self.events = 0
        def send_event(self, _ev): self.events += 1; return True

    class _RecEnc:
        def __init__(self): self._pad = TestResolutionAdapterApply._RecPad()
        def get_static_pad(self, _n): return self._pad

    def _adapter(self):
        import types
        from gstexp.camera import _ResolutionAdapter
        from gstexp.resolution_control import ResolutionController
        tier = lambda h, r: types.SimpleNamespace(height=h, min_rate_kbps=r)
        ladder = types.SimpleNamespace(
            tiers=[tier(1024, 2500), tier(512, 700), tier(384, 0)],
            hysteresis_up_hold_s=4.0, hysteresis_down_hold_s=0.5,
            min_switch_interval_s=2.0, ewma_alpha=1.0)
        ctrl = ResolutionController(ladder, 1280, 1024)
        rc, enc = self._RecCaps(), self._RecEnc()
        return _ResolutionAdapter(ctrl, rc, enc), ctrl, rc, enc

    def test_apply_uses_controller_current_tier_not_a_stale_one(self):
        ad, ctrl, rc, enc = self._adapter()
        # Scenario: a switch toward tier 1 was scheduled (_pending set), but the
        # controller has since committed on to tier 2 — a decision on_rate
        # dropped because _pending was already True. When the queued idle finally
        # fires, it must apply tier 2 (the controller's truth), not the stale
        # tier 1 it was originally scheduled for.
        ad._pending = True
        ctrl.idx = 1                       # what the switch was scheduled for
        ctrl.idx = 2                       # controller advanced; dropped commit
        ad._apply_switch()
        # tier 2 is 480x384; assert those dims and NOT tier 1's (640x512),
        # robust to GStreamer's caps-string formatting.
        self.assertIn("480", rc.caps_str)
        self.assertIn("384", rc.caps_str)
        self.assertNotIn("640", rc.caps_str)
        self.assertNotIn("512", rc.caps_str)
        self.assertFalse(ad._pending)      # cleared so the next switch can arm
        self.assertEqual(enc._pad.events, 1)   # exactly one forced keyframe

    def test_apply_clears_pending_even_when_send_event_raises(self):
        # A switch racing teardown (encoder pad gone) must not crash the loop or
        # wedge _pending True forever.
        ad, ctrl, rc, enc = self._adapter()
        ad._pending = True
        ctrl.idx = 2

        class _Boom:
            def get_static_pad(self, _n):
                raise RuntimeError("element torn down")
        ad._encoder = _Boom()
        ad._apply_switch()                 # must not raise
        self.assertFalse(ad._pending)      # still cleared in finally


if __name__ == "__main__":
    unittest.main()
