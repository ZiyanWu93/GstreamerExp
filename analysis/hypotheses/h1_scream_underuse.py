"""H1 — SCReAM under-utilizes bandwidth vs GCC on capacity-step networks.

Source: Zhang 2019 SIMUtools.
Prediction (per HYPOTHESES.md):
  - In phase 2 (300 kbps cap), SCReAM mean utilization < GCC mean utilization
    by >= 5 percentage points.
  - GCC's encoder-rate stddev within phase 2 > SCReAM's by >= 20%.

Loads the scream-vs-gcc-720p experiment record, uses the adaptation
dimension's analyzer to extract per-arm phase utilization and rate
stddev, and applies the thresholds.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import _verifier as V   # noqa: E402
from analysis.dimensions import adaptation       # noqa: E402


HID    = "H1"
CLAIM  = "SCReAM under-utilizes bandwidth vs GCC on capacity-step networks"
SOURCE = "Zhang 2019 SIMUtools (comparative-eval)"
DEFAULT_EXPERIMENT = "scream-vs-gcc-720p"
# The "impaired" phase index identifies the bottleneck steps in the
# network spec. For the original 3-phase fluctuating spec it's
# index 1 (single bottleneck step). For the 10-phase fast variant
# it's the indices of all 300 kbps steps, which we average over.
# The verifier picks all phases whose cap_kbps equals the spec's
# minimum cap, treating them collectively as "the impaired regime."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default=DEFAULT_EXPERIMENT,
                    help="experiment name (file stem under "
                         "runs/experiments/, default: scream-vs-gcc-720p)")
    args = ap.parse_args()

    V.print_header(f"{HID} [{args.experiment}]", CLAIM, SOURCE)
    rec = V.try_load_experiment(args.experiment)
    if rec is None:
        return V.print_untested(
            f"no experiment record at runs/experiments/{args.experiment}.json"
            f" — run `python3 experiment.py {args.experiment}` first")

    report = adaptation.analyze_experiment(rec)
    per_arm = report["per_arm"]

    # Find the SCReAM and GCC arms by config ids.
    # Convention in this experiment: configs declare their CC algorithm
    # via congestion_control.algorithm; we identify arms by reading the
    # config files, not by label string matching.
    import yaml
    arms_by_algo: dict[str, str] = {}
    for cid in per_arm:
        cfg_path = PROJECT_ROOT / "specs" / "configurations" / f"{cid}.yaml"
        if not cfg_path.is_file():
            continue
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        algo = ((cfg.get("congestion_control") or {}).get("algorithm"))
        if algo in ("scream", "gcc"):
            arms_by_algo[algo] = cid

    if "scream" not in arms_by_algo or "gcc" not in arms_by_algo:
        return V.print_untested(
            f"expected one scream and one gcc arm in {EXPERIMENT}; "
            f"found {sorted(arms_by_algo)}")

    def phase_stats(cid: str) -> tuple[float, float] | None:
        """(mean utilization, mean rate stddev) across the bottleneck
        phases. Bottleneck = phases whose cap_kbps equals the minimum
        cap across all phases. Aggregates across multiple bottleneck
        phases (the fast spec has 5; the slow spec has 1) by pooling
        their util_xs and mean_xs."""
        phases = per_arm[cid]["phases"]
        if not phases:
            return None
        caps = [p["cap_kbps"] for p in phases if p.get("cap_kbps")]
        if not caps:
            return None
        min_cap = min(caps)
        impaired = [p for p in phases if p.get("cap_kbps") == min_cap]
        util_xs = [u for p in impaired for u in p["util_xs"]]
        mean_xs = [m for p in impaired for m in p["mean_xs"]]
        if not util_xs or not mean_xs:
            return None
        util_mean = statistics.mean(util_xs)
        rate_stddev = (statistics.stdev(mean_xs) if len(mean_xs) > 1 else 0.0)
        return util_mean, rate_stddev

    scream_stats = phase_stats(arms_by_algo["scream"])
    gcc_stats    = phase_stats(arms_by_algo["gcc"])
    if scream_stats is None or gcc_stats is None:
        return V.print_untested(
            "no encoder_target_kbps adaptation samples in the impaired "
            "phase — re-run with `encoder_target_kbps` in scenario.metrics")

    s_util, s_var = scream_stats
    g_util, g_var = gcc_stats
    util_gap = g_util - s_util         # positive if SCReAM under-utilizes
    var_ratio = (g_var / s_var) if s_var > 0 else float("inf")

    util_pred = util_gap >= 0.05
    var_pred  = var_ratio >= 1.20

    numbers = {
        "scream_util_mean": f"{s_util:.3f}",
        "gcc_util_mean":    f"{g_util:.3f}",
        "util_gap (gcc - scream)": f"{util_gap:+.3f}  (need ≥ +0.050)",
        "scream_rate_stddev_kbps": f"{s_var:.1f}",
        "gcc_rate_stddev_kbps":    f"{g_var:.1f}",
        "var_ratio (gcc/scream)":  f"{var_ratio:.2f}  (need ≥ 1.20)",
    }
    if util_pred and var_pred:
        return V.print_verdict("supported", numbers,
                               "both predictions hold")
    if not util_pred and not var_pred:
        return V.print_verdict("refuted", numbers,
                               "neither prediction holds")
    return V.print_verdict("inconclusive", numbers,
                           f"util_pred={util_pred}, var_pred={var_pred}")


if __name__ == "__main__":
    main()
