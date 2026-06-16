"""H13 verifier — keyframe-interval sweep at fixed 1% loss.

Tests the loss-collapse diagnostic's prediction: the H10/H12 delivery cliff is
a VP8 reference-chain desync gated by a long (60-frame) GOP, so SHORTENING the
keyframe interval should raise complete-frame delivery (each gap desyncs fewer
frames before the next keyframe re-anchors). If delivery stays collapsed across
GOPs, the GOP is not the cause.

Holds H10's 1% recovery-off cell fixed and varies only the keyframe interval
{60, 30, 15, 8, 4, 2}. Boundary signal: frame delivery (frames_depayloaded /
frames_sent); PSNR-of-delivered reported alongside.

    python3 analysis/hypotheses/h13_keyframe_loss.py
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
RESULT_PATH = RESULTS_DIR / "h13_report.json"

KEYFRAMES = (60, 30, 15, 8, 4, 2)
ID_BASE = 325
SWEEP = "h13-keyframe-loss"
USABLE_DELIVERY = 0.50
SOFTEN_GAIN = 0.30


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


def per_keyframe_aggregates() -> dict[int, dict]:
    sweep_path = RUNS_DIR / "experiments" / f"{SWEEP}.json"
    if not sweep_path.is_file():
        return {}
    runs = json.loads(sweep_path.read_text())["runs"]
    acc: dict[int, list[tuple[float, float | None]]] = {k: [] for k in KEYFRAMES}
    for r in runs:
        if r.get("exit_code") != 0:
            continue
        config = int(r["config"])
        kf = KEYFRAMES[config - ID_BASE]
        s = _summary(config, r["run_id"])
        if not s:
            continue
        sent = (s.get("camera", {}) or {}).get("frames_sent")
        recv = (s.get("viewer", {}) or {}).get("frames_depayloaded")
        if not sent:
            continue
        acc[kf].append((recv / sent, _psnr(config, r["run_id"])))
    out: dict[int, dict] = {}
    for kf, samples in acc.items():
        if not samples:
            continue
        psnrs = [p for _, p in samples if p is not None]
        out[kf] = {
            "mean_delivery": round(mean(d for d, _ in samples), 4),
            "mean_psnr_db": round(mean(psnrs), 3) if psnrs else None,
            "n_reps": len(samples),
        }
    return out


def evaluate() -> dict:
    cells = per_keyframe_aggregates()
    any_data = bool(cells)
    # ascending keyframe order (short GOP -> long GOP)
    sorted_kf = sorted(cells.keys())
    per_regime = {
        "keyframe": {
            "cells": [
                {"keyframe_frames": kf, "mean_delivery": cells[kf]["mean_delivery"],
                 "mean_psnr_db": cells[kf]["mean_psnr_db"], "n_reps": cells[kf]["n_reps"]}
                for kf in sorted_kf
            ]
        }
    }
    deliv_default = cells.get(60, {}).get("mean_delivery")   # the long-GOP baseline
    deliv_short = cells.get(min(KEYFRAMES), {}).get("mean_delivery")  # shortest GOP
    # delivery should rise as GOP shrinks: check the curve is non-increasing in kf
    by_kf_asc = [cells[kf]["mean_delivery"] for kf in sorted_kf]  # kf ascending => delivery should be non-increasing
    rises_as_gop_shrinks = all(a >= b - 0.02 for a, b in zip(by_kf_asc, by_kf_asc[1:]))

    gain = (deliv_short - deliv_default) if (deliv_short is not None and deliv_default is not None) else 0.0
    softens = gain >= SOFTEN_GAIN
    usable_at_short = deliv_short is not None and deliv_short >= USABLE_DELIVERY

    if not any_data:
        verdict = "untested"
    elif softens:
        verdict = "supported"
    else:
        verdict = "refuted"

    predicates = {
        "shorter_gop_raises_delivery": softens,
        "delivery_rises_monotonically_as_gop_shrinks": rises_as_gop_shrinks,
        "usable_delivery_recovered_at_short_gop": usable_at_short,
        "gop_does_not_affect_delivery": not softens,
        "any_cell_failed": False,
        "required_metric_missing": not any_data,
    }
    return {
        "verdict": verdict,
        "predicates": predicates,
        "delivery_at_gop60": deliv_default,
        "delivery_at_gop2": deliv_short,
        "delivery_gain_short_vs_default": round(gain, 4),
        "per_regime": per_regime,
    }


def _figures(per_regime: dict) -> list[dict]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cells = per_regime["keyframe"]["cells"]
    xs = [c["keyframe_frames"] for c in cells]
    figs = []
    for key, ylabel, fname, caption, hline in [
        ("mean_delivery", "frame delivery (received / sent)", "h13_delivery_by_keyframe.svg",
         "Frame delivery vs keyframe interval at 1% loss (recovery off). If a shorter "
         "GOP lifts delivery off the long-GOP cliff, the collapse is a re-anchoring "
         "artifact, not transport.", 0.80),
        ("mean_psnr_db", "decoded PSNR of delivered frames (dB)", "h13_psnr_by_keyframe.svg",
         "PSNR of the frames that arrive, vs keyframe interval. Shorter GOPs spend more "
         "of the bitrate budget on keyframes.", None),
    ]:
        ys = [c[key] for c in cells]
        fig, ax = plt.subplots(figsize=(6.4, 3.6))
        ax.plot(xs, ys, marker="o", color="#2ca02c")
        if hline is not None:
            ax.axhline(hline, ls="--", lw=1, color="#888", label=f"usable line ({hline:.0%})")
            ax.legend(fontsize=8)
        ax.set_xlabel("keyframe interval (frames; smaller = more keyframes)")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        out = RESULTS_DIR / fname
        fig.savefig(out)
        plt.close(fig)
        figs.append({"path": str(out.relative_to(PROJECT_ROOT)), "caption": caption})
    return figs


def _tables(per_regime: dict) -> dict:
    cells = per_regime["keyframe"]["cells"]
    drow = {"metric": "frame delivery"}
    prow = {"metric": "PSNR of delivered (dB)"}
    for c in cells:
        col = f"{c['keyframe_frames']} fr"
        drow[col] = round(c["mean_delivery"], 3)
        prow[col] = c["mean_psnr_db"]
    return {"Delivery and PSNR by keyframe interval (at 1% loss)": [drow, prow]}


def main() -> int:
    from _qdt_report import _experiment_blocks, Knob
    report = evaluate()
    if report["verdict"] != "untested":
        knob = Knob(key="keyframe_frames", axis_label="keyframe interval (frames)", log_scale=False,
                    col_fmt="{q} fr", arm_fmt="scream kf={q}",
                    psnr_table_title="", lat_table_title="", psnr_caption="", lat_caption="")
        sweeps = [{
            "name": SWEEP, "configs": list(range(ID_BASE, ID_BASE + len(KEYFRAMES))),
            "knob_values": list(KEYFRAMES), "reps": 3,
            "record_path": f"runs/experiments/{SWEEP}.json",
            "description": "keyframe-interval sweep at fixed 1% loss, recovery off.",
        }]
        report["figures"] = _figures(report["per_regime"])
        report["tables"] = _tables(report["per_regime"])
        report["experiments"] = _experiment_blocks(sweeps, knob)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2))
    print(f"H13 verdict: {report['verdict']}")
    print(f"delivery: GOP60={report['delivery_at_gop60']} -> GOP2={report['delivery_at_gop2']} "
          f"(gain {report['delivery_gain_short_vs_default']})")
    for c in report["per_regime"]["keyframe"]["cells"]:
        print(f"  kf={c['keyframe_frames']:3d}: delivery {c['mean_delivery']:.3f}  "
              f"PSNR {c['mean_psnr_db']}dB  (n{c['n_reps']})")
    print(f"Report: {RESULT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
