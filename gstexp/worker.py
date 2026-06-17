#!/usr/bin/env python3
"""Worker subprocess.

Loads a camera or viewer JSON, builds the pipeline, runs it to
completion (EOS, ERROR, or SIGTERM), writes a result JSON.

Role is shape-inferred: a JSON with "source" is a camera, with
"ingress" is a viewer. Metric plugins (see metrics.py) attach to
the pipeline; their finalize() output is merged into the result file
under the "metrics" key.

Usage:
    python3 worker.py <spec.json> --result-out <result.json>
                                  [--pid-file <pid.json>]
                                  [--metric NAME ...]
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

from gstexp.camera import CameraPipeline
from gstexp.viewer import ViewerPipeline
from gstexp.pipeline_config import (
    Camera, CameraRecovery, CameraSource, Decoder, Depacketizer, Egress,
    Encoder, FileSource, GccCameraConfig, GccViewerConfig, Ingress, Packetizer,
    ScreamCameraConfig, ScreamViewerConfig, Sink, Source, SyntheticSource,
    Viewer, ViewerRecovery, Vp8Codec,
)


# Codec registry: maps the spec's `codec` string to its Codec class.
# Keep in lock-step with validation._IMPLEMENTED_CODECS.
_CODECS = {"vp8": Vp8Codec}


# Source backend registry: maps the spec's `source.backend` string to
# the SourceBackend class. Keep in lock-step with
# validation._IMPLEMENTED_SOURCE_BACKENDS.
_SOURCE_BACKENDS = {"synthetic": SyntheticSource, "file": FileSource,
                    "camera": CameraSource}


def _load_codec(name: str):
    cls = _CODECS.get(name)
    if cls is None:
        sys.exit(f"unknown codec {name!r}; expected one of {sorted(_CODECS)}")
    return cls()


def _load_source(d: dict) -> Source:
    backend_name = d["backend"]
    backend_cls = _SOURCE_BACKENDS.get(backend_name)
    if backend_cls is None:
        sys.exit(f"unknown source backend {backend_name!r}; "
                 f"expected one of {sorted(_SOURCE_BACKENDS)}")
    backend = backend_cls(**d.get(backend_name, {}))
    return Source(
        backend=backend,
        width=d["width"], height=d["height"], fps=d["fps"],
        num_frames=d["num_frames"], clock_overlay=d["clock_overlay"],
    )


def _load_camera_cc(cc):
    if cc is None:
        return None
    algo = cc.pop("algorithm")
    cls = {"scream": ScreamCameraConfig, "gcc": GccCameraConfig}[algo]
    return cls(**cc)


def _load_viewer_cc(cc):
    if cc is None:
        return None
    algo = cc.pop("algorithm")
    cls = {"scream": ScreamViewerConfig, "gcc": GccViewerConfig}[algo]
    return cls(**cc)


def _load_encoder(d: dict) -> Encoder:
    return Encoder(
        codec=_load_codec(d["codec"]),
        bitrate_kbps=d["bitrate_kbps"],
        keyframe_interval_frames=d["keyframe_interval_frames"],
    )


def _load_decoder(d: dict) -> Decoder:
    return Decoder(codec=_load_codec(d["codec"]))
from gstexp.metrics import make_metrics


def _seconds_until(start_at, now: float) -> float:
    """Seconds to wait before releasing the pipeline to hit a shared start
    epoch. 0 when no epoch is set or it has already passed (in which case
    the camera releases immediately — still aligned with its siblings, who
    compute the same already-passed target)."""
    if not start_at:
        return 0.0
    return max(0.0, float(start_at) - now)


def main():
    parser = argparse.ArgumentParser(description="Pipeline worker")
    parser.add_argument("spec", help="Camera or viewer JSON")
    parser.add_argument("--result-out", required=True, help="Result JSON path")
    parser.add_argument("--pid-file", default=None,
                        help="Optional: write own PID here (used by distributed cli.py)")
    parser.add_argument("--metric", action="append", default=[], dest="metric_names",
                        help="Enable a metric by name (repeatable). The caller "
                             "(cli.py) passes the configuration's scenario.metrics "
                             "list verbatim — there is no default.")
    parser.add_argument("--view-display", default=None,
                        help="Viewer only: when set, the viewer pipeline adds "
                             "a tee + autovideosink branch alongside the measurement "
                             "sink. autovideosink reads DISPLAY from the process "
                             "environment; the runner (expo.py) sets that.")
    parser.add_argument("--start-at", type=float, default=None,
                        help="Camera only: shared-epoch start barrier. A target "
                             "wall-clock time IN THIS HOST'S CLOCK; the camera "
                             "waits until then to go PLAYING so all of a run's "
                             "cameras (same host, same clock) release their first "
                             "frame together. The controller derives it from one "
                             "epoch + the measured host skew.")
    args = parser.parse_args()

    if args.pid_file:
        Path(args.pid_file).write_text(str(os.getpid()))

    data = json.loads(Path(args.spec).read_text())

    Gst.init(None)

    def load_camera(d: dict) -> Camera:
        return Camera(
            source=_load_source(d["source"]),
            encoder=_load_encoder(d["encoder"]),
            packetizer=Packetizer(**d["packetizer"]),
            egress=Egress(**d["egress"]),
            congestion_control=_load_camera_cc(d.get("congestion_control")),
            recovery=CameraRecovery(**d["recovery"]),
        )

    def load_viewer(d: dict) -> Viewer:
        return Viewer(
            ingress=Ingress(**d["ingress"]),
            depacketizer=Depacketizer(**d["depacketizer"]),
            decoder=_load_decoder(d["decoder"]),
            sink=Sink(**d["sink"]),
            congestion_control=_load_viewer_cc(d.get("congestion_control")),
            recovery=ViewerRecovery(**d["recovery"]),
        )

    if "source" in data:
        role = "camera"
        pipeline = CameraPipeline(load_camera(data))
    elif "ingress" in data:
        role = "viewer"
        pipeline = ViewerPipeline(load_viewer(data),
                                  view_display=args.view_display)
    else:
        sys.exit(f"not a camera or viewer JSON: {args.spec}")

    gst_pipeline = pipeline.build()

    # Metric plugins. The spec dict is passed so metrics that need
    # role-specific config (currently DecodedPsnr, which reads
    # ground_truth from the viewer spec) can self-configure. Most
    # metrics ignore it.
    metrics = make_metrics(args.metric_names, spec=data)
    for m in metrics:
        m.attach(gst_pipeline, role)

    # Lifecycle.
    loop = GLib.MainLoop()
    state = {"exit_reason": "UNKNOWN", "errors": []}

    def _on_glib_signal(*_args):
        state["exit_reason"] = "SIGTERM"
        loop.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, _on_glib_signal)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, _on_glib_signal)

    # Application-level frame cap. SyntheticSource's videotestsrc caps at
    # the source level (num-buffers); FileSource doesn't, because an
    # identity-eos-after inside the source would post EOS that the
    # seek-on-loop handler can't distinguish from file-end EOS. So
    # cap on the encoder's src pad instead — works regardless of source
    # backend and loop setting.
    if role == "camera" and pipeline.spec.source.num_frames > 0:
        target_frames = pipeline.spec.source.num_frames
        frame_count = {"n": 0}
        encoder_el = gst_pipeline.get_by_name("encoder")

        def _frame_cap_probe(_pad, _info):
            frame_count["n"] += 1
            if frame_count["n"] >= target_frames:
                if state["exit_reason"] == "UNKNOWN":
                    state["exit_reason"] = "EOS"
                # Defer the quit — we're inside a streaming-thread probe,
                # not the GLib main thread.
                GLib.idle_add(loop.quit)
                return Gst.PadProbeReturn.REMOVE
            return Gst.PadProbeReturn.OK

        encoder_el.get_static_pad("src").add_probe(
            Gst.PadProbeType.BUFFER, _frame_cap_probe)

    bus = gst_pipeline.get_bus()
    bus.add_signal_watch()

    # When the camera-side source declares loop=true (FileSource today),
    # EOS at the bus means "the file finished playing" — seek back to
    # t=0 instead of terminating. Synthetic sources never loop, and
    # viewer pipelines have no Source at all, so this only fires for
    # camera/FileSource specs.
    source_loops = role == "camera" and pipeline.spec.source.loops

    def on_message(_bus, msg):
        t = msg.type
        if t == Gst.MessageType.EOS:
            if source_loops:
                gst_pipeline.seek_simple(
                    Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
                return
            if state["exit_reason"] == "UNKNOWN":
                state["exit_reason"] = "EOS"
            loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            state["errors"].append({"message": err.message, "debug": debug or ""})
            state["exit_reason"] = "ERROR"
            loop.quit()

    bus.connect("message", on_message)

    GLib.timeout_add(100, lambda: True)   # keep Python signals responsive

    if role == "camera" and args.start_at:
        # Shared-epoch start barrier (see --start-at): preroll to PAUSED,
        # then release to PLAYING at the shared instant so all of this run's
        # cameras (same host, same clock) emit their first frame together.
        # Prerolling first keeps the PLAYING->first-frame latency uniform
        # across streams, so the barrier aligns the actual send, not just
        # the set_state() call.
        gst_pipeline.set_state(Gst.State.PAUSED)
        gst_pipeline.get_state(5 * Gst.SECOND)        # block until prerolled
        wait_s = _seconds_until(args.start_at, time.time())
        if wait_s:
            time.sleep(wait_s)
    started = time.time()
    gst_pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        gst_pipeline.set_state(Gst.State.NULL)
        # Tear down per-metric resources (sibling pipelines, timers).
        # Each metric's detach() is best-effort and won't raise.
        for m in metrics:
            try:
                m.detach()
            except Exception as e:
                print(f"[worker] {m.name}.detach() failed: {e}", flush=True)
    ended = time.time()

    result = {
        "role":             role,
        "started_at":       started,
        "ended_at":         ended,
        "duration_seconds": ended - started,
        "exit_reason":      state["exit_reason"],
        "errors":           state["errors"],
        "metrics":          {m.name: m.finalize() for m in metrics},
    }

    Path(args.result_out).write_text(json.dumps(result, indent=2))
    sys.exit(0 if not state["errors"] else 1)


if __name__ == "__main__":
    main()
