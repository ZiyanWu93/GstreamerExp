# GstreamerExp vs UMN Teleop-Gopher-streamer

Two parts. First, a **feature matrix** — a direct, feature-by-feature
table of what each implementation has and where in its source it lives.
Then a **gap-driven comparison**: for each open item in
[`docs/TODO.md`](../docs/TODO.md), how the UMN GopherNetLab
teleoperation streamer addresses it and whether the approach is portable
to our GStreamer pipeline.

UMN repo: <https://github.com/GopherNetLab/Teleop-Gopher-streamer>.
File links below are pinned to commit `7c585c2` ("Save teleop
integration baseline"). The repo is vendored locally at
`reference/external/teleop-gopher-streamer/` (gitignored).

## Feature matrix

Direct feature-by-feature comparison. Legend: ✓ present, ◐ partial,
✗ absent. "Our source" paths are relative to `projects/GstreamerExp/`;
"UMN source" paths are relative to the UMN repo root at commit
`7c585c2`.

### Media pipeline

| Feature | Ours | Our source | UMN | UMN source |
|---|---|---|---|---|
| Media framework | ✓ GStreamer 1.24 | `gstexp/camera.py`, `gstexp/viewer.py` | ✓ PyAV / libav | `src/gopher_streamer/transmitter/encoder.py` |
| VP8 codec | ✓ | `gstexp/camera.py` | ✓ | `transmitter/encoder.py` |
| VP9 / H.264 / HEVC / AV1 | ✗ | — | ✓ | `transmitter/encoder.py` |
| Hardware encode (NVENC / QSV / VAAPI) | ✗ | — | ✓ | `transmitter/encoder.py` |
| Adaptive resolution | ✗ | — | ◐ policy-driven, not CC-driven | `transmitter/encoder.py` |

### Transport

| Feature | Ours | Our source | UMN | UMN source |
|---|---|---|---|---|
| Standard RTP / RTCP | ✓ | `gstexp/camera.py`, `gstexp/viewer.py` | ✗ | — |
| Custom UDP framing | ✗ | — | ✓ | `src/gopher_streamer/shared/protocol.py` |
| SRTP / DTLS encryption | ✗ | — | ✗ | — |
| UDP socket buffer tuning | ✓ | `gstexp/camera.py` | ✓ | `src/gopher_streamer/shared/udp_buffers.py` |

### Congestion control

This is the one table where UMN's `scream-integration` branch diverges
from `main`, so it carries an extra column. On **`main`** (`7c585c2`),
UMN has no transport congestion control: `adaptive_bitrate` is `false`
in 84 of 100 configs, so the default pipeline is open-loop — fixed-CBR
encode, the sender blasts packets, and congestion surfaces only as UDP
socket-buffer overflow causing packet drops; the opt-in
`BandwidthAllocator` is a coarse 1 Hz local-qdisc bitrate degrader, not
a controller. The **`scream-integration`** branch (`502533c0`) adds
hand-written, *SCReAM-inspired* and *GCC-style* controllers plus a
receiver→sender feedback channel — UMN's own reimplementations, not the
reference algorithms our project wires in.

| Feature | Ours | Our source | UMN `main` | UMN `scream-integration` | UMN source |
|---|---|---|---|---|---|
| Transport congestion controller (SCReAM/GCC-class) | ✓ | `gstexp/camera.py` | ✗ | ◐ own reimplementation | `transmitter/scream_controller.py` |
| SCReAM | ✓ real (Ericsson, via `gstscream`) | `gstexp/camera.py` + `scream/` | ✗ | ◐ "SCReAM-inspired" Python AIMD loop, 100 ms | `transmitter/scream_controller.py` |
| Google CC | ✓ real (`rtpgccbwe`) | `gstexp/camera.py` | ✗ | ◐ "GCC-style" Python reimplementation | `transmitter/google_congestion_controller.py` |
| Path-feedback congestion signal (loss + one-way delay) | ✓ RTCP | `gstexp/camera.py` | ✗ | ✓ custom `RFBK` receiver feedback | `transmitter/receiver_feedback.py`, `shared/protocol.py` |
| Packet pacing | ✓ | `gstexp/camera.py` (SCReAM / `rtpbin`) | ✗ blast; drop on socket overflow | ✗ still blast | `transmitter/sender.py` |
| Application-level adaptive bitrate (allocator) | ✗ | — | ◐ opt-in (`adaptive_bitrate`, 16/100 configs) | ◐ opt-in, now one of `allocator` / `scream` / `gcc` / `fixed` | `transmitter/bandwidth_allocator.py` |
| qdisc-backlog capacity estimate | ✗ | — | ◐ only when adaptive_bitrate on | ◐ a SCReAM-controller input signal | `transmitter/throughput.py` |
| Multi-stream priority degradation ladder | ✗ | — | ◐ only when adaptive_bitrate on | ◐ only when adaptive_bitrate on | `transmitter/bandwidth_allocator.py` |

`scream-integration` UMN source paths are at branch head `502533c0`;
all other UMN paths in this document are at `main` / `7c585c2`.

### Loss recovery

| Feature | Ours | Our source | UMN | UMN source |
|---|---|---|---|---|
| NACK + RTX | ✓ | `gstexp/camera.py` (`_make_rtprtxsend`), `gstexp/viewer.py` | ✗ | — |
| PLI / keyframe request | ✓ | `gstexp/viewer.py` | ◐ KFRQ messages | `src/gopher_streamer/receiver/reassemble.py` |
| ULPFEC | ✓ | `gstexp/camera.py` (`_make_rtpulpfecenc`) | ✗ | — |
| Jitter buffer | ✓ | `gstexp/viewer.py` (`rtpjitterbuffer`) | ✓ | `receiver/reassemble.py` |
| Partial-frame decode | ✗ | — | ✓ | `receiver/reassemble.py` |

### Capture

| Feature | Ours | Our source | UMN | UMN source |
|---|---|---|---|---|
| Synthetic source (`videotestsrc`) | ✓ | `gstexp/camera.py` | ✗ | — |
| File-backed source | ✓ | `gstexp/camera.py` | ✓ | `src/gopher_streamer/shared/config.py` (`source_path`) |
| Real camera | ✗ | — | ✓ | `src/gopher_streamer/cameras/spinnaker_capture.py` |
| Multi-camera | ✗ | — | ✓ | `transmitter/main.py` |
| Camera hardware-timestamp metadata | ✗ | — | ✓ | `src/gopher_streamer/shared/frame_metadata.py` |

### Reception / display

| Feature | Ours | Our source | UMN | UMN source |
|---|---|---|---|---|
| Measurement sink (`fakesink` / `filesink`) | ✓ | `gstexp/viewer.py` | n/a | — |
| Real display sink | ◐ `autovideosink` (expo only) | `gstexp/expo.py` | ◐ Tkinter + Pillow | `src/gopher_streamer/receiver/display.py` |
| Multi-window / multi-monitor layout | ✗ | — | ✓ | `receiver/display.py` |
| Composited mosaic / picture-in-picture | ✗ | — | ✗ | — |

### Metrics & measurement

| Feature | Ours | Our source | UMN | UMN source |
|---|---|---|---|---|
| Frame count | ✓ | `gstexp/metrics.py` (`FrameCount`) | ✓ | `src/gopher_streamer/shared/metrics.py` |
| Wire bytes / bitrate | ✓ | `gstexp/metrics.py` (`WireBytes`) | ✓ | `src/gopher_streamer/plotting/plot_throughput.py` |
| Frame latency (RTP boundary) | ✓ | `gstexp/metrics.py` (`FrameLatency`) | ✗ | — |
| Per-stage latency (8 probes) | ✓ | `gstexp/metrics.py` (`StageLatency`) | ✗ | — |
| Source→decode latency | ◐ derivable | `gstexp/metrics.py` | ✓ | `src/gopher_streamer/receiver/decoder.py` |
| Glass-to-glass latency | ✗ | — | ✗ | — |
| Decoded PSNR | ✓ | `gstexp/metrics.py` (`DecodedPsnr`) | ✗ | — |
| SSIM / VMAF | ✗ | — | ✗ | — |
| Decoder-error accounting | ✓ | `gstexp/metrics.py` (`DecoderErrors`) | ◐ complete/partial/dropped | `src/gopher_streamer/plotting/analyze_receiver_log.py` |

### Recording

| Feature | Ours | Our source | UMN | UMN source |
|---|---|---|---|---|
| Dual-end forensic bitstream recording | ✗ | — | ✓ | `src/gopher_streamer/{transmitter,receiver}/save_encoded.py` |

### Network impairment

| Feature | Ours | Our source | UMN | UMN source |
|---|---|---|---|---|
| `tc` tbf + netem impairment | ✓ | `gstexp/validation.py` (compiler), `gstexp/runner.py` | ✗ | — |
| Declarative network specs | ✓ | `specs/networks/` | ✗ | — |
| mahimahi trace replay | ◐ translated into specs | `specs/networks/mahimahi-5g-*.yaml` | ✓ `mm-link` | `src/gopher_streamer/experiment/main.py`, `traces/` |

### Experiment methodology

| Feature | Ours | Our source | UMN | UMN source |
|---|---|---|---|---|
| Declarative validated specs | ✓ | `gstexp/validation.py`, `specs/` | ✗ | — |
| Schema validation at load time | ✓ | `gstexp/validation.py` | ✗ dataclass only | `src/gopher_streamer/shared/config.py` |
| Multi-rep experiment runner | ✓ | `experiment.py` | ◐ subprocess spawn | `src/gopher_streamer/experiment/main.py` |
| Hypothesis framework + verifiers | ✓ | `analysis/hypotheses/` | ✗ | — |

### Vehicle integration (out of scope for our testbed)

| Feature | Ours | Our source | UMN | UMN source |
|---|---|---|---|---|
| Vehicle controls (joystick → cmd_vel) | ✗ | — | ✓ | `src/vehicle_controls/zenoh/` |
| RAN telemetry | ✗ | — | ✓ | `src/ran/`, `src/telemetry/` |
| GPS / GNSS | ✗ | — | ✓ | `src/gopher_streamer/transmitter/gps.py`, `ros_gps.py` |
| Trajectory / routing | ✗ | — | ✓ | `src/trajectory/` |
| Ambient audio | ✗ | — | ✗ | — |

## The architectural split

The two projects are not the same kind of artifact, and the comparison
only makes sense once that is stated plainly.

| | GstreamerExp (this repo) | UMN Teleop-Gopher-streamer |
|---|---|---|
| **What it is** | A congestion-control *testbed* | A teleoperation *system* |
| **Media framework** | GStreamer 1.24, standard RTP elements | PyAV / libav, custom UDP framing |
| **Congestion control** | SCReAM and GCC, swappable per spec | A bespoke throughput-driven bandwidth allocator |
| **Scope** | One video flow, instrumented to death | Video + vehicle controls + RAN telemetry + trajectory + GPS |
| **Evaluation** | Declarative specs, hypothesis framework, verifier scripts | Ad-hoc experiment scripts, log parsing, matplotlib |
| **Codec** | VP8 only | VP8 / VP9 / H.264 / HEVC / AV1, software + QSV + NVENC + VAAPI |
| **Camera** | `videotestsrc` / `filesrc` | FLIR Blackfly S GigE, real multi-camera rigs |

Read this as: UMN has built the *teleoperation reality* — real cameras,
hardware encode, multi-stream, vehicle integration — that our
`docs/TODO.md` enumerates as missing. We have built the *measurement
rigor* — a real congestion controller comparison, validated specs, and
a falsifiable hypothesis framework — that they have not. The interesting
question per gap is whether their solution is a *design idea* we can lift
into a GStreamer pipeline, or whether it is welded to PyAV and their
custom protocol.

The gap sections below follow `docs/TODO.md` order.

---

## Capture

**Our gap.** `videotestsrc` (synthetic) or `filesrc → decodebin`
(file-backed) only. TODO wants real vehicle cameras, multi-camera
support, and camera frame-clock metadata.

**How UMN solves it.**
[`cameras/spinnaker_capture.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/cameras/spinnaker_capture.py)
drives FLIR Blackfly S GigE cameras through the PySpin SDK: continuous
acquisition, `GetNextImage()` polling with a wall-clock-paced loop,
Bayer-RG8 capture with OpenCV demosaicing, incomplete-frame detection.
Cameras are addressed by hard-coded serial number with per-camera IP,
binning, and GigE packet-delay settings. Multi-camera is real — the
live-camera configs run three cameras, the archived configs run six.
Crucially, they enable **camera chunk mode** and pull a hardware
timestamp (`camera_timestamp_ns`) plus a wall-clock source time
(`source_unix_ms`) per frame, attached via
[`shared/frame_metadata.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/shared/frame_metadata.py).

**Portability.** The PySpin capture loop is *not* portable as code — it
produces `av.VideoFrame` objects for a PyAV encoder. But the design is
portable: a GStreamer pipeline would bridge PySpin into an `appsrc`, or
adopt an existing Spinnaker GStreamer source element if one is
acceptable. The frame-clock metadata idea — read the camera's hardware
timestamp via chunk mode, carry it alongside the buffer — maps cleanly
onto GStreamer buffer PTS plus a `GstMeta`. **Idea portable, code not.**

## Encoding

**Our gap.** `vp8enc` software only, with `target-bitrate` as the single
knob. TODO wants a hardware-accelerated path and adaptive resolution
(drop 720p → 360p when the rate controller cuts the budget).

**How UMN solves it.**
[`transmitter/encoder.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/transmitter/encoder.py)
runs two encode paths. The PyAV path covers software (`libx264`,
`libx265`, `libvpx-vp9`) and hardware (`h264_nvenc`, `hevc_nvenc`,
`av1_nvenc`, `hevc_qsv`, `av1_qsv`, `vp9_qsv`); the VAAPI path shells out
to an FFmpeg subprocess for `hevc_vaapi`. They detect silent
hardware-encoder fallback by capturing C-level stderr during codec open
and auto-downgrade to the matching software library. Adaptive resolution
exists but is **policy-driven, not congestion-driven**: a per-frame
policy file can specify a target resolution, and the encoder pre-scales
all candidate resolutions and switches at a frame boundary. Their
bitrate-scaling formula is `fps_ratio × resolution_area_ratio ×
qp_ratio`.

**Portability.** GStreamer already has the hardware-encode element
matrix (`vah264enc`, `nvh264enc`, `qsvh264enc`, …), so we do not need
their encoder code — we need their *config model*: a per-codec table of
low-latency options (B-frames off, `async-depth 1`, GOP size, tuning
preset) and the fallback-detection discipline. Their adaptive-resolution
mechanism is a real answer to our TODO item, but note the difference:
TODO wants resolution driven by SCReAM's `current-max-bitrate` /
GCC's `estimated-bitrate`; UMN drives it from a static policy file.
The mechanism (pre-scale, switch at frame boundary, renegotiate caps)
is portable; the trigger is the part we would still have to build.
**Mechanism portable, trigger still ours to build.**

## Transport

**Our gap.** TODO's transport items are encryption (SRTP/DTLS),
authentication, redundant/multi-path transport, and per-stream DSCP
marking. Loss recovery on top of SCReAM is already done.

**How UMN solves it.** It mostly does not — and where it does, it is via
a path we would not follow.
[`shared/protocol.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/shared/protocol.py)
is a **custom UDP framing**, not RTP: an 11-to-37-byte variable header
carrying `stream_id`, `frame_id`, `packet_index`, `total_packets`, and
flag-gated extensions for a transmit timestamp, source frame id, and
inline GPS/camera metadata. No sequence numbers, no marker bit — a frame
is complete when the last `packet_index` arrives.
[`shared/udp_buffers.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/shared/udp_buffers.py)
tunes 4 MB socket buffers and warns on insufficient OS limits. There is
no encryption, no authentication, no multi-path, no DSCP.

**Portability.** Their custom protocol is the opposite of our direction
— we use standard RTP precisely so SRTP/DTLS and standard tooling drop
in. The one portable observation is the *value* of carrying rich
per-frame metadata inline (transmit timestamp, source frame id); RTP
header extensions are the standard-compliant way to get the same thing.
**Not portable; if anything, a cautionary contrast.** Our transport
TODO items remain unaddressed by either project.

## Reception

**Our gap.** `fakesink` / `filesink` measurement sinks; `autovideosink`
only in expo specs. TODO wants a real display sink (`glimagesink` /
framebuffer / HMD) and a per-camera composited view.

**How UMN solves it.**
[`receiver/display.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/receiver/display.py)
renders into a Tkinter + Pillow window, with a per-stream window-layout
config so a six-stream rig lays out as a 2×3 grid across monitors. It
falls back to a headless CSV mode when Tk is unavailable.

**Portability.** A Tk window is a functional display but not the
production sink our TODO envisions — there is no VSYNC alignment, no
direct framebuffer, no HMD path, and Tk + Pillow adds a copy and a
conversion on every frame. The portable idea is the **per-stream
window-layout config** (declare where each camera's view lands), which
maps onto a GStreamer `compositor` for a mosaic / picture-in-picture
view. **Layout-config idea portable; the sink itself is not what we
want.**

## Latency

**Our gap.** TODO wants glass-to-glass latency (capture → display) — the
headline teleop metric. Per-stage latency attribution is already done
(our `stage_latency` metric, 8 probes).

**How UMN solves it.** Partially.
[`receiver/decoder.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/receiver/decoder.py)
computes `receiver_latency_ms = decode_unix_ms - source_unix_ms` — from
the camera's wall-clock source time to **decoder output**, not to
display. Display render time is excluded.
[`plotting/plot_latency.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/plotting/plot_latency.py)
attributes total latency as `encoding_time + send_receive_latency` by
matching transmit and receive log lines on `(stream_id, frame_id)`. An
optional UDP time-sync exchange logs host-to-receiver clock skew for
after-the-fact correction; it is not a real-time correction.

**Portability.** UMN gets *closer* to glass-to-glass than we do because
they start the clock at a real camera hardware timestamp — but they
still stop at decoder output. Our per-stage attribution is finer-grained
than theirs. The portable idea is **starting the latency clock at the
camera's hardware timestamp** (chunk mode), which is the only honest
way to measure the "glass" at the capture end. The display end still
needs a probe at the sink. **Capture-side clock idea portable; neither
project has true glass-to-glass yet.**

## Quality

**Our gap.** `decoded_psnr` (Y-plane PSNR vs reproduced source) is done;
SSIM / VMAF still missing. TODO also wants a quality-degradation
indicator surfaced to the operator and audit-grade frame-loss
accounting.

**How UMN solves it.** It does not compute a reference quality metric at
all — no PSNR, no SSIM, no VMAF. What it does have is **partial-frame
decode** (see Resilience below), and
[`plotting/analyze_receiver_log.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/plotting/analyze_receiver_log.py)
classifies each frame as complete / partial / dropped — which is a form
of frame-loss accounting.

**Portability.** We are ahead on quality measurement. Their
complete/partial/dropped classification is a portable idea for the
"audit-grade frame loss accounting" TODO item — it answers *which*
frames and *why* — and is cheap to compute from our existing metrics.
**Frame-classification idea portable; quality metrics are our strength,
not theirs.**

## Audio

**Our gap.** No ambient audio capture from the vehicle.

**How UMN solves it.** It does not — there is no audio path in the UMN
streamer either.

**Portability.** Nothing to take. Both projects punt on audio.

## Recording / forensics

**Our gap.** TODO wants on-vehicle and on-operator recording of the
actual streamed bitstream for incident replay, plus a lossless local
copy alongside the lossy transmitted copy.

**How UMN solves it — and this is their clearest win.**
[`transmitter/save_encoded.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/transmitter/save_encoded.py)
and
[`receiver/save_encoded.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/receiver/save_encoded.py)
record the streamed bitstream **on both ends**, muxing the exact encoded
bytes — no re-encode — into MP4 / MKV / IVF containers with a sidecar
metadata file (frame rate, codec). Because there is no re-encode, the
transmitter-side and receiver-side files are byte-identical for every
delivered frame, which is exactly what incident replay needs. For
VP8/VP9/AV1 they use a direct IVF writer to skip FFmpeg overhead.

**Portability.** This maps directly and cleanly onto GStreamer: a `tee`
off the encoded stream into a `filesink` (or `matroskamux`/`mp4mux` →
`filesink`) on each side gives exactly the dual-end, no-re-encode
forensic record. Their design — record encoded bytes, not decoded
frames; write a codec/framerate sidecar; do it symmetrically on both
ends — is the right shape and is fully portable. **Strongly portable;
a near-direct adoption candidate.**

## Synchronization

**Our gap.** Single stream today. TODO wants vehicle wall-clock
timestamps in RTP headers (NTP/PTP-aligned) and cross-stream sync (the
same instant `T` arrives with the same `T` across all cameras).

**How UMN solves it.** Weakly. Each stream runs in an **independent
thread** ([`transmitter/main.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/transmitter/main.py));
there is no cross-stream frame-alignment barrier. Implicit alignment
comes only from every stream starting at frame 0 of its source and from
per-frame policies keyed to frame number. They *do* carry a transmit
timestamp and the camera hardware timestamp in the frame header, so the
data needed for sync is on the wire — they just do not act on it at the
receiver.

**Portability.** The portable piece is **carrying the camera hardware
timestamp end-to-end** (they have the plumbing; an RTP header extension
is the standard equivalent). Active cross-stream synchronization is
unsolved in both projects — GStreamer's `GstClock` plus RTCP
sender-report-based synchronization is the standard-compliant path and
is the more natural fit on our side. **Timestamp-on-the-wire idea
portable; active sync unsolved by both.**

## Resilience to loss

**Our gap.** Mostly done — NACK + RTX, PLI, and ULPFEC all landed and
orthogonal. The remaining TODO items are decoder error concealment /
freeze-on-bad-frame and tuning I-frame frequency to recovery time.

**How UMN solves it.** Differently, and with less.
[`receiver/reassemble.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/receiver/reassemble.py)
has **no retransmission, no NACK, no FEC**. Its recovery toolkit is: a
WebRTC-style jitter buffer; **partial-frame decode** — hand the decoder
the contiguous packet prefix from index 0 even when the frame is
incomplete; keyframe requests (KFRQ messages, receiver → transmitter,
equivalent to PLI); and a hard 800 ms stale-frame drop. Until the first
complete keyframe arrives the receiver drops every non-keyframe.

**Portability.** We are well ahead on recovery — they have a PLI
equivalent and nothing else. Their one genuinely interesting idea we do
not have is **partial-frame decode**: rather than discard a frame with a
missing tail, decode the prefix and accept the visual artifact. That is
a real error-concealment strategy and lines up with our open TODO item
"decoder error concealment / freeze-on-bad-frame." It is codec-dependent
(the decoder must tolerate truncated input) but the queueing logic
around it is protocol-agnostic. **Partial-frame-decode idea portable and
worth prototyping; the rest, we already do better.**

---

## Congestion control — not a TODO gap, but the core comparison

This is the one axis where the comparison is not gap-driven: congestion
control is our project's *existing core*, not a missing feature.

First the blunt fact: **UMN ships no transport congestion control, and
by default no rate adaptation at all.** `adaptive_bitrate` is `false` in
84 of 100 shipped configs; the default pipeline encodes at fixed CBR,
blasts packets, and lets congestion show up as UDP socket-buffer
overflow → packet drops. There is no SCReAM/GCC-class controller, no
path feedback, no congestion window, no pacing — anywhere in the repo.

What UMN *does* have, opt-in, is the `BandwidthAllocator`. It is the
only thing comparable to a controller, so it is what the rest of this
section compares against SCReAM and GCC — but keep in mind it is off in
the common case.

[`transmitter/bandwidth_allocator.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/transmitter/bandwidth_allocator.py)
and
[`transmitter/throughput.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/transmitter/throughput.py)
implement a **reactive, throughput-driven, priority-ordered multi-stream
allocator**. It estimates link capacity by polling Linux qdisc
backlog/drops with EWMA smoothing (and reads a Mahimahi trace directly
when running under `mm-link`), reserves a headroom margin, then
iteratively degrades the lowest-priority streams down a fixed ladder —
first FPS, then quality, finally stream shutdown — until total demand
fits the budget. It updates once per second.

This is a different control philosophy from both of ours:

| | UMN allocator | SCReAM | GCC |
|---|---|---|---|
| Default state | off (`adaptive_bitrate=false`, 84/100 configs) | on when CC selected | on when CC selected |
| Operates at | application layer (encoder bitrate/FPS) | transport layer (RTP rate) | transport layer (RTP rate) |
| Congestion signal | local qdisc backlog / drops | RTT + queue delay + loss | delay gradient + loss gradient |
| Loop style | reactive, throughput-driven | reactive, queue-delay-based | reactive, gradient-based |
| Response time | ~1–2 s (1 Hz poll + EWMA) | ~100–500 ms | ~50–200 ms |
| Multi-stream | priority-ordered degradation ladder | per-stream rate, shared signal | per-stream rate, shared signal |
| RAN-aware | no (despite having RAN gRPC infra) | no | no |

It is slower-reacting than either SCReAM or GCC and reads a signal
(qdisc state) that assumes the bottleneck is the local sender's qdisc —
which is true on a shaped testbed link and not true in general. But the
**priority-ordered multi-stream degradation ladder** is a genuinely
different idea: SCReAM and GCC give you a rate, and what to do with it
across N streams is left to the application. UMN's allocator *is* that
policy. That makes it a plausible third arm in our comparison — not as a
SCReAM/GCC replacement, but as a multi-stream allocation layer that
could sit on top of either. See the follow-up issue.

Notably, despite shipping a full RAN telemetry subsystem (gRPC stubs
under `src/ran/`), UMN does **not** feed any radio metric — RSRP, CQI,
handover state — into rate control. The allocator sees qdisc state and
nothing else. That is the same observation our H4 made about the
translated 5G traces: the controller responds to a proxy signal, not to
the radio.

---

## What to adopt, what to keep

**Adopt (portable ideas, follow-up issues filed):**

- **Dual-end forensic recording.** A `tee` → `filesink` of the encoded
  stream on both camera and viewer, no re-encode, with a codec/framerate
  sidecar. Near-direct adoption.
- **Real-camera capture path.** Bridge PySpin into `appsrc`, carry the
  camera hardware timestamp as buffer PTS + `GstMeta`. Idea portable,
  code is PyAV-specific.
- **Hardware-encode config discipline.** A per-codec low-latency option
  table and silent-fallback detection, over GStreamer's existing
  hardware encoder elements.
- **Partial-frame decode** as an error-concealment strategy for the open
  "decoder error concealment" TODO item.
- **UMN's bandwidth allocator as a third evaluation arm** — a
  priority-ordered multi-stream degradation layer, compared against
  doing the same on top of SCReAM/GCC rates.

**Keep (our strengths, do not regress):**

- Standard RTP transport — SRTP/DTLS and standard tooling depend on it;
  UMN's custom UDP protocol is the opposite direction.
- The declarative, validated spec layer and the hypothesis framework.
  UMN's experiments are ad-hoc scripts with no schema validation and no
  pass/fail verifier.
- SCReAM + GCC and the recovery stack (NACK+RTX, PLI, ULPFEC) — UMN has
  a PLI equivalent and nothing else.
- Reference quality metrics (`decoded_psnr`) and per-stage latency
  attribution — finer-grained than anything in the UMN streamer.

**Out of scope.** UMN's vehicle-controls (Zenoh + ROS), RAN telemetry,
and trajectory/routing subsystems have no counterpart in a
congestion-control testbed and are not evaluated here.
