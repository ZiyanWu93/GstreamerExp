"""H11 verifier — SCReAM jitter-boundary sweep (third §7.8 boundary axis).

On a fixed 5 Mbps / 20 ms link, sweep netem jitter {0, 5, 10, 20, 50, 100} ms
(delay 20 ms +/- J), no loss, recovery off. Jitter reorders/delays packets:
within the receiver jitter buffer they are reabsorbed (latency cost); beyond it
they arrive late and are dropped (delivery cost). Like loss (H10), the dominant
signal is FRAME DELIVERY, so we read the boundary off delivery and report
PSNR-of-delivered and p95 latency alongside.

Run from the project root:
    python3 analysis/hypotheses/h11_jitter_boundary.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"
RESULTS_DIR = PROJECT_ROOT / "analysis" / "hypotheses" / "results"
RESULT_PATH = RESULTS_DIR / "h11_report.json"

JITTER_MS = (0, 5, 10, 20, 50, 100)
ID_BASE = 313
SWEEP = "h11-jitter-boundary"
USABLE_DELIVERY = 0.80
COLLAPSE_DELIVERY = 0.50


def _summary(config: int, run_id: str) -> dict | None:
    p = RUNS_DIR / str(config) / run_id / "summary.json"
    return json.loads(p.read_text()) if p.is_file() else None


def _psnr(config: int, run_id: str) -> float | None:
    p = RUNS_DIR / str(config) / run_id / "viewer.json"
    if not p.is_file():
        return None
    v = json.loads(p.read_text())
    return (v.get("metrics", {}).get("decoded_psnr", {})
             .get("summary", {}).get("mean_psnr_db"))


def per_jitter_aggregates() -> dict[int, dict]:
    sweep_path = RUNS_DIR / "experiments" / f"{SWEEP}.json"
    if not sweep_path.is_file():
        return {}
    runs = json.loads(sweep_path.read_text())["runs"]
    acc: dict[int, list[tuple[float, float | None, float | None]]] = {j: [] for j in JITTER_MS}
    for r in runs:
        if r.get("exit_code") != 0:
            continue
        config = int(r["config"])
        jitter = JITTER_MS[config - ID_BASE]
        s = _summary(config, r["run_id"])
        if not s:
            continue
        sent = (s.get("camera", {}) or {}).get("frames_sent")
        recv = (s.get("viewer", {}) or {}).get("frames_depayloaded")
        if not sent:
            continue
        p95 = (s.get("latency", {}) or {}).get("p95_ms")
        acc[jitter].append((recv / sent, _psnr(config, r["run_id"]), p95))
    out: dict[int, dict] = {}
    for j, samples in acc.items():
        if not samples:
            continue
        psnrs = [p for _, p, _ in samples if p is not None]
        lats = [l for _, _, l in samples if l is not None]
        out[j] = {
            "mean_delivery": round(mean(d for d, _, _ in samples), 4),
            "mean_psnr_db": round(mean(psnrs), 3) if psnrs else None,
            "mean_p95_latency_ms": round(mean(lats), 3) if lats else None,
            "n_reps": len(samples),
        }
    return out


def evaluate() -> dict:
    cells = per_jitter_aggregates()
    any_data = bool(cells)
    sorted_j = sorted(cells.keys())
    per_regime = {
        "jitter": {
            "cells": [
                {"jitter_ms": j, "mean_delivery": cells[j]["mean_delivery"],
                 "mean_psnr_db": cells[j]["mean_psnr_db"],
                 "mean_p95_latency_ms": cells[j]["mean_p95_latency_ms"],
                 "n_reps": cells[j]["n_reps"]}
                for j in sorted_j
            ]
        }
    }
    deliv_low = cells.get(JITTER_MS[0], {}).get("mean_delivery")     # 0 jitter
    deliv_high = cells.get(JITTER_MS[-1], {}).get("mean_delivery")   # 100 ms
    all_deliv = [cells[j]["mean_delivery"] for j in sorted_j]

    usable_at_zero = deliv_low is not None and deliv_low >= 0.90
    collapses = (deliv_high is not None and deliv_high < COLLAPSE_DELIVERY) or \
                (all_deliv and min(all_deliv) < COLLAPSE_DELIVERY)
    threshold_ms = next((j for j in sorted_j if cells[j]["mean_delivery"] < USABLE_DELIVERY), None)

    if not any_data:
        verdict = "untested"
    elif not usable_at_zero:
        verdict = "untested"
    elif collapses:
        verdict = "supported"
    else:
        verdict = "refuted"

    predicates = {
        "usable_delivery_at_zero_jitter": usable_at_zero,
        "delivery_collapses_across_the_jitter_range": bool(collapses),
        "usable_threshold_found_below_max_jitter": threshold_ms is not None,
        "delivery_robust_across_whole_range": usable_at_zero and not collapses,
        "any_cell_failed": False,
        "required_metric_missing": not any_data,
    }
    return {
        "verdict": verdict,
        "predicates": predicates,
        "delivery_at_zero_jitter": deliv_low,
        "delivery_at_max_jitter": deliv_high,
        "usable_threshold_ms": threshold_ms,
        "per_regime": per_regime,
    }


def _figures(per_regime: dict) -> list[dict]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cells = per_regime["jitter"]["cells"]
    xs = [c["jitter_ms"] for c in cells]
    figs = []
    for key, ylabel, fname, caption, hline in [
        ("mean_delivery", "frame delivery (received / sent)", "h11_delivery_by_jitter.svg",
         "Frame delivery vs netem jitter (recovery off). The boundary is where late "
         "and reordered packets fall outside the jitter buffer and delivery drops.", 0.80),
        ("mean_psnr_db", "decoded PSNR of delivered frames (dB)", "h11_psnr_by_jitter.svg",
         "PSNR of the frames that do arrive, vs jitter.", None),
    ]:
        ys = [c[key] for c in cells]
        fig, ax = plt.subplots(figsize=(6.4, 3.6))
        ax.plot(xs, ys, marker="o", color="#9467bd")
        if hline is not None:
            ax.axhline(hline, ls="--", lw=1, color="#888", label=f"usable line ({hline:.0%})")
            ax.legend(fontsize=8)
        ax.set_xlabel("netem jitter (ms, on a 20 ms base delay)")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        out = RESULTS_DIR / fname
        fig.savefig(out)
        plt.close(fig)
        figs.append({"path": str(out.relative_to(PROJECT_ROOT)), "caption": caption})
    return figs


def _tables(per_regime: dict) -> dict:
    cells = per_regime["jitter"]["cells"]
    drow = {"metric": "frame delivery"}
    prow = {"metric": "PSNR of delivered (dB)"}
    lrow = {"metric": "p95 latency (ms)"}
    for c in cells:
        col = f"{c['jitter_ms']} ms"
        drow[col] = round(c["mean_delivery"], 3)
        prow[col] = c["mean_psnr_db"]
        lrow[col] = c["mean_p95_latency_ms"]
    return {"Delivery, PSNR and latency by jitter": [drow, prow, lrow]}


def main() -> int:
    from _qdt_report import _experiment_blocks, Knob
    report = evaluate()
    if report["verdict"] != "untested":
        jitter_knob = Knob(key="jitter_ms", axis_label="netem jitter (ms)", log_scale=False,
                           col_fmt="{q} ms", arm_fmt="scream jitter={q}ms",
                           psnr_table_title="", lat_table_title="",
                           psnr_caption="", lat_caption="")
        sweeps = [{
            "name": SWEEP, "configs": list(range(ID_BASE, ID_BASE + len(JITTER_MS))),
            "knob_values": list(JITTER_MS), "reps": 3,
            "record_path": f"runs/experiments/{SWEEP}.json",
            "description": "SCReAM jitter-boundary sweep on a fixed 5 Mbps / 20 ms link, recovery off.",
        }]
        report["figures"] = _figures(report["per_regime"])
        report["tables"] = _tables(report["per_regime"])
        report["experiments"] = _experiment_blocks(sweeps, jitter_knob)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2))
    print(f"H11 verdict: {report['verdict']}")
    print(f"delivery: {report['delivery_at_zero_jitter']} @0ms -> "
          f"{report['delivery_at_max_jitter']} @100ms; usable_threshold={report['usable_threshold_ms']}ms")
    for c in report["per_regime"]["jitter"]["cells"]:
        print(f"  {c['jitter_ms']:4d}ms: delivery {c['mean_delivery']:.3f}  "
              f"PSNR {c['mean_psnr_db']}dB  p95 {c['mean_p95_latency_ms']}ms  (n{c['n_reps']})")
    print(f"Report: {RESULT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
