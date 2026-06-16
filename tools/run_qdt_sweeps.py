"""Resumable runner for the H6/H7 queue-delay-target sweeps.

Runs the six sweeps in sequence, each with `experiment.py --resume`, so a
kill or failure at any point loses no completed work:

  - Per cell: experiment.py persists the sweep record after every cell, and
    --resume skips (config, rep) cells already completed with exit_code 0.
  - Per sweep: this runner checks each sweep's record for completeness
    (reps x configs cells, all exit_code 0) and skips fully-done sweeps.
  - Checkpoint: a manifest at runs/experiments/_qdt_manifest.json records
    each sweep's status after it finishes, for at-a-glance resume state.

Just re-run this script after any interruption -- it picks up where it
stopped. Idempotent when everything is already done.

    python3 tools/run_qdt_sweeps.py            # run/resume all six
    python3 tools/run_qdt_sweeps.py --status   # print manifest, run nothing
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = ROOT / "specs" / "experiments"
RECORDS_DIR = ROOT / "runs" / "experiments"
MANIFEST = RECORDS_DIR / "_qdt_manifest.json"

SWEEPS = [
    "h6-qdt-sweep-static",
    "h6-qdt-sweep-fluct",
    "h6-qdt-sweep-5g",
    "h7-qdt-sweep-static",
    "h7-qdt-sweep-fluct",
    "h7-qdt-sweep-5g",
    "h8-mulinc-sweep-static",
    "h8-mulinc-sweep-fluct",
    "h8-mulinc-sweep-5g",
    "h9-rtt-boundary",
    "h10-loss-boundary",
    "h11-jitter-boundary",
    "h12-loss-recovery-on",
    "h13-keyframe-loss",
]


def sweep_target(name: str) -> int:
    """Total cells expected for a sweep = reps x configs."""
    spec = yaml.safe_load((EXPERIMENTS_DIR / f"{name}.yaml").read_text())
    return int(spec["reps"]) * len(spec["configurations"])


def sweep_done_count(name: str) -> int:
    """Completed (exit_code 0) cells in a sweep's current record."""
    rec = RECORDS_DIR / f"{name}.json"
    if not rec.is_file():
        return 0
    try:
        runs = json.loads(rec.read_text()).get("runs", [])
    except json.JSONDecodeError:
        return 0
    # Count distinct (config, rep) with exit_code 0.
    done = {(str(r["config"]), r["rep"]) for r in runs if r.get("exit_code") == 0}
    return len(done)


def status() -> list[dict]:
    rows = []
    for name in SWEEPS:
        target = sweep_target(name)
        done = sweep_done_count(name)
        rows.append({"sweep": name, "done": done, "target": target,
                     "complete": done >= target})
    return rows


def write_manifest(rows: list[dict]) -> None:
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({"sweeps": rows}, indent=2))


def print_status(rows: list[dict]) -> None:
    print("queue-delay-target sweeps:")
    for r in rows:
        mark = "DONE" if r["complete"] else f"{r['done']}/{r['target']}"
        print(f"  {r['sweep']:<24} {mark}")


def main() -> int:
    if "--status" in sys.argv:
        rows = status()
        print_status(rows)
        write_manifest(rows)
        return 0

    for name in SWEEPS:
        target = sweep_target(name)
        done = sweep_done_count(name)
        if done >= target:
            print(f"[chain] {name}: already complete ({done}/{target}) — skipping",
                  flush=True)
            continue
        # Bounded auto-retry: a transient worker hiccup (SSH blip, camera
        # rc=1) fails one cell. Re-running with --resume retries only the
        # still-failed cells, so we give each sweep a few attempts before
        # giving up. This pushes through transient failures without losing
        # the fail-visible property for a persistently broken cell.
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            done = sweep_done_count(name)
            print(f"[chain] {name}: attempt {attempt}/{max_attempts} "
                  f"({done}/{target} done) with --resume", flush=True)
            r = subprocess.run(
                ["python3", str(ROOT / "experiment.py"), name, "--resume"],
                cwd=str(ROOT),
            )
            write_manifest(status())
            if sweep_done_count(name) >= target:
                break
            if attempt < max_attempts:
                print(f"[chain] {name}: {sweep_done_count(name)}/{target} done "
                      f"after attempt {attempt} (exit {r.returncode}); retrying "
                      f"the failed cell(s).", flush=True)
        if sweep_done_count(name) < target:
            print(f"[chain] {name}: still {sweep_done_count(name)}/{target} after "
                  f"{max_attempts} attempts; stopping chain. Re-run to resume.",
                  flush=True)
            return 1

    rows = status()
    write_manifest(rows)
    print_status(rows)
    if all(r["complete"] for r in rows):
        print("[chain] ALL_SWEEPS_DONE", flush=True)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
