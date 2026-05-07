# Hardware Video Codec Adapter

## What It Is

Interfaces with hardware video encoder and decoder accelerators (NVIDIA NVENC/NVDEC, Intel VAAPI/QSV, ARM VPU) through GStreamer elements or direct API calls. Translates SCReAM's target bitrate into encoder rate control parameters (CBR/VBR target, max bitrate, keyframe interval). Handles keyframe-on-demand requests triggered by SCReAM's RTP queue discard or packet loss detection.

## Why It Matters

Software video encoding at teleoperation resolutions (1080p, 30-60fps) consumes too much CPU and introduces too much latency. Hardware encoder and decoder accelerators (NVENC, VAAPI, QSV, ARM VPU) provide dedicated silicon that encodes and decodes within the frame interval. The Hardware Video Codec Adapter translates between SCReAM rate control signals (target bitrate, keyframe requests) and the hardware encoder native API, allowing the congestion controller to dynamically adjust video quality in response to network conditions without the pipeline knowing which specific hardware is in use.

## Notes

[Hardware Video Codec Adapter](hardware-video-codec-adapter.md) interfaces with hardware encoder and decoder accelerators within [Teleoperation Video Streaming Stack](teleoperation-video-stack.md), translating between SCReAM rate control signals and the hardware-specific APIs exposed through [GStreamer](gstreamer-overview.md) elements.

On the encode side, the adapter selects the appropriate GStreamer encoder element based on available hardware: nvh264enc for NVIDIA NVENC, vaapih264enc for Intel VAAPI, v4l2h264enc for ARM VPU, or omxh264enc for Qualcomm. The encoder is configured in constant-bitrate (CBR) mode with the initial target set by SCReAM Congestion Control Adapter. When SCReAM updates its target bitrate (every 20-50ms), the adapter translates the new value into the encoder property format (bits-per-second for most GStreamer elements) and applies it. Hardware encoders have a rate control loop that converges over several frames, so the actual output bitrate lags the target by 2-5 frame periods. The adapter compensates by using instantaneous rate control mode where available and applying SCReAM targetRateScale to account for encoder undershoot or overshoot.

Keyframe-on-demand is triggered when SCReAM reports loss (getTargetBitrate returns -1) or when the RTP queue is cleared due to excessive queue delay. The adapter sends a force-keyunit event through the GStreamer pipeline, causing the encoder to produce an IDR frame that allows the decoder to resynchronize without waiting for the next periodic keyframe.

On the decode side, the adapter selects the matching decoder element (nvh264dec, vaapih264dec, v4l2h264dec). The decoder operates in low-latency mode, outputting each frame as soon as it is fully decoded rather than buffering for reordering. Error concealment is enabled to handle partial NAL units from packet loss gracefully rather than stalling the pipeline.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Element](element.md), [GStreamer Pad](pad.md), [GStreamer Pipeline](pipeline.md), [GStreamer Caps Negotiation](caps-negotiation.md)
