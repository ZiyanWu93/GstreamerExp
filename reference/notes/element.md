# GStreamer Element

## What It Is

The fundamental processing unit in GStreamer. Derived from GstElement via the GObject type system, each element has named pads (source and sink), traverses four states (NULL, READY, PAUSED, PLAYING), and performs a specific media operation: generating data (sources), transforming data (filters), or consuming data (sinks). Elements are instantiated from element factories stored in the plugin registry.

## Why It Matters

[GStreamer](gstreamer-overview.md) decomposes media processing into elements because each media operation (decode, encode, filter, render) has different computational requirements, hardware dependencies, and reuse potential. The element abstraction allows codec developers, hardware vendors, and application authors to work independently: a new hardware decoder can be packaged as an element and dropped into any pipeline without modifying the pipeline code.

## Notes

[GStreamer Element](element.md) is instantiated from a GstElementFactory registered in the [GStreamer Plugin Registry](plugin-registry.md). The factory provides the element's class name, pad templates (describing what connections it supports), and a rank indicating preference when autoplugging selects among alternatives. Once instantiated, the element traverses four states: NULL (initial, no resources allocated), READY (resources allocated but no data flow), PAUSED (data flow active but clock not running, sink elements preroll by buffering one frame), and PLAYING (clock running, real-time data flow). State transitions propagate through the pipeline: sinks transition first to ensure downstream readiness before upstream activation. Each element exposes [GStreamer Pad](pad.md) instances as connection points. Source pads produce data; sink pads consume it. The element's chain function (registered on each sink pad) receives incoming GstBuffer objects and processes them: a decoder chain function transforms compressed frames into raw frames, a filter chain function applies transformations (color conversion, scaling, effects), and a sink chain function renders to a display or audio device. Elements communicate metadata through GstEvent objects (caps changes, seeking, end-of-stream) and report conditions to the application through GstMessage objects posted to the [GStreamer Bus](bus.md). The GObject type system provides property access (e.g., setting bitrate on an encoder) and signal emission (e.g., notifying when a pad is added dynamically).

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Pipeline](pipeline.md), [GStreamer Pad](pad.md), [GStreamer Caps Negotiation](caps-negotiation.md), [GStreamer Bus](bus.md)
