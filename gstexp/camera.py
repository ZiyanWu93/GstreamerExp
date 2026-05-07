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

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstRtp", "1.0")
from gi.repository import Gst, GstRtp

from gstexp.pipeline_config import Camera, Codec, GccCameraConfig, ScreamCameraConfig


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


def _on_bitrate_notify(screamtx, _pspec, encoder, codec):
    # SCReAM reports current-max-bitrate in kbps; convert to bps for the encoder.
    rate_kbps = screamtx.get_property("current-max-bitrate")
    if rate_kbps > 0:
        codec.set_encoder_target_bps(encoder, int(rate_kbps) * 1000)


def _on_gcc_bitrate_notify(gccbwe, _pspec, encoder, codec):
    # rtpgccbwe reports estimated-bitrate in bps directly.
    rate_bps = gccbwe.get_property("estimated-bitrate")
    if rate_bps > 0:
        codec.set_encoder_target_bps(encoder, int(rate_bps))


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

        src_caps = Gst.ElementFactory.make("capsfilter", "src_caps")
        src_caps.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,format=I420,"
            f"width={spec.source.width},height={spec.source.height},"
            f"framerate={spec.source.fps}/1"
        ))

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

        # head chain (raw video processing): src -> caps -> [overlay?] -> enc -> pay
        head = [src, src_caps]
        if overlay is not None:
            head.append(overlay)
        head.extend([enc, pay])

        cc = spec.congestion_control
        if cc is None:
            return self._assemble_bare(pipeline, head)
        if isinstance(cc, ScreamCameraConfig):
            return self._assemble_scream(pipeline, head)
        if isinstance(cc, GccCameraConfig):
            return self._assemble_gcc(pipeline, head)
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

    def _assemble_scream(self, pipeline, head_elements):
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
                         _on_bitrate_notify, encoder, codec)
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

    def _assemble_gcc(self, pipeline, head_elements):
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
                       _on_gcc_bitrate_notify, encoder, codec)

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
