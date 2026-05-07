#!/usr/bin/env python3
"""Cross-run summary across runs/<id>/<ts>/summary.json.

Walks every run directory under runs/, reads summary.json, and prints
a single comparison table. Default mode shows the most recent run per
config; `--all` shows every run.

Usage:
    python3 analyze.py                # latest run per config
    python3 analyze.py --all          # every run, sorted oldest first
    python3 analyze.py --config 7 8   # only specific config ids
    python3 analyze.py --json         # raw JSON instead of table
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable


def _runs_for(runs_root: Path, config_id: str) -> list[Path]:
    config_runs = runs_root / config_id
    if not config_runs.is_dir():
        return []
    return sorted(p for p in config_runs.iterdir()
                  if p.is_dir() and (p / "summary.json").exists())


def _row(config_id: str, run_dir: Path) -> dict:
    summary = json.loads((run_dir / "summary.json").read_text())
    s = summary.get("sender", {})
    r = summary.get("receiver", {})
    L = summary.get("latency", {})
    encoder_target = s.get("encoder_target", {})
    encoded = s.get("encoded_bitrate", {})
    s_wire = s.get("wire_bytes", {})
    r_wire = r.get("wire_bytes", {})
    derr = r.get("decoder_errors", {})

    sent = s.get("frames_sent", 0)
    recv = r.get("frames_depayloaded", 0)
    loss_pct = round(100.0 * (sent - recv) / sent, 1) if sent else 0.0

    return {
        "config":         config_id,
        "run":            run_dir.name,
        "verdict":        summary.get("verdict", "?"),
        "duration_s":     round(s.get("duration_seconds", 0), 1),
        "sent":           sent,
        "recv":           recv,
        "loss_pct":       loss_pct,
        "target_kbps":    f"{encoder_target.get('first_kbps','-')}→{encoder_target.get('last_kbps','-')}"
                          if encoder_target else "-",
        "encoded_mean":   encoded.get("mean_kbps", "-"),
        "wire_tx_mean":   s_wire.get("mean_kbps", "-"),
        "wire_rx_mean":   r_wire.get("mean_kbps", "-"),
        "lat_med_ms":     L.get("median_ms", "-"),
        "lat_p95_ms":     L.get("p95_ms", "-"),
        "lat_max_ms":     L.get("max_ms", "-"),
        "warn":           (derr.get("depay_warnings", 0)
                           + derr.get("decoder_warnings", 0)
                           + derr.get("other_warnings", 0)) if derr else "-",
    }


def _print_table(rows: Iterable[dict]) -> None:
    rows = list(rows)
    if not rows:
        print("no runs found")
        return
    cols = [
        ("config",       "cfg"),
        ("run",          "run"),
        ("verdict",      "verdict"),
        ("duration_s",   "dur_s"),
        ("sent",         "sent"),
        ("recv",         "recv"),
        ("loss_pct",     "loss%"),
        ("target_kbps",  "target_kbps"),
        ("encoded_mean", "enc_kbps"),
        ("wire_tx_mean", "tx_kbps"),
        ("wire_rx_mean", "rx_kbps"),
        ("lat_med_ms",   "lat_med"),
        ("lat_p95_ms",   "lat_p95"),
        ("lat_max_ms",   "lat_max"),
        ("warn",         "warn"),
    ]
    widths = {key: max(len(label), max(len(str(r.get(key, ""))) for r in rows))
              for key, label in cols}
    header = "  ".join(label.ljust(widths[key]) for key, label in cols)
    print(header)
    print("  ".join("-" * widths[key] for key, _ in cols))
    for r in rows:
        print("  ".join(str(r.get(key, "")).ljust(widths[key])
                        for key, _ in cols))


def main():
    parser = argparse.ArgumentParser(description="Cross-run summary")
    parser.add_argument("--all", action="store_true",
                        help="show every run (default: latest per config)")
    parser.add_argument("--config", nargs="*", default=None,
                        help="restrict to specific config ids (e.g. 7 8)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of a table")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    runs_root = project_root / "runs"
    if not runs_root.is_dir():
        sys.exit(f"no runs/ at {runs_root}")

    config_ids = sorted(
        (p.name for p in runs_root.iterdir() if p.is_dir()),
        key=lambda x: int(x) if x.isdigit() else 999,
    )
    rows = []
    for cid in config_ids:
        if args.config and cid not in args.config:
            continue
        runs = _runs_for(runs_root, cid)
        if not runs:
            continue
        chosen = runs if args.all else [runs[-1]]
        for run_dir in chosen:
            rows.append(_row(cid, run_dir))

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        _print_table(rows)


if __name__ == "__main__":
    main()
