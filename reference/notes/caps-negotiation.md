# GStreamer Caps Negotiation

## What It Is

The format agreement protocol between linked pads. Before data flows, source and sink pads exchange GstCaps structures describing media types (e.g. audio/x-raw, video/x-h264) with typed properties (sample rate, resolution, pixel format). Negotiation narrows the intersection of compatible formats to a single fixed caps, ensuring both elements agree on the exact data format crossing the link.

## Why It Matters

[GStreamer](gstreamer-overview.md) connects elements developed independently by different teams and vendors. These elements support overlapping but not identical media formats. Caps negotiation is the protocol that discovers the compatible intersection and fixes a single format before data flows, preventing format mismatches that would produce corrupted output or crashes.

## Notes

[GStreamer Caps Negotiation](caps-negotiation.md) proceeds through a query-and-fixation protocol on each [GStreamer Pad](pad.md) link. Each pad advertises its supported caps through a CAPS query: the sink pad returns the set of formats it can accept, the source pad returns the set of formats it can produce. The framework intersects these sets to find the compatible subset. If the intersection is empty, linking fails and the pipeline cannot be constructed. If the intersection contains multiple formats, the source pad proposes a specific fixed caps (selecting based on preference or downstream hints), and the sink pad either accepts or counter-proposes. Once both pads agree on a single fixed caps (all properties specified, no wildcards), the caps event is sent downstream and data flow begins. Renegotiation occurs when conditions change mid-stream: a video scaler element receiving a RECONFIGURE event from downstream may propose new resolution caps, triggering a fresh negotiation round without stopping the pipeline. Transform elements (converters, scalers) participate in negotiation by translating between formats: a videoconvert element can accept caps with one pixel format on its sink pad and produce caps with a different pixel format on its source pad, bridging gaps that would otherwise prevent linking. The caps system encodes media types as MIME-like strings with typed properties: "video/x-raw, format=NV12, width=1920, height=1080, framerate=30/1" is a fully fixed video caps. Partial caps use ranges or lists for unresolved properties: "video/x-raw, width=[320,3840]" indicates flexible resolution.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Pipeline](pipeline.md), [GStreamer Pad](pad.md), [GStreamer Bus](bus.md)
