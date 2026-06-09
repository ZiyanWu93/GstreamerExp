"""Shared report-section builder for the H6/H7 queue-delay-target sweeps.

The page template renders a hypothesis from `report` JSON keys:
  - tables       : {title: [row_dict, ...]}   -> results tables
  - figures      : [{path, caption}]           -> embedded SVGs
  - experiments  : [block, ...]                -> "Experimental setup"

The custom QDT verifiers compute `per_regime` curves; this module turns
those plus the sweep metadata into the three render-ready sections so the
setup and results actually show on the page.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = PROJECT_ROOT / "analysis" / "hypotheses" / "results"

REGIME_LABEL = {"static": "fixed 5 Mbps",
                "fluct": "capacity step (5 Mbps ⇄ 300 kbps)",
                "5g": "5G CQI trace"}
REGIME_COLOR = {"static": "#d62728", "fluct": "#1f77b4", "5g": "#2ca02c"}


def _curve_tables(per_regime: dict, regimes, knob_ms) -> dict[str, list[dict]]:
    """Two tables: PSNR and p95 latency, rows = regimes, cols = knob values."""
    def cell_map(reg, key):
        return {c["qdt_ms"]: c[key] for c in per_regime[reg]["cells"]}

    psnr_rows, lat_rows = [], []
    for reg in regimes:
        pm = cell_map(reg, "mean_psnr_db")
        lm = cell_map(reg, "mean_p95_latency_ms")
        prow = {"network": REGIME_LABEL.get(reg, reg)}
        lrow = {"network": REGIME_LABEL.get(reg, reg)}
        for q in knob_ms:
            prow[f"{q} ms"] = pm.get(q)
            lrow[f"{q} ms"] = lm.get(q)
        psnr_rows.append(prow)
        lat_rows.append(lrow)
    return {
        "Decoded PSNR (dB) by queue-delay-target": psnr_rows,
        "p95 frame latency (ms) by queue-delay-target": lat_rows,
    }


def _curve_figures(slug: str, per_regime: dict, regimes, knob_ms) -> list[dict]:
    """PSNR-vs-knob and latency-vs-knob line plots (one line per network)."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    figs = []
    for key, ylabel, fname, caption in [
        ("mean_psnr_db", "decoded PSNR (dB)", f"{slug}_psnr_by_knob.svg",
         "Decoded PSNR vs queue-delay-target. Flat or falling on every "
         "network — loosening the knob does not raise quality."),
        ("mean_p95_latency_ms", "p95 frame latency (ms)",
         f"{slug}_latency_by_knob.svg",
         "p95 frame latency vs queue-delay-target. Latency tracks the knob "
         "(the relative trend within a sweep is what matters; the absolute "
         "offset carries the aum/veda clock-skew artifact)."),
    ]:
        fig, ax = plt.subplots(figsize=(6.4, 3.6))
        for reg in regimes:
            cells = per_regime[reg]["cells"]
            xs = [c["qdt_ms"] for c in cells]
            ys = [c[key] for c in cells]
            ax.plot(xs, ys, marker="o", label=REGIME_LABEL.get(reg, reg),
                    color=REGIME_COLOR.get(reg))
        ax.set_xscale("log")
        ax.set_xticks(knob_ms)
        ax.set_xticklabels([str(q) for q in knob_ms])
        ax.set_xlabel("queue-delay-target (ms, log scale)")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        out = RESULTS_DIR / fname
        fig.savefig(out)
        plt.close(fig)
        figs.append({"path": str(out.relative_to(PROJECT_ROOT)), "caption": caption})
    return figs


def _experiment_blocks(sweeps: list[dict]) -> list[dict]:
    """One setup block per sweep: configs, reps, record path, completion.

    sweeps: [{name, configs:[int], knob_ms:[int], record_path, reps}]
    """
    import json
    blocks = []
    for s in sweeps:
        rec = PROJECT_ROOT / s["record_path"]
        completed = total = 0
        if rec.is_file():
            runs = json.loads(rec.read_text()).get("runs", [])
            total = len(runs)
            completed = sum(1 for r in runs if r.get("exit_code") == 0)
        arms = [{"config_id": c, "algorithm": f"scream qdt={q}ms"}
                for c, q in zip(s["configs"], s["knob_ms"])]
        blocks.append({
            "name": s["name"],
            "description": s.get("description", ""),
            "arms": arms,
            "reps": s.get("reps"),
            "spec_path": f"specs/experiments/{s['name']}.yaml",
            "record_path": s["record_path"],
            "command": f"python3 tools/run_qdt_sweeps.py  # or experiment.py {s['name']} --resume",
            "record_status": {"completed_runs": completed, "total_runs": total},
        })
    return blocks


def build_extras(slug: str, per_regime: dict, regimes, knob_ms,
                 sweeps: list[dict]) -> dict[str, Any]:
    """Return {tables, figures, experiments} ready to merge into the report."""
    return {
        "tables": _curve_tables(per_regime, regimes, knob_ms),
        "figures": _curve_figures(slug, per_regime, regimes, knob_ms),
        "experiments": _experiment_blocks(sweeps),
    }
