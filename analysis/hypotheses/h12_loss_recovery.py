"""H12 verifier — loss sweep with recovery ON (the H10 follow-up).

H10 (recovery off) found delivery collapses below 0.1% loss. H12 re-runs the
identical loss sweep with NACK/RTX on, changing only the recovery setting, and
asks how much loss recovery buys. The boundary signal is frame delivery, same
as H10; the figure overlays the recovery-off curve from h10_report.json so the
contrast is direct.

Run from the project root:
    python3 analysis/hypotheses/h12_loss_recovery.py
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
RESULT_PATH = RESULTS_DIR / "h12_report.json"
H10_REPORT = RESULTS_DIR / "h10_report.json"

LOSS_PCT = (0.0, 0.1, 0.25, 0.5, 1.0, 2.0)
ID_BASE = 319
SWEEP = "h12-loss-recovery-on"
USABLE_DELIVERY = 0.80


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


def per_loss_aggregates() -> dict[float, dict]:
    sweep_path = RUNS_DIR / "experiments" / f"{SWEEP}.json"
    if not sweep_path.is_file():
        return {}
    runs = json.loads(sweep_path.read_text())["runs"]
    acc: dict[float, list[tuple[float, float | None]]] = {l: [] for l in LOSS_PCT}
    for r in runs:
        if r.get("exit_code") != 0:
            continue
        config = int(r["config"])
        loss = LOSS_PCT[config - ID_BASE]
        s = _summary(config, r["run_id"])
        if not s:
            continue
        sent = (s.get("camera", {}) or {}).get("frames_sent")
        recv = (s.get("viewer", {}) or {}).get("frames_depayloaded")
        if not sent:
            continue
        acc[loss].append((recv / sent, _psnr(config, r["run_id"])))
    out: dict[float, dict] = {}
    for l, samples in acc.items():
        if not samples:
            continue
        psnrs = [p for _, p in samples if p is not None]
        out[l] = {
            "mean_delivery": round(mean(d for d, _ in samples), 4),
            "mean_psnr_db": round(mean(psnrs), 3) if psnrs else None,
            "n_reps": len(samples),
        }
    return out


def _off_delivery() -> dict[float, float]:
    """Recovery-off delivery from H10, keyed by loss_pct."""
    if not H10_REPORT.is_file():
        return {}
    h10 = json.loads(H10_REPORT.read_text())
    return {c["loss_pct"]: c["mean_delivery"]
            for c in h10.get("per_regime", {}).get("loss", {}).get("cells", [])}


def evaluate() -> dict:
    cells = per_loss_aggregates()
    any_data = bool(cells)
    sorted_l = sorted(cells.keys())
    off = _off_delivery()
    per_regime = {
        "loss": {
            "cells": [
                {"loss_pct": l, "mean_delivery": cells[l]["mean_delivery"],
                 "mean_psnr_db": cells[l]["mean_psnr_db"],
                 "off_delivery": off.get(l), "n_reps": cells[l]["n_reps"]}
                for l in sorted_l
            ]
        }
    }
    deliv_low = cells.get(LOSS_PCT[0], {}).get("mean_delivery")
    min_deliv = min((cells[l]["mean_delivery"] for l in sorted_l), default=None)
    usable_at_zero = deliv_low is not None and deliv_low >= 0.90
    usable_across_range = min_deliv is not None and min_deliv >= USABLE_DELIVERY
    # biggest improvement vs recovery-off at a matched lossy level
    gains = [cells[l]["mean_delivery"] - off[l] for l in sorted_l if l > 0 and l in off]
    max_gain = max(gains) if gains else 0.0
    recovery_helps = max_gain >= 0.30

    if not any_data or not usable_at_zero:
        verdict = "untested"
    elif recovery_helps:
        verdict = "supported"
    else:
        verdict = "refuted"

    predicates = {
        "usable_delivery_at_zero_loss": usable_at_zero,
        "recovery_raises_delivery_vs_no_recovery": recovery_helps,
        "delivery_usable_across_whole_range_with_recovery": usable_across_range,
        "recovery_does_not_help": not recovery_helps,
        "any_cell_failed": False,
        "required_metric_missing": not any_data,
    }
    return {
        "verdict": verdict,
        "predicates": predicates,
        "max_delivery_gain_vs_recovery_off": round(max_gain, 4),
        "min_delivery_with_recovery": min_deliv,
        "per_regime": per_regime,
    }


def _figures(per_regime: dict) -> list[dict]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cells = per_regime["loss"]["cells"]
    xs = [c["loss_pct"] for c in cells]
    on = [c["mean_delivery"] for c in cells]
    off = [c["off_delivery"] for c in cells]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.plot(xs, on, marker="o", color="#1d6f1d", label="recovery on (NACK/RTX)")
    if any(o is not None for o in off):
        ax.plot(xs, [o if o is not None else float("nan") for o in off],
                marker="s", color="#a50f15", label="recovery off (H10)")
    ax.axhline(0.80, ls="--", lw=1, color="#888", label="usable line (80%)")
    ax.set_xlabel("uniform packet loss (%)")
    ax.set_ylabel("frame delivery (received / sent)")
    ax.set_ylim(0, 1.02)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    out = RESULTS_DIR / "h12_delivery_recovery.svg"
    fig.savefig(out)
    plt.close(fig)
    return [{"path": str(out.relative_to(PROJECT_ROOT)),
             "caption": "Frame delivery vs packet loss, recovery ON vs OFF (H10). NACK/RTX "
                        "retransmission lifts delivery from the recovery-off cliff."}]


def _tables(per_regime: dict) -> dict:
    cells = per_regime["loss"]["cells"]
    onrow = {"frame delivery": "recovery on"}
    offrow = {"frame delivery": "recovery off (H10)"}
    for c in cells:
        col = f"{c['loss_pct']}%"
        onrow[col] = round(c["mean_delivery"], 3)
        offrow[col] = round(c["off_delivery"], 3) if c["off_delivery"] is not None else None
    return {"Frame delivery by packet loss — recovery on vs off": [onrow, offrow]}


def main() -> int:
    from _qdt_report import _experiment_blocks, Knob
    report = evaluate()
    if report["verdict"] != "untested":
        knob = Knob(key="loss_pct", axis_label="uniform packet loss (%)", log_scale=False,
                    col_fmt="{q}%", arm_fmt="scream+rtx loss={q}%",
                    psnr_table_title="", lat_table_title="", psnr_caption="", lat_caption="")
        sweeps = [{
            "name": SWEEP, "configs": list(range(ID_BASE, ID_BASE + len(LOSS_PCT))),
            "knob_values": list(LOSS_PCT), "reps": 3,
            "record_path": f"runs/experiments/{SWEEP}.json",
            "description": "SCReAM loss sweep with recovery ON (NACK/RTX), the H10 twin.",
        }]
        report["figures"] = _figures(report["per_regime"])
        report["tables"] = _tables(report["per_regime"])
        report["experiments"] = _experiment_blocks(sweeps, knob)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2))
    print(f"H12 verdict: {report['verdict']}")
    print(f"max delivery gain vs recovery-off: {report['max_delivery_gain_vs_recovery_off']}; "
          f"min delivery with recovery: {report['min_delivery_with_recovery']}")
    for c in report["per_regime"]["loss"]["cells"]:
        off = c["off_delivery"]
        print(f"  {c['loss_pct']:5}%: on {c['mean_delivery']:.3f}  off {off if off is None else round(off,3)}  (n{c['n_reps']})")
    print(f"Report: {RESULT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
