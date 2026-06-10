"""Shared report-section builder for the knob-sensitivity sweeps (H6/H7/H8).

The page template renders a hypothesis from `report` JSON keys:
  - tables       : {title: [row_dict, ...]}   -> results tables
  - figures      : [{path, caption}]           -> embedded SVGs
  - experiments  : [block, ...]                -> "Experimental setup"

The custom sweep verifiers compute `per_regime` curves; this module turns
those plus the sweep metadata into the three render-ready sections so the
setup and results actually show on the page.

The swept knob differs per hypothesis (queue-delay-target for H6/H7,
mul_increase for H8), so the axis labels, column headers, captions, and the
per-cell value key all come from a `Knob` descriptor rather than being
hard-coded. Each verifier passes the descriptor for its knob.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = PROJECT_ROOT / "analysis" / "hypotheses" / "results"

REGIME_LABEL = {"static": "fixed 5 Mbps",
                "fluct": "capacity step (5 Mbps ⇄ 300 kbps)",
                "5g": "5G CQI trace",
                "rtt": "SCReAM, fixed 5 Mbps"}
REGIME_COLOR = {"static": "#d62728", "fluct": "#1f77b4", "5g": "#2ca02c",
                "rtt": "#1f77b4"}


@dataclass(frozen=True)
class Knob:
    """Everything the report sections need to know about the swept knob."""
    key: str            # per-cell dict key holding the knob value
    axis_label: str     # x-axis label
    log_scale: bool     # log x-axis?
    col_fmt: str        # table column header, e.g. "{q} ms" or "mul {q}"
    arm_fmt: str        # experiment-arm label, e.g. "scream qdt={q}ms"
    psnr_table_title: str
    lat_table_title: str
    psnr_caption: str
    lat_caption: str


QDT = Knob(
    key="qdt_ms",
    axis_label="queue-delay-target (ms, log scale)",
    log_scale=True,
    col_fmt="{q} ms",
    arm_fmt="scream qdt={q}ms",
    psnr_table_title="Decoded PSNR (dB) by queue-delay-target",
    lat_table_title="p95 frame latency (ms) by queue-delay-target",
    psnr_caption=("Decoded PSNR vs queue-delay-target. Flat or falling on every "
                  "network — loosening the knob does not raise quality."),
    lat_caption=("p95 frame latency vs queue-delay-target. Latency tracks the knob "
                 "(the relative trend within a sweep is what matters; the absolute "
                 "offset carries the aum/veda clock-skew artifact)."),
)

RTT = Knob(
    key="rtt_ms",
    axis_label="base one-way delay (ms, log scale)",
    log_scale=True,
    col_fmt="{q} ms",
    arm_fmt="scream delay={q}ms",
    psnr_table_title="Decoded PSNR (dB) by base delay",
    lat_table_title="p95 frame latency (ms) by base delay",
    psnr_caption=("Decoded PSNR vs base one-way delay (the RTT boundary). The "
                  "degeneration point, if any, is the delay at which quality cliffs."),
    lat_caption=("p95 frame latency vs base delay. Latency rises partly because the "
                 "added netem delay is itself in the path; the absolute offset also "
                 "carries the aum/veda clock-skew artifact."),
)

LOSS = Knob(
    key="loss_pct",
    axis_label="uniform packet loss (%)",
    log_scale=False,
    col_fmt="{q}%",
    arm_fmt="scream loss={q}%",
    psnr_table_title="Decoded PSNR (dB) by packet loss",
    lat_table_title="p95 frame latency (ms) by packet loss",
    psnr_caption=("Decoded PSNR vs uniform packet loss (the loss boundary). With "
                  "recovery off, the degeneration point is the loss rate at which "
                  "quality cliffs."),
    lat_caption=("p95 frame latency vs packet loss. The within-sweep trend is what "
                 "matters; the absolute offset carries the aum/veda clock-skew artifact."),
)

MULINC = Knob(
    key="mul_increase",
    axis_label="mul_increase (multiplicative-increase coefficient, log scale)",
    log_scale=True,
    col_fmt="mul {q}",
    arm_fmt="scream mul_increase={q}",
    psnr_table_title="Decoded PSNR (dB) by mul_increase",
    lat_table_title="p95 frame latency (ms) by mul_increase",
    psnr_caption=("Decoded PSNR vs mul_increase (ramp aggressiveness). The fixed "
                  "5 Mbps link is the control, where ramp speed should not matter; "
                  "the capacity-step link is where a faster ramp should recover "
                  "quality after the step."),
    lat_caption=("p95 frame latency vs mul_increase. The within-sweep trend is what "
                 "matters; the absolute offset carries the aum/veda clock-skew "
                 "artifact."),
)


def _curve_tables(per_regime: dict, regimes, knob_values, knob: Knob) -> dict[str, list[dict]]:
    """Two tables: PSNR and p95 latency, rows = regimes, cols = knob values."""
    def cell_map(reg, key):
        return {c[knob.key]: c[key] for c in per_regime[reg]["cells"]}

    psnr_rows, lat_rows = [], []
    for reg in regimes:
        pm = cell_map(reg, "mean_psnr_db")
        lm = cell_map(reg, "mean_p95_latency_ms")
        prow = {"network": REGIME_LABEL.get(reg, reg)}
        lrow = {"network": REGIME_LABEL.get(reg, reg)}
        for q in knob_values:
            col = knob.col_fmt.format(q=q)
            prow[col] = pm.get(q)
            lrow[col] = lm.get(q)
        psnr_rows.append(prow)
        lat_rows.append(lrow)
    return {
        knob.psnr_table_title: psnr_rows,
        knob.lat_table_title: lat_rows,
    }


def _curve_figures(slug: str, per_regime: dict, regimes, knob_values, knob: Knob) -> list[dict]:
    """PSNR-vs-knob and latency-vs-knob line plots (one line per network)."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    figs = []
    for key, ylabel, fname, caption in [
        ("mean_psnr_db", "decoded PSNR (dB)", f"{slug}_psnr_by_knob.svg", knob.psnr_caption),
        ("mean_p95_latency_ms", "p95 frame latency (ms)", f"{slug}_latency_by_knob.svg", knob.lat_caption),
    ]:
        fig, ax = plt.subplots(figsize=(6.4, 3.6))
        for reg in regimes:
            cells = per_regime[reg]["cells"]
            xs = [c[knob.key] for c in cells]
            ys = [c[key] for c in cells]
            ax.plot(xs, ys, marker="o", label=REGIME_LABEL.get(reg, reg),
                    color=REGIME_COLOR.get(reg))
        if knob.log_scale:
            ax.set_xscale("log")
        ax.set_xticks(knob_values)
        ax.set_xticklabels([str(q) for q in knob_values])
        ax.set_xlabel(knob.axis_label)
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        out = RESULTS_DIR / fname
        fig.savefig(out)
        plt.close(fig)
        figs.append({"path": str(out.relative_to(PROJECT_ROOT)), "caption": caption})
    return figs


def _experiment_blocks(sweeps: list[dict], knob: Knob) -> list[dict]:
    """One setup block per sweep: configs, reps, record path, completion.

    sweeps: [{name, configs:[int], knob_values:[...], record_path, reps}]
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
        arms = [{"config_id": c, "algorithm": knob.arm_fmt.format(q=q)}
                for c, q in zip(s["configs"], s["knob_values"])]
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


def build_extras(slug: str, per_regime: dict, regimes, knob_values,
                 sweeps: list[dict], knob: Knob) -> dict[str, Any]:
    """Return {tables, figures, experiments} ready to merge into the report."""
    return {
        "tables": _curve_tables(per_regime, regimes, knob_values, knob),
        "figures": _curve_figures(slug, per_regime, regimes, knob_values, knob),
        "experiments": _experiment_blocks(sweeps, knob),
    }
