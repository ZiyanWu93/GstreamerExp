# GStreamer Plugin Queried

## What It Is

An application or autoplugging element queries the plugin registry for element factories matching required capabilities.

## Why It Matters

Plugin queries allow elements and applications to discover capabilities, resource availability, and configuration options at runtime. The [GStreamer Plugin Registry](plugin-registry.md) catalogs all available element factories. Query events propagate through the graph, enabling dynamic pipeline reconfiguration without rebuilding from scratch.

## Notes

An application or element issues a query (caps query, position query, duration query, allocation query) by sending a GstQuery event through a pad. Each element in the query path can modify or answer the query. The [GStreamer Plugin Registry](plugin-registry.md) resolves element factory lookups. Allocation queries negotiate buffer pools and memory allocators between linked elements for zero-copy operation.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Element](element.md), [GStreamer Pad](pad.md), [GStreamer Pipeline](pipeline.md), [GStreamer Bus](bus.md)
