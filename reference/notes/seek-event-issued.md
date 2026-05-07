# GStreamer Seek Event Issued

## What It Is

An application sends a seek event into the pipeline, requesting repositioning to a new timestamp.

## Why It Matters

Seek events allow the application to reposition playback within a media stream: jumping to a timestamp, changing playback rate, or flushing the pipeline for instant position changes. Seek handling must propagate through the entire graph, resynchronize all elements, and resume data flow from the new position without glitches.

## Notes

The application sends a GstEvent SEEK on a sink pad. The event propagates upstream through the graph until it reaches the source element. The source repositions its read pointer and sends a FLUSH_START event downstream to clear all buffered data, followed by FLUSH_STOP to resume. Each element resets its internal state and begins producing/consuming data from the new position. The pipeline clock is adjusted to reflect the new base time.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Element](element.md), [GStreamer Pad](pad.md), [GStreamer Pipeline](pipeline.md), [GStreamer Bus](bus.md)
