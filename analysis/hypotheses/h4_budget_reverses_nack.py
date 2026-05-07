"""H4 — A latency budget reverses NACK's apparent benefit.

Tests two predictions on the `nack-latency-budget` experiment, an
8-arm 2×2×2 over (CC algorithm) × (NACK on/off) × (budget 0 / 100 ms),
all on the same constant network (5 Mbps · 50 ms · 3% uniform loss).

Predictions, per CC algorithm:
  P1  Without budget enforcement (budget=0), NACK arms deliver ≥ 5%
      more frames than no-recovery arms — NACK's textbook wire-level
      benefit reproduces.
  P2  With a 100 ms budget, NACK arms deliver fewer frames inside
      the budget than no-recovery arms — NACK's added latency
      (RTX timer + jitter buffer) pushes more frames past the
      deadline than NACK rescues.

Verdict per CC: SUPPORTED iff P1 ∧ P2 (both predictions hold).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import _verifier as V   # noqa: E402

HID    = "H4"
CLAIM  = "A latency budget reverses NACK's apparent benefit"
SOURCE = "Project-internal (operational deadline framework)"
DEFAULT_EXPERIMENT = "nack-latency-budget"


def _arm_key(config_id: str) -> tuple[str, bool, int]:
    """Return (algo, nack_on, budget_ms) for a configuration."""
    cfg = yaml.safe_load(
        (PROJECT_ROOT / "specs" / "configurations" / f"{config_id}.yaml").read_text())
    algo = (cfg.get("congestion_control") or {}).get("algorithm")
    nack = bool((cfg.get("recovery") or {}).get("nack", False))
    budget = int(cfg.get("latency_budget_ms") or 0)
    return algo, nack, budget


def _delivered(config_id: str, run_id: str) -> int | None:
    """Pull late_drops.summary.delivered for one rep."""
    p = PROJECT_ROOT / "runs" / config_id / run_id / "viewer.json"
    if not p.is_file():
        return None
    try:
        v = json.loads(p.read_text())
    except Exception:
        return None
    summary = ((v.get("metrics") or {}).get("late_drops") or {}).get("summary") or {}
    d = summary.get("delivered")
    return int(d) if d is not None else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    args = ap.parse_args()

    V.print_header(HID, CLAIM, SOURCE)
    rec = V.try_load_experiment(args.experiment)
    if rec is None:
        return V.print_untested(
            f"no experiment record at runs/experiments/{args.experiment}.json"
            f" — run `python3 experiment.py {args.experiment}` first")

    arms: dict[tuple[str, bool, int], list[int]] = {}
    for r in rec["runs"]:
        if r["exit_code"] != 0:
            continue
        try:
            key = _arm_key(r["config"])
        except Exception:
            continue
        delivered = _delivered(r["config"], r["run_id"])
        if delivered is not None:
            arms.setdefault(key, []).append(delivered)

    def _mean(xs):
        return (statistics.mean(xs) if xs else None)

    numbers: dict[str, str] = {}
    verdicts: dict[str, str] = {}

    for algo in ("scream", "gcc"):
        baseline_b0 = arms.get((algo, False, 0), [])
        nack_b0     = arms.get((algo, True,  0), [])
        baseline_b1 = arms.get((algo, False, 100), [])
        nack_b1     = arms.get((algo, True,  100), [])
        if not (baseline_b0 and nack_b0 and baseline_b1 and nack_b1):
            verdicts[algo] = "untested"
            numbers[f"{algo}_status"] = "missing reps for one or more arms"
            continue

        m_b0 = _mean(baseline_b0)
        m_n0 = _mean(nack_b0)
        m_b1 = _mean(baseline_b1)
        m_n1 = _mean(nack_b1)

        ratio_no_budget = m_n0 / m_b0 if m_b0 > 0 else 0
        delta_with_budget = m_n1 - m_b1

        p1 = ratio_no_budget >= 1.05
        p2 = delta_with_budget < 0

        numbers[f"{algo}_p1_ratio (NACK / no-rec, budget=0)"] = (
            f"{ratio_no_budget:.3f}  (need ≥ 1.05)")
        numbers[f"{algo}_p2_delta (NACK − no-rec, budget=100)"] = (
            f"{delta_with_budget:+.1f} frames  (need < 0)")
        numbers[f"{algo}_means"] = (
            f"no-rec/b=0:{m_b0:.1f}  NACK/b=0:{m_n0:.1f}  "
            f"no-rec/b=100:{m_b1:.1f}  NACK/b=100:{m_n1:.1f}")
        verdicts[algo] = ("supported" if (p1 and p2) else
                          "refuted"   if (not p1 and not p2) else
                          "inconclusive")

    overall = ("supported" if "supported" in verdicts.values() else
               max(verdicts.values(),
                   key=lambda v: ["untested", "inconclusive",
                                  "refuted", "supported"].index(v)))
    note = "  ".join(f"{a}={v}" for a, v in verdicts.items())
    return V.print_verdict(overall, numbers, f"per-CC: {note}")


if __name__ == "__main__":
    main()
