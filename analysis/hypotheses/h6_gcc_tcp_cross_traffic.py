"""H6 — GCC's rate collapses under TCP cross-traffic; SCReAM holds rate.

Source: Drucker 2025 COMSNETS (and the broader De Cicco / Carlucci line).
Prediction (per HYPOTHESES.md):
  With a concurrent iperf TCP Cubic flow on the same NIC at a 5 Mbps
  shared bottleneck, GCC's mean encoder rate during the cross-traffic
  interval drops to < 30% of its no-cross-traffic baseline. SCReAM's
  mean encoder rate stays > 60% of its baseline.

Cross-traffic injection isn't in the runner yet. Verifier prints
`untested` with a pointer to the work needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import _verifier as V   # noqa: E402

HID    = "H6"
CLAIM  = "GCC's rate collapses under TCP cross-traffic; SCReAM holds rate"
SOURCE = "Drucker 2025 COMSNETS (also De Cicco 2013)"
EXPERIMENT = "scream-vs-gcc-tcp-cross"      # not yet authored


def main():
    V.print_header(HID, CLAIM, SOURCE)
    rec = V.try_load_experiment(EXPERIMENT)
    if rec is None:
        return V.print_untested(
            f"no experiment record at runs/experiments/{EXPERIMENT}.json. "
            f"Needs: (a) cross-traffic actor in runner.py — a third role "
            f"that runs `iperf -c <camera> -t <duration>` on a third host "
            f"or alongside the camera during the run window. (b) a "
            f"`cross_traffic` block in the configuration schema. (c) two "
            f"configs running SCReAM and GCC at 5 Mbps with cross_traffic "
            f"enabled, plus matching baseline configs without it. (d) the "
            f"experiment spec. Substantial — ~50 LoC for the runner role "
            f"+ schema + new specs.")

    # Implementation when the experiment exists:
    # 1. Identify the four arms by (algorithm, has_cross_traffic).
    # 2. For each algorithm, compute mean encoder rate during the
    #    cross-traffic interval / mean rate without cross-traffic.
    # 3. Verdict: supported if gcc_ratio < 0.30 AND scream_ratio > 0.60.
    raise NotImplementedError(
        "fill in once the experiment lands; see comment above for "
        "the intended logic.")


if __name__ == "__main__":
    main()
