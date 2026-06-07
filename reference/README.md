# GStreamer Reference

This folder contains the GStreamer context needed to start experiments without depending on the original research workspace.

## Start Here

- [GStreamer](notes/gstreamer-overview.md)
- [SCReAM Teleoperation Testbed](notes/scream-testbed.md)
- [Software Analysis: GStreamer](notes/software-analysis.md)
- [GStreamer Media Pipeline Adapter](notes/media-pipeline-adapter.md)
- [Hardware Video Codec Adapter](notes/hardware-video-codec-adapter.md)

## Architecture Notes

- [GStreamer Element](notes/element.md)
- [GStreamer Pad](notes/pad.md)
- [GStreamer Pipeline](notes/pipeline.md)
- [GStreamer Plugin Registry](notes/plugin-registry.md)
- [GStreamer Caps Negotiation](notes/caps-negotiation.md)
- [GStreamer Clock and Synchronization](notes/clock-and-synchronization.md)
- [GStreamer Bus](notes/bus.md)

## Runtime Events

- [GStreamer Caps Proposed to Peer](notes/caps-proposed-to-peer.md)
- [GStreamer Clock Distributed to Element](notes/clock-distributed-to-element.md)
- [GStreamer Element Message Posted to Bus](notes/element-message-posted-to-bus.md)
- [GStreamer Pipeline Launch Requested](notes/pipeline-launch-requested.md)
- [GStreamer State Change Commanded](notes/state-change-commanded.md)
- [GStreamer Buffer Pushed](notes/buffer-pushed.md)
- [GStreamer Plugin Queried](notes/plugin-queried.md)
- [GStreamer Seek Event Issued](notes/seek-event-issued.md)

## Diagrams

- [Component diagram](diagrams/component-diagram.html)
- [Flow diagram](diagrams/flow-diagram.html)
- [State machine](diagrams/state-machine.html)
- [Layer diagram](diagrams/layer-diagram.html)

## Long-Form Reference

- [Software analysis reference](software-analysis-reference.md)

## UMN reference

- [UMN Teleop-Gopher-Streamer: top-down design](umn-teleop-design.md) — standalone design note: what it is, how it is decomposed, how a frame flows through it, and where the design falls short
- [GstreamerExp vs UMN Teleop-Gopher-streamer](umn-teleop-gopher-comparison.md) — gap-driven comparison against the UMN GopherNetLab teleoperation streamer
- [Why GstreamerExp and UMN Teleop-Gopher diverge](umn-teleop-divergence-analysis.md) — root-cause analysis of the divergence and a gap severity/cost ranking
