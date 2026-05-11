# Hypotheses — claims about pipeline behavior we should validate

This document is the project's catalog of *claims* about how the
pipeline behaves under various conditions. Each claim is sourced from
the literature (see `literature_survey/surveys/scream-vs-gcc-mobile/`)
or from the project's own observations, and each one has a verifier
script that determines its current support status by reading the
experiment records under `runs/experiments/`.

The claim text and experimental design are frozen here. The **verdict**
on each claim — supported / refuted / inconclusive / untested — is
*not* frozen. It comes from running the verifier:

```sh
python3 analysis/hypotheses/<id>.py
```

This is principle #2 from the README in concrete form: claims are
worth keeping (they're the questions we're asking); verdicts must
re-derive from data (they're the answers, which change as code and
specs change).

## Conventions

- Each hypothesis has a stable id (`H1`, `H2`, ...) and a slug used
  for its verifier file (`h1_scream_underuse.py`, etc.).
- Each hypothesis also has a structured spec under
  `specs/hypotheses/<slug>.yaml`. That spec is the source of truth for
  claim text, setup, required metrics, expected experiment records,
  current conclusions, and website registry metadata.
- Source citations use the BibTeX keys from
  `literature_survey/surveys/scream-vs-gcc-mobile/references.bib`.
- Predictions are quantitative — framed in metrics our framework
  computes — so the verifier can mechanically decide.
- Use standard empirical-research labels in hypothesis pages and
  summaries: **Claim**, **Prediction**, **Experimental Setup**,
  **Results**, **Supporting Evidence**, **Findings**, and
  **Limitations**. Reserve **Threats to Validity** for methodological
  risks that could invalidate the measurement, not ordinary claim
  scope.
- A hypothesis stays in this catalog after it's tested. Refuted
  claims are part of the project's research record, not embarrassments
  to hide.
- Adding a hypothesis: append a block here AND add a verifier script.
  The verifier may print "untested" until the experiment runs, but
  the file must exist.
- Rebuild the website-facing registry after hypothesis metadata changes:
  `python3 analysis/hypotheses/build_registry.py`.

---

## H1 — SCReAM's bottleneck-phase sender rate is lower than GCC's on capacity-step networks

- **Source:** Zhang 2019 SIMUtools (`2019-zhang-congestion-control-for-rtp-media-a-comparison-on-simulated-environment`). Cited in the survey synthesis: "SCReAM with the lowest queue and lower utilization, GCC with sawtooth and slow recovery on dynamic capacity."
- **Scope of claim:** This hypothesis tests *bottleneck-phase* behavior only — sender rate during the lowest-cap (300 kbps) windows of a capacity-step network. It does not test overall bandwidth efficiency across the full run; that is a separate measurement (see "Companion: overall utilization" below). The survey-synthesis phrasing "SCReAM under-utilizes bandwidth" elides this scope distinction; this hypothesis preserves it.
- **Prediction:** On `fluctuating-5mbps-300kbps`, mean encoder rate / phase rate cap (averaged within bottleneck phases) is lower for SCReAM than GCC by ≥ 5 percentage points; GCC's encoder-rate stddev pooled across bottleneck phases is higher (sawtooth signature) than SCReAM's by ≥ 20%.
- **Design:** `scream-vs-gcc-720p` experiment (already exists); needs `frame_count` + `encoder_target_kbps` enabled (already are). Verifier reads per-arm adaptation samples and computes utilization + variability per phase.
- **Verifier:** `analysis/hypotheses/h1_scream_underuse.py`
- **Verdict (regime-dependent):**
  - On the original `fluctuating-5mbps-300kbps` (3 phases, 2 transitions in 20 s): **supported.** util_gap +0.387, var_ratio 4.73 (SCReAM σ=124 kbps, GCC σ=587 kbps — sawtooth signature reproduces).
  - On `fluctuating-fast-5mbps-300kbps` (10 phases, 9 transitions in 20 s): **refuted.** util_gap −0.156, var_ratio 1.13 — SCReAM now over-shoots the cap *more* than GCC because it can't ramp down between rapid bursts, and rate variance converges across both algorithms.
  - **Reading:** Zhang 2019's bottleneck-phase comparison is specific to networks where bottleneck phases are long enough for SCReAM's conservative ramp-up to be the binding constraint. With frequent transitions (sub-control-loop time scale), neither algorithm reaches steady state, GCC's responsiveness advantage disappears, and SCReAM's smoothing keeps it closer to the cap on average.
- **Companion: overall utilization.** A separate measurement integrates sender rate over the full run divided by integrated capacity — answering "across the full 20 s, how much of the available bandwidth did each algorithm use?" This is a different question from H1's bottleneck-only utilization. Reported alongside H1 on the same page; uses the same data, no re-run needed.

## H2 — GCC stays ahead across latency-deadline and retransmission settings

- **Source:** Project-internal fluctuating-network deadline/retransmission experiment.
- **Prediction:** On the fluctuating 5 Mbps/300 kbps capacity-step network, GCC should deliver more median viewer frames than SCReAM in every crossed cell of retransmission on/off and latency budget 0/50/100 ms.
- **Design:** `retransmission-deadline-fluc`, a 2×2×3 sweep over congestion controller (`scream`/`gcc`), retransmission off/on, and deadline 0/50/100 ms. The network, video workload, codec, and workers are held fixed.
- **Verifier:** `analysis/hypotheses/h2_fluctuating_deadline_ranking.py`
- **Verdict rule:** Supported if all six deadline/retransmission cells show GCC ahead of SCReAM by at least 30 median viewer frames.
- **Verdict (supported):** GCC leads SCReAM in all six cells. The smallest GCC median-frame advantage is 34 frames, and the largest is 41 frames. Neither deadline enforcement nor retransmission changes the SCReAM-vs-GCC ranking in this experiment.

## H3 — Media workload calibration gates realistic 5G trace comparisons

- **Source:** Project-internal validity check for realistic-trace SCReAM/GCC experiments.
- **Prediction:** The current `ball-720p30-30s` VP8 workload is not valid for comparing SCReAM and GCC on the translated 5G traces because observed camera wire bitrate is far below trace capacity. A workload is considered under-driving when every translated 5G trace has ≥ 10× median capacity headroom over observed camera wire bitrate and fewer than 15% of 100 ms bins below 1 Mbit/s.
- **Design:** Reuses the old trace records only as calibration data: `scream-vs-gcc-mahimahi-5g-cqi`, `scream-vs-gcc-mahimahi-5g-ho`, and `scream-vs-gcc-mahimahi-5g-rb`. It compares observed camera `wire_bytes.mean_kbps` against the translated Mahimahi trace capacity time series. It does not make a SCReAM-vs-GCC performance claim.
- **Verifier:** `analysis/hypotheses/h3_media_workload_calibration.py`
- **Verdict rule:** Supported if all tested realistic traces meet the under-driving rule; inconclusive if only some do; refuted if none do.
- **Verdict (supported):** CQI median capacity is 11160 kbps vs 224.3 kbps emitted media (49.8× headroom), HO is 6240 vs 227.5 kbps (27.4×), and RB is 5160 vs 223.0 kbps (23.1×). The previous realistic-trace SCReAM/GCC comparisons were not meaningful algorithm tests; the workload was too compressible to stress the traces.
- **Next design gate:** before adding a new SCReAM/GCC trace hypothesis, create a high-quality or high-motion workload and verify that observed emitted bitrate actually reaches the Mbps range and interacts with the low-capacity parts of the trace.

## H4 — SCReAM preserves more decodable video than GCC on calibrated stressed realistic 5G traces

- **Source:** Project-internal realistic-trace SCReAM/GCC experiment, after H3 showed the original ball workload under-drove the translated traces.
- **Prediction:** On translated 5G Mahimahi traces scaled to 33% capacity, the calibrated `snow-384x216-30s` VP8 workload should expose where the controller matters. CQI should be a high-capacity control where both controllers deliver nearly all frames. On the stressed HO and RB traces, SCReAM should deliver more viewer-depayloaded frames than GCC while using <= 85% of GCC's camera egress bitrate and reaching similar viewer ingress bitrate.
- **Design:** Three fixed-trace experiments: `scream-vs-gcc-mahimahi-5g-cqi-x0p33-snow-384x216` (configs 81/82), `scream-vs-gcc-mahimahi-5g-ho-x0p33-snow-384x216` (configs 79/80), and `scream-vs-gcc-mahimahi-5g-rb-x0p33-snow-384x216` (configs 83/84). Within each trace pair, the network trace, video, codec, bitrate bounds, recovery settings, sink, hosts, and run length are fixed; the only declared comparison field is `congestion_control.algorithm` (`scream` vs `gcc`). The run uses the local machine as controller and `aum`→`veda` as Tailscale-addressed workers.
- **Verifier:** `analysis/hypotheses/h4_scream_realistic_trace_advantage.py`
- **Verdict rule:** Supported if all runs pass, both stressed traces show SCReAM/GCC viewer-frame ratio >= 1.25, both stressed traces show camera egress mean bitrate <= 85% of GCC, viewer-side mean wire bitrate is within 10% of GCC on stressed traces, and at least one stressed trace shows >= 2x viewer-frame ratio.
- **Main claim:** SCReAM wins on the stressed HO/RB traces because its post-payload RTP pacing turns congestion-control decisions into lower camera egress, while GCC lowers its estimator target but this pipeline still emits multi-Mbit/s RTP that the trace cannot carry, leaving fewer complete frames decodable at the viewer.
- **Supporting evidence:** the comparison holds trace/workload/codec/sink/workers fixed within each pair; CQI acts as the high-capacity control; HO and RB show frame-delivery separation; SCReAM's advantage comes with lower camera egress, not more traffic; and GCC's lower target does not become lower emitted RTP in this pipeline.
- **Verdict (supported):** Across 3 repetitions per arm, CQI was non-discriminating: SCReAM delivered 903.7 frames vs GCC's 901.3 (1.00×). On HO, SCReAM delivered 544.7 frames vs GCC's 199.0 (2.74×) while producing 1696.0 vs 2353.0 kbps camera egress (0.72×). On RB, SCReAM delivered 445.3 frames vs GCC's 319.3 (1.39×) while producing 1480.8 vs 2324.9 kbps camera egress (0.64×). It does not establish a universal latency claim because the current latency metric still has clock-skew artifacts.

---

## Hypotheses requiring substantial infra (parking lot — won't test in this testbed)

- **Per-TTI scheduler behavior leaks information that controllers smooth away** — Balasingam 2019 MobiCom. Needs an SDR-RAN.
- **5G NR handover behavior** — Hassan 2022. Needs real 5G or Colosseum.
- **On-device modem firmware buffer dominates RTT** — Guo 2016. Needs real cellular UE.

These are recorded so future readers know the survey claims them, even though our testbed cannot validate them without significant infrastructure. They are parked outside the active numbered list so the website only advertises hypotheses with completed experiment evidence.
