# GStreamer Buffer Pushed

## What It Is

A source element or upstream peer pushes a GstBuffer into a sink pad, delivering media data with timestamps, duration, and flags.

## Why It Matters

Buffer push is the fundamental data flow event: a source or filter element pushes a GstBuffer through a sink pad to the next element in the graph. This is how media data moves through the pipeline. The push must be zero-copy where possible to meet latency and throughput requirements.

## Notes

An upstream element calls gst_pad_push() on a sink [GStreamer Pad](pad.md), passing a GstBuffer containing media data with timestamps, duration, and flags. The pad checks that the buffer's caps match the negotiated format. The receiving element's chain function processes the buffer (decode, encode, filter, or pass through) and either pushes results downstream or stores them for later output.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Element](element.md), [GStreamer Pad](pad.md), [GStreamer Pipeline](pipeline.md), [GStreamer Bus](bus.md)
