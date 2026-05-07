"""H3 — PLI's quality advantage over NACK widens with content motion.

Source: project-internal (FileSource design rationale).
Prediction (per HYPOTHESES.md):
  PLI minus NACK in PSNR p10 (worst-10% frames, mean across reps) is
  larger on teleop-realmotion than on ball-720p30, by >= 1 dB.

Requires two recovery experiments — one against ball-720p30, one
against teleop-realmotion — each with NACK-only and PLI-only arms,
each with decoded_psnr enabled. The closest existing records are
scream-recovery-2x2 (ball, but decoded_psnr was NOT enabled) and
scream-recovery-2x2-realmotion (real content, also no decoded_psnr).
So: untested until those experiments are re-run with the metric on.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import _verifier as V   # noqa: E402

HID    = "H3"
CLAIM  = "PLI's quality advantage over NACK widens with content motion"
SOURCE = "project-internal (FileSource design rationale)"

EXP_BALL = "scream-recovery-2x2"            # need PSNR re-run
EXP_REAL = "scream-recovery-2x2-realmotion"  # need PSNR re-run

# Within each experiment, identify NACK-only and PLI-only arms by their
# recovery flags rather than label string.
def _arm_with_recovery(record: dict, *, want_nack: bool, want_pli: bool) -> str | None:
    import yaml
    for cfg_id in {c["id"] for c in record["configurations"]}:
        cfg_path = PROJECT_ROOT / "specs" / "configurations" / f"{cfg_id}.yaml"
        if not cfg_path.is_file():
            continue
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        rec = cfg.get("recovery") or {}
        if (rec.get("nack") == want_nack and rec.get("pli") == want_pli
                and not rec.get("fec")):
            return cfg_id
    return None


def _psnr_p10_per_rep(record: dict, cid: str) -> list[float]:
    """Pull mean p10 PSNR per PASS rep for a given arm."""
    out = []
    for r in record["runs"]:
        if r["config"] != cid or r["exit_code"] != 0:
            continue
        v_path = PROJECT_ROOT / "runs" / cid / r["run_id"] / "viewer.json"
        if not v_path.is_file():
            continue
        try:
            v = json.loads(v_path.read_text())
        except Exception:
            continue
        psnr = ((v.get("metrics") or {}).get("decoded_psnr", {})
                .get("summary", {}))
        p10 = psnr.get("p10_psnr_db")
        if p10 is not None:
            out.append(p10)
    return out


def _gap_for(experiment_name: str) -> tuple[str, float | None]:
    """Returns (status_label, gap_dB) where status_label ∈
    {ok, missing_record, missing_arms, missing_psnr}."""
    rec = V.try_load_experiment(experiment_name)
    if rec is None:
        return ("missing_record", None)
    nack_id = _arm_with_recovery(rec, want_nack=True,  want_pli=False)
    pli_id  = _arm_with_recovery(rec, want_nack=False, want_pli=True)
    if not nack_id or not pli_id:
        return ("missing_arms", None)
    nack_p10s = _psnr_p10_per_rep(rec, nack_id)
    pli_p10s  = _psnr_p10_per_rep(rec, pli_id)
    if not nack_p10s or not pli_p10s:
        return ("missing_psnr", None)
    return ("ok", statistics.mean(pli_p10s) - statistics.mean(nack_p10s))


def main():
    V.print_header(HID, CLAIM, SOURCE)
    ball_status, ball_gap = _gap_for(EXP_BALL)
    real_status, real_gap = _gap_for(EXP_REAL)

    if ball_status != "ok" or real_status != "ok":
        return V.print_untested(
            f"need both experiments re-run with decoded_psnr enabled. "
            f"current: {EXP_BALL}={ball_status}, {EXP_REAL}={real_status}. "
            f"to fix: add `decoded_psnr` to scenario.metrics in configs "
            f"13/15 (ball) and 19/21 (realmotion), then re-run both "
            f"experiments.")

    growth = real_gap - ball_gap
    numbers = {
        "ball PLI p10 - NACK p10":  f"{ball_gap:+.2f} dB",
        "real PLI p10 - NACK p10":  f"{real_gap:+.2f} dB",
        "growth (real - ball)":     f"{growth:+.2f} dB  (need ≥ +1.00)",
    }
    if growth >= 1.0:
        return V.print_verdict("supported", numbers,
                               "PLI's relative advantage grows with motion as predicted")
    if growth <= 0:
        return V.print_verdict("refuted", numbers,
                               "advantage doesn't grow (or shrinks) with motion")
    return V.print_verdict("inconclusive", numbers,
                           "growth is positive but below the 1.00 dB threshold")


if __name__ == "__main__":
    main()
