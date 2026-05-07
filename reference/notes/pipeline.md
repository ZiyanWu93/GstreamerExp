# GStreamer Pipeline

## What It Is

A specialized GstBin that manages the top-level execution context for a media graph. Selects and distributes a global clock, maintains running_time (elapsed time in PLAYING state), coordinates state transitions from sinks to sources to ensure downstream readiness before upstream activation, and provides the bus through which element messages reach the application.

## Why It Matters

[GStreamer](gstreamer-overview.md) requires a top-level container that owns the global clock, coordinates state transitions across all elements, and provides the application-facing message bus. The pipeline is that container: it transforms a collection of independently developed elements into a synchronized, state-coordinated media processing system.

## Notes

[GStreamer Pipeline](pipeline.md) extends GstBin (a container for elements) with three capabilities that make it the top-level execution context. First, clock selection: the pipeline queries all elements for clock providers (audio sinks with hardware clocks, network sources with NTP clocks, or the default system monotonic clock) and selects one as the global clock. When the pipeline enters PLAYING, it records the current clock time as base_time and distributes it to all elements. [GStreamer Clock and Synchronization](clock-and-synchronization.md) uses base_time to compute presentation timestamps for each buffer. Second, state coordination: state changes propagate from sinks upstream to sources. This ordering guarantees that downstream elements are ready to receive data before upstream elements begin producing it, preventing buffer overflow during startup. The PAUSED state requires preroll: each sink element buffers at least one frame and reports ASYNC completion before the pipeline considers the transition done. This preroll mechanism ensures that the first frame is available for immediate rendering when the pipeline enters PLAYING. Third, the [GStreamer Bus](bus.md): the pipeline provides a thread-safe message channel through which any element can post messages (errors, warnings, state changes, buffering status, stream metadata) to the application. The application monitors the bus through GLib main loop integration or explicit polling. Pipeline construction typically uses gst_parse_launch(), which accepts a textual description (e.g., "filesrc location=video.mp4 ! decodebin ! autovideosink") and instantiates, links, and configures the elements automatically.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Pad](pad.md), [GStreamer Caps Negotiation](caps-negotiation.md), [GStreamer Bus](bus.md)
