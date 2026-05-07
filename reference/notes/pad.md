# GStreamer Pad

## What It Is

The connection point on a GStreamer element through which data flows. Each pad has a direction (source or sink), a presence mode (always, sometimes, request), and a set of capabilities describing the media formats it can handle. Pads are linked to form the edges of the pipeline graph. Data flows unidirectionally: buffers move from source pads to sink pads, while events can travel in both directions.

## Why It Matters

[GStreamer](gstreamer-overview.md) needs pads as the typed connection points that enforce format compatibility between elements. Without pads, elements would exchange raw buffers with no guarantee that the producer'"s output format matches the consumer"'s expectations. Pads carry capability descriptions that enable [GStreamer Caps Negotiation](caps-negotiation.md) to verify compatibility before data flows.

## Notes

[GStreamer Pad](pad.md) has three presence modes that determine when the pad exists. Always pads are created with the element and persist throughout its lifetime (e.g., a video encoder's sink and source pads). Sometimes pads appear dynamically when the element discovers stream content (e.g., a demuxer creates one source pad per audio/video stream found in the container). Request pads are created on demand when the application or another element explicitly requests a connection (e.g., a mixer creates a new sink pad for each input stream). Data flows through pads in two scheduling modes. In push mode, the upstream element calls gst_pad_push(buffer) on its source pad, which delivers the buffer to the linked sink pad's chain function. In pull mode, the downstream element calls gst_pad_pull_range() on its sink pad, requesting specific byte ranges from the upstream source pad's getrange function. Push mode dominates live pipelines (cameras, network sources); pull mode serves random-access scenarios (file demuxing). Each pad carries a GstCaps structure describing the media format it currently handles. When pads are linked, [GStreamer Caps Negotiation](caps-negotiation.md) intersects the source pad's supported caps with the sink pad's supported caps to find a mutually acceptable format. Events travel through pads alongside data: downstream events (EOS, segment, caps) are serialized with buffers; upstream events (seek, reconfigure, QoS) travel in the reverse direction.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Pipeline](pipeline.md), [GStreamer Caps Negotiation](caps-negotiation.md), [GStreamer Bus](bus.md)
