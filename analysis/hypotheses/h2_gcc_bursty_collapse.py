"""H2 — GCC's rate control collapses harder than SCReAM's under bursty loss.

Source: Mustang 2024 TOMM (transposed).
Prediction (per HYPOTHESES.md):
  - At the same average loss rate, bursty loss reduces GCC's mean
    encoder rate during the impaired phase by >= 25% more than uniform
    loss does.
  - SCReAM's degradation is < 10% across the same comparison.

Requires a 4-arm experiment (CC × loss model). The experiment doesn't
exist yet — verifier prints `untested` with a pointer to what's needed.
When it lands, the script computes the per-CC degradation ratio across
loss models and applies the thresholds.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import _verifier as V   # noqa: E402

HID    = "H2"
CLAIM  = "GCC's rate control collapses harder than SCReAM's under bursty loss"
SOURCE = "Mustang 2024 TOMM (architectural transpose)"
EXPERIMENT = "scream-vs-gcc-loss-model"     # not yet authored


def main():
    V.print_header(HID, CLAIM, SOURCE)
    rec = V.try_load_experiment(EXPERIMENT)
    if rec is None:
        return V.print_untested(
            f"no experiment record at runs/experiments/{EXPERIMENT}.json. "
            f"Needs a 4-arm spec crossing CC ∈ {{scream, gcc}} × loss ∈ "
            f"{{uniform 2%, bursty B=15}}, recovery off in all arms. "
            f"Author specs/experiments/{EXPERIMENT}.yaml + the four "
            f"GCC-flavoured configurations and re-run.")

    # Implementation when the experiment exists:
    # 1. Identify the four arms by (algorithm, network).
    # 2. For each algorithm, compute mean encoder rate during phase 2
    #    under uniform vs bursty.
    # 3. Compute per-CC degradation ratio: bursty_rate / uniform_rate.
    # 4. Verdict: supported if (1 - gcc_ratio) - (1 - scream_ratio) >= 0.25
    #    AND scream's (1 - ratio) < 0.10.
    raise NotImplementedError(
        "fill in once the experiment lands; see comment above for the "
        "intended logic.")


if __name__ == "__main__":
    main()
