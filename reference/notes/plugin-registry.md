# GStreamer Plugin Registry

## What It Is

The discovery and loading mechanism for GStreamer plugins. Maintains a cached binary description of all available element factories, pad templates, and capabilities without loading plugin shared libraries into memory. Enables lazy loading: applications query the registry for elements matching required capabilities, and the corresponding plugin is loaded only when an element is instantiated.

## Why It Matters

[GStreamer](gstreamer-overview.md) supports hundreds of elements across dozens of plugins, but loading all of them at startup would waste memory and delay initialization. The plugin registry solves this by caching element metadata (pad templates, capabilities, rank) in a binary file, enabling fast capability queries without loading any shared library until an element is actually needed.

## Notes

[GStreamer Plugin Registry](plugin-registry.md) operates in two phases. During scanning (triggered at first launch or when the cache is invalidated), the registry iterates through plugin directories (configured via GST_PLUGIN_PATH and default system paths), loads each shared library, calls its gst_plugin_register function to enumerate the element factories it provides, records each factory's name, type, pad templates, and rank in a binary cache file, then unloads the library. The cache persists across application launches, so subsequent startups skip the scan entirely and load the cache directly. When an application requests an element by name (gst_element_factory_make("x264enc")), the registry locates the factory, loads the containing plugin if not already resident, and returns a new element instance. When autoplugging (e.g., decodebin selecting a decoder for a specific codec), the registry queries all factories whose sink pad templates match the input caps, ranks them by preference (element rank, which can be overridden per-system), and selects the highest-ranked compatible factory. This mechanism allows hardware-accelerated elements (VAAPI, NVDEC) to be preferred over software implementations simply by assigning them higher ranks, without modifying any pipeline code. The registry also supports blocklisting: corrupted or incompatible plugins can be excluded without removing them from the filesystem.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Pipeline](pipeline.md), [GStreamer Pad](pad.md), [GStreamer Caps Negotiation](caps-negotiation.md), [GStreamer Bus](bus.md)
