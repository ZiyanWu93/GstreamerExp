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
- Source citations use the BibTeX keys from
  `literature_survey/surveys/scream-vs-gcc-mobile/references.bib`.
- Predictions are quantitative — framed in metrics our framework
  computes — so the verifier can mechanically decide.
- A hypothesis stays in this catalog after it's tested. Refuted
  claims are part of the project's research record, not embarrassments
  to hide.
- Adding a hypothesis: append a block here AND add a verifier script.
  The verifier may print "untested" until the experiment runs, but
  the file must exist.

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

## H2 — GCC's rate control collapses harder than SCReAM's under bursty loss

- **Source:** Mustang 2024 TOMM (`2024-yu-mustang-improving-qoe-for-real-time-video-in-cellular-networks-by-masking-jitter`), transposed: GCC's Kalman delay estimator is sensitive to bursty-loss-driven jitter; SCReAM's queue-delay tracker is design-arguably less so.
- **Prediction:** Compared at the same average loss rate (2%), bursty loss (`bursty-5mbps-300kbps-long`, B=15) reduces GCC's mean encoder rate during the impaired phase by ≥ 25% more than uniform loss does (`fluctuating-5mbps-300kbps`); SCReAM's degradation is < 10% across the same comparison.
- **Design:** New 4-arm experiment crossing CC × loss model: {SCReAM, GCC} × {uniform 2%, bursty B=15}. Configs needed: GCC variants of the recovery-disabled SCReAM configs already in the repo. Recovery off for both arms so the controllers' raw response is what's measured.
- **Verifier:** `analysis/hypotheses/h2_gcc_bursty_collapse.py`

## H3 — PLI's quality advantage over NACK widens with content motion

- **Source:** Project-internal, motivated by the FileSource design rationale in `pipeline_config.py:FileSource`: real-content P-frame variance amplifies the cost of a lost reference frame; PLI's keyframe rebuild is content-agnostic.
- **Prediction:** PLI minus NACK in PSNR p10 (worst-10% frames) is larger on `teleop-realmotion` than on `ball-720p30`, by ≥ 1 dB. (i.e., PLI's relative advantage over NACK grows with motion variance.)
- **Design:** 2×2 experiment (recovery × content): {NACK only, PLI only} × {ball-720p30, teleop-realmotion}. Configs 13/15 (NACK/PLI on ball) and 19/21 (NACK/PLI on realmotion) already exist. Add `decoded_psnr` to all four; needs no source change since both ball-720p30 and teleop-realmotion satisfy the PSNR validator (synthetic and file backends, both with `clock_overlay: false`).
- **Verifier:** `analysis/hypotheses/h3_pli_real_content.py`

## H4 — Latency-budget enforcement reverses NACK's apparent benefit

- **Source:** Project-internal, motivated by the operational truth that a stale frame is useless to a teleoperator. Standard CC research papers report frames-delivered without operational deadline.
- **Prediction:** Without a budget (`latency_budget_ms = 0`), NACK delivers ≥ 5% more frames than no-recovery (NACK's frame-count benefit). With a 100ms budget, NACK's `late_drops.delivered` falls *below* no-recovery's `late_drops.delivered` (the 200ms NACK jitter buffer pushes more frames past the 100ms budget than the savings from retransmissions).
- **Design:** 2×2 experiment (recovery × budget): {no-recovery, NACK} × {budget=0, budget=100}. Needs new configs that vary `latency_budget_ms`, plus an experiment spec that lists `latency_budget_ms` in `varies`.
- **Verifier:** `analysis/hypotheses/h4_budget_reverses_nack.py`

## H5 — GCC under-utilizes after a sudden RTT spike; SCReAM doesn't

- **Source:** Hassan 2022 SIGCOMM (`2022-hassan-vivisecting-mobility-management-in-5g-cellular-networks`) plus Mustang 2024 TOMM. A 5G handover is operationally a stepwise RTT inflation without congestion. GCC's RTT-driven backoff misfires on it.
- **Prediction:** With a network spec that holds rate constant at 5 Mbps but introduces a 200ms RTT step at midstream lasting 2s, GCC's mean encoder rate drops by ≥ 30% during and ≥ 1s after the spike. SCReAM's mean encoder rate drops by < 10% over the same interval.
- **Design:** New network spec `handover-rtt-step.yaml` (rate constant, delay step). Schema already supports it — just authoring. New configs running SCReAM and GCC against this network. Single-arm-each experiment.
- **Verifier:** `analysis/hypotheses/h5_gcc_rtt_step.py`

## H6 — GCC's rate collapses under TCP cross-traffic; SCReAM holds rate better

- **Source:** Drucker 2025 COMSNETS (`2025-drucker-investigating-webrtc-bbr-as-an-alternative-to-gcc-for-live-video-streaming`): "GCC's bitrate drops by 96% under one long-lived Cubic on a shared bottleneck." Reproduced over a decade of GCC-vs-TCP papers (De Cicco 2013 onward).
- **Prediction:** With concurrent iperf TCP Cubic flow on the same NIC during steady-state at 5 Mbps shared bottleneck, GCC's mean encoder rate during the cross-traffic interval drops to < 30% of its no-cross-traffic baseline. SCReAM's mean encoder rate stays > 60% of its baseline (or with comparable degradation, demonstrably reaches steady state under contention rather than collapse).
- **Design:** Needs a new "cross_traffic" hook role in `runner.py` (a third actor that runs `iperf -c <camera> -t <duration>` on the camera host during the camera's run). Possibly 50 lines of code + one schema field. Single-arm-each experiment with and without cross traffic.
- **Verifier:** `analysis/hypotheses/h6_gcc_tcp_cross_traffic.py`

---

## Hypotheses requiring substantial infra (parking lot — won't test in this testbed)

- **Per-TTI scheduler behavior leaks information that controllers smooth away** — Balasingam 2019 MobiCom. Needs an SDR-RAN.
- **5G NR handover behavior** — Hassan 2022. Needs real 5G or Colosseum.
- **On-device modem firmware buffer dominates RTT** — Guo 2016. Needs real cellular UE.

These are recorded so future readers know the survey claims them, even though our testbed can't validate them without significant infrastructure. The first two of the H1–H6 list are the literature's own gap (no head-to-head SCReAM-vs-GCC measurement on cellular exists per the synthesis); these three are the bigger gap (no existing testbed in the field can answer them at low cost).
