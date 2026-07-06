# Goal

Build and characterize a real-time GStreamer video pipeline so claims about congestion control, recovery, quality, latency, adaptation, and stability can be tested from preserved run records.

## Source-Scope Questions

- How does the pipeline behave under controlled network stress?
- Which claims about SCReAM, GCC, recovery, and workload sensitivity survive verifier-backed experiments?
- Which metrics expose throughput, quality, latency, adaptation, and stability without conflating them?

## Success Criteria

- Each durable claim has a hypothesis spec and verifier.
- Run records preserve enough data to regenerate reports.
- New behavior-affecting fields are declared in specs and validated at load.
