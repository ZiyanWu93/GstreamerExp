#!/usr/bin/env python3
"""Plot the SCReAM-vs-SCReAM comparison. Runs ON aum (headless, Agg).
Reads ~/comparison-runs/metrics.csv + timeseries/*.csv, writes PNGs to
~/comparison-runs/plots/.

Figures:
  1. bitrate-vs-time per trace: our SCReAM vs UMN SCReAM sent rate,
     overlaid on the capacity curve (rep1 shown).
  2. aggregate bars: utilization, overshoot fraction, delivered-frame
     fraction — per arm per trace, mean over reps with error bars.
"""
from __future__ import annotations
import csv, glob, os
from collections import defaultdict
from pathlib import Path
import statistics as st

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(os.path.expanduser("~/comparison-runs"))
PLOTS = OUT / "plots"
TRACES = ["ho", "rb", "cqi"]
ARMS = [("ours-scream", "Our SCReAM (reference)", "#1f77b4"),
        ("umn-scream-v2", "Rushi's SCReAM (reimpl., feedback on)", "#d62728")]


def load_ts(arm, trace, rep="rep1"):
    f = OUT / "timeseries" / f"{arm}-{trace}-{rep}.csv"
    if not f.exists():
        return [], [], []
    rows = list(csv.DictReader(open(f)))
    t = [float(r["t"]) for r in rows]
    sent = [float(r["sent_kbps"]) for r in rows]
    cap = [float(r["cap_kbps"]) for r in rows]
    return t, sent, cap


def fig_timeseries():
    fig, axes = plt.subplots(len(TRACES), 1, figsize=(11, 10), sharex=True)
    for ax, trace in zip(axes, TRACES):
        # capacity curve from whichever arm has it (same trace → same cap)
        t0, _, cap = load_ts("ours-scream", trace)
        if t0:
            ax.fill_between(t0, 0, cap, color="0.85", step="post",
                            label="link capacity (5G trace)")
        for arm, label, color in ARMS:
            t, sent, _ = load_ts(arm, trace)
            if t:
                ax.plot(t, sent, color=color, lw=1.4, label=label)
        ax.set_title(f"{trace.upper()} trace — sent rate vs capacity", fontsize=11)
        ax.set_ylabel("kbps")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="upper right")
    axes[-1].set_xlabel("time (s)")
    fig.suptitle("SCReAM sending rate vs 5G link capacity — "
                 "ours (reference) vs Rushi's (reimplementation)",
                 fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    p = PLOTS / "bitrate_vs_capacity.png"
    fig.savefig(p, dpi=120)
    print(f"wrote {p}")


def fig_bars():
    rows = list(csv.DictReader(open(OUT / "metrics.csv")))
    # group: metric -> arm -> trace -> [values]
    def grab(metric):
        g = defaultdict(lambda: defaultdict(list))
        for r in rows:
            try:
                g[r["arm"]][r["trace"]].append(float(r[metric]))
            except ValueError:
                pass
        return g

    metrics = [("utilization", "Link utilization (sent/capacity)", "higher = better"),
               ("overshoot_frac", "Overshoot fraction (sent > capacity)", "lower = better"),
               ("delivered_frac", "Delivered-frame fraction", "UMN inflated by partial-decode")]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    x = range(len(TRACES))
    w = 0.38
    for ax, (metric, title, note) in zip(axes, metrics):
        g = grab(metric)
        for i, (arm, label, color) in enumerate(ARMS):
            means = [st.mean(g[arm][tr]) if g[arm][tr] else 0 for tr in TRACES]
            errs = [st.pstdev(g[arm][tr]) if len(g[arm][tr]) > 1 else 0 for tr in TRACES]
            ax.bar([xi + (i - 0.5) * w for xi in x], means, w,
                   yerr=errs, capsize=3, color=color, label=label)
        ax.set_xticks(list(x))
        ax.set_xticklabels([t.upper() for t in TRACES])
        ax.set_title(title, fontsize=10)
        ax.set_xlabel(note, fontsize=8, color="0.4")
        ax.grid(alpha=0.3, axis="y")
        ax.legend(fontsize=8)
    fig.suptitle("SCReAM comparison — aggregate over reps (error bars = stdev)",
                 fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    p = PLOTS / "aggregate_bars.png"
    fig.savefig(p, dpi=120)
    print(f"wrote {p}")


if __name__ == "__main__":
    PLOTS.mkdir(parents=True, exist_ok=True)
    fig_timeseries()
    fig_bars()
