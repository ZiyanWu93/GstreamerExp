"""Emit a JSON data snapshot for the H1 visualization page.

The H1 verifier prints a single verdict per experiment. The HTML
visualization in `h1.html` needs richer data: per-rep encoder traces,
per-rep bottleneck stats, and the capacity envelope. This script writes
that snapshot to `analysis/hypotheses/h1_data_<experiment>.json`.

Run once per regime, then open `h1.html` to view.
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

from analysis import _common as C   # noqa: E402

ARM_COLORS = {"scream": "#d62728", "gcc": "#1f77b4"}


def _arm_algo(config_id: str) -> str | None:
    cfg = PROJECT_ROOT / "specs" / "configurations" / f"{config_id}.yaml"
    if not cfg.is_file():
        return None
    return ((yaml.safe_load(cfg.read_text()) or {})
            .get("congestion_control") or {}).get("algorithm")


def _phase_envelope(steps: list[dict]) -> list[dict]:
    out, cum = [], 0.0
    for s in steps:
        dur = float(s.get("duration") or 0)
        out.append({
            "start_s":  cum,
            "end_s":    cum + dur,
            "cap_kbps": int(s.get("rate_kbps") or 0),
            "label":    s.get("label") or "",
        })
        cum += dur
    return out


def _overall_util(samples: list, envelope: list[dict]) -> float | None:
    """Time-weighted ratio of encoder bits sent to network bits available
    across the full run. Step-integrates the encoder rate (each sample
    applies until the next) and integrates the capacity envelope over
    the same window. Returns total_encoder_bits / total_capacity_bits.

    > 1.0  encoder produced more bits than the network could carry
           (over-pushing summed across the run)
    < 1.0  encoder produced less than the network could carry
           (bandwidth left unused summed across the run)
    """
    if not samples or not envelope:
        return None
    t_end = envelope[-1]["end_s"]
    clipped = [(t, k) for t, k in samples if 0 <= t <= t_end]
    if len(clipped) < 2:
        return None
    enc_bits = 0.0
    for i in range(len(clipped) - 1):
        t_i, k_i = clipped[i]
        t_next, _ = clipped[i + 1]
        enc_bits += k_i * (t_next - t_i)
    t_last, k_last = clipped[-1]
    if t_end > t_last:
        enc_bits += k_last * (t_end - t_last)
    t_start = clipped[0][0]
    cap_bits = 0.0
    for p in envelope:
        a = max(p["start_s"], t_start)
        b = min(p["end_s"], t_end)
        if b > a:
            cap_bits += p["cap_kbps"] * (b - a)
    if cap_bits <= 0:
        return None
    return enc_bits / cap_bits


def _rep_snapshot(config_id: str, run_id: str,
                  steps: list[dict],
                  envelope: list[dict]) -> dict | None:
    run_dir = PROJECT_ROOT / "runs" / config_id / run_id
    cam = run_dir / "camera.json"
    if not cam.is_file():
        return None
    data = json.loads(cam.read_text())
    samples = ((data.get("metrics") or {})
               .get("encoder_target_kbps") or {}).get("samples") or []
    if not samples:
        return None
    # Receiver-side data: total frame count + per-second arrival rate.
    # frame_count.summary.frames = how many complete frames decoded.
    # wire_bytes.samples = per-second bandwidth arriving at viewer
    # (kbps), suitable for plotting as a time series alongside the
    # sender's encoder-target rate.
    viewer_frames = None
    viewer_wire_samples = []
    vp = run_dir / "viewer.json"
    if vp.is_file():
        try:
            vd = json.loads(vp.read_text())
            metrics = vd.get("metrics") or {}
            viewer_frames = ((metrics.get("frame_count") or {})
                             .get("summary") or {}).get("frames")
            viewer_wire_samples = ((metrics.get("wire_bytes") or {})
                                   .get("samples") or [])
        except Exception:
            viewer_frames = None
            viewer_wire_samples = []
    boundaries = C.phase_boundaries_ns(steps)
    min_cap = min((s.get("rate_kbps") or 10**9) for s in steps)
    bottleneck_means, bottleneck_caps, all_in_phase = [], [], []
    for i, (start_ns, end_ns, _) in enumerate(boundaries):
        cap = int(steps[i].get("rate_kbps") or 0)
        if cap != min_cap:
            continue
        in_phase = [k for t, k in samples
                    if start_ns / 1e9 <= t < end_ns / 1e9]
        if in_phase:
            bottleneck_means.append(statistics.mean(in_phase))
            bottleneck_caps.append(cap)
            all_in_phase.extend(in_phase)
    util_mean = (statistics.mean(m / c for m, c in
                                 zip(bottleneck_means, bottleneck_caps))
                 if bottleneck_means else None)
    # Per-rep sawtooth amplitude: stddev of encoder-rate samples pooled
    # across all bottleneck-phase windows in this rep. With one
    # bottleneck phase (sparse spec) this is the sample-level sawtooth
    # within that single window. With many (fast spec) it pools
    # across all of them, capturing the rep's typical bottleneck
    # variability. Use this for the per-rep distribution plot.
    rate_stddev_within = (statistics.stdev(all_in_phase)
                          if len(all_in_phase) > 1 else 0.0)
    return {
        "run_id":  run_id,
        "samples": [[round(t, 3), round(k, 1)] for t, k in samples],
        "bottleneck_util_mean":              util_mean,
        "bottleneck_rate_stddev_kbps":       rate_stddev_within,
        # Per-phase means within bottleneck phases. Pooled across reps
        # at the aggregate layer to match the verifier's `var_ratio`
        # (which is stddev across all (phase × rep) instances).
        "bottleneck_phase_means_kbps":       bottleneck_means,
        # Overall (whole-run) ratio of encoder bits sent to network
        # bits available. Different question from bottleneck_util_mean:
        # this folds in the high-capacity phases where the encoder is
        # bounded by its own configured max, not by the algorithm.
        "overall_util":                      _overall_util(samples, envelope),
        # Receiver-side: complete frames the viewer decoded. The
        # operationally relevant metric (the one a teleoperator sees).
        "viewer_frames":                     viewer_frames,
        # Receiver-side time series: bytes per second arriving at the
        # viewer. Same units and shape as `samples` (sender-side
        # encoder rate), suitable for parallel plotting.
        "viewer_wire_samples":               [[round(t, 2), round(k, 1)]
                                              for t, k in viewer_wire_samples],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True,
                    help="experiment name (e.g. scream-vs-gcc-720p)")
    ap.add_argument("--regime-label", required=True,
                    help="human-readable label for this regime")
    args = ap.parse_args()

    record_path = PROJECT_ROOT / "runs" / "experiments" / f"{args.experiment}.json"
    if not record_path.is_file():
        sys.exit(f"experiment record not found: {record_path}")
    record = json.loads(record_path.read_text())

    arms_by_algo: dict[str, dict] = {}
    for c in record["configurations"]:
        algo = _arm_algo(c["id"])
        if algo not in ARM_COLORS:
            continue
        arms_by_algo[algo] = {
            "config_id": c["id"],
            "label":     c["label"],
            "color":     ARM_COLORS[algo],
            "algorithm": algo,
            "reps":      [],
        }

    # Network envelope: assumes both arms use the same network spec
    # (which is the H1 design — sparse uses fluctuating-5mbps-300kbps,
    # fast uses fluctuating-fast-5mbps-300kbps; both arms within an
    # experiment share one). Compute once before the per-arm loop so
    # the rep snapshot can use it for the overall-utilization integral.
    first_steps = C.load_camera_steps_for_config(
        next(iter(arms_by_algo.values()))["config_id"]) or []
    envelope = _phase_envelope(first_steps)

    steps_by_arm: dict[str, list[dict]] = {}
    for algo, arm in arms_by_algo.items():
        steps = C.load_camera_steps_for_config(arm["config_id"]) or []
        steps_by_arm[algo] = steps
        for r in record["runs"]:
            if r["config"] != arm["config_id"] or r["exit_code"] != 0:
                continue
            snap = _rep_snapshot(arm["config_id"], r["run_id"],
                                 steps, envelope)
            if snap is not None:
                arm["reps"].append(snap)

    # Aggregate stats across reps per arm.
    def _mean_or_none(xs):
        xs = [x for x in xs if x is not None]
        return statistics.mean(xs) if xs else None

    s = arms_by_algo.get("scream", {}).get("reps", [])
    g = arms_by_algo.get("gcc", {}).get("reps", [])
    s_util = _mean_or_none([r["bottleneck_util_mean"] for r in s])
    g_util = _mean_or_none([r["bottleneck_util_mean"] for r in g])
    # Aggregate variance metric: cross-rep stddev of the per-rep mean
    # rate during the bottleneck phase. This matches the verifier's
    # `var_ratio` so the page's verdict text agrees with the CLI.
    def _xrep_stddev(reps):
        xs = [m for r in reps
              for m in (r.get("bottleneck_phase_means_kbps") or [])]
        return statistics.stdev(xs) if len(xs) > 1 else 0.0
    s_var  = _xrep_stddev(s)
    g_var  = _xrep_stddev(g)
    s_overall = _mean_or_none([r["overall_util"] for r in s])
    g_overall = _mean_or_none([r["overall_util"] for r in g])
    util_gap  = (g_util - s_util) if (s_util is not None and g_util is not None) else None
    var_ratio = (g_var / s_var) if (s_var and g_var and s_var > 0) else None
    verdict = None
    if util_gap is not None and var_ratio is not None:
        ok_util = util_gap >= 0.05
        ok_var  = var_ratio >= 1.20
        verdict = ("supported" if (ok_util and ok_var) else
                   "refuted"   if (not ok_util and not ok_var) else
                   "inconclusive")

    snapshot = {
        "experiment":   args.experiment,
        "regime_label": args.regime_label,
        "envelope":     envelope,
        "arms":         list(arms_by_algo.values()),
        "aggregate":    {
            "scream_util_mean":        s_util,
            "gcc_util_mean":           g_util,
            "util_gap":                util_gap,
            "scream_rate_stddev_kbps": s_var,
            "gcc_rate_stddev_kbps":    g_var,
            "var_ratio":               var_ratio,
            "verdict":                 verdict,
            "scream_overall_util":     s_overall,
            "gcc_overall_util":        g_overall,
        },
    }

    out = PROJECT_ROOT / "analysis" / "hypotheses" / f"h1_data_{args.experiment}.json"
    out.write_text(json.dumps(snapshot, indent=2))
    print(f"wrote {out}  ({sum(len(a['reps']) for a in snapshot['arms'])} reps)")


if __name__ == "__main__":
    main()
