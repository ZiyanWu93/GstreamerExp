# TODO — Functional gaps for teleoperation video

What's currently in scope: VP8 → RTP → (SCReAM | GCC | bare) → UDP over
loopback or between two real hosts, with `tc tbf` + `netem` impairment
(uniform iid or Gilbert-Elliott bursty loss) driven by `during_run`
hooks. Synthetic input (`videotestsrc`) or file-backed input
(`filesrc → decodebin`) on the camera side; measurement sink is
`fakesink` or `filesink` (visual rendering via `autovideosink` lives
in expo specs, not configurations). Recovery primitives NACK + RTX,
PLI, and ULPFEC are implemented and orthogonal. Per-frame Y-plane PSNR
against a reproduced source is the working quality metric.

What's missing for an actual teleoperation video setup, ignoring the
operator-control return path:

## Capture
- [ ] Real video from vehicle cameras (replace `videotestsrc`)
- [x] Multi-camera support (front / rear / sides; teleop typically ≥3 streams) — configurations carry a `streams:` list; a run spawns 2N workers (N cameras on one host, N viewers on the other), each stream with its own encoder/CC/recovery, port pair, and shaped tc lane. See specs/configurations/400.yaml (3-cam teleop).
- [ ] Hardware encoder on the vehicle side (V4L2 / NVENC / VAAPI)
- [ ] Camera timestamp / frame-clock metadata (for sensor fusion, AR overlays, replay)
- [ ] Camera calibration intrinsics/extrinsics passthrough

## Encoding
- [ ] Hardware-accelerated encoding path
- [ ] Adaptive resolution — drop from 720p to 480p/360p when the rate-controller cuts the bitrate budget below the threshold where the higher resolution can hold quality. vp8enc currently has only `target-bitrate` as a knob, so under tight bottlenecks it produces blocky 720p (worst-case ~10 dB PSNR observed) instead of clean 360p. Encoder-side control loop driven by SCReAM's `current-max-bitrate` notify or GCC's `estimated-bitrate`; reconfigures vp8enc + the source capsfilter via runtime renegotiation.
- [ ] Region-of-interest / variable quality across the frame (more bits on the road, fewer on the sky)
- [ ] Scene-change detection and forced-keyframe-on-cut
- [ ] HDR / wide-color-gamut preservation (taillight visibility, low-light scenes)

## Transport
- [ ] Encryption (SRTP / DTLS)
- [ ] Authentication (operator station identity)
- [ ] Redundant transport / multi-path (LTE + 5G failover)
- [ ] Per-stream priority / DSCP marking
- [x] Loss recovery on top of SCReAM: NACK + RTX, PLI, ULPFEC — all three landed and orthogonal

## Reception
- [ ] Real display sink (replace `fakesink` with `glimagesink` / direct framebuffer / HMD)
- [ ] Display sync (VSYNC alignment so frames present without tearing)
- [ ] Per-camera composited view (mosaic / picture-in-picture)
- [x] Multi-stream synchronization across cameras (so they show the same instant) — shared-epoch start barrier (sync.mode: shared_epoch) releases all cameras' first frame together (homogeneous streams measured at 0.9 ms median inter-stream skew, down from ~400 ms), plus a `sync_error` metric that reports residual inter-stream presentation skew. Measured same-host so the clock-skew artifact cancels exactly. (PTP-disciplined absolute capture timestamps remain a deferred seam — sync.mode: ptp.)

## Latency
- [ ] Glass-to-glass latency measurement (capture → display) — the headline metric for teleop
- [x] Per-stage latency attribution — `stage_latency` metric, 8 probes (encoder.sink/src, pay.src, udpsink.sink, udpsrc.src, depay.src, decoder.sink/src, convert.src) joined by RTP timestamp; `analysis/dimensions/latency.py` surfaces per-stage durations
- [ ] Latency-budget enforcement (drop frames when budget exceeded, don't queue)
- [ ] Operator-side jitter buffer with explicit depth and policy

## Quality
- [x] Per-frame quality metric — `decoded_psnr` (Y-plane PSNR vs reproduced source); SSIM / VMAF still missing
- [ ] Quality degradation indicator surfaced to the operator UI
- [ ] Audit-grade frame loss accounting (which frames, when, why)

## Audio
- [ ] Ambient audio from the vehicle (sirens, horns, impacts) — often safety-relevant

## Recording / forensics
- [ ] On-vehicle and on-operator recording of the actual streamed bitstream (for incident replay)
- [ ] Synchronized multi-camera + telemetry capture
- [ ] Lossless local copy alongside the lossy transmitted copy (so post-incident analysis isn't degraded)

## Synchronization
- [ ] Vehicle wall-clock timestamps in RTP headers (NTP / PTP-aligned)
- [x] Cross-stream sync (camera N's frame at time T arrives with the same T across all cameras) — shared-epoch start barrier aligns the cameras' release (same-host, so relative alignment is exact); the `sync_error` metric measures the residual. On heterogeneous paths the residual is genuine per-path delivery skew (e.g. SCReAM-clean vs GCC-50ms arriving up to ~350 ms apart), which is a real teleop finding, not start jitter.

## Resilience to loss
- [ ] Decoder error concealment / freeze-on-bad-frame strategy
- [x] Keyframe-on-loss request from receiver — PLI wired both sides (do-lost on rtpjitterbuffer → AVPF feedback)
- [x] Per-packet retransmit on loss — NACK + RTX wired via rtprtxsend / rtprtxreceive
- [x] Proactive redundancy on the wire — ULPFEC (rtpulpfecenc on camera, rtpstorage + rtpulpfecdec on viewer)
- [ ] I-frame frequency tuned to recovery time, not just to bitrate efficiency
