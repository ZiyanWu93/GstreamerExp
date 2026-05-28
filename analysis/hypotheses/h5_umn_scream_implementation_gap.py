"""H5 — Our reference SCReAM out-utilizes UMN's reimplementation, and the
gap is a controller-to-encoder implementation gap, not the algorithm or
its loop timing.

Evidence comes from the cross-implementation comparison harness, not
experiment.py, so this verifier reads the comparison metrics CSV
(analysis/umn-comparison/metrics.csv) produced by
tools/extract_comparison.py rather than a runs/experiments record.

Checks (per the spec verdict_rule):
  (a) CQI: ours utilization >= 2x UMN's (fair, network_time_sync on)
  (b) UMN's mean sent rate is near-constant across HO/RB/CQI (spread <= 15%)
  (c) faster timing (S2 50ms, S3 +ramp) does NOT recover CQI utilization
The command-following gap (d) is established by tools/diag_umn.py
(actual send > 1.3x commanded target in 89% of decisions) and is
reported as a note.
"""
from __future__ import annotations

import csv
import statistics as st
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import _verifier as V   # noqa: E402

HID = "H5"
CLAIM = ("Our reference SCReAM out-utilizes UMN's reimplementation; the gap "
         "is a controller-to-encoder implementation gap, not the algorithm/timing")
SOURCE = "Cross-implementation comparison vs UMN Teleop-Gopher-streamer (502533c)"
METRICS = PROJECT_ROOT / "analysis" / "umn-comparison" / "metrics.csv"


def _by_arm_trace(rows, metric):
    g: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        try:
            g.setdefault((r["arm"], r["trace"]), []).append(float(r[metric]))
        except ValueError:
            pass
    return {k: st.mean(v) for k, v in g.items() if v}


def main():
    V.print_header(HID, CLAIM, SOURCE)
    if not METRICS.is_file():
        return V.print_untested(f"no comparison metrics at {METRICS}")
    rows = list(csv.DictReader(open(METRICS)))
    util = _by_arm_trace(rows, "utilization")
    sent = _by_arm_trace(rows, "mean_sent_kbps")

    need = [("ours-scream", "cqi"), ("umn-scream-v2", "cqi"),
            ("umn-scream-v2", "ho"), ("umn-scream-v2", "rb")]
    if any(k not in util for k in need):
        return V.print_untested("comparison arms missing (need ours + umn-v2 on cqi/ho/rb)")

    # (a) CQI utilization advantage
    ours_cqi = util[("ours-scream", "cqi")]
    umn_cqi = util[("umn-scream-v2", "cqi")]
    util_ratio = ours_cqi / umn_cqi if umn_cqi else float("inf")

    # (b) UMN sent-rate spread across traces
    umn_sent = [sent[("umn-scream-v2", t)] for t in ("ho", "rb", "cqi")
                if ("umn-scream-v2", t) in sent]
    spread = (max(umn_sent) - min(umn_sent)) / st.mean(umn_sent) if umn_sent else 1.0

    # (c) faster timing does not recover CQI utilization
    s2 = util.get(("umn-scream-s2", "cqi"))
    s3 = util.get(("umn-scream-s3", "cqi"))
    timing_recovers = any(x is not None and x > umn_cqi * 1.25 for x in (s2, s3))

    cond_a = util_ratio >= 2.0
    cond_b = spread <= 0.15
    cond_c = not timing_recovers

    numbers = {
        "cqi_util_ours": round(ours_cqi, 3),
        "cqi_util_umn_v2": round(umn_cqi, 3),
        "cqi_util_ratio_ours_over_umn": round(util_ratio, 2),
        "umn_sent_kbps_by_trace": {t: round(sent[("umn-scream-v2", t)], 0)
                                   for t in ("ho", "rb", "cqi")
                                   if ("umn-scream-v2", t) in sent},
        "umn_sent_spread": round(spread, 3),
        "umn_cqi_util_s2_50ms": round(s2, 3) if s2 is not None else None,
        "umn_cqi_util_s3_fastramp": round(s3, 3) if s3 is not None else None,
        "cond_a_cqi_util_ratio>=2": cond_a,
        "cond_b_sent_spread<=15pct": cond_b,
        "cond_c_timing_does_not_recover": cond_c,
    }
    note = ("command-following gap (diag_umn.py): actual send > 1.3x commanded "
            "target in ~89% of decisions; UMN loss signal never fires.")

    if cond_a and cond_b and cond_c:
        return V.print_verdict("supported", numbers, note)
    if not cond_a:
        return V.print_verdict("refuted", numbers,
                               "CQI utilization gap did not hold; " + note)
    return V.print_verdict("inconclusive", numbers, note)


if __name__ == "__main__":
    main()
