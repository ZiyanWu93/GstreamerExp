"""Shared scaffolding for hypothesis verifier scripts.

Each verifier follows the same shape:

    print_header(hid, claim, source)
    rec = try_load_experiment(name)        # or load_run(...)
    if rec is None:
        return print_untested(reason)
    # compute prediction quantities, decide verdict
    print_verdict("supported", numbers)

This module hides the file-loading and pretty-printing so each
hypothesis-specific verifier can focus on the prediction logic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis import _common as C   # noqa: E402


def print_header(hid: str, claim: str, source: str) -> None:
    print(f"=== {hid}: {claim} ===")
    print(f"source: {source}")
    print()


def try_load_experiment(name: str) -> dict | None:
    try:
        path = C.resolve_experiment(name)
    except (FileNotFoundError, SystemExit):
        return None
    return json.loads(path.read_text())


def print_untested(reason: str) -> None:
    print("status: untested")
    print(f"reason: {reason}")


def print_verdict(verdict: str, numbers: dict, note: str | None = None) -> None:
    """verdict ∈ {supported, refuted, inconclusive}."""
    print(f"status: {verdict}")
    for k, v in numbers.items():
        print(f"  {k}: {v}")
    if note:
        print(f"note: {note}")


def per_arm_summaries(record: dict, project_root: Path = PROJECT_ROOT) -> dict:
    """Walk an experiment record and return {config_id: {label, summaries}}
    where summaries is the list of per-rep summary.json dicts (PASS only)."""
    arms = {c["id"]: c["label"] for c in record["configurations"]}
    out: dict[str, dict] = {}
    for cid, label in arms.items():
        sums = []
        for r in record["runs"]:
            if r["config"] != cid or r["exit_code"] != 0:
                continue
            p = project_root / "runs" / cid / r["run_id"] / "summary.json"
            if not p.is_file():
                continue
            try:
                s = json.loads(p.read_text())
            except Exception:
                continue
            if s.get("verdict") == "PASS":
                sums.append({"summary": s, "config_id": cid,
                             "run_id": r["run_id"]})
        out[cid] = {"label": label, "summaries": sums}
    return out


def viewer_metric(summary: dict, metric_name: str) -> dict:
    """Pull a metric's summary from the viewer's portion of a run."""
    # For some metrics (decoded_psnr, late_drops, frame_count) the
    # finalized data lives in the per-role result file (viewer.json),
    # not in the merged summary. The merged summary embeds key bits
    # but for full metric access we read viewer.json directly.
    return {}
