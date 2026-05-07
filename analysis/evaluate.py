"""Comprehensive evaluation report — single entry point.

Auto-detects whether the argument is a single run or an experiment record
and emits the same report shape: one section per evaluation dimension,
in the order declared by `analysis.dimensions.DIMENSION_MODULES`.

The driver knows nothing about what each dimension measures — it just
calls the dimension module's `analyze_*` function and renders the
result. Adding a sixth dimension is a localized change: drop a module
into `dimensions/` and add it to DIMENSION_MODULES.

    python3 analysis/evaluate.py 13              # most recent run of config 13
    python3 analysis/evaluate.py 13 2026-05-06T  # specific run
    python3 analysis/evaluate.py runs/13/2026-05-06T...
    python3 analysis/evaluate.py scream-recovery-2x2-realmotion
    python3 analysis/evaluate.py runs/experiments/<name>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis import _common as C   # noqa: E402
from analysis.dimensions import DIMENSION_MODULES   # noqa: E402


def _looks_like_experiment_arg(arg: str) -> bool:
    """Heuristic: an experiment record either ends in .json or names a
    file under runs/experiments/. A run argument is either a config id
    (matches runs/<N>/) or a path to a runs/<N>/<ts>/ directory."""
    p = Path(arg)
    if p.suffix == ".json":
        return True
    if (PROJECT_ROOT / "runs" / "experiments" / f"{arg}.json").is_file():
        return True
    return False


def evaluate_run(arg: str, optional_run_id: str | None) -> str:
    if optional_run_id is not None:
        run = C.resolve_run(arg)   # arg is config id; use most-recent
        rd = run.run_dir.parent / optional_run_id
        if rd.is_dir():
            run = C.RunFiles(rd, rd / "summary.json",
                             rd / "camera.json", rd / "viewer.json",
                             run.config_id)
    else:
        run = C.resolve_run(arg)

    summary = C.load_json(run.summary_path)
    header = []
    header.append(f"=== Evaluation: {run.run_dir.relative_to(PROJECT_ROOT)} ===")
    if run.config_id:
        header.append(f"config:   {run.config_id}")
    verdict = summary.get("verdict")
    if verdict:
        header.append(f"verdict:  {verdict}")
    header.append("")

    sections = [m.render(m.analyze_run(run)) for m in DIMENSION_MODULES]
    return "\n".join(header + sections)


def evaluate_experiment(arg: str) -> str:
    record_path = C.resolve_experiment(arg)
    record = json.loads(record_path.read_text())

    header = []
    header.append(f"=== Evaluation: {record['name']} ===")
    header.append(f"record:   {record_path.relative_to(PROJECT_ROOT)}")
    header.append(f"reps:     {record.get('reps', '?')}, "
                  f"arms:     {len(record['configurations'])}")
    header.append("")

    sections = [m.render(m.analyze_experiment(record))
                for m in DIMENSION_MODULES]
    return "\n\n".join(header + sections)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target",
                    help="config id, run path, experiment name, or "
                         "experiment record path")
    ap.add_argument("run_id", nargs="?", default=None,
                    help="(run mode only) timestamp under runs/<config>/")
    args = ap.parse_args()

    if args.run_id is None and _looks_like_experiment_arg(args.target):
        print(evaluate_experiment(args.target))
    else:
        print(evaluate_run(args.target, args.run_id))


if __name__ == "__main__":
    main()
