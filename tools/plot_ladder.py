#!/usr/bin/env python3
"""Plot the factor-isolation ladder. Reads ~/comparison-runs/metrics.csv
and shows, per trace, how utilization and overshoot move as we add back
one capability at a time:

  S0  umn-scream      : delay signal OFF (network_time_sync off)
  S1  umn-scream-v2   : + delay signal (network_time_sync on)
  S2  umn-scream-s2   : + faster loop (update_interval 200->50 ms)
  S3  umn-scream-s3   : + faster ramp (ramp_up_gain 1.05->1.15)
  REF ours-scream     : our reference SCReAM (upper bound)

The change between adjacent rungs = that factor's contribution.
"""
from __future__ import annotations
import csv, os
from collections import defaultdict
from pathlib import Path
import statistics as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(os.path.expanduser("~/comparison-runs"))
PLOTS = OUT / "plots"
# (arm dir, short rung label) in ladder order
RUNGS = [
    ("umn-scream",    "S0\nno delay"),
    ("umn-scream-v2", "S1\n+delay"),
    ("umn-scream-s2", "S2\n+50ms loop"),
    ("umn-scream-s3", "S3\n+fast ramp"),
    ("ours-scream",   "REF\nours"),
]
TRACES = ["ho", "cqi"]


def load():
    rows = list(csv.DictReader(open(OUT / "metrics.csv")))
    g = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # arm->trace->metric->[vals]
    for r in rows:
        for m in ("utilization", "overshoot_frac"):
            try:
                g[r["arm"]][r["trace"]][m].append(float(r[m]))
            except ValueError:
                pass
    return g


def main():
    PLOTS.mkdir(parents=True, exist_ok=True)
    g = load()
    fig, axes = plt.subplots(2, len(TRACES), figsize=(12, 8))
    labels = [lbl for _, lbl in RUNGS]
    x = range(len(RUNGS))
    for col, trace in enumerate(TRACES):
        for row, (metric, title, ideal) in enumerate([
            ("utilization", "Link utilization (sent / capacity)", 0.9),
            ("overshoot_frac", "Overshoot fraction (sent > capacity)", 0.0),
        ]):
            ax = axes[row][col]
            means = [st.mean(g[arm][trace][metric]) if g[arm][trace][metric] else 0
                     for arm, _ in RUNGS]
            errs = [st.pstdev(g[arm][trace][metric]) if len(g[arm][trace][metric]) > 1 else 0
                    for arm, _ in RUNGS]
            colors = ["#d62728", "#ff7f0e", "#bcbd22", "#2ca02c", "#1f77b4"]
            ax.bar(list(x), means, yerr=errs, capsize=3, color=colors)
            if metric == "utilization":
                ax.axhline(1.0, color="0.3", ls="--", lw=1, label="capacity (1.0)")
                ax.axhline(ideal, color="green", ls=":", lw=1, label="~ideal (0.9)")
                ax.legend(fontsize=7)
            ax.set_xticks(list(x)); ax.set_xticklabels(labels, fontsize=8)
            ax.set_title(f"{trace.upper()} — {title}", fontsize=10)
            ax.grid(alpha=0.3, axis="y")
            for xi, m in zip(x, means):
                ax.text(xi, m, f"{m:.2f}", ha="center", va="bottom", fontsize=8)
    fig.suptitle("Factor isolation ladder — adding back one capability per rung\n"
                 "(Rushi's SCReAM walked toward our reference)", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    p = PLOTS / "isolation_ladder.png"
    fig.savefig(p, dpi=120)
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
