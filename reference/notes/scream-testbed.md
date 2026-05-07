# SCReAM Teleoperation Testbed

## Purpose

The testbed is a software-first environment for validating low-latency teleoperation video over variable cellular-style links. It combines GStreamer media pipelines, SCReAM congestion control, RTP/RTCP feedback, and Linux network emulation so the transport behavior can be tested before moving to cameras, hardware encoders, modems, or vehicles.

## Core Idea

The sender captures or generates video frames, encodes them, packetizes them as RTP, passes them through `screamtx`, and sends them over UDP. The receiver accepts RTP packets through `screamrx`, generates RTCP feedback, depacketizes, decodes, and renders or discards frames depending on the experiment. SCReAM adjusts pacing and target bitrate from the feedback loop so the sender does not fill network queues and create control-breaking latency.

## Main Components

- Sender pipeline: video source, encoder, RTP payloader, `screamtx`, RTP/RTCP routing, UDP output.
- Receiver pipeline: UDP input, `screamrx`, RTP depayloader, decoder, sink, and RTCP feedback generation.
- `gstscream`: GStreamer plugin exposing `screamtx` and `screamrx`.
- SCReAM C++ core: sender, receiver, RTP queue, congestion window, packet pacing, and delay/ECN response logic.
- Network emulation: Linux namespaces plus `tc netem` and `tbf` for bandwidth caps, latency, jitter, and loss.
- Measurement: CSV logging of encoder bitrate and SCReAM statistics.

## First Experiments

1. Build `gstscream`.
2. Run a local VP8 pipeline with `videotestsrc`.
3. Add a 5 Mbps bandwidth cap, 20 ms one-way delay, and 5 ms jitter.
4. Run a step-down experiment by changing the bandwidth cap during transmission.
5. Compare how quickly SCReAM updates target bitrate and how much queueing delay appears.

## Source Folders

- [Minimal SCReAM pipeline](../../source/scream_pipeline_minimal/)
- [Experiment harness](../../source/gstreamer_scream_pipeline/)
- [Selected SCReAM source](../../source/scream/)

## Design Notes

SCReAM is most useful here because teleoperation cares more about keeping queues short than maximizing average throughput. A video stream that fills cellular buffers may show high bitrate while making the vehicle hard to control. The testbed should therefore track latency, queue delay, bitrate adaptation time, packet loss, and recovery after bandwidth changes.

The initial implementation is intentionally software-only. Once the feedback loop is stable, the encoder can be swapped from VP8 or x264 to NVENC, VAAPI, or another hardware encoder, and the synthetic source can be replaced with `v4l2src` or a real camera input.
