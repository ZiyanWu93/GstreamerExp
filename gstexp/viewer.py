"""ViewerPipeline — assembles a Gst.Pipeline from a Viewer configuration.

Run on the viewer actor (the actor that consumes the video).
Three shapes, dispatched by spec.congestion_control:
  - Bare    (cc is None)        : udpsrc -> caps -> depayloader -> decoder -> ... -> sink
  - SCReAM  (cc.algorithm=scream): udpsrc -> screamrx -> rtpbin -> depay -> ...
                                   plus RTCP return through funnel -> udpsink
  - GCC     (cc.algorithm=gcc)  : udpsrc -> rtpbin -> depay -> ...
                                   rtpbin generates TWCC feedback packets back
                                   to the camera on a fixed interval

The GCC viewer is structurally simpler than the SCReAM one: rtpbin
handles all the TWCC plumbing once the depayloader carries the TWCC
header extension. The bandwidth estimation lives entirely on the camera.
"""

from __future__ import annotations

from typing import Optional

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstRtp", "1.0")
from gi.repository import Gst, GstRtp

from gstexp.pipeline_config import Codec, GccViewerConfig, ScreamViewerConfig, Sink, Viewer


TWCC_URI = "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01"
TWCC_EXT_ID = 1

# Jitter buffer latency must accommodate NACK round-trip when recovery is on.
# 200 ms is RFC 4585's typical recommendation; 10 ms is fine when there's no
# retransmission to wait for.
_JB_LATENCY_NACK_MS = 200
_JB_LATENCY_NORMAL_MS = 10
# rtpstorage retention window: how far back rtpulpfecdec can reach to
# reconstruct a missing media packet from FEC repair packets. 200 ms
# matches the NACK-jitter-buffer figure — FEC recovery happens before
# the jitter buffer, so the storage just needs to outlive typical
# inter-arrival jitter.
_FEC_STORAGE_MS = 200


def _make_rtprtxreceive(codec: Codec):
    """Build an rtprtxreceive element for the codec's RTX→media PT demux."""
    rtx = Gst.ElementFactory.make("rtprtxreceive", "rtxrecv")
    pt_map = Gst.Structure.from_string(
        f"application/x-rtp-pt-map,"
        f"{codec.payload_type}=(uint){codec.rtx_payload_type}")[0]
    rtx.set_property("payload-type-map", pt_map)
    return rtx


def _make_fec_recv(codec: Codec):
    """Build the (rtpstorage, rtpulpfecdec) pair for ULPFEC recovery.

    rtpstorage holds a sliding window of incoming RTP buffers; when
    rtpulpfecdec sees an FEC packet (codec.fec_payload_type), it reaches
    back into storage to reconstruct any missing media packets it can.
    The two elements are linked by handing rtpstorage's `internal-storage`
    GObject to rtpulpfecdec's `storage` property.
    """
    storage = Gst.ElementFactory.make("rtpstorage", "fec_storage")
    storage.set_property("size-time", _FEC_STORAGE_MS * Gst.MSECOND)
    fecdec = Gst.ElementFactory.make("rtpulpfecdec", "fecdec")
    fecdec.set_property("pt", codec.fec_payload_type)
    fecdec.set_property("storage", storage.get_property("internal-storage"))
    return storage, fecdec


def _make_measurement_sink(sink_spec: Sink):
    """Build the measurement sink (`fake` or `file`).

    `autovideo` is not a measurement sink and isn't accepted here — visual
    rendering is added as a separate tee branch when the worker receives
    `--view-display` (see _attach_sink_chain).
    """
    if sink_spec.backend == "file":
        if not sink_spec.path:
            raise ValueError("sink.backend='file' requires sink.path")
        s = Gst.ElementFactory.make("filesink", "sink")
        s.set_property("location", sink_spec.path)
    elif sink_spec.backend == "fake":
        s = Gst.ElementFactory.make("fakesink", "sink")
    else:
        raise ValueError(f"unsupported sink backend: {sink_spec.backend!r}")
    s.set_property("sync", sink_spec.sync)
    return s


def _attach_sink_chain(pipeline, upstream, sink_spec: Sink, view_enabled: bool) -> None:
    """Attach the viewer's terminal chain after `upstream` (videoconvert).

    Without view: a single measurement sink (fake or file).
    With view:    upstream -> tee -> measurement sink                (measurement)
                                  -> queue -> autovideosink          (rendering)

    autovideosink reads DISPLAY (and XAUTHORITY) from the worker process
    environment — set by the runner that spawned the worker, not by us.
    """
    measurement = _make_measurement_sink(sink_spec)
    pipeline.add(measurement)
    if not view_enabled:
        upstream.link(measurement)
        return

    tee = Gst.ElementFactory.make("tee", "sink_tee")
    # The measurement branch is a queue+sink so it has its own thread
    # boundary — without it, tee couples the two branches' progress.
    queue_meas = Gst.ElementFactory.make("queue", "meas_queue")
    queue_meas.set_property("max-size-buffers", 5)
    queue_meas.set_property("max-size-bytes", 0)
    queue_meas.set_property("max-size-time", 0)
    # Leaky queue (downstream) on the view branch: if autovideosink can't
    # keep up — or can't connect to a display at all — drop view frames
    # rather than back-pressure the tee.
    queue_view = Gst.ElementFactory.make("queue", "view_queue")
    queue_view.set_property("max-size-buffers", 5)
    queue_view.set_property("max-size-bytes", 0)
    queue_view.set_property("max-size-time", 0)
    queue_view.set_property("leaky", 2)
    view_sink = Gst.ElementFactory.make("autovideosink", "view_sink")
    view_sink.set_property("sync", False)
    pipeline.add(tee)
    pipeline.add(queue_meas)
    pipeline.add(queue_view)
    pipeline.add(view_sink)
    upstream.link(tee)
    # Explicit request-pad linking — Element.link() doesn't always allocate
    # tee request pads correctly under PyGObject.
    tee_src_meas = tee.get_request_pad("src_%u")
    tee_src_view = tee.get_request_pad("src_%u")
    tee_src_meas.link(queue_meas.get_static_pad("sink"))
    tee_src_view.link(queue_view.get_static_pad("sink"))
    queue_meas.link(measurement)
    queue_view.link(view_sink)


class ViewerPipeline:
    """Builds a Gst.Pipeline graph from a Viewer configuration."""

    name = "viewer"

    def __init__(self, spec: Viewer, view_display: Optional[str] = None):
        self.spec = spec
        # view_display is a runtime overlay set by expo.py via worker's
        # --view-display flag. autovideosink itself reads DISPLAY from
        # the process environment; we just need to know whether to add
        # the tee + autovideosink branch.
        self.view_enabled = view_display is not None

    def build(self) -> Gst.Pipeline:
        cc = self.spec.congestion_control
        if cc is None:
            return self._build_bare()
        if isinstance(cc, ScreamViewerConfig):
            return self._build_scream()
        if isinstance(cc, GccViewerConfig):
            return self._build_gcc()
        raise TypeError(f"unsupported congestion-control type: {type(cc).__name__}")

    def _build_bare(self) -> Gst.Pipeline:
        spec = self.spec
        codec = spec.decoder.codec
        pipeline = Gst.Pipeline.new(self.name)

        udpsrc = Gst.ElementFactory.make("udpsrc", "udpsrc")
        udpsrc.set_property("port", spec.ingress.port)
        udpsrc.set_property("address", spec.ingress.host)

        rtp_caps = Gst.ElementFactory.make("capsfilter", "rtp_caps")
        rtp_caps.set_property("caps", Gst.Caps.from_string(codec.rtp_caps()))
        depay = Gst.ElementFactory.make(codec.depayloader_factory, "depay")
        dec = Gst.ElementFactory.make(codec.decoder_factory, "decoder")
        convert = Gst.ElementFactory.make("videoconvert", "convert")

        elements = [udpsrc, rtp_caps, depay, dec, convert]
        for el in elements:
            pipeline.add(el)
        for u, d in zip(elements, elements[1:]):
            u.link(d)
        _attach_sink_chain(pipeline, convert, spec.sink, self.view_enabled)

        return pipeline

    def _build_scream(self) -> Gst.Pipeline:
        spec = self.spec
        codec = spec.decoder.codec
        rtp_port = spec.ingress.port
        rtcp_port = spec.ingress.rtcp_port or rtp_port + 1
        nack = spec.recovery.nack
        pli = spec.recovery.pli
        fec = spec.recovery.fec

        pipeline = Gst.Pipeline.new(self.name)

        rtpbin = Gst.ElementFactory.make("rtpbin", "rtpbin")
        rtpbin.set_property("latency",
                            _JB_LATENCY_NACK_MS if nack else _JB_LATENCY_NORMAL_MS)
        if nack or pli:
            rtpbin.set_property("rtp-profile", 3)
        if nack:
            rtpbin.set_property("do-retransmission", True)
        if pli:
            # do-lost makes rtpjitterbuffer emit a force-key-unit upstream
            # event when packets are determined lost. rtpsession turns that
            # event into an outbound PLI RTCP feedback packet.
            rtpbin.set_property("do-lost", True)

        udpsrc_rtp = Gst.ElementFactory.make("udpsrc", "udpsrc_rtp")
        udpsrc_rtp.set_property("port", rtp_port)
        udpsrc_rtp.set_property("address", spec.ingress.host)

        screamrx = Gst.ElementFactory.make("screamrx", "screamrx")

        # When nack is on, rtprtxreceive is inserted between screamrx and
        # the capsfilter. It demuxes RTX packets (codec.rtx_payload_type)
        # back into the original media stream (codec.payload_type) on its
        # payload-type-map, so the capsfilter downstream only sees the
        # post-demux media. When fec is on, rtpstorage + rtpulpfecdec sit
        # just before the capsfilter and consume FEC packets
        # (codec.fec_payload_type) to recover missing media packets.
        rtp_caps = Gst.ElementFactory.make("capsfilter", "rtp_caps")
        rtp_caps.set_property("caps", Gst.Caps.from_string(codec.rtp_caps()))
        rtprtxreceive = _make_rtprtxreceive(codec) if nack else None
        fec_storage, fecdec = (_make_fec_recv(codec) if fec else (None, None))

        depay = Gst.ElementFactory.make(codec.depayloader_factory, "depay")
        dec = Gst.ElementFactory.make(codec.decoder_factory, "decoder")
        convert = Gst.ElementFactory.make("videoconvert", "convert")

        queue = Gst.ElementFactory.make("queue", "queue")
        queue.set_property("max-size-buffers", 2)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-time", 0)

        funnel = Gst.ElementFactory.make("funnel", "funnel")
        queue_rtcp = Gst.ElementFactory.make("queue", "queue_rtcp")

        # RTCP back to camera: peer_host is the camera's IP.
        udpsink_rtcp = Gst.ElementFactory.make("udpsink", "udpsink_rtcp")
        udpsink_rtcp.set_property(
            "host", spec.ingress.peer_host or spec.ingress.host
        )
        udpsink_rtcp.set_property("port", rtcp_port)
        udpsink_rtcp.set_property("sync", False)
        udpsink_rtcp.set_property("async", False)

        # RTCP from camera — bind to viewer's address to avoid colliding with
        # the camera's own RTCP listener on the same port.
        udpsrc_rtcp = Gst.ElementFactory.make("udpsrc", "udpsrc_rtcp")
        udpsrc_rtcp.set_property("port", rtcp_port)
        udpsrc_rtcp.set_property("address", spec.ingress.host)

        elements = [rtpbin, udpsrc_rtp, screamrx, rtp_caps, depay, dec,
                    convert, queue, funnel, queue_rtcp,
                    udpsink_rtcp, udpsrc_rtcp]
        if rtprtxreceive is not None:
            elements.append(rtprtxreceive)
        if fec_storage is not None:
            elements.extend([fec_storage, fecdec])
        for el in elements:
            pipeline.add(el)

        # RTP path: udpsrc -> screamrx -> rtprtxreceive? -> rtpstorage? ->
        #           rtpulpfecdec? -> capsfilter (codec media PT) -> rtpbin.
        # Each recovery element is optional; the chain skips over it
        # when its flag is off. The screamrx hop uses link_pads explicitly
        # because screamrx exposes both `src` (RTP) and `rtcp_src` (RTCP),
        # and a bare link() would resolve ambiguously.
        udpsrc_rtp.link(screamrx)
        rtp_chain = [el for el in (rtprtxreceive, fec_storage, fecdec, rtp_caps)
                     if el is not None]
        screamrx.link_pads("src", rtp_chain[0], None)
        for u, d in zip(rtp_chain, rtp_chain[1:]):
            u.link(d)
        rtp_caps.link_pads("src", rtpbin, "recv_rtp_sink_0")

        def on_pad_added(_element, pad, target_depay):
            if pad.get_name().startswith("recv_rtp_src_"):
                pad.link(target_depay.get_static_pad("sink"))

        rtpbin.connect("pad-added", on_pad_added, depay)

        depay.link(dec)
        dec.link(queue)
        queue.link(convert)
        _attach_sink_chain(pipeline, convert, spec.sink, self.view_enabled)

        # RTCP path: rtpbin RTCP + screamrx RTCP -> funnel -> udpsink
        rtpbin.link_pads("send_rtcp_src_0", funnel, "sink_0")
        screamrx.link_pads("rtcp_src", funnel, "sink_1")
        funnel.link(queue_rtcp)
        queue_rtcp.link(udpsink_rtcp)

        # RTCP from camera into rtpbin
        udpsrc_rtcp.link_pads("src", rtpbin, "recv_rtcp_sink_0")

        return pipeline

    def _build_gcc(self) -> Gst.Pipeline:
        """GCC viewer: rtpbin generates TWCC feedback back to the camera.

        Pipeline topology:
            udpsrc_rtp -> rtpbin (recv_rtp_sink_0)
            rtpbin pad-added -> depayloader -> decoder -> videoconvert -> sink
            rtpbin (send_rtcp_src_0) -> udpsink_rtcp   # TWCC + RR back to camera
            udpsrc_rtcp -> rtpbin (recv_rtcp_sink_0)   # SR from camera

        The TWCC header extension is registered on the depayloader so rtpbin
        knows which packets to track for arrival-time reporting. Setting
        rtpbin.twcc-feedback-interval makes it emit TWCC RTCP packets at
        the requested cadence. The camera's rtpbin parses those and feeds
        them to rtpgccbwe.
        """
        spec = self.spec
        codec = spec.decoder.codec
        rtp_port = spec.ingress.port
        rtcp_port = spec.ingress.rtcp_port or rtp_port + 1
        nack = spec.recovery.nack
        pli = spec.recovery.pli
        fec = spec.recovery.fec

        pipeline = Gst.Pipeline.new(self.name)

        rtpbin = Gst.ElementFactory.make("rtpbin", "rtpbin")
        rtpbin.set_property("latency",
                            _JB_LATENCY_NACK_MS if nack else _JB_LATENCY_NORMAL_MS)
        # AVPF profile (3) — required for TWCC, NACK, and PLI alike.
        rtpbin.set_property("rtp-profile", 3)
        if nack:
            rtpbin.set_property("do-retransmission", True)
        if pli:
            rtpbin.set_property("do-lost", True)
        # Optional: how often to emit TWCC RTCP. Default 100ms; the
        # rtpbin property takes nanoseconds.
        cc = spec.congestion_control
        if cc.twcc_feedback_interval_ms is not None:
            rtpbin.set_property("twcc-feedback-interval",
                                cc.twcc_feedback_interval_ms * Gst.MSECOND)

        udpsrc_rtp = Gst.ElementFactory.make("udpsrc", "udpsrc_rtp")
        udpsrc_rtp.set_property("port", rtp_port)
        udpsrc_rtp.set_property("address", spec.ingress.host)
        # extmap-<id>=<URI> in the caps is how rtpsession learns which
        # RTP header extensions are present on incoming packets. Without
        # it, rtpsession sees TWCC sequence numbers but logs
        # "no extension registered; ignoring" and never tracks arrival
        # times for TWCC feedback. When fec is on, drop the media-PT
        # constraint so FEC packets (codec.fec_payload_type) flow through
        # to rtpulpfecdec instead of being filtered out at the udpsrc
        # caps; downstream rtpulpfecdec consumes them and rtpbin sees
        # only the media PT.
        extmap_extras = f"extmap-{TWCC_EXT_ID}={TWCC_URI}"
        if fec:
            # Build PT-less caps (just media+codec+clock-rate+extmap) so
            # FEC packets are not filtered before rtpulpfecdec.
            caps_str = (f"application/x-rtp,media=video,"
                        f"encoding-name={codec.encoding_name},"
                        f"clock-rate={codec.clock_rate},{extmap_extras}")
        else:
            caps_str = codec.rtp_caps(extras=extmap_extras)
        udpsrc_rtp.set_property("caps", Gst.Caps.from_string(caps_str))
        # When nack is on, rtprtxreceive sits between udpsrc and rtpbin
        # and demuxes RTX packets back into the media stream via its
        # payload-type-map (codec.rtx_payload_type → codec.payload_type).
        rtprtxreceive = _make_rtprtxreceive(codec) if nack else None
        fec_storage, fecdec = (_make_fec_recv(codec) if fec else (None, None))

        depay = Gst.ElementFactory.make(codec.depayloader_factory, "depay")
        twcc_ext = GstRtp.RTPHeaderExtension.create_from_uri(TWCC_URI)
        if twcc_ext is None:
            raise RuntimeError("TWCC header extension not available")
        twcc_ext.set_id(TWCC_EXT_ID)
        depay.emit("add-extension", twcc_ext)

        dec = Gst.ElementFactory.make(codec.decoder_factory, "decoder")
        convert = Gst.ElementFactory.make("videoconvert", "convert")

        queue = Gst.ElementFactory.make("queue", "queue")
        queue.set_property("max-size-buffers", 2)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-time", 0)

        # RTCP back to camera carries TWCC feedback packets.
        udpsink_rtcp = Gst.ElementFactory.make("udpsink", "udpsink_rtcp")
        udpsink_rtcp.set_property(
            "host", spec.ingress.peer_host or spec.ingress.host
        )
        udpsink_rtcp.set_property("port", rtcp_port)
        udpsink_rtcp.set_property("sync", False)
        udpsink_rtcp.set_property("async", False)

        udpsrc_rtcp = Gst.ElementFactory.make("udpsrc", "udpsrc_rtcp")
        udpsrc_rtcp.set_property("port", rtcp_port)
        udpsrc_rtcp.set_property("address", spec.ingress.host)

        elements = [rtpbin, udpsrc_rtp, depay, dec, queue, convert,
                    udpsink_rtcp, udpsrc_rtcp]
        if rtprtxreceive is not None:
            elements.append(rtprtxreceive)
        if fec_storage is not None:
            elements.extend([fec_storage, fecdec])
        for el in elements:
            pipeline.add(el)

        # RTP path: udpsrc -> rtprtxreceive? -> rtpstorage? ->
        #           rtpulpfecdec? -> rtpbin (recv_rtp_sink_0).
        rtp_chain = [el for el in (rtprtxreceive, fec_storage, fecdec) if el is not None]
        prev = udpsrc_rtp
        for el in rtp_chain:
            prev.link(el)
            prev = el
        prev.link_pads("src", rtpbin, "recv_rtp_sink_0")

        def on_pad_added(_element, pad, target_depay):
            if pad.get_name().startswith("recv_rtp_src_"):
                pad.link(target_depay.get_static_pad("sink"))
        rtpbin.connect("pad-added", on_pad_added, depay)

        depay.link(dec)
        dec.link(queue)
        queue.link(convert)
        _attach_sink_chain(pipeline, convert, spec.sink, self.view_enabled)

        rtpbin.link_pads("send_rtcp_src_0", udpsink_rtcp, "sink")
        udpsrc_rtcp.link_pads("src", rtpbin, "recv_rtcp_sink_0")

        return pipeline
