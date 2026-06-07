"""Metric plugins for the worker.

Each metric attaches probes/observers to a built Gst.Pipeline and
returns {samples, summary} at finalize time. The worker iterates
a list of enabled metrics, attaches them all, samples through the
GLib main loop, and merges the result into the result file under
the "metrics" key.

Cli.py walks both result files at end-of-run; metrics that need
camera/viewer correlation (e.g. frame_latency) are merged into the
summary JSON.

To add a new metric:
  1) Subclass Metric, set `name` and `dimension`, implement
     attach() / finalize() (and detach() if it owns resources).
  2) Register in METRIC_CLASSES below.

Configurations enable metrics explicitly via scenario.metrics — there
is no DEFAULT set; absence is rejected by the validator.
"""

from __future__ import annotations

import math
import statistics
import time
from typing import Any, Dict, List

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstRtp", "1.0")
from gi.repository import Gst, GstRtp, GLib

try:
    import numpy as np
except ImportError:                 # only DecodedPsnr needs numpy
    np = None


# Evaluation dimensions — one of these tags every Metric. The five
# dimensions mirror analysis/dimensions/ and the README's "Evaluation
# framework" section: each metric serves exactly one dimension's
# question. Adding a metric without a dimension is a structural bug;
# adding a dimension without a corresponding analysis module is the same.
DIMENSIONS = ("throughput", "quality", "latency", "adaptation", "stability")


class Metric:
    """Base class. Subclasses set `name`, `dimension`, and implement
    attach/finalize.

    `dimension` ties the metric to one of the five evaluation axes
    (see analysis/dimensions/). The analysis layer reads this attribute
    to know which dimension consumes which metric — no opaque name-string
    convention in the analysis code."""

    name: str = "metric"
    dimension: str = ""        # one of DIMENSIONS; subclasses must set this

    def attach(self, gst_pipeline: Gst.Pipeline, role: str) -> None:
        """Install probes/observers after build(), before set_state(PLAYING)."""

    def detach(self) -> None:
        """Tear down resources owned by the metric (sibling pipelines,
        background timers). Default no-op. The worker calls this in a
        finally block before exit, so it must be safe to call even if
        attach() never succeeded."""

    def finalize(self) -> Dict[str, Any]:
        """Return {'samples': [...], 'summary': {...}}.

        Either key is optional. Metrics that only have a summary (e.g.
        a count) can omit `samples`; metrics that only collect raw
        samples (e.g. frame_latency) can omit `summary`.
        """
        return {}


# ----- Frame count ---------------------------------------------------------

class FrameCount(Metric):
    """Count buffers leaving a named element's src pad.

    Camera:   probe encoder.src (frames encoded).
    Viewer:   probe depay.src   (frames depayloaded).
    """
    name = "frame_count"
    dimension = "throughput"

    def __init__(self):
        self.count = 0

    def attach(self, gst_pipeline, role):
        probe_element = "encoder" if role == "camera" else "depay"
        el = gst_pipeline.get_by_name(probe_element)
        if el is None:
            return
        pad = el.get_static_pad("src")
        if pad is None:
            return

        def on_buffer(_pad, _info):
            self.count += 1
            return Gst.PadProbeReturn.OK

        pad.add_probe(Gst.PadProbeType.BUFFER, on_buffer)

    def finalize(self):
        return {"summary": {"frames": self.count}}


# ----- Encoder target bitrate ----------------------------------------------

class EncoderTargetKbps(Metric):
    """Sample vp8enc.target-bitrate every second (camera only).

    The encoder's target-bitrate is the property the congestion
    controller writes into. SCReAM drives it via current-max-bitrate
    (kbps); GCC drives it via estimated-bitrate (bps). Either way,
    this metric reads what the encoder is currently configured for —
    the unified view across algorithms.
    """
    name = "encoder_target_kbps"
    dimension = "adaptation"

    def __init__(self):
        self.samples: List[List[float]] = []   # [(elapsed_s, kbps), ...]
        self._encoder = None
        self._pipeline = None

    def attach(self, gst_pipeline, role):
        if role != "camera":
            return
        self._encoder = gst_pipeline.get_by_name("encoder")
        if self._encoder is None:
            return
        self._pipeline = gst_pipeline
        GLib.timeout_add(1000, self._sample)

    def _sample(self):
        if self._encoder is None:
            return False
        kbps = self._encoder.get_property("target-bitrate") // 1000
        elapsed = (
            self._pipeline.get_pipeline_clock().get_time()
            - self._pipeline.get_base_time()
        ) / Gst.SECOND
        self.samples.append([round(elapsed, 2), int(kbps)])
        return True

    def finalize(self):
        if not self.samples:
            return {}
        kbps = [k for _, k in self.samples]
        return {
            "samples": self.samples,
            "summary": {
                "first_kbps": kbps[0],
                "last_kbps":  kbps[-1],
                "min_kbps":   min(kbps),
                "max_kbps":   max(kbps),
            },
        }


# ----- Per-frame end-to-end latency ----------------------------------------

class FrameLatency(Metric):
    """Per-frame send/receive wall-clock timestamps, keyed by RTP timestamp.

    Camera:   probe udpsink_rtp.sink (or udpsink.sink on the bare camera) —
                                     RTP packets at network egress.
    Viewer:   probe udpsrc_rtp.src   (or udpsrc.src   on the bare viewer) —
                                     RTP packets at network ingress.

    Probing symmetrically at the network boundary keeps the measurement
    independent of any congestion-control element in between (screamtx,
    rtpbin, rtpgccbwe). Each side records (rtp_timestamp, wall_time) on
    the marker-bit packet — the last RTP packet of a video frame. The
    RTP timestamp is the same value on both sides because it travels in
    the packet header, so cli.py joins the lists by RTP timestamp
    directly. This is robust to packet loss: each frame's identity is
    intrinsic to the packet, not a position counter.

    Absolute latency is biased by ±50–150 ms from SSH-based clock-skew
    estimation. Relative differences across runs (e.g. config 7 vs 8)
    are preserved.
    """
    name = "frame_latency"
    dimension = "latency"

    def __init__(self):
        self.samples: List[List[float]] = []

    def attach(self, gst_pipeline, role):
        if role == "camera":
            el = (gst_pipeline.get_by_name("udpsink_rtp")
                  or gst_pipeline.get_by_name("udpsink"))
            pad_name = "sink"
        else:
            el = (gst_pipeline.get_by_name("udpsrc_rtp")
                  or gst_pipeline.get_by_name("udpsrc"))
            pad_name = "src"
        if el is None:
            return
        pad = el.get_static_pad(pad_name)
        if pad is None:
            return

        def on_buffer(_pad, info):
            buf = info.get_buffer()
            if buf is None:
                return Gst.PadProbeReturn.OK
            ok, rtp = GstRtp.RTPBuffer.map(buf, Gst.MapFlags.READ)
            if not ok:
                return Gst.PadProbeReturn.OK
            try:
                # marker=1 selects the last RTP packet of a video frame,
                # so we record exactly once per frame on each side.
                if rtp.get_marker():
                    self.samples.append([rtp.get_timestamp(), time.time()])
            finally:
                rtp.unmap()
            return Gst.PadProbeReturn.OK

        pad.add_probe(Gst.PadProbeType.BUFFER, on_buffer)

    def finalize(self):
        return {"samples": self.samples}


# ----- Per-stage latency attribution ---------------------------------------

class StageLatency(Metric):
    """Per-frame wall-clock timestamps at every named stage in the pipeline.

    Pairs with `frame_latency` to attribute the end-to-end number to its
    constituent parts. Stages on each side:

      Camera:  encoder_in (encoder.sink), encoder_out (encoder.src),
               pay_out (pay.src), wire_out (udpsink_rtp.sink or udpsink.sink)
      Viewer:  wire_in (udpsrc_rtp.src or udpsrc.src), depay_out (depay.src),
               decode_in (decoder.sink), decode_out (decoder.src),
               render_in (convert.src)

    Join key is the RTP timestamp. Pre-pay stages don't have RTP packets;
    the buffer's PTS converts to RTP timestamp via `RTP_TS = pts * 90000
    / 1e9` (vp8/VP9 use the standard 90 kHz RTP clock). Post-depay stages
    on the viewer side: rtpjitterbuffer rewrites PTS to a local clock
    base, so we use a small in-process `pts → rtp_ts` cache populated at
    the depay stage to translate each downstream buffer's PTS back to the
    original RTP timestamp from the wire. The cache is bounded
    (_PTS_CACHE_DEPTH entries) — it just needs to hold long enough for a
    buffer to traverse depay → decoder → convert.

    Output: per-frame, per-stage samples as
    [stage_name, rtp_timestamp, wall_time_seconds] tuples. The latency
    dimension's render() joins them by rtp_timestamp to produce per-stage
    durations (e.g. encode = encoder_out - encoder_in).
    """
    name = "stage_latency"
    dimension = "latency"

    # vp8/VP9 use 90 kHz RTP clock (RFC 3551). Conversion factor for
    # PTS (ns) → RTP timestamp.
    _RTP_CLOCK_HZ = 90000

    # Bounded cache for pts → rtp_ts translation on the viewer side.
    # Sized for the typical jitter buffer + decode latency window
    # (frames in flight between depay and convert).
    _PTS_CACHE_DEPTH = 256

    # Camera pre-pay stages — PTS-keyed (probe converts pts to rtp_ts
    # at record time; one sample per frame because one buffer per frame).
    _CAMERA_PRE_PAY_STAGES = [
        ("encoder_in",  "encoder", "sink"),
        ("encoder_out", "encoder", "src"),
    ]
    # wire_out and pay_out are RTP-packet stages — read RTP_TS from
    # the packet header on the marker bit. Buffers here may carry
    # CLOCK_TIME_NONE PTS depending on the payloader's bookkeeping,
    # so PTS-keyed probing is unreliable.
    _VIEWER_STAGES_AFTER_DEPAY = [
        ("decode_in",  "decoder", "sink"),
        ("decode_out", "decoder", "src"),
        ("render_in",  "convert", "src"),
    ]

    def __init__(self):
        # samples = [[stage_name, rtp_ts, wall_time_s], ...]
        self.samples: List[List] = []
        # pts_ns → rtp_ts cache, populated at depay.src on the viewer
        # so downstream stages (decoder, convert) can translate their
        # buffer PTS back to the wire RTP timestamp.
        self._pts_to_rtp: dict = {}

    def attach(self, gst_pipeline, role):
        if role == "camera":
            self._attach_camera(gst_pipeline)
        else:
            self._attach_viewer(gst_pipeline)

    def _attach_camera(self, gst_pipeline):
        # Pre-pay stages (raw + encoded video frames) — PTS-keyed.
        for stage, el_name, pad_name in self._CAMERA_PRE_PAY_STAGES:
            self._add_pts_probe(gst_pipeline, el_name, pad_name, stage)
        # pay.src: RTP packets. The payloader's RFC 3550 random
        # timestamp-offset means header_rtp_ts != pts_to_rtp_ts(buffer.pts);
        # to make the encoder→pay join work without changing the
        # pipeline, the pay probe records *two* samples per marker-bit
        # frame — one keyed by the header RTP_TS (for downstream
        # pay→wire joins) and one keyed by the PTS-derived RTP_TS (for
        # upstream encoder→pay joins).
        pay_el = gst_pipeline.get_by_name("pay")
        self._add_pay_marker_probe(pay_el, "src", "pay_out")
        # wire_out: udp sink, marker-bit probe.
        wire_el = (gst_pipeline.get_by_name("udpsink_rtp")
                   or gst_pipeline.get_by_name("udpsink"))
        self._add_rtp_marker_probe(wire_el, "sink", "wire_out")

    def _attach_viewer(self, gst_pipeline):
        # wire_in: RTP-packet probe on the udp source, marker bit only.
        wire_el = (gst_pipeline.get_by_name("udpsrc_rtp")
                   or gst_pipeline.get_by_name("udpsrc"))
        self._add_rtp_marker_probe(wire_el, "src", "wire_in")
        # depay_out: dual-purpose — record the sample AND populate the
        # pts→rtp_ts cache. Probed on the depayloader's src pad, where
        # buffers carry both PTS and an attached RTP timestamp via
        # GstReferenceTimestampMeta. We extract RTP_TS from the depay's
        # own bookkeeping (buffer.pts before rtpjitterbuffer's rewrite
        # would be ideal but isn't available here) — instead we read the
        # marker-bit packet's RTP_TS at the wire_in probe and assume
        # FIFO frame ordering through depay. See _on_depay_buffer.
        depay_el = gst_pipeline.get_by_name("depay")
        if depay_el is not None:
            depay_el.get_static_pad("src").add_probe(
                Gst.PadProbeType.BUFFER, self._on_depay_buffer)
        # Post-depay stages: PTS-keyed lookup against the cache.
        for stage, el_name, pad_name in self._VIEWER_STAGES_AFTER_DEPAY:
            self._add_pts_lookup_probe(gst_pipeline, el_name, pad_name, stage)

    def _add_pts_probe(self, gst_pipeline, el_name, pad_name, stage):
        """Per-frame probe that converts buffer.pts → RTP timestamp at
        record time. Used for camera-side pre-pay stages where the
        buffer is raw video or encoded video (no RTP header yet)."""
        el = gst_pipeline.get_by_name(el_name)
        if el is None:
            return
        pad = el.get_static_pad(pad_name)
        if pad is None:
            return

        def on_buffer(_pad, info):
            buf = info.get_buffer()
            if buf is None or buf.pts == Gst.CLOCK_TIME_NONE:
                return Gst.PadProbeReturn.OK
            rtp_ts = self._pts_to_rtp_ts(buf.pts)
            self.samples.append([stage, rtp_ts, time.time()])
            return Gst.PadProbeReturn.OK

        pad.add_probe(Gst.PadProbeType.BUFFER, on_buffer)

    def _add_rtp_marker_probe(self, el, pad_name, stage):
        """Probe that records once per frame at the marker-bit packet,
        reading RTP_TS directly from the packet header. Used for
        camera pay_out / wire_out and viewer wire_in.

        Handles both single-buffer probes and buffer-list probes — RTP
        payloaders (rtpvp8pay) often emit BufferList containing all
        RTP packets of one frame in a single push, in which case
        info.get_buffer() returns None and we have to iterate the list."""
        if el is None:
            return
        pad = el.get_static_pad(pad_name)
        if pad is None:
            return

        def _record_if_marker(buf):
            if buf is None:
                return
            ok, rtp = GstRtp.RTPBuffer.map(buf, Gst.MapFlags.READ)
            if not ok:
                return
            try:
                if rtp.get_marker():
                    self.samples.append(
                        [stage, rtp.get_timestamp(), time.time()])
            finally:
                rtp.unmap()

        def on_data(_pad, info):
            buf = info.get_buffer()
            if buf is not None:
                _record_if_marker(buf)
            else:
                buf_list = info.get_buffer_list()
                if buf_list is not None:
                    for i in range(buf_list.length()):
                        _record_if_marker(buf_list.get(i))
            return Gst.PadProbeReturn.OK

        # Subscribe to BOTH plain buffers and buffer lists — payloaders
        # use one or the other depending on rtpbin's mode.
        pad.add_probe(
            Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST,
            on_data,
        )

    def _add_pay_marker_probe(self, el, pad_name, stage):
        """Variant of _add_rtp_marker_probe specialized for pay.src.
        Records two samples per marker-bit frame: one keyed by the
        header RTP_TS (joins downstream stages that read from header)
        and one keyed by the PTS-derived RTP_TS (joins pre-pay stages
        that don't see RTP packets). This sidesteps the rtpvp8pay
        random timestamp-offset without changing pipeline semantics."""
        if el is None:
            return
        pad = el.get_static_pad(pad_name)
        if pad is None:
            return

        def _record_pay(buf):
            if buf is None:
                return
            ok, rtp = GstRtp.RTPBuffer.map(buf, Gst.MapFlags.READ)
            if not ok:
                return
            try:
                if not rtp.get_marker():
                    return
                wall = time.time()
                header_ts = rtp.get_timestamp()
                self.samples.append([stage, header_ts, wall])
                if buf.pts != Gst.CLOCK_TIME_NONE:
                    derived_ts = self._pts_to_rtp_ts(buf.pts)
                    if derived_ts != header_ts:
                        self.samples.append(
                            [stage + "_pts", derived_ts, wall])
            finally:
                rtp.unmap()

        def on_data(_pad, info):
            buf = info.get_buffer()
            if buf is not None:
                _record_pay(buf)
            else:
                buf_list = info.get_buffer_list()
                if buf_list is not None:
                    for i in range(buf_list.length()):
                        _record_pay(buf_list.get(i))
            return Gst.PadProbeReturn.OK

        pad.add_probe(
            Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST,
            on_data,
        )

    def _on_depay_buffer(self, _pad, info):
        """Viewer depay.src probe: records the depay_out sample AND
        populates the pts→rtp_ts cache for downstream stages.

        rtpjitterbuffer rewrites PTS to a local clock base before
        depay sees the buffer, so the buffer's pts here is NOT the
        camera's source PTS. To still attribute downstream stages to
        the original RTP timestamp from the wire, we reconstruct
        rtp_ts from the wire_in samples (FIFO assumption: the Nth
        marker-bit packet at wire_in corresponds to the Nth depay
        output) and cache pts → rtp_ts so decoder/convert probes can
        look up by their buffer's pts.
        """
        buf = info.get_buffer()
        if buf is None or buf.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        # Pair the Nth depay output with the Nth wire_in marker-bit
        # packet. Count both depay outputs and wire_in samples we've
        # seen so far. Invariant: depay output count <= wire_in count.
        wire_ins = [s for s in self.samples if s[0] == "wire_in"]
        depay_outs = [s for s in self.samples if s[0] == "depay_out"]
        idx = len(depay_outs)
        if idx >= len(wire_ins):
            # Wire_in sample hasn't landed yet (race in the streaming
            # thread); skip — the next frame will catch up.
            return Gst.PadProbeReturn.OK
        rtp_ts = wire_ins[idx][1]
        self.samples.append(["depay_out", rtp_ts, time.time()])
        self._pts_to_rtp[buf.pts] = rtp_ts
        # Bound the cache.
        if len(self._pts_to_rtp) > self._PTS_CACHE_DEPTH:
            oldest = next(iter(self._pts_to_rtp))
            del self._pts_to_rtp[oldest]
        return Gst.PadProbeReturn.OK

    def _add_pts_lookup_probe(self, gst_pipeline, el_name, pad_name, stage):
        """Per-frame probe on a viewer post-depay stage. Looks up
        buffer.pts in the cache populated by _on_depay_buffer to find
        the original wire RTP timestamp."""
        el = gst_pipeline.get_by_name(el_name)
        if el is None:
            return
        pad = el.get_static_pad(pad_name)
        if pad is None:
            return

        def on_buffer(_pad, info):
            buf = info.get_buffer()
            if buf is None or buf.pts == Gst.CLOCK_TIME_NONE:
                return Gst.PadProbeReturn.OK
            rtp_ts = self._pts_to_rtp.get(buf.pts)
            if rtp_ts is None:
                return Gst.PadProbeReturn.OK
            self.samples.append([stage, rtp_ts, time.time()])
            return Gst.PadProbeReturn.OK

        pad.add_probe(Gst.PadProbeType.BUFFER, on_buffer)

    def _pts_to_rtp_ts(self, pts_ns: int) -> int:
        """PTS (nanoseconds, GStreamer convention) → RTP timestamp
        (32-bit, 90 kHz). Modulo 2**32 because RTP timestamps wrap."""
        return int(pts_ns * self._RTP_CLOCK_HZ // 1_000_000_000) & 0xFFFFFFFF

    def finalize(self):
        return {"samples": self.samples}


# ----- Byte-rate base ------------------------------------------------------

class _ByteRateMetric(Metric):
    """Counts bytes through one pad and reports a per-second kbps trajectory.

    Subclasses override _find_pad() to choose where to probe and set
    `report_total_bytes` if the cumulative byte count belongs in the summary.
    """
    report_total_bytes: bool = False

    def __init__(self):
        self._bytes = 0
        self._pipeline = None
        self.samples: List[List[float]] = []   # [(elapsed_s, kbps)]
        self._last_bytes = 0
        self._last_elapsed = 0.0

    def _find_pad(self, gst_pipeline, role):
        """Return the Gst.Pad to probe, or None to disable this metric for
        the current role/pipeline shape."""
        raise NotImplementedError

    def attach(self, gst_pipeline, role):
        pad = self._find_pad(gst_pipeline, role)
        if pad is None:
            return
        self._pipeline = gst_pipeline

        def on_buffer(_pad, info):
            buf = info.get_buffer()
            if buf is not None:
                self._bytes += buf.get_size()
            return Gst.PadProbeReturn.OK

        pad.add_probe(Gst.PadProbeType.BUFFER, on_buffer)
        GLib.timeout_add(1000, self._sample)

    def _sample(self):
        if self._pipeline is None:
            return False
        elapsed = (
            self._pipeline.get_pipeline_clock().get_time()
            - self._pipeline.get_base_time()
        ) / Gst.SECOND
        delta_bytes = self._bytes - self._last_bytes
        delta_t = max(elapsed - self._last_elapsed, 1e-6)
        kbps = (delta_bytes * 8.0) / 1000.0 / delta_t
        self.samples.append([round(elapsed, 2), round(kbps, 1)])
        self._last_bytes = self._bytes
        self._last_elapsed = elapsed
        return True

    def finalize(self):
        kbps = [k for _, k in self.samples]
        if not kbps:
            return ({"summary": {"total_bytes": self._bytes}}
                    if self.report_total_bytes else {})
        summary = {
            "first_kbps": kbps[0],
            "last_kbps":  kbps[-1],
            "min_kbps":   round(min(kbps), 1),
            "max_kbps":   round(max(kbps), 1),
            "mean_kbps":  round(sum(kbps) / len(kbps), 1),
        }
        if self.report_total_bytes:
            summary["total_bytes"] = self._bytes
        return {"samples": self.samples, "summary": summary}


# ----- On-wire throughput --------------------------------------------------

class WireBytes(_ByteRateMetric):
    """Bytes through the UDP element at the network boundary.

    Camera:   udpsink_rtp.sink (SCReAM/GCC) or udpsink.sink (bare).
    Viewer:   udpsrc_rtp.src   (SCReAM/GCC) or udpsrc.src   (bare).

    The on-wire rate, distinct from `encoder_target_kbps` (what the CC
    asked for) and `encoded_bitrate` (vp8enc's actual output) — useful
    for confirming CC cuts actually reach the wire.
    """
    name = "wire_bytes"
    dimension = "throughput"
    report_total_bytes = True

    def _find_pad(self, gst_pipeline, role):
        if role == "camera":
            el = (gst_pipeline.get_by_name("udpsink_rtp")
                  or gst_pipeline.get_by_name("udpsink"))
            pad_name = "sink"
        else:
            el = (gst_pipeline.get_by_name("udpsrc_rtp")
                  or gst_pipeline.get_by_name("udpsrc"))
            pad_name = "src"
        return el.get_static_pad(pad_name) if el else None


# ----- Encoded byte-rate (actual encoder output) ---------------------------

class EncodedBitrate(_ByteRateMetric):
    """Per-second kbps actually emitted by vp8enc (camera only).

    Compare against `encoder_target_kbps` (what the CC asked for) to spot
    encoder lag — vp8enc takes a keyframe interval to honor target-bitrate
    changes, so the two can diverge during fast CC cuts.

    Note: this is *not* per-frame VP8 QP. vp8enc does not expose QP via
    GObject properties — extracting it would require parsing the VP8
    bitstream. Encoded byte-rate is the cleanest available bitrate-side
    quality proxy.
    """
    name = "encoded_bitrate"
    dimension = "throughput"

    def _find_pad(self, gst_pipeline, role):
        if role != "camera":
            return None
        el = gst_pipeline.get_by_name("encoder")
        return el.get_static_pad("src") if el else None


# ----- Decoder / depay warnings (corruption / packet loss) -----------------

class DecoderErrors(Metric):
    """Count GStreamer bus warnings from rtpvp8depay and vp8dec.

    Viewer-side. Watches the pipeline bus for WARNING messages
    whose source element is the depacketizer or decoder. On a clean
    LAN this stays at zero; under packet loss or bitstream
    corruption it grows.

    Recorded fields:
      - depay_warnings: count from rtpvp8depay (typically lost RTP
        packets, malformed payloads)
      - decoder_warnings: count from vp8dec (corrupt frames,
        missing references)
      - last_messages: up to 10 most recent warning strings, for
        debugging
    """
    name = "decoder_errors"
    dimension = "quality"

    def __init__(self):
        self.depay_warnings = 0
        self.decoder_warnings = 0
        self.other_warnings = 0
        self.recent: List[str] = []

    def attach(self, gst_pipeline, role):
        if role != "camera":
            self._install(gst_pipeline)

    def _install(self, gst_pipeline):
        bus = gst_pipeline.get_bus()
        # add_signal_watch is idempotent on most builds; worker.py
        # already calls it, so connecting another handler is safe.
        bus.connect("message::warning", self._on_warning)

    def _on_warning(self, _bus, msg):
        try:
            err, _debug = msg.parse_warning()
            text = err.message if err else ""
        except Exception:
            text = ""
        src = msg.src.get_name() if msg.src else ""
        if src == "depay":
            self.depay_warnings += 1
        elif src == "decoder":
            self.decoder_warnings += 1
        else:
            self.other_warnings += 1
        if len(self.recent) < 10:
            self.recent.append(f"{src}: {text}")

    def finalize(self):
        return {
            "summary": {
                "depay_warnings":    self.depay_warnings,
                "decoder_warnings":  self.decoder_warnings,
                "other_warnings":    self.other_warnings,
                "last_messages":     self.recent,
            }
        }


# ----- Decoded-frame PSNR (viewer only) ------------------------------------

class DecodedPsnr(Metric):
    """Per-frame Y-plane PSNR at the viewer, vs a ground-truth source
    reproduced locally.

    Two pipelines run on the viewer:
      - The main viewer pipeline (built by ViewerPipeline) produces
        decoded frames at the `decoder` element's src pad. We add a
        pad probe there to extract each decoded buffer's Y plane.
      - A sibling ground-truth pipeline (built here) reproduces the
        camera's source — videotestsrc with the same pattern, dims,
        fps — into an appsink that captures Y planes keyed by frame
        index in arrival order.

    For each decoded frame, frame_n = round(buffer.pts / frame_duration_ns).
    We look up the truth's frame_n entry and compute PSNR over Y.

    PTS preservation: rtpjitterbuffer rewrites incoming PTS to a local
    wall-clock base, so absolute PTS at vp8dec.src is *not* the camera's
    source PTS — but relative offsets within the stream are preserved.
    The metric captures the first decoded frame's PTS as a baseline
    and derives source frame index from `(pts - first_pts) / frame_dur`.
    Assumes the first received decoded frame = source frame 0, which
    holds under every spec's clean opening phase.

    Memory bound: the truth dict holds every source frame for the run
    (populated up-front during attach() before the main pipeline plays),
    so lookup is O(1) and never misses on timing. Cost is num_frames ×
    W × H bytes (Y plane only): ~92 MB for 300×640×480, ~552 MB for
    600×1280×720.
    """

    name = "decoded_psnr"
    dimension = "quality"

    def __init__(self, ground_truth: dict | None = None):
        self.ground_truth = ground_truth
        self.samples: List[List[float]] = []      # [pts_ns, frame_n, psnr_db]
        self._truth_frames: Dict[int, bytes] = {}  # frame_n -> Y plane bytes
        self._truth_next_n = 0                     # running counter for appsink
        self._truth_pipeline: Gst.Pipeline | None = None
        self._fps: int = 0
        self._width: int = 0
        self._height: int = 0
        # Source frame index comes from the RTP timestamp, NOT the decoded
        # buffer's PTS. rtpjitterbuffer rewrites PTS to a local wall-clock
        # arrival base, and under congestion-control pacing + network delay
        # frames do not arrive at the source's nominal cadence — so deriving
        # the frame index from PTS drifts off the true source frame as the
        # run progresses (decoded frame N gets scored against truth frame
        # M != N, collapsing PSNR to the ~10 dB uncorrelated floor).
        #
        # The RTP timestamp travels in the packet header and encodes the
        # source frame's original PTS (RFC 3551 90 kHz video clock for VP8),
        # so it is robust to arrival jitter and to dropped frames. We
        # reconstruct it the same way StageLatency does: read the wire RTP
        # timestamp at the udp source's marker bit, FIFO-pair it with the
        # depayloader output, and cache pts -> rtp_ts so the decoder.src
        # probe can translate its buffer PTS back to the wire timestamp.
        self._rtp_clock_hz = 90000
        self._ticks_per_frame = 0                   # set in attach() from fps
        self._first_rtp_ts: int | None = None
        self._wire_in_rtp_ts: List[int] = []        # marker-bit RTP ts, in arrival order
        self._depay_count = 0                        # FIFO index into wire_in list
        self._pts_to_rtp: Dict[int, int] = {}        # decoder/depay PTS -> wire rtp_ts
        self._pts_cache_depth = 256
        # Truth-index offset: decoded frame_n maps to truth[frame_n + offset].
        # The first received frame is not source frame 0 (the camera streams
        # during the setup window before the viewer's socket is up, so the
        # opening frames are dropped on the wire) -- so we calibrate the offset
        # by content. And because the viewer-side rtp_ts<->decoded-frame
        # association can shift by a frame whenever the jitter buffer drops a
        # frame, the offset is NOT constant for the whole run: we re-calibrate
        # (self-healing) whenever alignment breaks. Re-lock only when a nearby
        # offset beats the current alignment by a large margin -- that
        # distinguishes a misalignment shift (a sharp better match exists
        # elsewhere) from genuine congestion quality loss (no better match
        # anywhere), so real degradation is recorded, not "fixed away".
        self._truth_offset: int | None = None
        self._calibrate_max_search = 300            # initial wide skew scan (frames)
        self._lock_db = 25.0                        # min PSNR to accept an offset lock
        self._relock_trigger_db = 22.0              # below this, suspect a shift
        self._relock_window = 40                    # +/- search around current offset
        self._relock_min_gain_db = 10.0             # new match must beat current by this
        self._n_relocks = 0
        self._n_matched = 0
        self._n_unmatched = 0
        self._n_size_mismatch = 0

    def attach(self, gst_pipeline, role):
        if role != "viewer" or self.ground_truth is None:
            return                                 # camera-side: no-op
        if np is None:
            print("[decoded_psnr] numpy not available — metric disabled",
                  flush=True)
            return
        gt = self.ground_truth
        if gt.get("backend") not in ("synthetic", "file"):
            print(f"[decoded_psnr] backend {gt.get('backend')!r} not "
                  f"supported (synthetic, file only) — metric disabled",
                  flush=True)
            return

        self._fps = int(gt["fps"])
        self._ticks_per_frame = self._rtp_clock_hz // self._fps
        self._width = int(gt["width"])
        self._height = int(gt["height"])

        self._truth_pipeline = self._build_truth_pipeline(gt)
        # Block until truth populates fully (or we hit a generous timeout).
        # Otherwise the main pipeline's first decoded frames arrive before
        # the truth dict has them, manifesting as a leading n_unmatched
        # bucket that's actually a timing race, not real data loss.
        # Synthetic populates ~300 small frames in well under a second;
        # file-backed decoding takes longer (vp8/webm decode at full
        # speed), so 60s leaves comfortable margin even for a 30-second
        # 720p clip on a slow decoder.
        self._truth_pipeline.set_state(Gst.State.PLAYING)
        bus = self._truth_pipeline.get_bus()
        msg = bus.timed_pop_filtered(
            60 * Gst.SECOND,
            Gst.MessageType.EOS | Gst.MessageType.ERROR,
        )
        if msg is None:
            print(f"[decoded_psnr] truth pipeline didn't reach EOS in 10s "
                  f"(populated {self._truth_next_n} frames so far) — "
                  f"continuing anyway", flush=True)
        elif msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            print(f"[decoded_psnr] truth pipeline error: {err.message} "
                  f"({debug}) — metric will see all decoded frames as "
                  f"unmatched", flush=True)

        # RTP-timestamp reconstruction probes (mirror StageLatency):
        #   wire_in  — marker-bit RTP ts at the udp source, in arrival order
        #   depay.src — FIFO-pair with wire_in and cache pts -> rtp_ts
        wire_el = (gst_pipeline.get_by_name("udpsrc_rtp")
                   or gst_pipeline.get_by_name("udpsrc"))
        if wire_el is not None:
            wpad = wire_el.get_static_pad("src")
            if wpad is not None:
                wpad.add_probe(
                    Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST,
                    self._on_wire_in)
        depay_el = gst_pipeline.get_by_name("depay")
        if depay_el is not None:
            dpad = depay_el.get_static_pad("src")
            if dpad is not None:
                dpad.add_probe(Gst.PadProbeType.BUFFER, self._on_depay_buffer)

        decoder = gst_pipeline.get_by_name("decoder")
        if decoder is None:
            return
        pad = decoder.get_static_pad("src")
        if pad is None:
            return
        pad.add_probe(Gst.PadProbeType.BUFFER, self._on_decoded_buffer)

    def _build_truth_pipeline(self, gt: dict) -> Gst.Pipeline:
        """Build the source-of-truth pipeline. Dispatches by backend.

        Both branches end in a capsfilter that forces I420 at the
        camera's resolution/fps + an appsink whose `new-sample`
        callback stores Y-plane bytes keyed by a running counter.
        Running not-live lets the truth dict pre-populate before the
        main viewer pipeline starts seeing decoded frames; lookup by
        frame_n is O(1) and never misses because of timing skew
        between the truth and decoded pipelines.

        Memory bound: num_frames × W × H bytes (Y plane only). For a
        300-frame 640x480 run, ~92 MB. For 600 × 1280 × 720, ~552 MB —
        acceptable for a research testbed but a future cap may want to
        evict frames once they've been matched.
        """
        backend = gt["backend"]
        if backend == "synthetic":
            return self._build_truth_pipeline_synthetic(gt)
        if backend == "file":
            return self._build_truth_pipeline_file(gt)
        # validation.py rejects other backends; this is unreachable
        # in practice but kept as a defensive fallback.
        raise RuntimeError(f"unsupported truth backend: {backend!r}")

    def _build_truth_pipeline_synthetic(self, gt: dict) -> Gst.Pipeline:
        pattern = (gt.get("synthetic") or {}).get("pattern", "ball")
        num_frames = int(gt.get("num_frames") or 0)
        pipe = Gst.Pipeline.new("decoded_psnr_truth")
        src = Gst.ElementFactory.make("videotestsrc", "truth_src")
        src.set_property("is-live", False)
        src.set_property("pattern", pattern)
        if num_frames > 0:
            src.set_property("num-buffers", num_frames)

        caps = self._make_truth_capsfilter()
        sink = self._make_truth_appsink()
        for el in (src, caps, sink):
            pipe.add(el)
        src.link(caps)
        caps.link(sink)
        return pipe

    def _build_truth_pipeline_file(self, gt: dict) -> Gst.Pipeline:
        """File-backed truth: filesrc → decodebin → videoconvert →
        videoscale → videorate → capsfilter (I420 at target dims/fps)
        → appsink. Mirrors pipeline_config.FileSource.build but ends
        in an appsink instead of clocksync — we want frames pumped at
        full speed into the dict, not paced to wall-clock.

        Same path on the viewer host as on the camera host. Validator
        forbids loop=true here (camera and viewer seek-on-EOS aren't
        synchronized), so a single-pass playback gives a deterministic
        frame sequence both sides agree on."""
        path = (gt.get("file") or {}).get("path", "")
        num_frames = int(gt.get("num_frames") or 0)
        pipe = Gst.Pipeline.new("decoded_psnr_truth")

        filesrc = Gst.ElementFactory.make("filesrc", "truth_filesrc")
        filesrc.set_property("location", path)
        decode  = Gst.ElementFactory.make("decodebin", "truth_decode")
        convert = Gst.ElementFactory.make("videoconvert", "truth_convert")
        scale   = Gst.ElementFactory.make("videoscale", "truth_scale")
        rate    = Gst.ElementFactory.make("videorate", "truth_rate")
        caps    = self._make_truth_capsfilter()
        sink    = self._make_truth_appsink()

        # `eos-after` on identity caps the truth at the camera's
        # num_frames so we don't accumulate extras when the file is
        # longer than the run. Mirrors the worker.py app-level frame
        # cap on the camera side.
        cap_el = None
        if num_frames > 0:
            cap_el = Gst.ElementFactory.make("identity", "truth_cap")
            cap_el.set_property("eos-after", num_frames)

        for el in (filesrc, decode, convert, scale, rate, caps, sink):
            pipe.add(el)
        if cap_el is not None:
            pipe.add(cap_el)

        filesrc.link(decode)
        convert.link(scale)
        scale.link(rate)
        if cap_el is not None:
            rate.link(cap_el)
            cap_el.link(caps)
        else:
            rate.link(caps)
        caps.link(sink)

        # decodebin's video src pad appears dynamically.
        def _on_pad_added(_decode, pad):
            pad_caps = pad.get_current_caps() or pad.query_caps(None)
            if pad_caps and pad_caps.get_structure(0).get_name().startswith("video/"):
                pad.link(convert.get_static_pad("sink"))
        decode.connect("pad-added", _on_pad_added)

        return pipe

    def _make_truth_capsfilter(self) -> Gst.Element:
        caps = Gst.ElementFactory.make("capsfilter", "truth_caps")
        caps.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,format=I420,"
            f"width={self._width},height={self._height},"
            f"framerate={self._fps}/1"
        ))
        return caps

    def _make_truth_appsink(self) -> Gst.Element:
        sink = Gst.ElementFactory.make("appsink", "truth_sink")
        sink.set_property("emit-signals", True)
        sink.set_property("max-buffers", 16)
        sink.set_property("drop", False)
        sink.set_property("sync", False)
        sink.connect("new-sample", self._on_truth_sample)
        return sink

    def _on_truth_sample(self, sink) -> int:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            y_size = self._width * self._height
            if info.size < y_size:
                return Gst.FlowReturn.OK
            # Copy out so we don't hold the GstMapInfo past unmap. bytes()
            # over a memoryview is a copy.
            y_plane = bytes(info.data[:y_size])
        finally:
            buf.unmap(info)

        n = self._truth_next_n
        self._truth_next_n += 1
        self._truth_frames[n] = y_plane
        return Gst.FlowReturn.OK

    def _on_wire_in(self, _pad, info):
        """Record each frame's wire RTP timestamp at its marker-bit packet,
        in arrival order. Mirrors StageLatency's marker probe."""
        def _record(buf):
            if buf is None:
                return
            ok, rtp = GstRtp.RTPBuffer.map(buf, Gst.MapFlags.READ)
            if not ok:
                return
            try:
                if rtp.get_marker():
                    self._wire_in_rtp_ts.append(rtp.get_timestamp())
            finally:
                rtp.unmap()

        buf = info.get_buffer()
        if buf is not None:
            _record(buf)
        else:
            blist = info.get_buffer_list()
            if blist is not None:
                for i in range(blist.length()):
                    _record(blist.get(i))
        return Gst.PadProbeReturn.OK

    def _on_depay_buffer(self, _pad, info):
        """FIFO-pair the Nth depay output with the Nth wire_in marker packet
        and cache pts -> wire rtp_ts for the decoder.src probe to look up."""
        buf = info.get_buffer()
        if buf is None or buf.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        idx = self._depay_count
        if idx >= len(self._wire_in_rtp_ts):
            # wire_in sample for this frame hasn't landed yet (streaming-thread
            # race); skip caching — next frame catches up.
            return Gst.PadProbeReturn.OK
        self._depay_count += 1
        self._pts_to_rtp[buf.pts] = self._wire_in_rtp_ts[idx]
        if len(self._pts_to_rtp) > self._pts_cache_depth:
            del self._pts_to_rtp[next(iter(self._pts_to_rtp))]
        return Gst.PadProbeReturn.OK

    def _on_decoded_buffer(self, _pad, info):
        buf = info.get_buffer()
        if buf is None:
            return Gst.PadProbeReturn.OK
        pts = buf.pts
        if pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        # Translate the decoded buffer's PTS back to the wire RTP timestamp,
        # then derive the source frame index from RTP ts (robust to arrival
        # jitter and drops). Falls through to unmatched if the cache lacks
        # this PTS (e.g. a frame whose wire_in marker never arrived).
        rtp_ts = self._pts_to_rtp.get(pts)
        if rtp_ts is None:
            self._n_unmatched += 1
            return Gst.PadProbeReturn.OK
        if self._first_rtp_ts is None:
            self._first_rtp_ts = rtp_ts
        delta_ticks = (rtp_ts - self._first_rtp_ts) & 0xFFFFFFFF
        frame_n = int(round(delta_ticks / self._ticks_per_frame))

        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.PadProbeReturn.OK
        try:
            y_size = self._width * self._height
            if mapinfo.size < y_size:
                self._n_size_mismatch += 1
                return Gst.PadProbeReturn.OK
            decoded_y = bytes(mapinfo.data[:y_size])
        finally:
            buf.unmap(mapinfo)

        # Initial calibration: wide content scan for the startup skew.
        if self._truth_offset is None:
            best_off, best_db = self._search_offset(
                decoded_y, frame_n, 0, self._calibrate_max_search)
            if best_db < self._lock_db:
                # No convincing match yet (early loss/corruption); defer.
                self._n_unmatched += 1
                return Gst.PadProbeReturn.OK
            self._truth_offset = best_off

        truth = self._truth_frames.get(frame_n + self._truth_offset)
        psnr = _psnr_y(decoded_y, truth) if truth is not None else -1.0

        # Self-healing: a low score may be misalignment (the rtp_ts<->decoded
        # association shifted at a jitter-buffer drop) OR genuine quality loss.
        # Re-search nearby; re-lock ONLY if a different offset matches clearly
        # better. A real degraded frame has no sharp match anywhere, so it is
        # recorded as-is rather than masked.
        if psnr < self._relock_trigger_db:
            lo = self._truth_offset - self._relock_window
            hi = self._truth_offset + self._relock_window + 1
            cand_off, cand_db = self._search_offset(decoded_y, frame_n, lo, hi)
            if cand_db >= self._lock_db and cand_db >= psnr + self._relock_min_gain_db:
                self._truth_offset = cand_off
                self._n_relocks += 1
                truth = self._truth_frames.get(frame_n + self._truth_offset)
                psnr = cand_db

        if truth is None:
            self._n_unmatched += 1
            return Gst.PadProbeReturn.OK

        self.samples.append([int(pts), frame_n, round(psnr, 3)])
        self._n_matched += 1
        return Gst.PadProbeReturn.OK

    def _search_offset(self, decoded_y: bytes, frame_n: int, lo: int, hi: int):
        """Return (best_offset, best_psnr_db) over truth[frame_n + off] for
        off in [lo, hi). Skips offsets with no truth frame."""
        best_off, best_db = self._truth_offset or 0, -1.0
        for off in range(lo, hi):
            t = self._truth_frames.get(frame_n + off)
            if t is None:
                continue
            p = _psnr_y(decoded_y, t)
            if p > best_db:
                best_off, best_db = off, p
        return best_off, best_db

    def detach(self):
        if self._truth_pipeline is not None:
            self._truth_pipeline.set_state(Gst.State.NULL)
            self._truth_pipeline = None
        self._truth_frames.clear()
        self._pts_to_rtp.clear()
        self._wire_in_rtp_ts.clear()
        self._truth_offset = None

    def finalize(self):
        psnrs = [row[2] for row in self.samples
                 if not math.isnan(row[2])]
        summary: Dict[str, Any] = {
            "n_matched": self._n_matched,
            "n_unmatched": self._n_unmatched,
            "n_size_mismatch": self._n_size_mismatch,
            "n_relocks": self._n_relocks,
        }
        if psnrs:
            psnrs_sorted = sorted(psnrs)
            summary.update({
                "mean_psnr_db":   round(sum(psnrs) / len(psnrs), 3),
                "median_psnr_db": round(statistics.median(psnrs_sorted), 3),
                "p10_psnr_db":    round(_percentile(psnrs_sorted, 10), 3),
                "p99_psnr_db":    round(_percentile(psnrs_sorted, 99), 3),
                "min_psnr_db":    round(psnrs_sorted[0], 3),
                "max_psnr_db":    round(psnrs_sorted[-1], 3),
            })
        return {"samples": self.samples, "summary": summary}


def _psnr_y(decoded_y: bytes, source_y: bytes) -> float:
    """Per-pixel Y-plane PSNR in dB. 100.0 when identical (capped to
    avoid +inf for the all-frames-match degenerate case)."""
    a = np.frombuffer(decoded_y, dtype=np.uint8).astype(np.int32)
    b = np.frombuffer(source_y, dtype=np.uint8).astype(np.int32)
    if a.shape != b.shape:
        return float("nan")
    diff = a - b
    mse = float(np.mean(diff * diff))
    if mse <= 0:
        return 100.0
    return 10.0 * math.log10(255.0 ** 2 / mse)


def _percentile(sorted_xs: List[float], p: float) -> float:
    """Linear-interpolation percentile on a presorted list."""
    if not sorted_xs:
        return float("nan")
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    k = (len(sorted_xs) - 1) * (p / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


# ----- Latency budget enforcement (viewer only) ----------------------------

class LateDrops(Metric):
    """Drop frames at the viewer's render boundary that exceed the
    configured latency budget; count the drops.

    Concept: a frame older than `latency_budget_ms` at the moment it
    reaches the sink is operationally useless to the teleoperator
    (their reaction is already obsolete). Default behaviour today is
    to deliver all such late frames anyway. With a positive budget,
    this metric drops them at convert.src and reports how many.

    Lateness model (single-host, no skew dependence): on the first
    decoded frame, capture (wall_first, pts_first). For each
    subsequent frame compute

        lateness = (wall_now - wall_first) - (pts_now - pts_first)

    which captures how much real-time the pipeline has fallen behind
    the source's intended cadence (positive = late). The first frame
    is treated as on-time by definition; the budget catches drift
    accumulated after that.

    The metric reads `latency_budget_ms` from the viewer spec via
    make_metrics(spec=...). Budget == 0 → no enforcement, no drops,
    counter stays at 0 — safe to leave the metric enabled by default.
    """
    name = "late_drops"
    dimension = "latency"

    def __init__(self, latency_budget_ms: int = 0):
        self.budget_ms = int(latency_budget_ms or 0)
        self.dropped = 0
        self.delivered = 0
        self.lateness_samples_ms: List[float] = []  # [(pts_ns, lateness_ms), ...]
        self._first_wall_s: float | None = None
        self._first_pts_ns: int | None = None

    def attach(self, gst_pipeline, role):
        if role != "viewer":
            return                          # camera-side: no-op
        # convert.src is the last point before the sink chain (tee +
        # measurement sink, plus optional view branch). Dropping here
        # costs almost nothing (the buffer's already decoded) but
        # captures the operational moment "frame about to be presented."
        # We accept the wasted decode work as the price for the cleaner
        # measurement semantics; dropping at depay would save decode
        # cycles but conflate jitter-buffer-late vs decode-late.
        convert = gst_pipeline.get_by_name("convert")
        if convert is None:
            return
        pad = convert.get_static_pad("src")
        if pad is None:
            return
        pad.add_probe(Gst.PadProbeType.BUFFER, self._on_buffer)

    def _on_buffer(self, _pad, info):
        buf = info.get_buffer()
        if buf is None or buf.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        now = time.time()
        if self._first_wall_s is None:
            self._first_wall_s = now
            self._first_pts_ns = buf.pts
            self.delivered += 1
            return Gst.PadProbeReturn.OK
        wall_elapsed_s = now - self._first_wall_s
        pts_elapsed_s = (buf.pts - self._first_pts_ns) / 1e9
        lateness_ms = (wall_elapsed_s - pts_elapsed_s) * 1000.0
        self.lateness_samples_ms.append([int(buf.pts), round(lateness_ms, 3)])
        if self.budget_ms > 0 and lateness_ms > self.budget_ms:
            self.dropped += 1
            return Gst.PadProbeReturn.DROP
        self.delivered += 1
        return Gst.PadProbeReturn.OK

    def finalize(self):
        total = self.delivered + self.dropped
        summary: Dict[str, Any] = {
            "budget_ms":  self.budget_ms,
            "delivered":  self.delivered,
            "dropped":    self.dropped,
            "total":      total,
        }
        if total > 0:
            summary["dropped_frac"] = round(self.dropped / total, 4)
        if self.lateness_samples_ms:
            ms = sorted(s[1] for s in self.lateness_samples_ms)
            summary["lateness_p50_ms"] = round(_percentile(ms, 50), 3)
            summary["lateness_p99_ms"] = round(_percentile(ms, 99), 3)
            summary["lateness_max_ms"] = round(ms[-1], 3)
        return {"samples": self.lateness_samples_ms, "summary": summary}


# ----- Registry ------------------------------------------------------------

METRIC_CLASSES = {
    FrameCount.name:        FrameCount,
    EncoderTargetKbps.name: EncoderTargetKbps,
    FrameLatency.name:      FrameLatency,
    StageLatency.name:      StageLatency,
    WireBytes.name:         WireBytes,
    EncodedBitrate.name:    EncodedBitrate,
    DecoderErrors.name:     DecoderErrors,
    DecodedPsnr.name:       DecodedPsnr,
    LateDrops.name:         LateDrops,
}

def make_metrics(names: List[str], spec: dict | None = None) -> List[Metric]:
    """Instantiate metrics by name. Unknown names raise KeyError —
    validation.py cross-checks against METRIC_CLASSES at config load,
    so an unknown name here means a bug in the caller.

    `spec` is the worker's loaded role spec dict (camera or viewer).
    Most metrics ignore it; DecodedPsnr reads `spec["ground_truth"]`
    when on the viewer side."""
    out: List[Metric] = []
    for n in names:
        cls = METRIC_CLASSES[n]
        if cls is DecodedPsnr:
            out.append(cls(ground_truth=(spec or {}).get("ground_truth")))
        elif cls is LateDrops:
            out.append(cls(latency_budget_ms=(spec or {}).get(
                "latency_budget_ms", 0)))
        else:
            out.append(cls())
    return out


# Structural invariant: every registered metric tags itself with one of
# the five DIMENSIONS, and only those. Caught at import time so a metric
# missing or mistyping its dimension fails before any pipeline starts.
for _name, _cls in METRIC_CLASSES.items():
    if _cls.dimension not in DIMENSIONS:
        raise AssertionError(
            f"metric {_name!r} has dimension {_cls.dimension!r}; "
            f"expected one of {DIMENSIONS}")
