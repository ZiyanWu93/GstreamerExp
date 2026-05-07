"""Quality dimension.

Question: How visually intelligible was what arrived?

Sources:
  - decoded_psnr metric (per-frame Y-plane PSNR vs ground truth)
  - decoder_errors metric (depay + decoder warnings on the bus)

For a single run, surfaces the PSNR distribution (mean / median / p10 /
min / max) plus the per-second trajectory across phases. For an
experiment, per-arm aggregate of the distribution scalars. Frame-count
alone can't distinguish "arrived clean" from "arrived corrupted because
a keyframe was lost upstream"; PSNR can.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis import _common as C   # noqa: E402


name = "quality"


def analyze_run(run: C.RunFiles) -> dict:
    summary = C.load_json(run.summary_path)
    viewer  = C.load_json(run.viewer_path)
    metrics = viewer.get("metrics") or {}
    psnr_metric = metrics.get("decoded_psnr") or {}
    psnr_summary = psnr_metric.get("summary") or {}
    psnr_samples = psnr_metric.get("samples") or []

    decoder_errors_summary = (
        (summary.get("viewer") or {}).get("decoder_errors") or {}
    )

    # Per-second binning of the PSNR trajectory. This is the trace that
    # reveals the recovery curve after an impaired phase ends.
    per_sec: dict[int, list[float]] = {}
    for pts_ns, _frame_n, db in psnr_samples:
        sec = int(pts_ns) // 1_000_000_000
        per_sec.setdefault(sec, []).append(db)
    trajectory = []
    for sec in sorted(per_sec):
        vals = sorted(per_sec[sec])
        trajectory.append({
            "t_s":    sec,
            "n":      len(vals),
            "mean":   sum(vals) / len(vals),
            "p10":    C.percentile(vals, 10),
            "min":    vals[0],
            "max":    vals[-1],
        })

    return {
        "dimension":     name,
        "psnr_summary":  psnr_summary,
        "trajectory":    trajectory,
        "decoder_errors": decoder_errors_summary,
        "psnr_enabled":  bool(psnr_summary),
    }


def analyze_experiment(record: dict) -> dict:
    arms = {c["id"]: c["label"] for c in record["configurations"]}
    per_arm: dict[str, dict] = {}
    for cid, label in arms.items():
        runs = [r for r in record["runs"]
                if r["config"] == cid and r["exit_code"] == 0]
        per_run = []
        for r in runs:
            run_dir = PROJECT_ROOT / "runs" / cid / r["run_id"]
            rep = analyze_run(C.RunFiles(
                run_dir, run_dir / "summary.json",
                run_dir / "camera.json", run_dir / "viewer.json", cid,
            ))
            per_run.append(rep)
        means = [r["psnr_summary"].get("mean_psnr_db") for r in per_run
                 if r["psnr_summary"].get("mean_psnr_db") is not None]
        p10s  = [r["psnr_summary"].get("p10_psnr_db") for r in per_run
                 if r["psnr_summary"].get("p10_psnr_db") is not None]
        mins  = [r["psnr_summary"].get("min_psnr_db") for r in per_run
                 if r["psnr_summary"].get("min_psnr_db") is not None]
        decoder_warnings = [r["decoder_errors"].get("decoder_warnings", 0)
                             for r in per_run]
        per_arm[cid] = {
            "label": label,
            "n":     len(runs),
            "psnr_mean_xs": means,
            "psnr_p10_xs":  p10s,
            "psnr_min_xs":  mins,
            "decoder_warnings_xs": decoder_warnings,
        }
    return {"dimension": name, "per_arm": per_arm}


def render(report: dict) -> str:
    if "per_arm" in report:
        return _render_experiment(report)
    return _render_run(report)


def _render_run(report: dict) -> str:
    lines = ["[quality] How visually intelligible was what arrived?"]
    if not report["psnr_enabled"]:
        lines.append("  decoded_psnr was not enabled for this run "
                     "(add it to scenario.metrics to populate).")
        de = report["decoder_errors"]
        lines.append(f"  decoder_errors  depay={de.get('depay_warnings', 0)}  "
                     f"decoder={de.get('decoder_warnings', 0)}  "
                     f"other={de.get('other_warnings', 0)}")
        return "\n".join(lines)
    s = report["psnr_summary"]
    lines.append(f"  psnr (dB)  mean={s.get('mean_psnr_db', '—')}  "
                 f"median={s.get('median_psnr_db', '—')}  "
                 f"p10={s.get('p10_psnr_db', '—')}  "
                 f"min={s.get('min_psnr_db', '—')}  "
                 f"max={s.get('max_psnr_db', '—')}")
    lines.append(f"  coverage   matched={s.get('n_matched', 0)}  "
                 f"unmatched={s.get('n_unmatched', 0)}")
    de = report["decoder_errors"]
    lines.append(f"  decoder_errors  depay={de.get('depay_warnings', 0)}  "
                 f"decoder={de.get('decoder_warnings', 0)}")
    lines.append("  trajectory (per-second mean PSNR):")
    for row in report["trajectory"]:
        lines.append(f"    t={row['t_s']:>3}s  n={row['n']:>3}  "
                     f"mean={row['mean']:>5.1f}  p10={row['p10']:>5.1f}  "
                     f"min={row['min']:>5.1f}  max={row['max']:>5.1f}")
    return "\n".join(lines)


def _render_experiment(report: dict) -> str:
    lines = ["[quality] How visually intelligible was what arrived?"]
    any_psnr = any(arm["psnr_mean_xs"] for arm in report["per_arm"].values())
    if not any_psnr:
        lines.append("  decoded_psnr was not enabled for any arm; nothing "
                     "to compare. Add 'decoded_psnr' to scenario.metrics "
                     "to populate this section.")
        return "\n".join(lines)
    lines.append(f"  {'arm':<14} {'n':>3} "
                 f"{'psnr_mean µ±σ':>16} {'psnr_p10 µ±σ':>16} "
                 f"{'psnr_min µ±σ':>16}")
    for cid, arm in report["per_arm"].items():
        lines.append(f"  {arm['label']:<14} {arm['n']:>3} "
                     f"{C.msd(arm['psnr_mean_xs'], '{:.2f}'):>16} "
                     f"{C.msd(arm['psnr_p10_xs'], '{:.2f}'):>16} "
                     f"{C.msd(arm['psnr_min_xs'], '{:.2f}'):>16}")
    return "\n".join(lines)
