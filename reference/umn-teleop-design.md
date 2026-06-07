# UMN Teleop-Gopher-Streamer: top-down design

A standalone design note for the UMN GopherNetLab teleoperation streamer,
vendored locally at `reference/external/teleop-gopher-streamer/`
(upstream <https://github.com/GopherNetLab/Teleop-Gopher-streamer>).

Companion docs:

- [`umn-teleop-gopher-comparison.md`](umn-teleop-gopher-comparison.md) — feature-by-feature vs. GstreamerExp.
- [`umn-teleop-divergence-analysis.md`](umn-teleop-divergence-analysis.md) — why the two systems diverge.

This note is different in kind. It describes UMN on its own terms: **what
it is, how it is decomposed, how a frame flows through it, what its parts
actually do, and where the design falls short of the bar it is aiming
at.** It is meant to be readable without first reading the comparison.

File references are pinned to upstream commit `7c585c2` on `main`. Where
something only exists on `scream-integration` (`502533c0`), that is
called out explicitly.

---

## 1. Purpose and scope

UMN built a **teleoperation video product**. The README's working
description is "live FLIR/Blackfly camera streaming with per-frame ROS
GNSS/RTK metadata," with RAN telemetry, trajectory streaming, and
Zenoh-based vehicle controls added into the same repo.

The artifact has to do three things at once:

1. **Multi-camera video** from a vehicle to an operator station, with
   low-enough latency that the operator can drive and robust-enough
   recovery that a 5G handover does not freeze the view.
2. **Per-frame vehicle context** — camera hardware timestamps, GPS / RTK
   coordinates — carried alongside each frame so the operator's view can
   be cross-referenced with the vehicle's position.
3. **A vehicle-integration surface** — joystick → `cmd_vel`, RAN radio
   telemetry, route playback — that lets the streamer drop into a real
   deployment, not just a demo loop.

What it is *not*: a controlled environment for measuring a congestion
controller. That distinction matters for §8 and §11.

---

## 2. System context

Two endpoints separated by a (typically cellular) IP link, plus a
collection of teleop subsystems that the streamer carries data for but
does not itself drive.

```
            ┌──────────────────────────┐                ┌──────────────────────────┐
            │       VEHICLE            │                │     OPERATOR STATION     │
            │                          │                │                          │
   FLIR ───►│  Capture                 │   UDP video    │   Reassemble + jitter    │
   GigE     │  → Encode (PyAV/FFmpeg)  │  ════════════► │   → Decode (PyAV)        │──► Tk window
   cams     │  → Custom-UDP packetize  │   (forward)    │   → Display              │    grid
            │  → Send (blast)          │                │                          │
            │                          │   RFBK / KFRQ  │                          │
            │  Receiver-feedback rx ◄──┼ ═════════════ ─┤   RFBK/KFRQ tx           │
            │                          │   (reverse)    │                          │
            │  Congestion controller   │                │                          │
            │       │   budgets        │                │                          │
            │       ▼                  │                │                          │
            │  Per-stream encoder      │                │                          │
            └──────────────┬───────────┘                └──────────────────────────┘
                           │ shared in-process (no IPC)
            ┌──────────────┴─────────────────────────────────────────────────────┐
            │  Vehicle subsystems (separate packages, independent of video)      │
            │   vehicle_controls/   ran/        telemetry/        trajectory/    │
            │   joystick → Zenoh    gRPC RAN    ROS GNSS sender   Google Maps    │
            └────────────────────────────────────────────────────────────────────┘
```

Two important properties of this context:

- **The video and teleop subsystems share a process** on the vehicle but
  do not exchange data. RAN telemetry is reported out over gRPC; it is
  *not* fed back into rate control. Vehicle controls are dispatched over
  Zenoh; they do not affect the streamer. The repo bundles them; the
  design keeps them separate.
- **There is no signalling layer.** No SDP, no ICE, no DTLS handshake.
  The transmitter sends to a fixed `receiver_host:port`; the receiver
  binds and waits. The first packet establishes the return address used
  for feedback and keyframe requests.

---

## 3. Top-down decomposition

### 3.1 Level 1 — system

| Component | Role |
|---|---|
| **Transmitter** (`src/gopher_streamer/transmitter/`) | Capture → encode → packetize → blast UDP. Hosts the congestion controller and the feedback-receive thread. |
| **Receiver** (`src/gopher_streamer/receiver/`) | Reassemble UDP → jitter-buffer → decode → display. Sends keyframe requests and receiver feedback back to the transmitter. |
| **Shared** (`src/gopher_streamer/shared/`) | Wire protocol, dataclass config, metrics writers, frame-metadata structs, UDP socket buffer tuning. |
| **Experiment harness** (`src/gopher_streamer/experiment/`) | Spawns transmitter+receiver as subprocesses from one YAML, optionally wraps the transmitter in `mm-link` for Mahimahi trace replay. |
| **Teleop subsystems** (`src/{ran,telemetry,trajectory,vehicle_controls}/`) | Independent integration surfaces, described in §10. |

### 3.2 Level 2 — the streamer (`gopher_streamer/`)

```
gopher_streamer/
├── transmitter/                            ← sender side, hosted on the vehicle
│   ├── main.py                  1191 LoC   ← orchestration: starts everything below
│   ├── encoder.py               1605 LoC   ← per-stream encode loop (PyAV + VAAPI subprocess)
│   ├── sender.py                  251 LoC  ← packetize one frame, blast over UDP
│   ├── smart_buffer.py            528 LoC  ← optional Linux low-latency sender path
│   ├── scream_controller.py       226 LoC  ← (scream-integration) SCReAM-style AIMD
│   ├── google_congestion_controller.py 407 ← (scream-integration) GCC-style controller
│   ├── bandwidth_allocator.py     303 LoC  ← legacy priority-degrader (1 Hz)
│   ├── throughput.py              526 LoC  ← qdisc-backlog capacity estimator
│   ├── receiver_feedback.py        75 LoC  ← RFBK ingestion, shared state for controllers
│   ├── gps.py                     165 LoC  ← serial Xsens/NMEA reader
│   ├── ros_gps.py                  13 LoC  ← ROS GNSS reader stub
│   ├── display.py                 234 LoC  ← optional local preview window
│   └── save_encoded.py             53 LoC  ← on-vehicle bitstream recording
│
├── receiver/                               ← operator side
│   ├── main.py                             ← orchestration
│   ├── reassemble.py                       ← jitter buffer, partial-frame decode, feedback tx
│   ├── decoder.py                          ← PyAV decode + receiver_latency_ms
│   ├── display.py                          ← Tk + Pillow window grid (or headless CSV)
│   └── save_encoded.py                     ← on-operator bitstream recording
│
├── shared/                                 ← used by both sides
│   ├── protocol.py                         ← wire format (§5)
│   ├── config.py                           ← YAML → dataclass (§7)
│   ├── frame_metadata.py                   ← GPS / camera-timestamp struct
│   ├── udp_buffers.py                      ← 4 MB SO_RCVBUF/SO_SNDBUF tuning
│   ├── metrics.py                          ← CSV / JSONL event loggers
│   ├── protocol.py                         ← (above)
│   └── session_control.py                  ← session-lifecycle helpers
│
├── experiment/                             ← multi-process runner (§9)
│   └── main.py
│
├── cameras/spinnaker_capture.py            ← FLIR Blackfly capture via PySpin
└── plotting/                               ← post-hoc log analysis + matplotlib
```

### 3.3 Level 3 — components that warrant a closer look

The dense LoC numbers in §3.2 are misleading: `encoder.py` (1605 lines)
is most of the system's complexity, then `main.py` (1191 lines) which is
mostly wiring. Everything else is small enough to read in one sitting.

§4–§8 below open each of the load-bearing components in turn.

---

## 4. The video data path (frame lifecycle)

A single frame's journey, end to end, in execution order. This is the
clearest way to see what the system actually does.

### 4.1 Capture

For a file source (`source_path` set to a video file), `encoder.py`
opens the file with PyAV and reads `av.VideoFrame` objects in a loop.
For a live camera (`source_path = "LIVE_CAMERA"`),
`cameras/spinnaker_capture.py` drives a FLIR Blackfly via the PySpin
SDK: continuous acquisition, `GetNextImage()` polling, Bayer-RG8 capture
with OpenCV demosaicing, and **camera chunk-mode hardware timestamps**
(`camera_timestamp_ns`) attached to each frame.

The capture step also stamps `source_unix_ms` (wall-clock time at
capture). Together, the camera-hardware and wall-clock timestamps are
the only signal the system has about how old the content of any
delivered frame really is.

### 4.2 Encode

`transmitter/encoder.py` runs one of two paths:

- **PyAV path** (the common case). Drives `libav`'s codec context
  directly. Covers software (`libx264`, `libx265`, `libvpx-vp9`) and
  hardware (`h264_nvenc`, `hevc_nvenc`, `av1_nvenc`, `hevc_qsv`,
  `av1_qsv`, `vp9_qsv`).
- **VAAPI subprocess path** (`_VaapiSubprocessEncoder`). Shells out to a
  long-lived `ffmpeg -i pipe:0 -c:v hevc_vaapi …` process, pipes raw NV12
  in, reads encoded NAL units out. Used for `hevc_vaapi` because the
  PyAV → VAAPI binding is fragile.

Two specific choices are worth surfacing:

- **Silent-fallback detection** (`_open_codec_and_detect_fallback`).
  Hardware codecs can silently downgrade to software when the GPU is
  busy or the driver mismatches. UMN captures C-level stderr on codec
  open, scans for the telltale strings, and refuses to proceed under a
  silent fallback. This is genuinely good practice — a fixed-budget
  benchmark over a quietly-software path is misleading data.
- **Adaptive resolution is policy-driven, not CC-driven.** A per-frame
  JSONL policy file (`policy_file` in the transmitter config) can
  specify a target resolution per `frame_id`. The encoder pre-builds
  scalers for every candidate resolution and switches at a frame
  boundary. The congestion controller does *not* drive resolution; it
  drives bitrate factor.

The encoder polls a **`get_budget()` callback** before each frame to
read its current `StreamBudget(active, fps_factor, bitrate_factor)` from
whichever rate controller is selected. When `bitrate_factor < 1.0`, the
encoder scales its target bitrate accordingly. When `fps_factor < 1.0`,
the encoder skips source frames to thin the stream. When `active` is
False, the stream is suspended.

### 4.3 Packetize + send (the wire)

`transmitter/sender.py` does both in one call (`send_frame`):

1. Split the encoded `payload` into `ceil(len / max_payload)` chunks of
   ≤ `max_packet_size - header_size_v4` bytes (default `max_packet_size`
   is 1400, comfortably under typical MTU).
2. For each chunk, build a header with `(stream_id, frame_id,
   packet_index, total_packets, flags)` plus the v2/v3/v4 extensions
   (TX timestamp, source frame id, inline frame metadata). See §5.
3. **`sock.sendto(data, MSG_DONTWAIT, dest)` in a tight loop**, retrying
   up to 100× with 1 ms sleeps on `BlockingIOError` (UDP buffer full).
   Past 100 retries, the packet is dropped and a warning is logged.

There is **no packet pacing**. There is no smoothing across the frame.
The sender blasts the entire frame at line rate; congestion shows up as
UDP-buffer overflow, either at the kernel send side (the retry loop) or
at the receive side (lost packets).

The socket is shared across all stream threads. `MSG_DONTWAIT` is used
deliberately to avoid one thread flipping the socket's blocking mode
while another is mid-`sendto`.

#### Optional `smart_buffer` path (Linux only)

When `transmitter.smart_buffer: true`, `smart_buffer.py` interposes a
coordinator between the encoder and `sender.py`. Encoded frames are
posted to a shared queue and a dedicated sender thread drains them.
The intent is to keep the encoder loop tight on Linux when the network
path is bursty. It is opt-in and off in most shipped configs.

### 4.4 Receive (reassemble + jitter buffer)

The receiver runs a **single thread** through `reassemble.py`'s
`reassemble_frames_with_jitter` generator. The loop:

1. `select()` across all bound sockets with a 1 ms timeout. For each
   ready socket, `recvfrom()` drain until `BlockingIOError`.
2. For each received packet, decode the header. Three packet types are
   demuxed by content:
   - **Time-sync request (`TREQ`)** → reply with `TRES` carrying the
     receiver's `time.time()`.
   - **Video packet** → look up or create the matching `PendingFrame`,
     add this packet's payload at its `packet_index`.
   - Anything else → drop.
3. Update per-stream `FeedbackStats` (packets received/expected, bytes
   since last report). One-way delay is computed from `tx_unix_ms` and
   the receiver's wall clock; jitter is EWMA of `|Δdelay|` with α = 1/16.
4. **Decide what to deliver.** For each stream, find the highest
   `frame_id` that is either (a) complete, or (b) past its
   `jitter_buffer_ms` deadline (default 30 ms). Hand it to the decoder.

### 4.5 Three things the receiver does that are worth knowing

- **Partial-frame decode.** If a frame is incomplete when its jitter
  window expires, the receiver assembles the contiguous-from-packet-0
  prefix and hands that to the decoder (if it is ≥ 512 bytes). The
  decoder either renders a frame with visible tearing/artifacts or
  errors out, in which case the frame is dropped. This is the system's
  one nontrivial loss-recovery mechanism, and it is real — most teleop
  stacks freeze or NACK instead.
- **Lock-step keyframe gating.** Until a *complete* keyframe arrives
  for a stream, every non-keyframe is dropped (a decoder cannot
  initialise without the sequence header). When a non-keyframe arrives
  in that "not yet locked" state, the receiver fires a `KFRQ` to push
  the transmitter to emit one. The retry is rate-limited to 1.5 s.
- **Hard 800 ms stale drop.** Any deliverable frame whose `tx_unix_ms`
  is more than 800 ms old when it would be passed to the decoder is
  dropped instead. This bounds visible latency but silently throws
  away anything that arrives late after a bad rebuffer.

### 4.6 Decode + display

`receiver/decoder.py` runs a PyAV codec context per stream, computes
`receiver_latency_ms = decode_unix_ms - source_unix_ms` (the closest
thing in the system to a glass-to-glass measurement; it stops at decoder
output, not display), and emits decoded `av.VideoFrame`s. `display.py`
renders them into a Tkinter + Pillow window per stream. Layout is
configurable: each `stream_id` can be assigned a window position, size,
and fullscreen flag, so a six-stream rig lays out as a 2×3 grid across
monitors. When Tk is unavailable, it falls back to headless CSV mode
(no rendering, metrics still written).

---

## 5. The wire protocol

`shared/protocol.py` defines a **custom UDP framing**, not RTP. All
messages — video data, keyframe requests, time sync, receiver feedback —
ride the same socket and are demultiplexed by leading bytes.

### 5.1 Video data packet

Header version evolved through `v1 → v4`; the transmitter today always
emits `v4`.

```
+--------------------------------------------------------+
| stream_id (2)  frame_id (4)  pkt_index (2)  total (2)  |  v1 (10 B)
| flags (1)                                              |  +1 → v2
| tx_unix_ms (8)                                         |  +8 → v2+TS
| source_frame_id (4)                                    |  +4 → v3
| frame_metadata (33: src_ts, cam_ts, lat, lon, alt, …)  |  +33 → v4
+--------------------------------------------------------+
| payload (≤ max_packet_size − header)                   |
+--------------------------------------------------------+
```

Notable: **there is no packet sequence number.** A frame is "complete"
when packets for every `packet_index ∈ [0, total_packets)` have arrived
(`pending[(stream_id, frame_id)]`). Loss within a frame is detected at
frame-completion time, not per-packet. Loss *between* frames is detected
only when a later frame's `frame_id` lands without an earlier one
showing up — i.e. statistically over a window.

### 5.2 Control messages (same socket, demuxed by magic)

| Message | Direction | Magic | Body | Purpose |
|---|---|---|---|---|
| `KFRQ` (keyframe request) | receiver → tx | `b"KFRQ"` | `stream_id` (2) | "Send a keyframe for this stream." Receiver fires this when it has not yet seen a complete keyframe, or after a hard drop. |
| `TREQ` / `TRES` (time sync) | tx → rx → tx | `b"TREQ"` / `b"TRES"` | `TRES` carries `receiver_unix_ms` (8) | One-shot wall-clock offset estimate. Not a continuous skew correction. |
| `RFBK` (receiver feedback) | receiver → tx | `b"RFBK"` | `stream_id, feedback_unix_ms, highest_frame_id, packets_received, packets_expected, complete_frames, incomplete_frames, bitrate_bps, one_way_delay_ms, jitter_ms` | Periodic feedback driving rate adaptation on the transmitter. |

The receiver sends `RFBK` every `feedback_interval_s` (default 200 ms),
plus a forced send on a delivery/drop event. The transmitter's
`receiver_feedback.py` keeps a `ReceiverFeedbackState` snapshot per
stream that the congestion controller reads each tick.

### 5.3 What's missing on the wire

- **No RTP** — no SSRC, no marker bit, no header extensions, no padding,
  no timestamp clock-rate, no payload-type field. Codec is inferred
  globally from `ReceiverConfig.codec_name`.
- **No RTCP** — `RFBK` is functionally a feedback channel but is not
  RFC-compliant; SR / RR / TWCC / RFC 8888 do not apply.
- **No encryption, no authentication.** Anyone who can hit the receiver's
  UDP port can inject packets indistinguishable from real video.
- **No NACK, no FEC.** Partial-frame decode is the only recovery
  besides a full keyframe request.

---

## 6. The control plane (rate adaptation)

The rate-control architecture on the transmitter is the most
interesting part of the design. The shape:

```
   ┌─────────────────────────────────────────────────────────────┐
   │  ReceiverFeedbackState (shared)                             │
   │      ▲                                                      │
   │      │ updates on RFBK arrival                              │
   │  ┌───┴─────────────────┐                                    │
   │  │  feedback rx thread │  ← decodes RFBK from receiver      │
   │  └─────────────────────┘                                    │
   │                                                             │
   │      ▲                                                      │
   │      │ read once per tick                                   │
   │  ┌───┴─────────────────────────────────┐    1 Hz / 100 ms   │
   │  │   Congestion controller             │ ─────────────►     │
   │  │   (allocator | scream | gcc | fixed)│                    │
   │  └───┬─────────────────────────────────┘                    │
   │      │ writes per-stream StreamBudget under lock            │
   │      ▼                                                      │
   │  {budgets[stream_id]: StreamBudget(active, fps, bitrate)}   │
   │      ▲                                                      │
   │      │ polled before each encoded frame via get_budget()    │
   │  ┌───┴────────────────┐                                     │
   │  │ encoder threads    │  (one per stream)                   │
   │  └────────────────────┘                                     │
   └─────────────────────────────────────────────────────────────┘
```

Two design points are doing the load-bearing work here:

1. **The controller does not pace packets.** It writes a budget; the
   encoder reads it and shapes the next frame. The actual `sendto`
   loop is unchanged regardless of which controller is selected. There
   is no congestion window, no transmit gate.
2. **Controllers are interchangeable behind a single
   `make_budget_getter(stream_id)` interface.** The encoder doesn't
   know which controller is running. Switching from `allocator` to
   `scream` to `gcc` to `fixed` is a one-line config change.

The four selectable controllers are described in §8.

---

## 7. Configuration model

`shared/config.py` defines a small hierarchy of `@dataclass`es loaded
from one YAML file:

```
AppConfig
├── TransmitterConfig
│   ├── streams: list[StreamConfig]
│   ├── scream:  ScreamConfig
│   ├── gcc:     GccConfig
│   └── (flat fields: receiver_host/port, max_packet_size, smart_buffer,
│        congestion_controller, bandwidth_headroom, policy_file,
│        save_video, loop_video, no_display, gps_*, ros_gnss_*, …)
└── ReceiverConfig
    ├── codec_name, decoder_preference, decoder_hwaccel(_device)
    ├── jitter_buffer_ms, test_drop_rate, save_video, no_display
    └── windows: dict[stream_id → WindowConfig]
```

Three things worth flagging:

- **No schema validation.** The dataclasses parse YAML opportunistically:
  unknown keys are silently ignored, type errors only show up at
  use-site, and required keys default to soft values (`receiver_host =
  "127.0.0.1"`, `receiver_port = 9000`). The system will load a typo
  and run it.
- **`policy_file` is a parallel control plane.** It is a JSONL keyed
  by `frame_id` carrying `{stream_name: {quality: {priority, ...}}}`
  records. The `BandwidthAllocator` reads it at startup to extract
  per-stream priority; `encoder.py` reads it per-frame to drive
  resolution/QP changes. Two consumers, one file, two different access
  patterns.
- **`congestion_controller`** is a string switch: `"allocator"`,
  `"scream"`, `"gcc"`, or `"fixed"`. The default is `"allocator"`,
  *but* allocator only activates when `adaptive_bitrate: true` — and
  that is `false` in ~84 of 100 shipped configs (see §11).

---

## 8. Rate adaptation: the four modes

All four sit behind the same `StreamBudget` interface (§6). They differ
in what signals they react to and how aggressive the loop is.

### 8.1 `fixed` (and the default-default)

Each stream encodes at its configured `bitrate_kbps`, full FPS, no
adaptation. The "default-default" is effectively this: `allocator` is
the nominal default, but unless `adaptive_bitrate: true` is set the
allocator stays in `OK` state and produces unit budgets — the same
behaviour as `fixed`.

### 8.2 `allocator` (`bandwidth_allocator.py`, 303 LoC, 1 Hz)

A **priority-ordered multi-stream degrader**. It estimates link
capacity from local qdisc state (§throughput.py: backlog and drop
counters, EWMA-smoothed), reserves `bandwidth_headroom` (default 10 %),
then iteratively walks lowest-priority streams down a **discrete 7-step
degradation ladder**:

```
(fps_factor, quality_factor) =
   (1.00, 1.00) → (0.75, 1.00) → (0.50, 1.00)
                → (0.50, 0.75) → (0.33, 0.75)
                → (0.25, 0.50) → (0.17, 0.50) → OFF
```

At each step the allocator asks "does total demand fit the budget?"; if
not, it picks the lowest-priority stream (ties broken by "least
degraded so far") and bumps it one step. It iterates until demand fits
or all streams are off. Updates once per second; cold-starts after 4
throughput samples.

This is the only one of the four controllers that does cross-stream
allocation directly. The other three give a per-stream rate; what to do
across N streams is left to the encoder. The allocator *is* that
policy.

### 8.3 `scream` (`scream_controller.py`, 226 LoC, 100 ms — `scream-integration` branch only)

UMN's own re-implementation of a **SCReAM-style AIMD loop**. The
docstring is honest: "SCReAM-inspired" and "intentionally conservative
for the first hardware integration." Per 100 ms tick:

1. Read `(effective_capacity_bps, backlog_bytes, drop_rate)` from the
   throughput estimator and `(loss_fraction, one_way_delay_ms)` from
   `ReceiverFeedbackState`.
2. Classify the state:
   - **loss** if `receiver_loss > 2 %` or local drops > 0
     → `target *= min(ramp_down_gain, loss_backoff)` (≈0.80–0.85)
   - **delay or queue** if `receiver_delay_ms > queue_delay_target_ms`
     or local backlog > 0 → `target *= ramp_down_gain` (0.85)
   - **otherwise** → `target *= ramp_up_gain` (1.08)
3. Clamp to `[min, capacity*(1−headroom), max]`.
4. Reallocate the new total across streams in proportion to
   `priority × base_bitrate`.

This is a multiplicative AIMD on a fixed timer. It is **not the
Ericsson SCReAM algorithm**: there is no congestion window, no
self-clocking, no RTT-gradient OWD estimator, no fast-recover, no L4S /
ECN response, no separate fast and slow loops. The "SCReAM" label
describes the family of signals used (queue delay + loss), not the
algorithm. The fixed 100 ms timer is the key shape — it is not
ack-clocked, so the response time is bounded above by the timer no
matter how fast the link reacts.

### 8.4 `gcc` (`google_congestion_controller.py`, 407 LoC, 200 ms — `scream-integration` branch only)

A "GCC-style" reimplementation. Trendline filter over recent
`receiver-side` delay samples, overuse detector with a hysteresis
threshold, additive-increase / multiplicative-decrease. Like the SCReAM
one, this is *family-of-signals* GCC, not the WebRTC `rtpgccbwe`
implementation: no Kalman-filter delay model, no `rtcp-twcc` feedback,
no per-packet delay arrivals.

### 8.5 Putting them on one axis

| | `fixed` | `allocator` | `scream` | `gcc` |
|---|---|---|---|---|
| Loop tick | — | 1 Hz | 100 ms | 200 ms |
| Capacity signal | — | qdisc EWMA | qdisc + receiver loss/delay | trendline of receiver delay + loss |
| Output | per-stream bitrate (static) | per-stream `StreamBudget` (degradation steps) | total target, allocated by priority | per-stream target |
| Cross-stream policy | trivial (each fixed) | explicit ladder | proportional to `prio × base_br` | per-stream independent |
| Default in shipped configs | — | nominal | rare | rare |

---

## 9. Experiment harness (`experiment/main.py`)

Modest by design. The runner:

1. Loads the YAML config and runs early dataclass validation.
2. Spawns a **receiver subprocess** if the config has a `receiver:`
   section.
3. Spawns a **transmitter subprocess** if the config has a
   `transmitter:` section.
4. Optionally wraps the transmitter in `mm-link <up-trace>
   <down-trace>` for Mahimahi trace replay.
5. Forwards `-v/--verbose` to both children.
6. Shuts everything down on Ctrl-C or when either child exits.

What it deliberately does *not* do: schedule reps, sweep parameters,
validate that the run produced expected output, score hypotheses, or
aggregate across runs. Each run leaves CSV / JSONL logs behind;
analysis is done out-of-band by the scripts in `plotting/` and
matplotlib. This is *very different* from the testbed model in
GstreamerExp's `experiment.py` + `analysis/hypotheses/`, and the
contrast is the central topic of `umn-teleop-divergence-analysis.md`.

---

## 10. Teleop subsystems

The four sibling packages under `src/` are bundled with the streamer
but do not participate in the video pipeline. They are described here
for completeness; the comparison with GstreamerExp ignores them.

- **`vehicle_controls/`** — joystick → ROS / Zenoh. `zenoh/control.py`
  publishes `cmd_vel`-style messages; `zenoh/recorded_joy.py` replays
  recorded joystick traces. There is a ROS bridge under `zenoh/ros_nodes.py`.
- **`ran/`** — RAN telemetry via gRPC (`grpc/`, with a `mock_server.py`
  for offline runs). `modes/` defines op-modes (video, etc.) that
  *could* be driven by RAN state. **Nothing in the streamer reads RAN
  telemetry.**
- **`telemetry/`** — ROS GNSS reading (`ros/gnss.py`) and a gRPC sender
  for shipping RAN-state out (`senders/ran_grpc.py`).
- **`trajectory/`** — Google-Maps route playback (`google_maps_live.py`,
  `windows/ahead.py`). Used for canned operator trajectories.

The video pipeline draws GPS metadata from `transmitter/gps.py`
(serial NMEA) or `transmitter/ros_gps.py` (ROS GNSS topic). That GPS
metadata flows *into* `FrameMetadata` and rides each packet's v4
header — i.e. the streamer uses GPS as a per-frame label, not as a
control input.

---

## 11. Gaps

Three categories: functional gaps (things the system does not do),
design gaps (architectural limits that close the ceiling), and
operational/methodology gaps (limits on what conclusions you can draw
from running it).

### 11.1 Functional gaps

| Gap | Where it bites |
|---|---|
| **No packet pacing.** The sender blasts an entire frame at line rate. Bursts of size `frame_bytes / interval` arrive at the bottleneck queue all at once. | Self-induced queueing delay; spikes the very signal the controllers are trying to read. |
| **No NACK / RTX.** Lost packets are not recovered; the receiver either decodes a partial prefix or drops the frame. | Frame freeze / artifact on every burst loss. The 800 ms stale-drop cap blocks waiting indefinitely, but the missing recovery means every loss is visible. |
| **No FEC.** No redundancy across packets. | Same as NACK above, even on links where retransmission would not fit the latency budget. |
| **No reference quality metric.** No PSNR, no SSIM, no VMAF — only `complete / partial / dropped` frame counts. | "How good does the operator's view look" can be reported as "92 % complete frames" but not as a fidelity number. |
| **`receiver_latency_ms` stops at decoder output.** Display render time is excluded. | Glass-to-glass latency is unknown. The headline teleop number is not measured. |
| **One-shot time sync only.** `TREQ/TRES` is fired at startup; there is no continuous skew tracking. | One-way delay drifts with clock skew over minutes-long runs. |
| **No L4S / ECN.** Outgoing packets do not set ECT(1); incoming ECN bits are ignored. | The lowest-latency operating point of any modern CC is unreachable. |
| **No RTP, so no SRTP/DTLS.** The custom UDP wire format forecloses standard encryption. | Production teleop deployments need encryption; this design cannot give it without redesigning the wire. |
| **No multi-path or per-stream DSCP.** | Mobile teleop typically needs at least DSCP marking so the radio scheduler prioritises video. |

### 11.2 Design gaps

| Gap | Why it limits the design |
|---|---|
| **The controllers cannot pace; they can only resize.** Because the sender is unconditioned blast, the only knob a controller has is "encoder bitrate" / "encoder fps". This means controllers cannot smooth a frame within an interval; they can only avoid sending too much in the next one. Any real SCReAM/GCC implementation paces between the controller decision and the wire — this one cannot. |
| **No congestion window.** With no window, there is no notion of "how much is in flight." After a 200 ms feedback gap (the RFBK interval), the controller has no idea whether the link is empty, full, or already overrun. SCReAM and GCC both depend on knowing this; UMN's versions cannot. |
| **Fixed-timer controllers, not ack-clocked.** The fastest the SCReAM-style controller can react is 100 ms; GCC-style is 200 ms. A real `gstscream` reacts on every feedback message (sub-RTT). |
| **One thread per stream + one shared socket.** With six streams blasting concurrently into one UDP socket and no priority awareness, the sender can re-order or drop packets across streams in ways the controller has no model for. |
| **Single-threaded receiver with select().** Fine for moderate rates; binds reassembly + decode-driving + feedback emission to one event loop. Under load, all three contend. |
| **Frame loss detection is per-frame, not per-packet.** No packet sequence number. Loss within a frame is observed at the moment of frame completion; loss between frames is only inferable statistically. A real-time CC working from this view will see loss with a one-frame minimum granularity. |
| **No schema validation on config.** Typos load silently. The system runs on whatever the YAML happened to parse to. |
| **Adaptive resolution decoupled from CC.** Resolution comes from a static policy file keyed by `frame_id`; the controller drives bitrate factor only. A 5G handover that drops capacity 10× cannot trigger a resolution drop — only an encoder QP and FPS reduction. |
| **RAN telemetry exists but is not used.** The `src/ran/` gRPC stack reports radio state outward. Nothing reads RSRP / CQI / handover state into rate control, even though the design has every other necessary part. |
| **No active cross-stream synchronization.** Each stream's thread runs independently. The wire carries the camera hardware timestamp, but the receiver does not align streams to a shared display clock. |
| **Tk + Pillow display copies every frame.** No VSync, no zero-copy, no HMD path. Fine for development; not a production operator sink. |

### 11.3 Operational / methodology gaps

| Gap | Consequence |
|---|---|
| **Default configuration is open-loop.** `adaptive_bitrate: false` in ~84/100 shipped configs. The system, as shipped, mostly does not adapt at all. | A naive "we ran the UMN system on this link" measurement is almost certainly a no-adaptation run unless the operator explicitly turned the allocator on. |
| **The shipped controllers are reimplementations, not reference algorithms.** Calling the result "SCReAM vs. SCReAM" against a real Ericsson `gstscream` is unfair both ways: any difference is between two different algorithms in addition to any genuine implementation gap. | This is the central caveat behind any A/B comparison against GstreamerExp — see `umn-teleop-divergence-analysis.md`. |
| **Experiments are ad-hoc subprocesses.** No rep counter, no parameter sweep, no pass/fail verifier. Analysis is post-hoc matplotlib over JSONL. | Reproducing a number across runs requires the operator's discipline; the framework does not enforce it. |
| **No silent-fallback discipline outside the encoder.** The encoder path detects silent hardware-codec fallback. Nothing checks whether the kernel actually accepted the configured `tc` qdisc, whether the receiver actually bound the configured port, whether `mm-link` actually shaped traffic, etc. | A misconfigured run produces plausible-looking but wrong numbers. |
| **Dual-end forensic recording is good — and rare.** `save_encoded.py` on both sides writes the byte-identical encoded stream, with codec/framerate sidecars, for incident replay. Strongly designed and worth lifting. | This is the one area where UMN's operational discipline clearly *exceeds* the testbed's. Surfaced here because §11 should call out what works, not only what does not. |

---

## 12. Compared to GstreamerExp (ours)

The two repos are different *kinds* of artifact: UMN is a teleop product
that ships a streamer; GstreamerExp is a controller testbed that ships a
measurement harness. They are not interchangeable. But it is still
useful, once §11's gaps are on the table, to lay them next to ours so
the asymmetry is explicit. This is a compressed view; the full
feature-by-feature matrix is in
[`umn-teleop-gopher-comparison.md`](umn-teleop-gopher-comparison.md).

### 12.1 Where GstreamerExp is ahead (UMN gaps that ours fills)

| Area | Ours | UMN |
|---|---|---|
| **Congestion controllers are the reference algorithms.** | Ericsson `gstscream` (real SCReAM) and `rtpgccbwe` (real GCC), wired in as GStreamer elements. | "SCReAM-inspired" 100 ms AIMD loop (226 LoC) and "GCC-style" 200 ms reimplementation. Same family of signals, none of the algorithmic structure. |
| **The controller paces packets.** | SCReAM / `rtpbin` self-clock; transmission gated by ack-driven pacing. | Blast-send (`MSG_DONTWAIT`); controllers can only resize the next frame, not smooth the current one. |
| **Standard RTP / RTCP transport.** | RTP on the wire, RTCP feedback, SRTP/DTLS drop-in. | Custom UDP, no sequence numbers, no RTCP, no encryption path. |
| **Per-packet loss recovery.** | NACK + RTX and ULPFEC orthogonal; PLI when a keyframe is needed. | KFRQ keyframe request only. No NACK, no FEC. Partial-frame decode is the one nontrivial concealment. |
| **Reference quality metrics.** | Decoded PSNR, eight-probe per-stage latency, decoder-error accounting. | Complete / partial / dropped frame classification; no PSNR / SSIM / VMAF. |
| **Declarative validated specs.** | Specs in `specs/`, schema-validated at load, hypothesis framework with pass/fail verifiers. | YAML loaded into dataclasses with no schema validation; experiments are ad-hoc subprocess scripts. |
| **Declarative network impairment.** | `tc` (tbf + netem) compiled from `specs/networks/`, including translated Mahimahi 5G traces. | `mm-link` trace replay, but no first-class impairment specs. |
| **Hypothesis framework.** | `analysis/hypotheses/` with H1–H5 and explicit verifiers (the index page links each one). | No equivalent — analysis is post-hoc matplotlib over JSONL. |

### 12.2 Where UMN is ahead (gaps in ours)

| Area | UMN | Ours |
|---|---|---|
| **Codec breadth.** | VP9, H.264, HEVC, AV1 — software and hardware. | VP8 only (`vp8enc`). |
| **Hardware encode (with silent-fallback detection).** | NVENC, QSV, VAAPI paths in `encoder.py`, including a separate `_VaapiSubprocessEncoder` for `hevc_vaapi`. Stderr-sniff for silent SW fallback. | No hardware encode wired in yet. GStreamer's HW elements are available; the integration is the gap. |
| **Real-camera capture.** | FLIR Blackfly S GigE via PySpin with hardware-timestamp chunk mode (`camera_timestamp_ns`) and multi-camera rigs. | `videotestsrc` / `filesrc` only. |
| **Adaptive resolution mechanism.** | Encoder pre-builds scalers for every candidate resolution and switches at a frame boundary, driven by the policy file. | No resolution adaptation at all — only `target-bitrate`. |
| **Partial-frame decode.** | Receiver hands the contiguous prefix from packet 0 to the decoder when the tail is missing. A real error-concealment strategy. | Frames with missing packets are dropped. |
| **Multi-window / multi-monitor display layout.** | `WindowConfig` per stream (x, y, w, h, fullscreen) → 2×3 grid across monitors out of the box. | `autovideosink` for the expo specs; no layout config. |
| **Dual-end forensic bitstream recording.** | `transmitter/save_encoded.py` and `receiver/save_encoded.py` write byte-identical encoded streams on both ends, no re-encode, with codec/framerate sidecars. | Not yet — and this one is a near-direct adoption candidate via a GStreamer `tee` → `filesink`. |
| **Inline per-frame metadata on the wire.** | v4 header carries source timestamp, camera hardware timestamp, GPS lat/lon/alt, RTK status. | RTP header extensions are the standard equivalent; not wired yet. |
| **Multi-stream priority-ordered degradation ladder.** | The `BandwidthAllocator`: drop FPS, then quality, then turn off, lowest priority first. | Single-flow testbed; no multi-stream allocation policy. |
| **Vehicle integration surface.** | Joystick / Zenoh / ROS / RAN gRPC / Maps trajectory bundled in the same repo. | Out of scope for the testbed. |

### 12.3 Axes the comparison does not carry

A small number of axes are not really comparable because the two
projects are built for different jobs.

- **Experiment harness vs. vehicle integration.** GstreamerExp's
  hypothesis framework and validated specs do not have a counterpart in
  UMN, and UMN's vehicle-controls / RAN / trajectory subsystems do not
  have a counterpart in ours. Each is load-bearing for its own purpose.
- **One flow vs. multi-stream.** GstreamerExp instruments one video
  flow to death. UMN runs 1–6 streams with cross-stream priority. The
  multi-stream allocator design and the testbed's per-frame metrics
  rigour are roughly orthogonal pieces of engineering.
- **Default-on vs. default-off adaptation.** Our controllers are on
  whenever the spec selects them. UMN's controllers are gated behind
  `congestion_controller` *plus* `adaptive_bitrate: true`, and the
  latter is `false` in ~84 of 100 shipped configs. The shipped UMN
  artifact is mostly open-loop by default; we are not. A naive A/B
  reading easily misses this.

The headline: **ours is a measurement instrument with a thin codec/transport
surface; theirs is a teleop product with a thin measurement surface.** The
load-bearing things ours does better — real CC, pacing, RTP, recovery,
metrics, hypothesis framework — line up with what makes a *controller
comparison trustworthy*. The load-bearing things UMN does better — codec
breadth, hardware encode, real cameras, multi-stream, vehicle integration
— line up with what makes a *teleop deployment workable*. Picking a
"winner" without saying which question you are answering will produce a
confident wrong answer.

---

## 13. Source map (file → role)

For navigation. All paths are relative to the repo root
(`reference/external/teleop-gopher-streamer/`).

### Transmitter
- `src/gopher_streamer/transmitter/main.py` — process entry, thread orchestration, controller selection (lines ~1004–1056).
- `src/gopher_streamer/transmitter/encoder.py` — per-stream encoder loop. The `encode_stream()` function (line 779) is the entry point; `_VaapiSubprocessEncoder` (line 277) is the VAAPI side-path.
- `src/gopher_streamer/transmitter/sender.py` — `send_frame()` (line 17) packetizes + blasts.
- `src/gopher_streamer/transmitter/scream_controller.py` — `ScreamCongestionController` (line 32).
- `src/gopher_streamer/transmitter/google_congestion_controller.py` — `GoogleCongestionController`.
- `src/gopher_streamer/transmitter/bandwidth_allocator.py` — `BandwidthAllocator` (line 67), `DEGRADATION_STEPS` (line 26).
- `src/gopher_streamer/transmitter/throughput.py` — `ThroughputEstimator` (line 62), Mahimahi-trace integration (line 447).
- `src/gopher_streamer/transmitter/receiver_feedback.py` — `ReceiverFeedbackState`, shared snapshot for controllers.
- `src/gopher_streamer/transmitter/smart_buffer.py` — `SmartBufferCoordinator` (Linux low-latency send path).
- `src/gopher_streamer/transmitter/gps.py` / `ros_gps.py` — serial / ROS GNSS readers.

### Receiver
- `src/gopher_streamer/receiver/main.py` — process entry.
- `src/gopher_streamer/receiver/reassemble.py` — `reassemble_frames_with_jitter()` (line 279) is the central loop. `PendingFrame` (line 33) accumulates packets; `FeedbackStats` (line 71) drives RFBK.
- `src/gopher_streamer/receiver/decoder.py` — PyAV decode, `receiver_latency_ms` calculation.
- `src/gopher_streamer/receiver/display.py` — Tk + Pillow window grid, layout config consumer.
- `src/gopher_streamer/receiver/save_encoded.py` — on-operator bitstream recording.

### Shared
- `src/gopher_streamer/shared/protocol.py` — wire format (header, `Packet`, `ReceiverFeedback`, KFRQ / TREQ / TRES / RFBK).
- `src/gopher_streamer/shared/config.py` — dataclasses + YAML loader.
- `src/gopher_streamer/shared/frame_metadata.py` — `FrameMetadata` (GPS + camera-timestamp struct).
- `src/gopher_streamer/shared/udp_buffers.py` — socket buffer tuning, OS-limit warnings.
- `src/gopher_streamer/shared/metrics.py` — `CsvEventLogger`, `JsonlEventLogger`.

### Experiment + analysis
- `src/gopher_streamer/experiment/main.py` — multi-process runner.
- `src/gopher_streamer/plotting/` — post-hoc analysis scripts (`analyze_receiver_log.py`, `plot_throughput.py`, `plot_delivery_times.py`, `plot_latency.py`).

### Capture
- `src/gopher_streamer/cameras/spinnaker_capture.py` — FLIR Blackfly via PySpin, hardware-timestamp extraction.

### Teleop subsystems (outside the video pipeline)
- `src/vehicle_controls/` — Zenoh + ROS joystick → `cmd_vel`.
- `src/ran/` — RAN gRPC stubs and modes.
- `src/telemetry/` — ROS GNSS sender, RAN-state gRPC sender.
- `src/trajectory/` — Google-Maps route playback.

---

## Reading order if you are new to the repo

1. `README.md`
2. `src/gopher_streamer/shared/protocol.py` — start here; everything
   else is shaped by the wire format.
3. `src/gopher_streamer/shared/config.py` — the dataclass shape is the
   ground truth for what the system can be told to do.
4. `src/gopher_streamer/transmitter/sender.py` — short; tells you what
   the on-the-wire send path actually does.
5. `src/gopher_streamer/receiver/reassemble.py` — long but the only
   nontrivial loss-handling logic.
6. `src/gopher_streamer/transmitter/scream_controller.py` and
   `bandwidth_allocator.py` — small, side-by-side.
7. `src/gopher_streamer/transmitter/main.py` — only after the above; it
   is wiring, and most of the wiring will be obvious by then.
