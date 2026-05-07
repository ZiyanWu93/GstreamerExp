# GStreamer Clock and Synchronization

## What It Is

The timing subsystem that synchronizes media rendering across the pipeline. The pipeline selects a global clock (from source elements, sink elements, or the system clock), establishes a base_time reference, and distributes it to all elements. Elements use clock time to schedule buffer presentation, ensuring audio and video remain synchronized and frames are rendered at the correct rate.

## Why It Matters

[GStreamer](gstreamer-overview.md) processes audio and video streams that must be rendered at precise times relative to each other and to the media timeline. Without a shared clock and synchronization protocol, audio would drift from video, frames would render at incorrect rates, and seeking would produce temporal discontinuities. The clock subsystem provides the shared time reference that makes multi-stream synchronization possible.

## Notes

[GStreamer Clock and Synchronization](clock-and-synchronization.md) operates through three coordinated mechanisms. Clock selection: when [GStreamer Pipeline](pipeline.md) enters PLAYING, it queries all elements for clock providers. Audio sinks (alsasink, pulsesink) provide hardware clocks driven by the audio DAC sample rate, which are the most stable reference for playback. Network sources may provide NTP or PTP clocks for synchronized multi-device playback. If no element provides a clock, the pipeline uses the system monotonic clock. The selected clock is distributed to all elements via the pipeline's clock property. Base time establishment: the pipeline records the clock's current value as base_time at the moment of entering PLAYING. Every buffer carries a running_time computed from its PTS (presentation timestamp) and the current segment. Sink elements compute the wall-clock presentation time as base_time + running_time and call gst_clock_id_wait() to sleep until that time arrives. This wait-then-render mechanism ensures that frames appear at the correct rate regardless of how quickly upstream elements produce them. QoS (Quality of Service) feedback: when a sink element detects that it is rendering late (the clock time has already passed the buffer's presentation time), it sends a QoS event upstream. Upstream elements can respond by dropping frames (e.g., a decoder skipping B-frames) or reducing quality to maintain real-time throughput. Seeking resets the timing: a seek event causes the pipeline to flush pending data, establish new segment boundaries, and recalculate base_time offsets so that playback resumes at the correct position.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Pipeline](pipeline.md), [GStreamer Pad](pad.md), [GStreamer Caps Negotiation](caps-negotiation.md), [GStreamer Bus](bus.md)
