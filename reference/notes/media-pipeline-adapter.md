# GStreamer Media Pipeline Adapter

## What It Is

Wraps GStreamer to construct the video capture, encoding, decoding, and rendering pipeline. On the vehicle side: v4l2src or equivalent camera source, hardware encoder element (nvh264enc, vaapih264enc), RTP payloader, and UDP sink. On the operator side: UDP source, RTP depayloader, hardware decoder, and display sink. The gstscream plugin element sits between the RTP payloader and UDP sink to enforce SCReAM's pacing and rate decisions.

## Why It Matters

Video data must pass through a chain of processing stages (capture, encode, packetize, depayload, decode, render) with precise control over buffering at each boundary. GStreamer provides the pipeline framework that connects these stages as a directed processing graph with managed threading, buffer negotiation, and clock synchronization. The GStreamer Media Pipeline Adapter wraps this framework to construct and manage the specific pipeline configurations required for teleoperation video, ensuring zero-copy buffer passing and single-frame-depth queues where possible.

## Notes

[GStreamer Media Pipeline Adapter](media-pipeline-adapter.md) constructs and manages the [GStreamer](gstreamer-overview.md) pipelines that carry video through [Teleoperation Video Streaming Stack](teleoperation-video-stack.md). Two pipelines are built: one on the vehicle and one at the operator station.

The vehicle pipeline chains: v4l2src (or equivalent camera source) for capture, capsfilter to enforce target resolution and frame rate, the hardware encoder element from [Hardware Video Codec Adapter](hardware-video-codec-adapter.md) (nvh264enc, vaapih264enc, or equivalent), rtph264pay for RTP packetization, the gstscream element from SCReAM Congestion Control Adapter for pacing, and udpsink for network transmission via UDP ECN Transport Adapter.

The operator pipeline chains: udpsrc for packet reception, rtph264depay for RTP depacketization, the hardware decoder element from [Hardware Video Codec Adapter](hardware-video-codec-adapter.md), and autovideosink for display rendering.

Pipeline construction uses GStreamer element factory to instantiate elements, link them in sequence, and configure properties. For teleoperation latency, all inter-element queues are set to max-size-buffers=1 with leaky=downstream to prevent frame accumulation. The pipeline clock is configured in pipeline mode so that the camera capture timestamp propagates through to SCReAM timing rather than being replaced by the encoder output timestamp.

Session startup pre-rolls the hardware encoder during pipeline construction so the first frame can be encoded immediately. Teardown sends an EOS event through the pipeline and waits for each element to flush its buffers before releasing resources. The adapter handles the GStreamer bus messages (errors, state changes, EOS) and translates them into stack-level events for session management.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Element](element.md), [GStreamer Pad](pad.md), [GStreamer Pipeline](pipeline.md), [GStreamer Caps Negotiation](caps-negotiation.md)
