"""Latency dimension.

Question: How fresh was what arrived?

Two complementary inputs:

1. The merged latency block in summary.json (computed from camera/viewer
   frame_latency samples joined by RTP timestamp; already clock-skew-
   corrected by reporting.py). This is the end-to-end number.

2. The stage_latency metric's per-frame, per-stage timestamps in
   camera.json and viewer.json. We join within each side by RTP
   timestamp to compute per-stage durations:

     encode    = encoder_out - encoder_in     (camera, vp8 encode)
     payload   = pay_out     - encoder_out    (camera, RTP payloader)
     egress    = wire_out    - pay_out        (camera, udp send queue)
     network   = wire_in     - wire_out - skew (cross-host, on the wire)
     depay     = depay_out   - wire_in        (viewer, jitter buf + depay)
     decode_q  = decode_in   - depay_out      (viewer, queue between)
     decode    = decode_out  - decode_in      (viewer, vp8 decode)
     render_q  = render_in   - decode_out     (viewer, queue to sink)

The breakdown turns "latency went up" into "latency went up because
the depay stage widened" — the difference between description and
diagnosis.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis import _common as C   # noqa: E402


name = "latency"


# Stage-pair definitions: (name, predecessor_stage, successor_stage,
# host). The host says which side's wall-clock timeline we use to
# compute the duration; "cross" means we need clock-skew correction.
# Stage pairs joined within each host (or cross-host for "network").
# Note: pre-pay stages (encoder_in, encoder_out) key their samples by
# pts-derived rtp_ts; pay's outbound RTP timestamp uses the payloader's
# random offset (RFC 3550), so pre-pay → pay joins use the metric's
# `pay_out_pts` companion sample (pts-derived key); pay → wire joins
# use `pay_out` (header rtp_ts). See StageLatency._add_pay_marker_probe.
_STAGE_PAIRS = [
    ("encode",   "encoder_in", "encoder_out", "camera"),
    ("payload",  "encoder_out", "pay_out_pts", "camera"),
    ("egress",   "pay_out",    "wire_out",    "camera"),
    ("network",  "wire_out",   "wire_in",     "cross"),
    ("depay",    "wire_in",    "depay_out",   "viewer"),
    ("decode_q", "depay_out",  "decode_in",   "viewer"),
    ("decode",   "decode_in",  "decode_out",  "viewer"),
    ("render_q", "decode_out", "render_in",   "viewer"),
]


def _stage_index(samples: list, stage_name: str) -> dict:
    """Pick out a single stage's samples and index by RTP timestamp."""
    return {row[1]: row[2] for row in samples if row[0] == stage_name}


def _per_stage_durations(camera_samples, viewer_samples, skew_offset_s):
    """Compute per-stage per-frame durations in milliseconds. Returns
    {stage_name: [duration_ms, ...]}. RTP timestamps that don't appear
    in both endpoints of a pair are dropped silently."""
    cam_idx = {stage: _stage_index(camera_samples, stage)
               for stage in {p for s in _STAGE_PAIRS for p in (s[1], s[2])}}
    vie_idx = {stage: _stage_index(viewer_samples, stage)
               for stage in {p for s in _STAGE_PAIRS for p in (s[1], s[2])}}

    out: dict[str, list] = {}
    for pair_name, pred, succ, host in _STAGE_PAIRS:
        if host == "camera":
            pred_t = cam_idx.get(pred, {})
            succ_t = cam_idx.get(succ, {})
        elif host == "viewer":
            pred_t = vie_idx.get(pred, {})
            succ_t = vie_idx.get(succ, {})
        else:  # cross
            pred_t = cam_idx.get(pred, {})
            succ_t = vie_idx.get(succ, {})
        if not pred_t or not succ_t:
            out[pair_name] = []
            continue
        durations = []
        for rtp_ts, t1 in pred_t.items():
            t2 = succ_t.get(rtp_ts)
            if t2 is None:
                continue
            d_s = t2 - t1
            if host == "cross":
                d_s -= skew_offset_s
            durations.append(d_s * 1000.0)
        out[pair_name] = durations
    return out


def analyze_run(run: C.RunFiles) -> dict:
    summary = C.load_json(run.summary_path)
    lat = summary.get("latency") or {}
    end_to_end = {
        "samples_count":  int(lat.get("samples_count", 0)),
        "median_ms":      lat.get("median_ms"),
        "p95_ms":         lat.get("p95_ms"),
        "p99_ms":         lat.get("p99_ms"),
        "max_ms":         lat.get("max_ms"),
        "min_ms":         lat.get("min_ms"),
    }

    # Per-stage breakdown. Pull stage_latency samples from both sides;
    # join within each side; pick up the cross-host skew from the run's
    # _clock_skew.json.
    camera = C.load_json(run.camera_path)
    viewer = C.load_json(run.viewer_path)
    cam_samples = ((camera.get("metrics") or {}).get("stage_latency", {})
                   .get("samples") or [])
    vie_samples = ((viewer.get("metrics") or {}).get("stage_latency", {})
                   .get("samples") or [])
    skew_path = run.run_dir / "_clock_skew.json"
    skew_doc = C.load_json(skew_path)
    cam_skew = float(skew_doc.get("camera_skew_s", 0.0))
    vie_skew = float(skew_doc.get("viewer_skew_s", 0.0))
    skew_offset_s = vie_skew - cam_skew

    if cam_samples or vie_samples:
        durations = _per_stage_durations(cam_samples, vie_samples, skew_offset_s)
    else:
        durations = {}

    return {
        "dimension":      name,
        **end_to_end,
        "stage_durations": durations,
    }


def analyze_experiment(record: dict) -> dict:
    arms = {c["id"]: c["label"] for c in record["configurations"]}
    per_arm: dict[str, dict] = {}
    for cid, label in arms.items():
        runs = [r for r in record["runs"]
                if r["config"] == cid and r["exit_code"] == 0]
        p99_xs = []
        max_xs = []
        median_xs = []
        # Per-stage means across reps. {stage: [per-rep mean ms, ...]}
        stage_rep_means: dict[str, list] = {}
        for r in runs:
            run_dir = PROJECT_ROOT / "runs" / cid / r["run_id"]
            rep = analyze_run(C.RunFiles(
                run_dir, run_dir / "summary.json",
                run_dir / "camera.json", run_dir / "viewer.json", cid,
            ))
            if rep["samples_count"] > 0:
                if rep["p99_ms"] is not None:
                    p99_xs.append(rep["p99_ms"])
                if rep["max_ms"] is not None:
                    max_xs.append(rep["max_ms"])
                if rep["median_ms"] is not None:
                    median_xs.append(rep["median_ms"])
            for stage, durs in rep["stage_durations"].items():
                if durs:
                    stage_rep_means.setdefault(stage, []).append(
                        statistics.mean(durs))
        per_arm[cid] = {
            "label":      label,
            "n":          len(runs),
            "median_xs":  median_xs,
            "p99_xs":     p99_xs,
            "max_xs":     max_xs,
            "stage_rep_means": stage_rep_means,
        }
    return {"dimension": name, "per_arm": per_arm}


def render(report: dict) -> str:
    if "per_arm" in report:
        return _render_experiment(report)
    return _render_run(report)


def _render_run(report: dict) -> str:
    lines = ["[latency] How fresh was what arrived?"]
    if report["samples_count"] == 0:
        lines.append("  no end-to-end latency samples (frame_latency "
                     "disabled or no marker-bit packets observed).")
    else:
        lines.append(f"  end-to-end  samples={report['samples_count']}  "
                     f"median={C.fmt(report['median_ms'])}ms  "
                     f"p95={C.fmt(report['p95_ms'])}ms  "
                     f"p99={C.fmt(report['p99_ms'])}ms  "
                     f"max={C.fmt(report['max_ms'])}ms")
        lines.append("  Note: absolute end-to-end is biased by SSH-based "
                     "skew estimation (~±30 ms); relative comparisons "
                     "and per-stage durations are unaffected.")

    durations = report.get("stage_durations") or {}
    if not any(durations.values()):
        lines.append("  per-stage breakdown unavailable "
                     "(stage_latency was not enabled).")
        return "\n".join(lines)

    lines.append("  per-stage breakdown (median ms; n = matched frames):")
    total = 0.0
    for pair_name, _pred, _succ, _host in _STAGE_PAIRS:
        durs = durations.get(pair_name) or []
        if not durs:
            lines.append(f"    {pair_name:<10} —")
            continue
        med = statistics.median(durs)
        total += med
        lines.append(f"    {pair_name:<10} {med:>8.2f}  "
                     f"(n={len(durs)})")
    lines.append(f"    {'─'*10:<10}")
    lines.append(f"    {'total':<10} {total:>8.2f}")
    return "\n".join(lines)


def _render_experiment(report: dict) -> str:
    lines = ["[latency] How fresh was what arrived?"]
    lines.append(f"  {'arm':<14} {'n':>3} "
                 f"{'median µ±σ (ms)':>17} "
                 f"{'p99 µ±σ (ms)':>16} "
                 f"{'max µ±σ (ms)':>16}")
    for cid, arm in report["per_arm"].items():
        lines.append(f"  {arm['label']:<14} {arm['n']:>3} "
                     f"{C.msd(arm['median_xs'], '{:.1f}'):>17} "
                     f"{C.msd(arm['p99_xs'], '{:.1f}'):>16} "
                     f"{C.msd(arm['max_xs'], '{:.1f}'):>16}")

    # Per-stage table when at least one arm has stage data.
    any_stages = any(arm.get("stage_rep_means")
                     for arm in report["per_arm"].values())
    if not any_stages:
        return "\n".join(lines)
    lines.append("")
    lines.append("  per-stage breakdown (median ms across reps):")
    header = (f"  {'arm':<14} " +
              " ".join(f"{p[0]:>9}" for p in _STAGE_PAIRS))
    lines.append(header)
    for cid, arm in report["per_arm"].items():
        cells = []
        for pair_name, *_ in _STAGE_PAIRS:
            xs = arm["stage_rep_means"].get(pair_name) or []
            cells.append(f"{statistics.mean(xs):>9.2f}" if xs else f"{'—':>9}")
        lines.append(f"  {arm['label']:<14} " + " ".join(cells))
    return "\n".join(lines)
