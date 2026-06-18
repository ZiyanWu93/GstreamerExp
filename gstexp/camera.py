"""CameraPipeline — assembles a Gst.Pipeline from a Camera configuration.

Run on the camera actor (the actor that holds the source of video).
Three shapes, dispatched by spec.congestion_control:
  - Bare    (cc is None)        : videotestsrc -> caps -> encoder -> payloader -> udpsink
  - SCReAM  (cc.algorithm=scream): inserts screamtx + RTCP feedback loop;
                                   notify::current-max-bitrate drives encoder
  - GCC     (cc.algorithm=gcc)  : inserts rtpgccbwe + TWCC RTP header extension
                                   + rtpbin RTCP loop; notify::estimated-bitrate
                                   drives encoder

The SCReAM and GCC branches share a similar structure (encoder -> pay ->
estimator -> rtpbin -> udpsink, plus a separate RTCP loop) but use
different bandwidth-estimation algorithms.
"""

from __future__ import annotations

import time

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstRtp", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import GLib, Gst, GstRtp, GstVideo

from gstexp.pipeline_config import Camera, Codec, GccCameraConfig, ScreamCameraConfig
from gstexp.resolution_control import ResolutionController


# TWCC ("transport-wide congestion control") RTP header extension URI.
# rtpgccbwe needs every outgoing RTP packet tagged with a transport-wide
# sequence number so the viewer side can report per-packet arrival times.
TWCC_URI = "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01"
TWCC_EXT_ID = 1


def _make_rtprtxsend(codec: Codec, rtx_buffer_ms: int):
    """Build an rtprtxsend element for the codec's media→RTX PT pair (RFC 4588)."""
    rtx = Gst.ElementFactory.make("rtprtxsend", "rtxsend")
    pt_map = Gst.Structure.from_string(
        f"application/x-rtp-pt-map,"
        f"{codec.payload_type}=(uint){codec.rtx_payload_type}")[0]
    rtx.set_property("payload-type-map", pt_map)
    # max-size-time is in milliseconds for rtprtxsend.
    rtx.set_property("max-size-time", rtx_buffer_ms)
    return rtx


def _make_rtpulpfecenc(codec: Codec, percentage: int):
    """Build an rtpulpfecenc element with the configured overhead.

    multipacket=True so each FEC packet can protect multiple media
    packets (better recovery for bursty loss; same overhead as
    single-packet mode at the same percentage).
    """
    enc = Gst.ElementFactory.make("rtpulpfecenc", "fecenc")
    enc.set_property("pt", codec.fec_payload_type)
    enc.set_property("percentage", percentage)
    enc.set_property("multipacket", True)
    return enc


class _ResolutionAdapter:
    """Bridges the CC bitrate-notify to runtime resolution switching.

    The bitrate-notify fires on a streaming thread; the ResolutionController
    decision runs inline there (pure Python, safe), but the actual capsfilter
    change + forced keyframe are marshaled onto the GLib main loop via
    idle_add. Mutating the capsfilter caps from the app/main-loop thread is the
    standard dynamic-resolution pattern (capsfilter applies the new caps to the
    next buffer); doing it inline on the notify thread can stall the pipeline,
    and doing it from inside a pad probe on res_caps' own pad re-enters
    negotiation on that pad and crashes this GStreamer binding. VP8 carries
    frame dimensions only in the keyframe header (RFC 6386 §9.1) and re-inits
    on an input-resolution change, so the caps switch itself yields a keyframe;
    the explicit force-key-unit is belt-and-suspenders.

    Threading: on_rate is driven by exactly one CC notify per camera (SCReAM
    current-max-bitrate OR GCC estimated-bitrate), i.e. a single producer
    thread, so the _pending check-then-set needs no lock. Do not fan rate
    notifies in from multiple element threads without adding one.
    """

    def __init__(self, controller, res_caps, encoder):
        self._ctrl = controller
        self._res_caps = res_caps
        self._encoder = encoder
        self._kf_count = 0
        self._pending = False     # one in-flight switch at a time

    def on_rate(self, rate_kbps):
        dims = self._ctrl.update(rate_kbps, time.monotonic())
        if dims is None or self._pending:
            return
        self._pending = True
        # Marshal off the (possibly streaming) caller thread onto the main
        # loop; the worker's GLib.MainLoop services this between frames. Note
        # we pass no dims — _apply_switch reads the controller's live tier.
        GLib.idle_add(self._apply_switch)

    def _apply_switch(self):
        # Apply the controller's CURRENT committed tier, not a tier captured
        # when this idle was scheduled. update() commits its tier change before
        # on_rate inspects _pending, so if the rate moved the controller again
        # while this callback was queued (e.g. a stalled main loop), reading
        # current_dims() here catches up to the truth in one switch instead of
        # leaving the encoded resolution a tier behind the controller's idx.
        try:
            w, h = self._ctrl.current_dims()
            self._res_caps.set_property("caps", Gst.Caps.from_string(
                f"video/x-raw,width={w},height={h}"))
            self._kf_count += 1
            self._encoder.get_static_pad("sink").send_event(
                GstVideo.video_event_new_downstream_force_key_unit(
                    Gst.CLOCK_TIME_NONE, Gst.CLOCK_TIME_NONE,
                    Gst.CLOCK_TIME_NONE, True, self._kf_count))
        except Exception as e:
            # A switch racing pipeline teardown must not crash the main loop.
            print(f"[adapter] resolution switch skipped: {e}", flush=True)
        finally:
            self._pending = False
        return GLib.SOURCE_REMOVE


def _on_bitrate_notify(screamtx, _pspec, encoder, codec, adapter=None):
    # SCReAM reports current-max-bitrate in kbps; convert to bps for the encoder.
    rate_kbps = screamtx.get_property("current-max-bitrate")
    if rate_kbps > 0:
        codec.set_encoder_target_bps(encoder, int(rate_kbps) * 1000)
        if adapter is not None:
            adapter.on_rate(float(rate_kbps))


def _on_gcc_bitrate_notify(gccbwe, _pspec, encoder, codec, adapter=None):
    # rtpgccbwe reports estimated-bitrate in bps directly.
    rate_bps = gccbwe.get_property("estimated-bitrate")
    if rate_bps > 0:
        codec.set_encoder_target_bps(encoder, int(rate_bps))
        if adapter is not None:
            adapter.on_rate(rate_bps / 1000.0)


def _build_scream_params(cc: ScreamCameraConfig) -> str:
    """Build the screamtx.params string from a ScreamCameraConfig.

    Required: -forceidr (so a step-down forces a fresh keyframe at the new
    rate) and the bitrate bounds. Every other Optional field maps to its
    SCReAM CLI flag iff it's not None — absence means "let SCReAM use its
    documented default for that flag."
    """
    parts = ["-forceidr",
             f"-initrate {cc.init_bitrate_kbps}",
             f"-minrate {cc.min_bitrate_kbps}",
             f"-maxrate {cc.max_bitrate_kbps}"]

    # Scalar overrides
    scalars = (
        ("delay_target_seconds",     "-delaytarget"),
        ("ect",                      "-ect"),
        ("pacing_headroom",          "-paceheadroom"),
        ("adaptive_pacing_headroom", "-adaptivepaceheadroom"),
        ("inflight_headroom",        "-inflightheadroom"),
        ("max_window_headroom",      "-maxwindowheadroom"),
        ("min_packets_in_flight",    "-minpktsinflight"),
        ("microburst_interval_ms",   "-microburstinterval"),
        ("reorder_time_seconds",     "-reordertime"),
        ("mul_increase",             "-mulincrease"),
    )
    for field, flag in scalars:
        v = getattr(cc, field)
        if v is not None:
            parts.append(f"{flag} {v}")

    # Boolean flags
    if cc.relaxed_pacing:
        parts.append("-relaxedpacing")
    if cc.no_pacing:
        parts.append("-nopace")

    return " ".join(parts)


class CameraPipeline:
    """Builds a Gst.Pipeline graph from a Camera configuration."""

    name = "camera"

    def __init__(self, spec: Camera):
        self.spec = spec

    def build(self) -> Gst.Pipeline:
        spec = self.spec
        pipeline = Gst.Pipeline.new(self.name)

        # source: backend builds its own subgraph (single element today
        # for synthetic; future file/camera backends include conversion +
        # scaling) and returns the tail element to link downstream from.
        # The capsfilter enforces dimensions independent of backend.
        src = spec.source.backend.build(pipeline, spec.source.num_frames)

        # Adaptive resolution (encoder.resolution_ladder) makes the encode
        # resolution a CC-driven actuator. When OFF (None) the head chain is
        # exactly today's: src_caps pins width/height, no res_scale/res_caps,
        # no controller — byte-for-byte the prior behavior.
        ladder = spec.encoder.resolution_ladder
        adaptive = ladder is not None

        src_caps = Gst.ElementFactory.make("capsfilter", "src_caps")
        if adaptive:
            # MUST NOT pin width/height here — res_caps downstream owns the
            # switchable dimensions, and an upstream fixed-caps width/height
            # would swallow the RECONFIGURE event and silently stall switches.
            src_caps_str = f"video/x-raw,format=I420,framerate={spec.source.fps}/1"
        else:
            src_caps_str = (f"video/x-raw,format=I420,"
                            f"width={spec.source.width},height={spec.source.height},"
                            f"framerate={spec.source.fps}/1")
        src_caps.set_property("caps", Gst.Caps.from_string(src_caps_str))

        # optional clock overlay: burns HH:MM:SS into raw frames before encode
        overlay = None
        if spec.source.clock_overlay:
            overlay = Gst.ElementFactory.make("clockoverlay", "clock")
            overlay.set_property("font-desc", "Sans Bold 36")
            overlay.set_property("time-format", "%H:%M:%S")
            overlay.set_property("halignment", "center")
            overlay.set_property("valignment", "bottom")
            overlay.set_property("shaded-background", True)

        # encoder + packetizer: per-codec factory names, codec-owned
        # property mapping (CBR realtime tunings live in the codec class).
        codec = spec.encoder.codec
        enc = Gst.ElementFactory.make(codec.encoder_factory, "encoder")
        codec.configure_encoder(enc, spec.encoder.bitrate_kbps,
                                spec.encoder.keyframe_interval_frames)
        pay = Gst.ElementFactory.make(codec.payloader_factory, "pay")

        # head chain (raw video processing):
        #   src -> src_caps -> [overlay?] -> [res_scale -> res_caps]? -> enc -> pay
        head = [src, src_caps]
        if overlay is not None:
            head.append(overlay)

        adapter = None
        if adaptive:
            # videoscale + a switchable capsfilter own the encode resolution:
            # the controller picks the tier, the adapter applies it at a frame
            # boundary. res_scale must sit directly upstream of res_caps.
            res_scale = Gst.ElementFactory.make("videoscale", "res_scale")
            res_caps = Gst.ElementFactory.make("capsfilter", "res_caps")
            controller = ResolutionController(ladder, spec.source.width,
                                              spec.source.height)
            top_w, top_h = controller.dims(0)   # top tier == source resolution
            res_caps.set_property("caps", Gst.Caps.from_string(
                f"video/x-raw,width={top_w},height={top_h}"))
            head.extend([res_scale, res_caps])
            # throttle keyframe storms (each switch forces one); pairs with the
            # controller's min_switch_interval.
            try:
                enc.set_property("min-force-key-unit-interval",
                                 int(ladder.min_switch_interval_s * Gst.SECOND))
            except Exception:
                pass
            adapter = _ResolutionAdapter(controller, res_caps, enc)

        head.extend([enc, pay])

        cc = spec.congestion_control
        if cc is None:
            return self._assemble_bare(pipeline, head)
        if isinstance(cc, ScreamCameraConfig):
            return self._assemble_scream(pipeline, head, adapter)
        if isinstance(cc, GccCameraConfig):
            return self._assemble_gcc(pipeline, head, adapter)
        raise TypeError(f"unsupported congestion-control type: {type(cc).__name__}")

    def _assemble_bare(self, pipeline, head_elements):
        """Linear chain ending in udpsink (no SCReAM, no RTCP)."""
        spec = self.spec
        sink = Gst.ElementFactory.make("udpsink", "udpsink")
        sink.set_property("host", spec.egress.host)
        sink.set_property("port", spec.egress.port)
        sink.set_property("sync", False)

        elements = head_elements + [sink]
        for el in elements:
            pipeline.add(el)
        for u, d in zip(elements, elements[1:]):
            u.link(d)
        return pipeline

    def _assemble_scream(self, pipeline, head_elements, adapter=None):
        """Insert screamtx + RTCP loop through rtpbin.

        head_elements is the linear raw-video chain ending in
        the codec's payloader (so the LAST element of head_elements is `pay`).
        """
        spec = self.spec
        cc = spec.congestion_control
        codec = spec.encoder.codec
        rtp_port = spec.egress.port
        rtcp_port = spec.egress.rtcp_port or rtp_port + 1
        pay = head_elements[-1]

        queue1 = Gst.ElementFactory.make("queue", "queue1")
        screamtx = Gst.ElementFactory.make("screamtx", "screamtx")
        screamtx.set_property("params", _build_scream_params(cc))
        encoder = next(e for e in head_elements if e.get_name() == "encoder")
        screamtx.connect("notify::current-max-bitrate",
                         _on_bitrate_notify, encoder, codec, adapter)
        queue2 = Gst.ElementFactory.make("queue", "queue2")

        rtpbin = Gst.ElementFactory.make("rtpbin", "rtpbin")
        # AVPF profile (3) is required for any RTCP feedback message —
        # NACK and PLI both use it. Default profile (AVP) drops feedback.
        if spec.recovery.nack or spec.recovery.pli:
            rtpbin.set_property("rtp-profile", 3)

        udpsink_rtp = Gst.ElementFactory.make("udpsink", "udpsink_rtp")
        udpsink_rtp.set_property("host", spec.egress.host)
        udpsink_rtp.set_property("port", rtp_port)
        udpsink_rtp.set_property("sync", False)

        # Camera's RTCP listener — bind to the camera's own IP (so the
        # viewer's RTCP back to us doesn't collide with the viewer's
        # own listener on the same port on loopback).
        udpsrc_rtcp = Gst.ElementFactory.make("udpsrc", "udpsrc_rtcp")
        udpsrc_rtcp.set_property("port", rtcp_port)
        if spec.egress.bind_host:
            udpsrc_rtcp.set_property("address", spec.egress.bind_host)

        queue_rtcp_in = Gst.ElementFactory.make("queue", "queue_rtcp_in")

        udpsink_rtcp = Gst.ElementFactory.make("udpsink", "udpsink_rtcp")
        udpsink_rtcp.set_property("host", spec.egress.host)
        udpsink_rtcp.set_property("port", rtcp_port)
        udpsink_rtcp.set_property("sync", False)
        udpsink_rtcp.set_property("async", False)

        rtprtxsend = (_make_rtprtxsend(codec, spec.recovery.rtx_buffer_ms)
                      if spec.recovery.nack else None)
        rtpulpfecenc = (_make_rtpulpfecenc(codec, spec.recovery.fec_percentage)
                        if spec.recovery.fec else None)

        elements = head_elements + [queue1, screamtx, queue2,
                                    rtpbin, udpsink_rtp, udpsrc_rtcp,
                                    queue_rtcp_in, udpsink_rtcp]
        if rtprtxsend is not None:
            elements.append(rtprtxsend)
        if rtpulpfecenc is not None:
            elements.append(rtpulpfecenc)
        for el in elements:
            pipeline.add(el)

        # link the head chain (src -> ... -> pay)
        for u, d in zip(head_elements, head_elements[1:]):
            u.link(d)
        # Pipeline order: pay -> rtpulpfecenc? -> queue1 -> screamtx ->
        #                 rtprtxsend? -> queue2 -> rtpbin -> udpsink.
        #
        # FEC sits BEFORE screamtx so repair packets are rate-paced
        # alongside media (same SSRC, just a different PT — screamtx
        # treats them as part of the same stream).
        #
        # RTX sits AFTER screamtx for two reasons. (1) RFC 4588 RTX uses
        # a distinct SSRC for retransmissions; if rtprtxsend ran upstream
        # of screamtx, its retransmissions would arrive at screamtx as a
        # never-registered SSRC ("getStream: no stream"), which crashed
        # the camera under load. (2) Retransmissions are reactive to
        # actual loss, not predicted by the rate controller — pacing
        # them through screamtx would just delay recovery without
        # benefiting the rate estimator. NACK feedback events still
        # propagate from rtpbin upstream to rtprtxsend through the
        # send_rtp_sink_0 pad — rtpbin's RTPRetransmissionRequest events
        # don't need to cross screamtx to reach rtprtxsend.
        chain_after_pay = [pay]
        if rtpulpfecenc is not None:
            chain_after_pay.append(rtpulpfecenc)
        chain_after_pay.append(queue1)
        for u, d in zip(chain_after_pay, chain_after_pay[1:]):
            u.link(d)
        queue1.link(screamtx)
        # post-screamtx tail: optional rtprtxsend, then queue2, into rtpbin.
        post_scream = [screamtx]
        if rtprtxsend is not None:
            post_scream.append(rtprtxsend)
        post_scream.append(queue2)
        for u, d in zip(post_scream, post_scream[1:]):
            u.link(d)
        queue2.link_pads("src", rtpbin, "send_rtp_sink_0")
        rtpbin.link_pads("send_rtp_src_0", udpsink_rtp, "sink")

        udpsrc_rtcp.link(queue_rtcp_in)
        queue_rtcp_in.link_pads("src", screamtx, "rtcp_sink")
        screamtx.link_pads("rtcp_src", rtpbin, "recv_rtcp_sink_0")
        rtpbin.link_pads("send_rtcp_src_0", udpsink_rtcp, "sink")

        return pipeline

    def _assemble_gcc(self, pipeline, head_elements, adapter=None):
        """Insert rtpgccbwe + TWCC feedback loop through rtpbin.

        Pipeline topology:
            ... -> pay -> rtpgccbwe -> rtpbin (send_rtp_sink_0)
                                       rtpbin (send_rtp_src_0) -> udpsink_rtp
            udpsrc_rtcp -> rtpbin (recv_rtcp_sink_0)   # TWCC feedback in
            rtpbin (send_rtcp_src_0) -> udpsink_rtcp   # SR out

        How it works:
          1. The TWCC RTP header extension is added to the codec's
             payloader so each outgoing RTP packet carries a
             transport-wide sequence number.
          2. The receiver tags arrival times for those sequence numbers
             and sends them back as TWCC RTCP feedback packets.
          3. rtpbin parses the feedback and routes Twcc structures
             upstream as custom events; rtpgccbwe consumes them, runs the
             delay-gradient + loss algorithm, and updates
             estimated-bitrate.
          4. notify::estimated-bitrate drives the encoder's target
             bitrate via codec.set_encoder_target_bps. The conversion
             from rtpgccbwe's bps output to whatever units the encoder
             takes is the codec's responsibility, not the callback's.
        """
        spec = self.spec
        cc = spec.congestion_control
        codec = spec.encoder.codec
        rtp_port = spec.egress.port
        rtcp_port = spec.egress.rtcp_port or rtp_port + 1
        pay = head_elements[-1]
        encoder = next(e for e in head_elements if e.get_name() == "encoder")

        # Add TWCC extension to the payloader.
        twcc_ext = GstRtp.RTPHeaderExtension.create_from_uri(TWCC_URI)
        if twcc_ext is None:
            raise RuntimeError(
                "TWCC header extension not available — GStreamer too old "
                "or rsrtp plugin not loaded"
            )
        twcc_ext.set_id(TWCC_EXT_ID)
        pay.emit("add-extension", twcc_ext)

        gccbwe = Gst.ElementFactory.make("rtpgccbwe", "gccbwe")
        if gccbwe is None:
            raise RuntimeError("rtpgccbwe missing — gst-plugins-rs/rtp not built or not on GST_PLUGIN_PATH")
        gccbwe.set_property("min-bitrate", cc.min_bitrate_kbps * 1000)
        gccbwe.set_property("max-bitrate", cc.max_bitrate_kbps * 1000)
        gccbwe.set_property("estimated-bitrate", cc.init_bitrate_kbps * 1000)
        if cc.estimator is not None:
            gccbwe.set_property("estimator", cc.estimator)
        gccbwe.connect("notify::estimated-bitrate",
                       _on_gcc_bitrate_notify, encoder, codec, adapter)

        rtpbin = Gst.ElementFactory.make("rtpbin", "rtpbin")
        # AVPF profile (3) enables RTCP feedback packets — required for
        # TWCC feedback flow. Default is plain AVP which doesn't carry
        # feedback messages.
        rtpbin.set_property("rtp-profile", 3)

        udpsink_rtp = Gst.ElementFactory.make("udpsink", "udpsink_rtp")
        udpsink_rtp.set_property("host", spec.egress.host)
        udpsink_rtp.set_property("port", rtp_port)
        udpsink_rtp.set_property("sync", False)

        # Camera's RTCP listener — bind to the camera's own IP so the
        # viewer's RTCP back to us doesn't collide with the viewer's
        # own listener on the same port (matters on loopback configs).
        udpsrc_rtcp = Gst.ElementFactory.make("udpsrc", "udpsrc_rtcp")
        udpsrc_rtcp.set_property("port", rtcp_port)
        if spec.egress.bind_host:
            udpsrc_rtcp.set_property("address", spec.egress.bind_host)

        udpsink_rtcp = Gst.ElementFactory.make("udpsink", "udpsink_rtcp")
        udpsink_rtcp.set_property("host", spec.egress.host)
        udpsink_rtcp.set_property("port", rtcp_port)
        udpsink_rtcp.set_property("sync", False)
        udpsink_rtcp.set_property("async", False)

        rtprtxsend = (_make_rtprtxsend(codec, spec.recovery.rtx_buffer_ms)
                      if spec.recovery.nack else None)
        rtpulpfecenc = (_make_rtpulpfecenc(codec, spec.recovery.fec_percentage)
                        if spec.recovery.fec else None)

        elements = head_elements + [gccbwe, rtpbin, udpsink_rtp,
                                    udpsrc_rtcp, udpsink_rtcp]
        if rtprtxsend is not None:
            elements.append(rtprtxsend)
        if rtpulpfecenc is not None:
            elements.append(rtpulpfecenc)
        for el in elements:
            pipeline.add(el)

        for u, d in zip(head_elements, head_elements[1:]):
            u.link(d)

        # Pipeline order: pay -> rtpulpfecenc? -> gccbwe -> rtprtxsend? ->
        #                 rtpbin -> udpsink. Mirrors the SCReAM branch:
        # FEC before the rate controller (repair packets paced alongside
        # media), RTX after the rate controller (RTX uses a distinct
        # SSRC per RFC 4588 — placing it upstream of a stream-tracking
        # element causes a fatal SSRC mismatch under load).
        pre_cc = [pay]
        if rtpulpfecenc is not None:
            pre_cc.append(rtpulpfecenc)
        pre_cc.append(gccbwe)
        for u, d in zip(pre_cc, pre_cc[1:]):
            u.link(d)
        post_cc = [gccbwe]
        if rtprtxsend is not None:
            post_cc.append(rtprtxsend)
        for u, d in zip(post_cc, post_cc[1:]):
            u.link(d)
        post_cc[-1].link_pads("src", rtpbin, "send_rtp_sink_0")
        rtpbin.link_pads("send_rtp_src_0", udpsink_rtp, "sink")
        udpsrc_rtcp.link_pads("src", rtpbin, "recv_rtcp_sink_0")
        rtpbin.link_pads("send_rtcp_src_0", udpsink_rtcp, "sink")

        return pipeline
