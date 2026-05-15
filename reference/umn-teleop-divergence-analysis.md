# Why GstreamerExp and UMN Teleop-Gopher diverge

A companion to [`umn-teleop-gopher-comparison.md`](umn-teleop-gopher-comparison.md).
That note is a *gap survey* — for each `docs/TODO.md` item, what UMN does.
This note answers the next question: **why** do the two systems differ
at all, and which of the surface differences are independent choices
versus forced consequences of one upstream decision.

It has two parts:

1. **Root-cause divergence analysis** — the ~20 surface differences
   collapse onto four foundational decisions. Trace the cascade.
2. **Gap severity + cost ranking** — for each gap, how load-bearing it
   is for real teleoperation and what closing it would cost us.

UMN file references are pinned to upstream commit `7c585c2`.

---

# Part 1 — Root-cause divergence analysis

## The trap: treating surface differences as independent

The gap survey lists differences one per row — codec, transport,
recovery, capture, display, evaluation — as if each were a separate
decision someone made. It is not. Most of those rows are *entailed* by
a much smaller set of choices. If you "fixed" the codec row without
touching the decision upstream of it, you would be fighting the
architecture. The useful model is a dependency tree, not a list.

Four foundational decisions explain almost everything:

```
  D1  Purpose:      operate a vehicle   vs   measure a controller
       │
       ├──────────────┬─────────────────────┐
       ▼              ▼                     ▼
  D2 Framework    D-eval Evaluation     D-scope Scope
  PyAV vs          ad-hoc scripts vs    controls/RAN/GPS
  GStreamer        hypothesis framework  vs one video flow
       │
       ▼
  D3 Transport:  custom UDP   vs   RTP
       │
       ▼
  D4 CC signal:  local qdisc state   vs   path feedback (RTCP)
```

Read top to bottom: **D1 is a genuine choice. D2 is mostly a choice.
D3 is largely forced by D2. D4 is largely forced by D3.** By the time
you reach the surface gaps, almost nothing is independent.

## D1 — Purpose: operate a vehicle vs measure a controller

This is the only decision that is fully free, and it is the one that
matters most.

**UMN is building a system that operates a teleoperated vehicle.** The
artifact's job is to put a drivable video feed in front of a remote
operator, with vehicle controls and telemetry alongside it. Success is
"the operator can drive."

**GstreamerExp is building an instrument that measures a congestion
controller.** The artifact's job is to produce falsifiable, reproducible
claims about how SCReAM and GCC behave under stress. Success is "the
hypothesis verifier returns a verdict that re-derives from `runs/`."

Everything else follows from which sentence you are trying to make true.
Three immediate consequences branch straight off D1:

- **D-scope.** Operating a vehicle requires cameras, GPS, vehicle
  controls (Zenoh + ROS), RAN telemetry, trajectory/routing — UMN has
  all of it. Measuring a controller requires exactly one video flow,
  instrumented exhaustively — we have only that. Neither project is
  missing the other's subsystems by oversight; each built what its
  sentence required.

- **D-eval.** When success is "the operator can drive," evaluation is a
  demo: run it, watch it, plot the logs
  ([`experiment/main.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/experiment/main.py)
  spawns transmitter + receiver subprocesses and nothing more;
  `analyze_policies.py` and `plotting/` parse logs after the fact).
  When success is "the claim is falsifiable," evaluation must be a
  framework: declarative validated specs, a hypothesis catalog, verifier
  scripts that return supported/refuted/inconclusive. Our `specs/`,
  `analysis/hypotheses/`, and the five-dimension framework are not
  rigor for its own sake — they *are* the deliverable. UMN has no
  schema validation and no pass/fail verifier because a vehicle that
  drives does not need one.

- **D2 — framework choice.** This is the consequential branch; it gets
  its own section.

The honest framing for any future comparison: **the two projects do not
disagree about how to build a teleop streamer. They are not building the
same thing.** UMN's surface is wide and shallow by necessity — many
subsystems, each at demo maturity. Ours is narrow and deep by necessity
— one subsystem, instrumented past the point any operator would need.

## D2 — Framework: PyAV vs GStreamer

UMN builds on **PyAV** — Python bindings to FFmpeg/libav. We build on
**GStreamer 1.24**. This is mostly a free choice (a team fluent in
Python, moving fast, reaches for PyAV), but it is the decision that
forces the most downstream.

What PyAV gives UMN: direct, frame-level control in Python. An
`av.VideoFrame` is a Python object; you can attach metadata to
`frame.opaque`, splice in a custom allocator, hand bytes to a custom
protocol — all in application code, no plugin to write. That is why
their bandwidth allocator, smart buffer, and custom protocol exist *as
ordinary Python modules*.

What PyAV does **not** give UMN: a media-transport ecosystem. libav
decodes and encodes; it does not do RTP, RTCP, congestion control, or
FEC. Those are not in the box.

What GStreamer gives us: that entire ecosystem as pre-built elements.
`rtpvp8pay`/`rtpvp8depay`, `rtpbin`, `rtprtxsend`/`rtprtxreceive`,
`rtpulpfecenc`/`rtpulpfecdec`, the `gstscream` plugin, `rtpgccbwe` — RTP,
both congestion controllers, NACK/RTX, PLI, and FEC all ship as
elements we wire together. We did not build SCReAM or GCC; we declared
them in a spec.

What GStreamer costs us: rigidity. The pipeline is a negotiated graph,
not a Python loop. Splicing in something the framework did not
anticipate means writing an element or an `appsrc` bridge.

**The cascade.** Because UMN chose PyAV, they had no RTP and no
congestion controller available — so they *had to* hand-roll transport
(D3) and rate control (D4). Their custom protocol and custom allocator
are not ambition; they are the bill for D2. Because we chose GStreamer,
SCReAM, GCC, RTP, and FEC arrived for free — which is *why* a
congestion-control testbed could even be built at this depth by a small
effort. D2 is the hinge of the whole tree.

## D3 — Transport: custom UDP vs RTP

UMN's
[`shared/protocol.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/shared/protocol.py)
is a custom UDP framing: an 11-to-37-byte header with `stream_id`,
`frame_id`, `packet_index`, `total_packets`, and flag-gated extensions.
We use standard RTP via GStreamer payloaders.

This is **largely forced by D2** — PyAV ships no RTP, so a PyAV project
that wants to send video over UDP either writes RTP from scratch or
writes something simpler. UMN wrote something simpler. But part of D3
is a genuine choice with a genuine upside, and it is worth being fair
about it:

- **The cost.** No SRTP/DTLS (our TODO encryption item is unreachable
  for them without re-doing transport), no standard interop, no
  RTCP feedback channel.
- **The upside.** Their header carries a transmit timestamp, the source
  frame id, and inline GPS/camera metadata as first-class fields. In
  RTP that same metadata needs header extensions and more plumbing.
  For a project whose frames *are* vehicle telemetry carriers, inlining
  the metadata is ergonomically reasonable.

The decisive downstream consequence: a custom protocol with no RTCP
means **no standard receiver→sender feedback channel**. They added a
narrow one anyway — keyframe requests (KFRQ) and a time-sync exchange
(TREQ/TRES) — but there is no per-packet acknowledgement, no loss
report, no receiver-estimated-bitrate message. That directly sets up D4.

## D4 — Congestion-control signal: local qdisc vs path feedback

This is the deepest point and the one most relevant to *our* project,
so it gets the most space.

SCReAM and GCC are **path-feedback controllers**. They learn about the
bottleneck from the receiver: RTCP carries acknowledgements, loss
reports, and timing back to the sender, and the controller infers
queue delay (SCReAM) or delay gradient (GCC) from that feedback. The
controller sees the path.

UMN's controller cannot see the path, because D3 left it with no
feedback channel. So
[`throughput.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/transmitter/throughput.py)
does the only thing still available: it reads the **local sender
qdisc**. It polls `tc qdisc show` for bytes sent, drops, and backlog,
and — critically — only updates its capacity estimate when
`saw_pressure` is true, i.e. when the local queue already shows backlog
or drops (`throughput.py` lines 416-424). The estimator learns capacity
*during* congestion, from the sender's own queue, never from the path.

Two hard consequences fall out, and both showed up in the gap survey as
separate rows without the cause being named:

1. **It assumes the bottleneck is the sender's own qdisc.** True on a
   shaped lab link where `tc`/`mm-link` *is* the bottleneck. Not true on
   a real cellular uplink, where the bottleneck is in the radio or the
   carrier network and the sender's local qdisc may never fill. On a
   real network UMN's estimator can be blind to the actual constraint.
   SCReAM and GCC infer the bottleneck wherever it sits, because path
   feedback reports the queue that actually formed.

2. **It is slow.** A 1 Hz poll plus EWMA smoothing
   ([`bandwidth_allocator.py`](https://github.com/GopherNetLab/Teleop-Gopher-streamer/blob/7c585c2/src/gopher_streamer/transmitter/bandwidth_allocator.py)
   line 90; `_MIN_SAMPLES_BEFORE_ACTION = 4`, line 38) gives a 1-2 s
   reaction. SCReAM reacts in 100-500 ms, GCC in 50-200 ms. This is the
   same axis our H1 found decisive: when network transitions are faster
   than the controller's loop, the controller never reaches steady
   state. UMN's loop is the slowest of the three by an order of
   magnitude.

There is one nuance worth stating precisely, because it keeps D4 honest:
UMN's protocol *does* put a transmit timestamp on the wire (`FLAG_TX_TS`)
and the receiver *does* know `tx_unix_ms`, so the raw material for a
one-way-delay signal exists. They use it for latency *logging*, not for
control. So D4 is not 100% forced — a delay-based controller was
*reachable* — but the simple path, given a shaped lab link where the
local qdisc genuinely is the bottleneck, is to read the qdisc. They took
it.

**What D4 is genuinely good at.** One thing in UMN's controller is not a
constraint-driven compromise and is a real idea: the **priority-ordered
multi-stream degradation ladder** (`bandwidth_allocator.py` lines 26-34,
179-229). SCReAM and GCC each produce *a rate*. With N camera streams,
something must decide which stream gives up bitrate first. SCReAM and
GCC do not answer that — it is out of their scope. UMN's allocator *is*
that answer: a fixed ladder (drop FPS, then quality, then shut the
stream off), applied lowest-priority-first. That is a layer that could
sit on top of a SCReAM or GCC rate just as well as on top of a qdisc
estimate — which is exactly what issue #6 proposes to test.

## The cascade, stated as one paragraph

UMN set out to operate a vehicle (D1). That required a wide subsystem
scope (D-scope) and made a demo-style evaluation sufficient (D-eval).
Operating a vehicle in Python, fast, led to PyAV (D2). PyAV ships no
RTP, so transport had to be hand-rolled as custom UDP (D3). Custom UDP
has no RTCP, so the rate controller had no path feedback and fell back
to reading the local sender qdisc (D4) — a signal that is fine on a
shaped lab link and unreliable on a real radio, and a loop an order of
magnitude slower than SCReAM or GCC. Every surface gap in the
companion note — no SRTP, no NACK/FEC, slow adaptation, no reference
quality metric, ad-hoc experiments — is a leaf on that four-node tree.
**The codec matrix and the real cameras are the only major UMN
strengths that are not consequences of D1-D4 — they are consequences of
D-scope, i.e. of actually having a vehicle.**

## What this means for us

- **Do not "adopt UMN's transport" or "adopt UMN's CC."** Those are D3
  and D4 — the forced-compromise nodes. Adopting them means importing
  the constraint that produced them. We are on the other branch of D2
  precisely so we do not have to.
- **Do adopt UMN's D-scope leaves** where they are portable: real-camera
  capture, hardware encode, dual-end recording, the degradation ladder.
  Those are the parts UMN has because it has a vehicle, not because it
  has a constraint. Issues #4-#7 already target exactly these.
- **The one idea worth lifting from the forced-compromise side** is the
  multi-stream degradation ladder (issue #6) — but lift it *off* its
  qdisc signal and *onto* a SCReAM/GCC rate. That keeps the good part
  (a stream-priority policy) and drops the bad part (a blind, slow
  signal).

---

# Part 2 — Gap severity + cost ranking

For each `docs/TODO.md` gap: how load-bearing it is for *real*
teleoperation (not for the testbed), what it would cost us to close in
our GStreamer pipeline, and whether UMN already has a portable answer.

**Severity** — for actual teleoperated driving: Critical / High /
Medium / Low.
**Cost** — to close in our pipeline: Low (days) / Medium (1-2 weeks) /
High (weeks, or needs hardware).
**UMN** — do they have a portable answer? Yes / Partial / No.

| Gap | Severity | Cost | UMN | Issue |
|---|---|---|---|---|
| Glass-to-glass latency measurement | Critical | Medium | Partial | — |
| Real-camera capture + frame-clock metadata | Critical | High (hardware) | Partial | #5 |
| Decoder error concealment / partial-frame decode | High | Medium | Yes | #7 |
| Adaptive resolution (congestion-triggered) | High | Medium | Partial | — |
| Dual-end forensic bitstream recording | High | Low | Yes | #4 |
| Hardware-accelerated encode | High | Medium | Yes | — |
| Multi-camera support | High | Medium | Yes | — |
| Latency-budget enforcement (drop, don't queue) | High | Low | No | — |
| Real display sink (glimagesink / framebuffer / HMD) | High | Medium | Partial | — |
| Encryption (SRTP/DTLS) | High | Low | No | — |
| Multi-stream camera synchronization | Medium | High | Partial | — |
| Operator-side jitter buffer w/ explicit policy | Medium | Medium | Partial | — |
| Per-camera composited view (mosaic / PiP) | Medium | Low | Partial | — |
| Quality-degradation indicator to operator UI | Medium | Low | No | — |
| Audit-grade frame-loss accounting | Medium | Low | Partial | — |
| SSIM / VMAF quality metrics | Medium | Medium | No | — |
| Authentication (operator station identity) | Medium | Medium | No | — |
| Vehicle wall-clock timestamps in RTP headers | Medium | Low | Partial | — |
| Scene-change detection + forced keyframe | Medium | Medium | No | — |
| I-frame frequency tuned to recovery time | Medium | Low | No | — |
| Redundant / multi-path transport (LTE+5G) | Low–High* | High | No | — |
| Region-of-interest / variable quality | Low | High | No | — |
| Ambient audio from the vehicle | Low | Medium | No | — |
| Camera calibration intrinsics/extrinsics | Low | Low | No | — |
| HDR / wide-color-gamut preservation | Low | High | No | — |
| Per-stream priority / DSCP marking | Low | Low | No | — |

\* Multi-path matters enormously for a *production* teleop vehicle but
is Low priority for *this testbed's* current question.

## Priority tiers

**Tier 1 — close next.** High or Critical severity, Low or Medium cost,
and (mostly) a portable UMN answer to copy from.

- **Dual-end forensic recording** (#4) — High severity, Low cost, direct
  GStreamer adoption. Best effort-to-value ratio on the board. Do first.
- **Partial-frame decode** (#7) — High severity, Medium cost, real UMN
  idea. Also our cheapest move toward the open "error concealment" item.
- **Latency-budget enforcement** — High severity, Low cost, *no* UMN
  help — but it is nearly free for us: drop a frame whose deadline has
  passed instead of queueing it. The `latency_budget` field already
  exists in the spec; this is wiring, not design.
- **Glass-to-glass latency** — Critical severity. Medium cost and
  unblocked the moment real-camera capture (#5) lands a hardware
  timestamp at the capture end; the viewer-side probe is the easy half.

**Tier 2 — real teleop value, but gated on hardware or a bigger build.**

- **Real-camera capture** (#5) and **hardware encode** — both High, but
  both need a physical camera / encoder on the bench. Issue-tracked;
  schedule when hardware is available.
- **Multi-camera + multi-stream sync** — High/Medium severity. This is
  the prerequisite for issue #6 (the degradation ladder) and for a
  per-camera composited view. A genuine multi-week build.
- **Adaptive resolution** — High severity. UMN has the *mechanism*
  (pre-scale, switch at frame boundary); we still have to build the
  *trigger* (drive it from SCReAM `current-max-bitrate` / GCC
  `estimated-bitrate`). The trigger is the research-interesting half and
  belongs to us regardless.

**Tier 3 — defer.** Low severity for the testbed's current question, or
High cost with no near-term payoff: HDR, region-of-interest, ambient
audio, redundant/multi-path transport, DSCP marking, camera calibration
passthrough. Record them; do not schedule them.

## The ranking's one surprise

The gap that scores best on effort-to-value — **dual-end forensic
recording** — is also the one where UMN's solution is most directly
portable, and it is *not* a congestion-control feature at all. The gaps
that are most central to our project's identity — adaptive resolution
driven by the CC, latency-budget enforcement — are exactly the ones
where **UMN has no answer or only half of one**, because they sit on
our side of the D2 split. That is the ranking restating Part 1: UMN
helps most with the vehicle-reality leaves (D-scope) and least with the
controller-behaviour core (D4). Adopt accordingly.

---

# Phase D — empirical comparison: feasibility

The fourth investigation angle was to run both stacks under the same
network trace and *measure* where behaviour diverges. Feasibility was
assessed; the verdict is **feasible but non-trivial, and gated on a
WSL2 risk** — recommend a decision before committing.

**In favour.** UMN ships `traces/mahimahi/{cqi,ho,rb}.trace` — the same
5G trace families our H3/H4 already use. That is a real apples-to-apples
basis: both stacks, same CQI/HO/RB capacity profile. UMN's
`experiment/main.py` already wraps the transmitter in `mm-link`, and
`configs/modes/video-only.yaml` disables the camera/ROS/Zenoh/RAN
subsystems, so a file-source video-only run is a supported path.

**Friction.**
- `poetry` and `mahimahi` are both not installed on this machine.
- `mm-link` uses Linux network namespaces; **WSL2 support for mahimahi
  is historically unreliable** — this is the real risk and should be
  smoke-tested before any other effort.
- UMN's sample configs use `hevc_nvenc` (needs NVENC hardware); a run
  here must switch to a software codec (`libx265` / `libvpx-vp9`).
- UMN needs a video file (`data/scene_010/videos/front_left.mp4` is
  gitignored and absent); we would supply our own.
- Metrics are defined differently — UMN's `receiver_latency_ms`
  (source→decode) vs our `frame_latency` (RTP-boundary) — so a fair
  comparison needs an agreed common metric, most likely
  delivered-frame-count and camera-egress bitrate, which both stacks
  report cleanly.

**Recommendation.** Do Phase D as a *separate, time-boxed spike*: first
a 30-minute check that `mm-link` runs at all on this WSL2 host. If it
does, a one-trace comparison (HO trace, software codec, file source) is
perhaps a day of work and would directly corroborate the H1/H4-style
claim that UMN's 1 Hz qdisc loop adapts visibly slower than SCReAM. If
`mm-link` does not run on WSL2, Phase D needs a real Linux host and
should be deferred rather than worked around. Tracked on issue #1; not
started.
