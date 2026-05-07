# GStreamer Pipeline Launch Requested

## What It Is

An application calls gst_parse_launch() or constructs a pipeline programmatically, specifying elements and their connections as a graph description.

## Why It Matters

Pipeline launch is the event that turns a declarative pipeline description into a running media system. The application specifies elements and connections; the framework must instantiate implementations, negotiate formats between pads, allocate buffers, and prepare the graph for data flow. A launch failure leaves no media processing.

## Notes

The application calls gst_parse_launch() with a textual pipeline description or constructs the graph programmatically. [GStreamer](gstreamer-overview.md) instantiates each [GStreamer Element](element.md) from the [GStreamer Plugin Registry](plugin-registry.md), links elements through their [GStreamer Pad](pad.md) connections, negotiates capabilities between linked pads, and transitions the [GStreamer Pipeline](pipeline.md) to PAUSED state. Format negotiation resolves compatible media types across the graph.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Element](element.md), [GStreamer Pad](pad.md), [GStreamer Pipeline](pipeline.md), [GStreamer Bus](bus.md)
