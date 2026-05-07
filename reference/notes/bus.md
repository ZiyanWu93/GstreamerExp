# GStreamer Bus

## What It Is

The asynchronous message delivery channel between elements and the application. Elements post messages (errors, warnings, EOS, state changes, buffering progress) to the bus, which the application monitors via a watch callback or polling. Decouples element-internal events from the application thread, providing a thread-safe communication path from any pipeline element to the main loop.

## Why It Matters

[GStreamer](gstreamer-overview.md) elements run on multiple threads (each source element typically drives its own streaming thread), but the application needs to respond to element events (errors, state changes, buffering progress) on its own thread. The bus provides the thread-safe channel that bridges this gap, allowing elements to post messages from any thread and the application to consume them on the main loop without locks or race conditions.

## Notes

[GStreamer Bus](bus.md) is a GObject-based message queue attached to each [GStreamer Pipeline](pipeline.md). Any [GStreamer Element](element.md) can post a GstMessage to the bus from any thread using gst_bus_post(). Messages carry a type (ERROR, WARNING, EOS, STATE_CHANGED, BUFFERING, TAG, ELEMENT), a source reference (the element that posted it), and a type-specific payload (error details, buffering percentage, stream tags). The application consumes messages through two mechanisms. Bus watch: gst_bus_add_watch() registers a callback that the GLib main loop invokes whenever a message arrives, ensuring message handling runs on the application's main thread. This is the standard approach for GUI applications that must update UI elements in response to pipeline events. Polling: gst_bus_pop() or gst_bus_timed_pop_filtered() allows synchronous message retrieval, used in command-line applications or dedicated pipeline management threads. Critical messages drive application behavior: an ERROR message triggers error handling and pipeline shutdown; an EOS (end-of-stream) message signals that all data has been processed and the application can clean up; a BUFFERING message reports download progress for network streams and allows the application to pause playback until sufficient data is buffered; a STATE_CHANGED message confirms that a requested state transition completed. The bus decouples element behavior from application logic: elements post messages without knowing whether or how the application will respond.

## Related Notes

[GStreamer](gstreamer-overview.md), [GStreamer Pipeline](pipeline.md), [GStreamer Pad](pad.md), [GStreamer Caps Negotiation](caps-negotiation.md)
