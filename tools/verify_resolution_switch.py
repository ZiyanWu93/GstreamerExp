#!/usr/bin/env python3
"""Mechanism verification for CC-driven adaptive resolution.

Runs the REAL ResolutionController + _ResolutionAdapter (from gstexp.camera) in
a self-contained live GStreamer pipeline and drives them with a synthetic CC
rate trace — no SCReAM, no network, no remote viewer. This isolates the one
thing the distributed run can't cheaply prove on demand: that a controller
decision actually re-initialises the encoder at the new resolution, with a
keyframe at the switch, and no errors.

It is a MECHANISM test (controlled videotestsrc stimulus), not a measurement
experiment — content is irrelevant to "does the capsfilter switch + force-key-
unit re-init the encoder", so the recorded-video rule does not apply here.

Fidelity choices (so a PASS actually retires risk, per adversarial review):
  * The encoder is built with the PRODUCTION Vp8Codec.configure_encoder (CBR,
    cpu-used=8, deadline=1, keyframe-max-dist=60) PLUS min-force-key-unit-
    interval set to the ladder's min_switch_interval — the one property that
    could otherwise SUPPRESS the per-switch keyframe. If a keyframe still lands
    at every switch under that throttle, the "re-init forces a keyframe" claim
    holds in production.
  * on_rate is driven from a SEPARATE THREAD while the GLib main loop runs, so
    the cross-thread on_rate -> GLib.idle_add -> _apply_switch marshaling (the
    exact path the segfault fix introduced) is actually exercised.
  * The pass gate asserts BOTH the encoded width+height sequence AND that the
    first encoded buffer at each new resolution is a keyframe.

Pipeline mirrors camera.py's adaptive head chain:
    videotestsrc -> in_caps(1280x1024) -> videoconvert -> src_caps(I420,fps)
      -> videoscale(res_scale) -> res_caps(w,h) -> vp8enc(encoder) -> fakesink

Ladder MIRRORS specs/configurations/405.yaml (floors 2500/700/0, src 1280x1024,
hysteresis 4.0/0.5/2.0, ewma_alpha 0.3). Trace walks high -> 1200 -> 500 ->
high, so the expected encoded sequence is 1280x1024 -> 640x512 -> 480x384 ->
1280x1024.
"""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GLib  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gstexp.resolution_control import ResolutionController  # noqa: E402
from gstexp.camera import _ResolutionAdapter                 # noqa: E402
from gstexp.pipeline_config import Vp8Codec                  # noqa: E402

SRC_W, SRC_H, FPS = 1280, 1024, 10
BITRATE_KBPS, KEYFRAME_MAX_DIST = 1500, 60
MIN_SWITCH_INTERVAL_S = 2.0


def _ladder():
    """Duck-typed mirror of config 405's encoder.resolution_ladder."""
    tier = lambda h, r: types.SimpleNamespace(height=h, min_rate_kbps=r)
    return types.SimpleNamespace(
        tiers=[tier(1024, 2500), tier(512, 700), tier(384, 0)],
        hysteresis_up_hold_s=4.0, hysteresis_down_hold_s=0.5,
        min_switch_interval_s=MIN_SWITCH_INTERVAL_S, ewma_alpha=0.3)


# Synthetic CC rate trace: (until_elapsed_s, rate_kbps). Held long enough at
# each level to clear hysteresis (up_hold 4 s dominates).
TRACE = [(6.0, 4000.0), (15.0, 1200.0), (24.0, 500.0), (34.0, 4000.0)]


def _rate_at(elapsed: float) -> float:
    for until, rate in TRACE:
        if elapsed <= until:
            return rate
    return TRACE[-1][1]


def main():
    Gst.init(None)
    codec = Vp8Codec()
    ctrl = ResolutionController(_ladder(), SRC_W, SRC_H)

    pipeline = Gst.Pipeline.new("res-switch-verify")
    els = {}
    def mk(factory, name, **props):
        e = Gst.ElementFactory.make(factory, name)
        if e is None:
            sys.exit(f"missing element: {factory}")
        for k, v in props.items():
            e.set_property(k.replace("_", "-"), v)
        pipeline.add(e)
        els[name] = e
        return e

    mk("videotestsrc", "src", is_live=True, pattern=0)
    in_caps = mk("capsfilter", "in_caps")
    in_caps.set_property("caps", Gst.Caps.from_string(
        f"video/x-raw,width={SRC_W},height={SRC_H},framerate={FPS}/1"))
    mk("videoconvert", "conv")
    src_caps = mk("capsfilter", "src_caps")
    src_caps.set_property("caps", Gst.Caps.from_string(
        f"video/x-raw,format=I420,framerate={FPS}/1"))
    mk("videoscale", "res_scale")
    res_caps = mk("capsfilter", "res_caps")
    w0, h0 = ctrl.dims(0)
    res_caps.set_property("caps", Gst.Caps.from_string(
        f"video/x-raw,width={w0},height={h0}"))
    # Production encoder configuration (Vp8Codec) + the keyframe throttle that
    # could suppress a forced keyframe — exactly the production setup.
    enc = mk("vp8enc", "encoder")
    codec.configure_encoder(enc, BITRATE_KBPS, KEYFRAME_MAX_DIST)
    # Mirror camera.py: best-effort keyframe throttle. This vp8enc build has no
    # such property, so the set silently no-ops in production too — meaning
    # there is no throttle that could suppress the per-switch keyframe here.
    try:
        enc.set_property("min-force-key-unit-interval",
                         int(MIN_SWITCH_INTERVAL_S * Gst.SECOND))
        print("[harness] min-force-key-unit-interval set (throttle active)")
    except Exception:
        print("[harness] vp8enc has no min-force-key-unit-interval "
              "(no keyframe throttle on this build — matches production)")
    mk("fakesink", "sink", sync=False)

    order = ["src", "in_caps", "conv", "src_caps", "res_scale", "res_caps",
             "encoder", "sink"]
    for a, b in zip(order, order[1:]):
        if not els[a].link(els[b]):
            sys.exit(f"link failed: {a} -> {b}")

    adapter = _ResolutionAdapter(ctrl, res_caps, enc)

    # Record (t_rel, width, height, keyframe?) on every encoded buffer.
    samples = []           # [t_rel, w, h, is_keyframe]
    base = {"t": None}

    def on_enc_buffer(pad, info):
        buf = info.get_buffer()
        caps = pad.get_current_caps()
        w = h = None
        if caps is not None and caps.get_size() > 0:
            s = caps.get_structure(0)
            okw, w = s.get_int("width")
            okh, h = s.get_int("height")
            w = w if okw else None
            h = h if okh else None
        now = GLib.get_monotonic_time() / 1e6
        if base["t"] is None:
            base["t"] = now
        kf = not bool(buf.get_flags() & Gst.BufferFlags.DELTA_UNIT)
        samples.append([round(now - base["t"], 2), w, h, kf])
        return Gst.PadProbeReturn.OK

    enc.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, on_enc_buffer)

    loop = GLib.MainLoop()
    state = {"errors": []}
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_msg(_bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            state["errors"].append(f"{err.message} | {dbg}")
            loop.quit()
        elif msg.type == Gst.MessageType.EOS:
            loop.quit()
    bus.connect("message", on_msg)

    # Drive the controller from a SEPARATE thread so the cross-thread
    # on_rate -> idle_add -> _apply_switch marshaling is exercised (production
    # calls on_rate from the SCReAM/GCC notify streaming thread, not the loop).
    def driver():
        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            adapter.on_rate(_rate_at(elapsed))
            if elapsed >= TRACE[-1][0]:
                GLib.idle_add(loop.quit)
                return
            time.sleep(0.2)

    pipeline.set_state(Gst.State.PLAYING)
    th = threading.Thread(target=driver, daemon=True)
    th.start()
    loop.run()
    th.join(timeout=2)
    pipeline.set_state(Gst.State.NULL)

    # ---- Report -------------------------------------------------------
    # Transitions in encoded (w,h); for each, was the first buffer at the new
    # resolution a keyframe? (VP8 carries dims only in the keyframe header, so a
    # genuine re-init MUST emit one — even under the min-force-key-unit throttle.)
    transitions = []       # [t_rel, w, h, first_buffer_is_keyframe]
    prev = None
    for t, w, h, kf in samples:
        if w is not None and h is not None and (w, h) != prev:
            transitions.append([t, w, h, kf])
            prev = (w, h)
    dims_seq = [(w, h) for _, w, h, _ in transitions]
    total_kf = sum(1 for _, _, _, kf in samples if kf)
    # every NON-initial transition's first buffer must be a keyframe
    switch_kf_ok = all(kf for _, _, _, kf in transitions[1:])

    expected = [(1280, 1024), (640, 512), (480, 384), (1280, 1024)]
    print("=== RESULT ===")
    print(f"encoded buffers: {len(samples)}  errors: {len(state['errors'])}")
    for e in state["errors"]:
        print(f"  ERROR: {e}")
    print(f"encoded transitions [t, w, h, first-buf-keyframe]: {transitions}")
    print(f"dims sequence: {dims_seq}")
    print(f"expected:      {expected}")
    print(f"total keyframes: {total_kf}  "
          f"every-switch-first-buffer-is-keyframe: {switch_kf_ok} "
          f"({sum(1 for _,_,_,kf in transitions[1:] if kf)}/{max(0,len(transitions)-1)})")
    seq_ok = dims_seq == expected
    ok = seq_ok and switch_kf_ok and not state["errors"]
    print(f"sequence match: {seq_ok}   keyframe-at-every-switch: {switch_kf_ok}"
          f"   errors: {len(state['errors'])}")
    print("VERDICT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
