"""Pipeline configuration — declarative dataclasses for Camera and Viewer.

A `Camera` describes the video-source pipeline (Source -> Encoder ->
Packetizer -> Egress) run on the actor that holds the camera. A `Viewer`
describes the matching destination pipeline (Ingress -> Depacketizer ->
Decoder -> Sink) run on the actor that consumes the video. The worker
loads the spec into these dataclasses and the GStreamer assembly in
camera.py / viewer.py reads from them.

Every field is required — no dataclass-level defaults. The values
come from the user-authored configuration (validated and projected by
validation.py); the only injection step is cli.main() filling host
fields from the scenario block.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst


# --- codecs ----------------------------------------------------------------

# A Codec class owns every codec-specific detail — element factory names
# (encoder/decoder/payloader/depayloader), the payload-type triple
# (media/RTX/FEC), clock rate, encoding-name string for RTP caps, and
# the per-codec property mapping (target-bitrate setter/getter, encoder
# tunings). camera.py and viewer.py reach for the codec object instead
# of typing literal element names; recovery helpers (_make_rtprtxsend,
# _make_rtpulpfecenc, _make_rtprtxreceive, _make_fec_recv) take a codec
# argument. Adding a second codec is one new class — no edits below.
#
# Discriminator: today only "vp8" is implemented. The validator and the
# worker loader dispatch on the algorithm string, mirroring the
# congestion_control / loss / recovery patterns elsewhere in this project.

@dataclass
class Vp8Codec:
    """VP8 metadata + property mapping.

    Today no user-facing knobs (vp8enc realtime tunings cpu-used=8,
    deadline=1, end-usage=CBR are hardcoded in configure_encoder).
    Adding tuning would extend this dataclass with optional fields.
    """

    # Element factories — what `Gst.ElementFactory.make(...)` consumes.
    encoder_factory:    str = "vp8enc"
    payloader_factory:  str = "rtpvp8pay"
    decoder_factory:    str = "vp8dec"
    depayloader_factory: str = "rtpvp8depay"

    # Payload types. Pipeline-internal convention; both ends agree.
    payload_type:     int = 96            # VP8 RTP media
    rtx_payload_type: int = 97            # NACK + RTX retransmissions (RFC 4588)
    fec_payload_type: int = 100           # ULPFEC repair packets (RFC 5109)

    # RTP transport metadata.
    clock_rate:    int = 90000            # standard for video codecs
    encoding_name: str = "VP8"            # used in `application/x-rtp,encoding-name=...`

    def configure_encoder(self, enc, bitrate_kbps: int,
                          keyframe_interval_frames: int) -> None:
        """Apply CBR realtime settings to a vp8enc element."""
        enc.set_property("target-bitrate", bitrate_kbps * 1000)
        enc.set_property("end-usage", 1)                                      # CBR
        enc.set_property("keyframe-max-dist", keyframe_interval_frames)
        enc.set_property("cpu-used", 8)
        enc.set_property("deadline", 1)

    def get_encoder_target_bps(self, enc) -> int:
        """Read the encoder's currently-configured target bitrate in bps."""
        return int(enc.get_property("target-bitrate"))

    def set_encoder_target_bps(self, enc, bps: int) -> None:
        """Write the encoder's target bitrate in bps. SCReAM and GCC
        bitrate-notify callbacks call this when the controller updates."""
        enc.set_property("target-bitrate", int(bps))

    def rtp_caps(self, *, payload: int | None = None,
                 extras: str = "") -> str:
        """Build an `application/x-rtp,...` caps string for udpsrc / capsfilter.

        `payload` defaults to the codec's media PT but can be overridden when
        a caller deliberately wants to drop the PT constraint (e.g. when
        FEC packets need to flow through the same udpsrc).
        """
        pt = payload if payload is not None else self.payload_type
        clauses = [
            "application/x-rtp",
            "media=video",
            f"encoding-name={self.encoding_name}",
            f"clock-rate={self.clock_rate}",
            f"payload={pt}",
        ]
        caps = ",".join(clauses)
        return caps + ("," + extras if extras else "")


# Codec union: extend when more codecs land. The discriminator is the
# `algorithm` string in the YAML (e.g. `codec: vp8`); validation.py
# rejects unknown values, worker.py dispatches the string to the matching
# Codec class, and the rest of the pipeline holds an instance.
Codec = Vp8Codec   # widen to Union[Vp8Codec, H264Codec, ...] when extended


# --- source backends -------------------------------------------------------

# A Source backend class owns its element-construction logic and any
# backend-specific knobs. The build(pipeline, num_frames) method creates
# the element subgraph (single element for synthetic / camera; a small
# graph for file), adds it to the pipeline, and returns the tail element
# the caller links downstream from. Adding a new backend is one new
# dataclass — same pattern as the Codec abstraction.
#
# Discriminator: today only "synthetic" is implemented. The validator
# and worker loader dispatch on the backend string, mirroring the
# congestion_control / loss / recovery / codec patterns.

@dataclass
class SyntheticSource:
    """videotestsrc backend — synthetic patterns at the requested
    dimensions and rate. Single element; build() creates it, sets
    is-live / pattern / num-buffers, and returns the tail element. The
    caller adds the tail to the pipeline as the head of its element
    chain.

    Multi-element backends (e.g. FileSource: filesrc → decodebin →
    videoconvert → videoscale → videorate) use the `pipeline` argument
    to add their UPSTREAM elements and return the tail. The caller's
    pattern is the same: take the returned element as the head of its
    post-source chain."""

    pattern: str                           # videotestsrc pattern name (ball, smpte, snow, ...)

    def build(self, pipeline, num_frames: int):
        src = Gst.ElementFactory.make("videotestsrc", "src")
        # is-live=True so the source honors frame-rate timestamps —
        # downstream behaves like real-time video, not as-fast-as-possible.
        src.set_property("is-live", True)
        src.set_property("pattern", self.pattern)
        if num_frames > 0:
            src.set_property("num-buffers", num_frames)
        return src


@dataclass
class FileSource:
    """File-backed source. Pipeline added internally:

        filesrc → decodebin → videoconvert → videoscale → videorate
                                                              ↓
                                                          [identity (eos-after=N)]?
                                                              ↓
                                                            tail

    decodebin's video src pad is created dynamically; we wire pad-added
    to link it into convert. Non-video pads (audio, subtitles) are left
    unlinked, which decodebin tolerates as long as the video pad arrives.

    `loop: True` means the worker should seek-on-EOS rather than
    terminate (see worker.py's EOS handler + Source.loops). The seek
    happens at the bus level — not inside this builder — because the
    pipeline reference at that moment must be the one running.

    `num_frames` is NOT capped inside the source (an `identity
    eos-after=N` here would post EOS that conflicts with seek-on-loop:
    the worker can't tell file-EOS from cap-EOS). Instead, worker.py
    adds an application-level frame counter on the encoder's src pad
    that terminates the run when num_frames is reached, regardless of
    how many file loops it took to get there.
    """

    path: str                              # absolute path on the camera host
    loop: bool                             # restart from t=0 when EOS reached

    def build(self, pipeline, num_frames: int):
        # num_frames is ignored here; capping is application-level (worker.py).
        # clocksync at the tail paces buffer emission to wall-clock — without
        # it, decodebin pumps frames as fast as it can decode, the encoder
        # drowns, and a 20-second run completes in ~3 seconds (network
        # impairments don't get to apply naturally because the rate
        # controller doesn't see live timing). videotestsrc's is-live=true
        # covers this for SyntheticSource; FileSource needs an explicit
        # clocksync because filesrc isn't live.
        filesrc = Gst.ElementFactory.make("filesrc", "src_filesrc")
        filesrc.set_property("location", self.path)
        decode  = Gst.ElementFactory.make("decodebin", "src_decode")
        convert = Gst.ElementFactory.make("videoconvert", "src_convert")
        scale   = Gst.ElementFactory.make("videoscale", "src_scale")
        rate    = Gst.ElementFactory.make("videorate", "src_rate")
        sync    = Gst.ElementFactory.make("clocksync", "src_clocksync")
        for el in (filesrc, decode, convert, scale, rate, sync):
            pipeline.add(el)
        filesrc.link(decode)
        convert.link(scale)
        scale.link(rate)
        rate.link(sync)

        def _on_pad_added(_decode, pad):
            caps = pad.get_current_caps()
            if caps is None:
                caps = pad.query_caps(None)
            structure = caps.get_structure(0)
            if structure.get_name().startswith("video/"):
                pad.link(convert.get_static_pad("sink"))
        decode.connect("pad-added", _on_pad_added)

        return sync


# Source backend union — extend as more land.
SourceBackend = Union[SyntheticSource, FileSource]


# --- camera stages ---------------------------------------------------------

@dataclass
class Source:
    """Where raw video frames come from. Dimensions (width/height/fps)
    are shared across every backend; the backend object owns the element
    factory and any backend-specific knobs (e.g. videotestsrc.pattern,
    filesrc.path)."""

    backend: SourceBackend
    width: int
    height: int
    fps: int
    num_frames: int                        # 0 = unbounded
    clock_overlay: bool                    # overlay HH:MM:SS wall-clock on each frame

    @property
    def loops(self) -> bool:
        """True when the backend wants seek-on-EOS rather than termination.
        worker.py's EOS handler reads this to decide between seek_simple()
        and loop.quit(). Synthetic sources never loop (no `loop` attr →
        getattr returns False); FileSource opts in via its `loop` field."""
        return getattr(self.backend, "loop", False)


@dataclass
class Encoder:
    """How raw frames become a compressed bitstream.

    CBR-only with realtime tunings hardcoded inside the codec object's
    configure_encoder method — adding VBR or non-realtime modes would
    extend the codec class with the matching plumbing.
    """

    codec: Codec                           # Vp8Codec today; widen Union when more land
    bitrate_kbps: int
    keyframe_interval_frames: int


@dataclass
class Packetizer:
    """How an encoded bitstream is wrapped for transport."""

    protocol: str                          # always "rtp" today; injected by validation.project_to_roles


@dataclass
class Egress:
    """Where packets leave the process."""

    transport: str                         # always "udp" today; injected
    port: int                              # RTP port
    rtcp_port: int                         # always port+1 today; injected
    host: str                              # destination (peer's IP); injected from scenario.actors.viewer.host
    bind_host: str                         # local bind for RTCP listener; injected from scenario.actors.camera.host
    stream_id: int                         # this stream's index in streams[]; injected by project_to_roles


# --- congestion control ----------------------------------------------------

# Each algorithm has its own configuration space, exposed in full so the
# testbed can study performance implications across every meaningful knob.
# Bitrate bounds (init/min/max) are required because we always intend to
# pick them. Algorithm-specific knobs are Optional; absence means
# "no override — let the algorithm use its documented default." This is
# the only place in the project where defaults live, and they live with
# the algorithms (SCReAM C++ defaults; rtpgccbwe property defaults), not
# in our code.

@dataclass
class ScreamCameraConfig:
    """SCReAM sender-side knobs — anything that goes into screamtx.params.
    Lives on the camera actor (which runs the screamtx element).

    The flag names in scream_sender.cpp are mapped to readable Python
    field names; the Python→flag mapping lives in camera.py
    (_build_scream_params). Defaults shown are SCReAM's, applied when
    the field is None.
    """

    init_bitrate_kbps: int                                   # -initrate
    min_bitrate_kbps:  int                                   # -minrate
    max_bitrate_kbps:  int                                   # -maxrate
    delay_target_seconds:     Optional[float] = None         # -delaytarget        (default 0.06s)
    ect:                      Optional[int]   = None         # -ect                (default 0; valid {-1,0,1,3})
    pacing_headroom:          Optional[float] = None         # -paceheadroom       (default 1.5)
    adaptive_pacing_headroom: Optional[float] = None         # -adaptivepaceheadroom (default 1.5)
    inflight_headroom:        Optional[float] = None         # -inflightheadroom   (default 2.0)
    max_window_headroom:      Optional[float] = None         # -maxwindowheadroom  (default 5.0)
    min_packets_in_flight:    Optional[int]   = None         # -minpktsinflight    (default 0)
    microburst_interval_ms:   Optional[float] = None         # -microburstinterval (default 0.5; range 0.2..20)
    reorder_time_seconds:     Optional[float] = None         # -reordertime        (default 0.03)
    mul_increase:             Optional[float] = None         # -mulincrease        (default 0.05)
    relaxed_pacing:           Optional[bool]  = None         # -relaxedpacing      (flag)
    no_pacing:                Optional[bool]  = None         # -nopace             (flag)


@dataclass
class GccCameraConfig:
    """rtpgccbwe knobs (lives on the camera actor)."""

    init_bitrate_kbps: int                                   # estimated-bitrate (initial)
    min_bitrate_kbps:  int                                   # min-bitrate
    max_bitrate_kbps:  int                                   # max-bitrate
    estimator: Optional[str] = None                          # "kalman" (default) | "linear-regression"


@dataclass
class ScreamViewerConfig:
    """SCReAM viewer-side knobs (lives on the viewer actor's screamrx).

    Empty by construction: the current `gstscream` plugin's `screamrx`
    element exposes no GObject properties — `ackDiff` and
    `nReportedRtpPackets` are baked into the C++ constructor and never
    surfaced. Extending the plugin would unlock these knobs.
    """


@dataclass
class GccViewerConfig:
    """GCC viewer-side knobs (set on the viewer actor's rtpbin)."""

    twcc_feedback_interval_ms: Optional[int] = None          # rtpbin.twcc-feedback-interval (default 100ms)


# --- packet recovery (NACK + RTX) -----------------------------------------

# Recovery is orthogonal to congestion control: SCReAM and GCC adapt the
# rate, but neither replaces lost packets. NACK + RTX (RFC 4585 / 4588)
# adds receiver-driven retransmission: receiver sends RTCP NACK on
# sequence-number gaps; sender reinjects the missing packet from a small
# ring buffer. Per-role split mirrors the CC dataclasses — different
# fields apply on each side.

@dataclass
class CameraRecovery:
    """Camera-side recovery — parameterizes rtprtxsend and rtpulpfecenc.

    pli is independent of nack: when on, the camera's rtpbin parses
    incoming PLI RTCP and emits a force-key-unit upstream event that
    propagates to vp8enc. AVPF profile is required whenever nack or pli
    is on (FEC works in plain AVP — no RTCP feedback path).

    fec adds proactive redundancy on the wire via ULPFEC (RFC 5109).
    fec_percentage is the overhead percentage; required when fec=True.
    """

    nack: bool                              # enables rtprtxsend
    pli: bool                               # enables AVPF profile for inbound PLI handling
    fec: bool                               # enables rtpulpfecenc
    rtx_buffer_ms: int = 0                  # rtprtxsend ring depth; required when nack=True
    fec_percentage: int = 0                 # rtpulpfecenc overhead 1-100; required when fec=True


@dataclass
class ViewerRecovery:
    """Viewer-side recovery — enables rtprtxreceive + rtpbin
    do-retransmission (when nack), rtpbin do-lost (when pli) which
    causes rtpjitterbuffer to emit keyframe-request events that
    rtpsession turns into outbound PLI RTCP, and the rtpstorage +
    rtpulpfecdec pair (when fec) which recovers lost media packets
    from FEC repair packets without any feedback round-trip.
    """

    nack: bool
    pli: bool
    fec: bool


# --- viewer stages ---------------------------------------------------------

@dataclass
class Ingress:
    """Where packets enter the process."""

    transport: str                         # always "udp" today; injected
    port: int
    rtcp_port: int                         # always port+1 today; injected
    host: str                              # local bind; injected from scenario.actors.viewer.host
    peer_host: str                         # where to send RTCP back; injected from scenario.actors.camera.host
    stream_id: int                         # this stream's index in streams[]; injected by project_to_roles


@dataclass
class Depacketizer:
    """How transport packets are unwrapped into a codec bitstream."""

    protocol: str                          # always "rtp" today; injected


@dataclass
class Decoder:
    """How the compressed bitstream becomes raw frames."""

    codec: Codec                           # mirrors Encoder.codec; both sides agree


@dataclass
class Sink:
    """Where decoded frames go."""

    backend: str                           # "fake" | "file"
    sync: bool                             # render-by-clock vs. as-fast-as-possible
    path: str = ""                         # required iff backend=="file"; validator enforces


# --- top-level pipelines ---------------------------------------------------

@dataclass
class Camera:
    """A complete video-source pipeline configuration (run by the
    camera actor — the one that holds the original video)."""

    source:             Source
    encoder:            Encoder
    packetizer:         Packetizer
    egress:             Egress
    # Optional: when present, screamtx (or rtpgccbwe) is inserted
    # between the packetizer and the egress with an RTCP feedback loop.
    # The concrete type discriminates the algorithm.
    congestion_control: Optional[Union[ScreamCameraConfig, GccCameraConfig]] = None
    # Always present after validation. recovery.nack=True wires NACK+RTX
    # into the pipeline.
    recovery:           Optional[CameraRecovery] = None


@dataclass
class Viewer:
    """A complete video-destination pipeline configuration (run by the
    viewer actor — the one that consumes the video)."""

    ingress:            Ingress
    depacketizer:       Depacketizer
    decoder:            Decoder
    sink:               Sink
    # Optional: when present, screamrx (or the GCC TWCC return path) is
    # inserted after the ingress.
    congestion_control: Optional[Union[ScreamViewerConfig, GccViewerConfig]] = None
    recovery:           Optional[ViewerRecovery] = None
    # Optional ground-truth source for the decoded_psnr metric. When the
    # configuration enables that metric, validation.project_to_roles
    # copies the camera's source here so the viewer-side metric can
    # reproduce frame N as raw bytes for per-frame PSNR comparison.
    # Holds the same Source dataclass shape as Camera.source — but only
    # the dimensions and backend are used; num_frames/clock_overlay are
    # not relevant to the ground-truth pipeline.
    ground_truth:       Optional[Source] = None
    # Operational frame-age cap enforced at convert.src by the
    # late_drops metric. 0 means no enforcement; any positive int is a
    # hard cap in milliseconds — a frame older than this on arrival at
    # the sink is dropped rather than presented late. Required at
    # config-load time (validator) but defaults here to 0 because
    # worker.py loads viewer dicts that may predate the field.
    latency_budget_ms:  int = 0
