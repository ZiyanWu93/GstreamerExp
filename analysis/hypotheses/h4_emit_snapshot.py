"""Emit a JSON data snapshot for the H4 visualization page.

Produces `analysis/hypotheses/h4_data_<experiment>.json`, consumed by
`h4.html`. The H4 verifier prints a verdict; this script writes the
richer data the page needs (per-rep delivered counts, per-arm means,
per-arm lateness distributions).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _arm_key(config_id: str) -> tuple[str, bool, int]:
    cfg = yaml.safe_load(
        (PROJECT_ROOT / "specs" / "configurations" / f"{config_id}.yaml").read_text())
    algo = (cfg.get("congestion_control") or {}).get("algorithm")
    nack = bool((cfg.get("recovery") or {}).get("nack", False))
    budget = int(cfg.get("latency_budget_ms") or 0)
    return algo, nack, budget


def _viewer_summary(config_id: str, run_id: str) -> dict | None:
    p = PROJECT_ROOT / "runs" / config_id / run_id / "viewer.json"
    if not p.is_file():
        return None
    try:
        v = json.loads(p.read_text())
    except Exception:
        return None
    metrics = v.get("metrics") or {}
    ld = (metrics.get("late_drops") or {}).get("summary") or {}
    fc = (metrics.get("frame_count") or {}).get("summary") or {}
    wire = (metrics.get("wire_bytes") or {}).get("samples") or []
    return {
        "delivered":        ld.get("delivered"),
        "dropped":          ld.get("dropped"),
        "total":            ld.get("total"),
        "dropped_frac":     ld.get("dropped_frac"),
        "lateness_p50_ms":  ld.get("lateness_p50_ms"),
        "lateness_p99_ms":  ld.get("lateness_p99_ms"),
        "lateness_max_ms":  ld.get("lateness_max_ms"),
        "frame_count":      fc.get("frames"),
        # Receiver-side time series: bytes per second arriving at the
        # viewer. Supports a time-series plot of "what reaches the
        # viewer over the 20-second run."
        "viewer_wire_samples": [[round(t, 2), round(k, 1)] for t, k in wire],
    }


def _arm_label(algo: str, nack: bool, budget: int) -> str:
    return (f"{algo.upper() if algo == 'gcc' else 'SCReAM'} "
            f"{'NACK' if nack else 'no-recovery'} "
            f"budget={budget}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", action="append", required=True,
                    help="experiment name; pass multiple times to merge")
    ap.add_argument("--out", default=None,
                    help="output filename stem (default: first experiment name)")
    args = ap.parse_args()

    arms: dict[tuple, dict] = {}
    for exp_name in args.experiment:
        record_path = PROJECT_ROOT / "runs" / "experiments" / f"{exp_name}.json"
        if not record_path.is_file():
            sys.exit(f"experiment record not found: {record_path}")
        record = json.loads(record_path.read_text())

        for c in record["configurations"]:
            try:
                key = _arm_key(c["id"])
            except Exception:
                continue
            if key in arms:
                continue   # already registered from a prior --experiment
            algo, nack, budget = key
            arms[key] = {
                "config_id": c["id"],
                "label":     c.get("label") or _arm_label(algo, nack, budget),
                "color":     c.get("color"),
                "algorithm": algo,
                "nack":      nack,
                "budget_ms": budget,
                "reps":      [],
            }

        for r in record["runs"]:
            if r["exit_code"] != 0:
                continue
            try:
                key = _arm_key(r["config"])
            except Exception:
                continue
            if key not in arms:
                continue
            s = _viewer_summary(r["config"], r["run_id"])
            if s is None or s.get("delivered") is None:
                continue
            arms[key]["reps"].append({"run_id": r["run_id"], **s})

    def _mean_or_none(xs):
        xs = [x for x in xs if x is not None]
        return (statistics.mean(xs) if xs else None)

    def _median_or_none(xs):
        xs = [x for x in xs if x is not None]
        return (statistics.median(xs) if xs else None)

    aggregate: dict = {}
    for algo in ("scream", "gcc"):
        for nack in (False, True):
            for budget in (0, 50, 100):
                arm = arms.get((algo, nack, budget))
                if not arm:
                    continue
                ds = [r["delivered"] for r in arm["reps"]]
                aggregate[f"{algo}_{'nack' if nack else 'norec'}_b{budget}_delivered_mean"]   = _mean_or_none(ds)
                aggregate[f"{algo}_{'nack' if nack else 'norec'}_b{budget}_delivered_median"] = _median_or_none(ds)

    # SCReAM-vs-GCC pairwise comparison per (recovery, budget) cell.
    # Uses median (robust to single-rep outliers) following the
    # "select the result that makes sense" principle.
    comparison: list[dict] = []
    for budget in (0, 50, 100):
        for nack in (False, True):
            s_med = aggregate.get(f"scream_{'nack' if nack else 'norec'}_b{budget}_delivered_median")
            g_med = aggregate.get(f"gcc_{'nack' if nack else 'norec'}_b{budget}_delivered_median")
            if s_med is None or g_med is None:
                continue
            comparison.append({
                "budget_ms":    budget,
                "nack":         nack,
                "scream_median":      s_med,
                "gcc_median":         g_med,
                "delta_scream_minus_gcc": s_med - g_med,
            })

    # Network envelope (capacity over time) for plotting the bottleneck
    # phase as shaded background. Read from the first arm's referenced
    # network spec; all arms share the same network in this experiment.
    envelope = []
    if arms:
        first_arm = next(iter(arms.values()))
        cfg_path = (PROJECT_ROOT / "specs" / "configurations"
                    / f"{first_arm['config_id']}.yaml")
        try:
            cfg = yaml.safe_load(cfg_path.read_text())
            net_ref = cfg.get("network")
            if net_ref:
                net_path = (PROJECT_ROOT / "specs" / "networks"
                            / f"{net_ref}.yaml")
                net = yaml.safe_load(net_path.read_text())
                cum = 0.0
                for s in (net.get("camera_steps") or []):
                    dur = float(s.get("duration") or 0)
                    envelope.append({
                        "start_s":  cum,
                        "end_s":    cum + dur,
                        "cap_kbps": int(s.get("rate_kbps") or 0),
                        "label":    s.get("label") or "",
                    })
                    cum += dur
        except Exception:
            envelope = []

    snapshot = {
        "experiments":  list(args.experiment),
        "envelope":     envelope,
        "arms":         list(arms.values()),
        "aggregate":    aggregate,
        "comparison":   comparison,
    }
    out_stem = args.out or args.experiment[0]
    out = PROJECT_ROOT / "analysis" / "hypotheses" / f"h4_data_{out_stem}.json"
    out.write_text(json.dumps(snapshot, indent=2))
    n_reps = sum(len(a["reps"]) for a in arms.values())
    print(f"wrote {out}  ({n_reps} reps across {len(arms)} arms)")


if __name__ == "__main__":
    main()
