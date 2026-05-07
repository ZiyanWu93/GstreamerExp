# GStreamer State Change Commanded

## What It Is

An application sets the pipeline state (NULL, READY, PAUSED, PLAYING), triggering cascading state transitions through all child elements from sinks to sources.

## Why It Matters

State changes control the pipeline lifecycle: NULL to READY allocates resources, READY to PAUSED opens devices, PAUSED to PLAYING starts data flow. Each transition must propagate through every element in the graph. A failed transition on any element blocks the entire pipeline, so the framework must handle partial failures gracefully.

## Notes

The application or an internal event requests a state change on the [GStreamer Pipeline](pipeline.md). The pipeline propagates the change to each child element in dependency order. Each element performs the work for that transition (opening devices, allocating buffers, starting clocks). The element reports success, failure, or async-pending. The pipeline aggregates results and posts a state-changed message on the [GStreamer Bus](bus.md).

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Element](element.md), [GStreamer Pad](pad.md), [GStreamer Pipeline](pipeline.md), [GStreamer Bus](bus.md)
