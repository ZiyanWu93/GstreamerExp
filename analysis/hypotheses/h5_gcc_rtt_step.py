"""H5 — GCC under-utilizes after a sudden RTT spike; SCReAM doesn't.

Source: Hassan 2022 SIGCOMM + Mustang 2024 TOMM.
Prediction (per HYPOTHESES.md):
  With a network spec that holds rate constant at 5 Mbps but introduces
  a 200ms RTT step at midstream lasting 2s, GCC's mean encoder rate
  drops by >= 30% during and >= 1s after the spike. SCReAM's mean
  encoder rate drops by < 10% over the same interval.

Needs a `handover-rtt-step.yaml` network spec (rate constant, delay
step) and one config per CC against it. Spec authoring not yet done.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import _verifier as V   # noqa: E402

HID    = "H5"
CLAIM  = "GCC under-utilizes after a sudden RTT spike; SCReAM doesn't"
SOURCE = "Hassan 2022 SIGCOMM + Mustang 2024 TOMM"
EXPERIMENT = "scream-vs-gcc-rtt-step"          # not yet authored


def main():
    V.print_header(HID, CLAIM, SOURCE)
    rec = V.try_load_experiment(EXPERIMENT)
    if rec is None:
        net_spec = "specs/networks/handover-rtt-step.yaml"
        return V.print_untested(
            f"no experiment record at runs/experiments/{EXPERIMENT}.json. "
            f"Needs: (a) author {net_spec} with three steps — "
            f"5 Mbps/20ms/clean × 5s, then 5 Mbps/220ms/clean × 2s "
            f"(RTT step, no rate change), then 5 Mbps/20ms/clean × 13s. "
            f"(b) two configs running SCReAM and GCC against this network. "
            f"(c) experiment spec listing congestion_control.algorithm in "
            f"`varies`. Then run.")

    # Implementation when the experiment exists:
    # 1. Identify scream and gcc arms.
    # 2. For each arm, find encoder_target_kbps samples in the window
    #    [rtt_step_start, rtt_step_end + 1s].
    # 3. Compute drop = 1 - mean(window) / mean(pre-step).
    # 4. Verdict: supported if gcc_drop >= 0.30 AND scream_drop < 0.10.
    raise NotImplementedError(
        "fill in once the experiment lands; see comment above for "
        "the intended logic.")


if __name__ == "__main__":
    main()
