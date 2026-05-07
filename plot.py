#!/usr/bin/env python3
"""Plot bitrate trajectories from an experiment's experiment record.

Usage:
    python3 plot.py runs/experiments/<name>.json
    python3 plot.py --latest

experiment.py writes the experiment record at runs/experiments/<name>.json
after every individual run lands; it carries the experiment spec's
configurations + the shared network's steps + the list of completed
runs, so plot.py is self-contained against this single file.

Renders bitrate, latency, and frame-arrival ribbons (faint per-run +
bold mean) per configuration, overlaid on the impairment-step shading
the network spec declared. Output lands at runs/figures/<name>.png.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")  # headless — no need for X server
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parent
RUNS_ROOT = PROJECT_ROOT / "runs"
EXPT_DIR = RUNS_ROOT / "experiments"
FIG_DIR = RUNS_ROOT / "figures"

# Network steps, labels, and colors all come from the experiment record:
# experiment.py copies the shared network spec's `steps` block in as
# `network_steps`, and the configurations' label/color from the
# experiment spec. plot.py is generic across experiments — no
# scenario-specific constants live here.


def _steps_to_intervals(steps: list[dict]) -> list[tuple]:
    """Convert a list of {duration, rate_kbps, ..., label} steps into
    (t_start, t_end, link_kbps, label) tuples for the plotter. Returns
    [] when the experiment has no impairment (e.g. loopback)."""
    out = []
    t = 0.0
    for s in steps:
        t_end = t + float(s.get("duration", 0))
        out.append((t, t_end, int(s["rate_kbps"]), s["label"]))
        t = t_end
    return out


def _load_bitrate_traj(run_dir: Path) -> np.ndarray | None:
    """[N,2] array of (elapsed_s, kbps) encoder-target samples."""
    camera_json = run_dir / "camera.json"
    if not camera_json.exists():
        return None
    data = json.loads(camera_json.read_text())
    samples = (data.get("metrics", {})
                   .get("encoder_target_kbps", {})
                   .get("samples", []))
    if not samples:
        return None
    return np.asarray(samples, dtype=float)


def _load_latency_traj(run_dir: Path) -> np.ndarray | None:
    """[N,2] array of (camera_elapsed_s, latency_ms) per frame.

    Reads camera.json + viewer.json (frame_latency samples) and
    _clock_skew.json (per-host skew vs controller). Correlates samples
    by RTP timestamp, applies skew correction, returns per-frame latency
    indexed by camera's elapsed time since first sample. The first
    camera sample is t=0 — same convention as encoder_target_kbps.
    """
    s_path = run_dir / "camera.json"
    r_path = run_dir / "viewer.json"
    if not s_path.exists() or not r_path.exists():
        return None
    camera = json.loads(s_path.read_text())
    viewer = json.loads(r_path.read_text())
    s_samples = (camera.get("metrics", {})
                       .get("frame_latency", {})
                       .get("samples", []))
    r_samples = (viewer.get("metrics", {})
                         .get("frame_latency", {})
                         .get("samples", []))
    if not s_samples or not r_samples:
        return None
    skew = {"camera_skew_s": 0.0, "viewer_skew_s": 0.0}
    skew_path = run_dir / "_clock_skew.json"
    if skew_path.exists():
        skew = json.loads(skew_path.read_text())
    s_skew = float(skew.get("camera_skew_s", 0.0))
    r_skew = float(skew.get("viewer_skew_s", 0.0))

    # Samples are [rtp_timestamp, wall_time]; rtp_timestamp identifies
    # the same frame on both sides because it travels in the packet header.
    s_by_ts = {ts: t - s_skew for ts, t in s_samples}
    r_by_ts = {ts: t - r_skew for ts, t in r_samples}
    t0 = min(s_by_ts.values())
    pairs = []
    for ts, st in s_by_ts.items():
        if ts in r_by_ts:
            pairs.append((st - t0, (r_by_ts[ts] - st) * 1000.0))
    if not pairs:
        return None
    pairs.sort()
    return np.asarray(pairs, dtype=float)


def _resample(traj: np.ndarray, t_grid: np.ndarray) -> np.ndarray:
    """Linear-interpolate a (t, y) trajectory onto a fixed time grid."""
    t = traj[:, 0]
    y = traj[:, 1]
    return np.interp(t_grid, t, y, left=y[0], right=y[-1])


def _bin_to_grid(traj: np.ndarray, t_grid: np.ndarray,
                 half_window: float = 0.5) -> np.ndarray:
    """For each grid point, take the mean of trajectory samples falling
    in [t-half, t+half). Empty buckets are NaN. Used for irregularly-
    sampled data like per-frame latency, which can't be interpolated
    naively (frame loss leaves gaps)."""
    out = np.full(t_grid.shape, np.nan, dtype=float)
    if traj.size == 0:
        return out
    t_arr = traj[:, 0]
    y_arr = traj[:, 1]
    for i, t_c in enumerate(t_grid):
        mask = (t_arr >= t_c - half_window) & (t_arr < t_c + half_window)
        if mask.any():
            out[i] = y_arr[mask].mean()
    return out


def _count_to_grid(arrival_times: np.ndarray, t_grid: np.ndarray,
                   half_window: float = 0.5) -> np.ndarray:
    """For each grid point, count the number of arrival times falling
    in [t-half, t+half). Returns 0 inside the run window (no NaN), so
    'no frames received this second' is correctly reported as a stall
    rather than missing data."""
    out = np.zeros(t_grid.shape, dtype=float)
    if arrival_times.size == 0:
        return out
    rate_per_window = 1.0 / (2 * half_window)   # convert count → /sec
    for i, t_c in enumerate(t_grid):
        mask = ((arrival_times >= t_c - half_window) &
                (arrival_times <  t_c + half_window))
        out[i] = mask.sum() * rate_per_window
    return out


def _load_recv_wire_traj(run_dir: Path) -> np.ndarray | None:
    """[N,2] of (elapsed_s, kbps) for viewer-side wire_bytes — the
    actual on-wire RX rate, after losses. Compare with camera's target
    bitrate to see "intent vs reality" on the viewer side."""
    r_path = run_dir / "viewer.json"
    if not r_path.exists():
        return None
    viewer = json.loads(r_path.read_text())
    samples = (viewer.get("metrics", {})
                       .get("wire_bytes", {})
                       .get("samples", []))
    if not samples or len(samples) < 2:
        return None
    return np.asarray(samples, dtype=float)


def _load_recv_arrivals(run_dir: Path) -> np.ndarray | None:
    """Per-frame arrival times at the viewer, expressed as elapsed
    seconds since the camera's first send. Used to count frames/sec
    on the same time axis as the bitrate and latency panels."""
    s_path = run_dir / "camera.json"
    r_path = run_dir / "viewer.json"
    if not s_path.exists() or not r_path.exists():
        return None
    camera = json.loads(s_path.read_text())
    viewer = json.loads(r_path.read_text())
    s_samples = (camera.get("metrics", {})
                       .get("frame_latency", {})
                       .get("samples", []))
    r_samples = (viewer.get("metrics", {})
                         .get("frame_latency", {})
                         .get("samples", []))
    if not s_samples or not r_samples:
        return None
    skew = {"camera_skew_s": 0.0, "viewer_skew_s": 0.0}
    skew_path = run_dir / "_clock_skew.json"
    if skew_path.exists():
        skew = json.loads(skew_path.read_text())
    s_skew = float(skew.get("camera_skew_s", 0.0))
    r_skew = float(skew.get("viewer_skew_s", 0.0))
    # Samples are [rtp_timestamp, wall_time]; we discard the timestamp
    # here and only report when each receive event happened.
    t0 = min(t - s_skew for _, t in s_samples)
    return np.asarray([t - r_skew - t0 for _, t in r_samples], dtype=float)


def _ribbon(traj_list_resampled: list[np.ndarray]) -> tuple[np.ndarray, ...]:
    """Stack per-run trajectories and return (min, mean, max) along axis 0,
    NaN-aware so missing buckets don't poison neighbors."""
    arr = np.stack(traj_list_resampled)
    return (np.nanmin(arr, axis=0),
            np.nanmean(arr, axis=0),
            np.nanmax(arr, axis=0))


def _resolve_record(arg: str | None, latest: bool) -> Path:
    if latest:
        records = sorted(EXPT_DIR.glob("*.json"))
        if not records:
            sys.exit(f"no experiment records under {EXPT_DIR}")
        return records[-1]
    if not arg:
        sys.exit("experiment path required (or --latest)")
    p = Path(arg)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if not p.is_file():
        sys.exit(f"not a file: {arg}")
    return p


def main():
    parser = argparse.ArgumentParser(description="Trajectory plotter")
    parser.add_argument("experiment", nargs="?",
                        help="Path to runs/experiments/<name>.json")
    parser.add_argument("--latest", action="store_true",
                        help="Use the most recent experiment record under runs/experiments")
    parser.add_argument("--out", default=None,
                        help="Output figure path (default: runs/figures/<name>.png)")
    args = parser.parse_args()

    record_path = _resolve_record(args.experiment, args.latest)
    experiment = json.loads(record_path.read_text())
    name = experiment["name"]

    # Pull labels and colors from the experiment's `configurations` block —
    # experiment.py copies these in from the experiment spec, so plot.py
    # is generic across experiments without needing per-config dicts.
    cfg_meta = {str(c["id"]): c for c in experiment.get("configurations", [])}
    if not cfg_meta:
        sys.exit(f"{record_path.name} has no `configurations` block — "
                 f"old-format record from a previous experiment.py. "
                 f"Re-run the experiment to regenerate.")

    def color_for(cid: str) -> str:
        return cfg_meta.get(cid, {}).get("color", "#444444")

    def label_for(cid: str) -> str:
        return cfg_meta.get(cid, {}).get("label", f"config {cid}")

    # Group successful runs by config; collect bitrate, latency, and
    # viewer-arrival trajectories. Each may be absent independently
    # (latency + arrivals need both camera + viewer result files).
    bitrate_by_cfg: dict[str, list[np.ndarray]] = {}
    rxwire_by_cfg:  dict[str, list[np.ndarray]] = {}
    latency_by_cfg: dict[str, list[np.ndarray]] = {}
    arrivals_by_cfg: dict[str, list[np.ndarray]] = {}
    skipped = 0
    for r in experiment["runs"]:
        if r.get("exit_code", 1) != 0 or not r.get("run_id"):
            skipped += 1
            continue
        run_dir = RUNS_ROOT / r["config"] / r["run_id"]
        b = _load_bitrate_traj(run_dir)
        if b is not None and len(b) >= 2:
            bitrate_by_cfg.setdefault(r["config"], []).append(b)
        rxw = _load_recv_wire_traj(run_dir)
        if rxw is not None:
            rxwire_by_cfg.setdefault(r["config"], []).append(rxw)
        lat = _load_latency_traj(run_dir)
        if lat is not None and len(lat) >= 2:
            latency_by_cfg.setdefault(r["config"], []).append(lat)
        arr = _load_recv_arrivals(run_dir)
        if arr is not None and arr.size >= 2:
            arrivals_by_cfg.setdefault(r["config"], []).append(arr)

    if not bitrate_by_cfg:
        sys.exit(f"no usable trajectories in {record_path.name}")

    intervals = _steps_to_intervals(experiment.get("network_steps") or [])

    # Common time grid based on the longest trajectory's range, extended
    # to cover the network steps if any are declared.
    t_max_bitrate = max(t[-1, 0] for tl in bitrate_by_cfg.values() for t in tl)
    t_max_latency = (max((t[-1, 0] for tl in latency_by_cfg.values() for t in tl),
                         default=0.0))
    t_max_steps = intervals[-1][1] if intervals else 0.0
    t_max = max(t_max_bitrate, t_max_latency, t_max_steps)
    t_grid = np.arange(0.0, float(t_max) + 1.0, 0.5)
    x_max = t_max

    # Four panels stacked vertically, sharing X:
    #   top      — link capacity over time (context)
    #   panel 2  — camera target + viewer wire bitrate (intent vs arrival)
    #   panel 3  — end-to-end latency (perceived delay)
    #   bottom   — frames/sec at viewer (perceived smoothness / stalls)
    # Heights 1 : 2.5 : 2.5 : 2.5. Width 11 to leave room on the right
    # for legends placed outside the data area.
    fig, (ax_link, ax_br, ax_lat, ax_fps) = plt.subplots(
        4, 1, figsize=(11, 9.5), sharex=True,
        gridspec_kw={"height_ratios": [1, 2.5, 2.5, 2.5]},
    )

    # Shade every below-cap step across all panels — the visual cue for
    # "constrained period." Skipped entirely when the experiment has no
    # impairment (e.g. loopback, intervals = []).
    if intervals:
        peak_cap = max(cap for _, _, cap, _ in intervals)
        for ax in (ax_link, ax_br, ax_lat, ax_fps):
            for t0, t1, cap, _ in intervals:
                if cap < peak_cap:
                    ax.axvspan(t0, t1, color="#cccccc", alpha=0.35, zorder=0)

    # ----- TOP: link capacity -----
    if intervals:
        cap_x, cap_y = [], []
        for t0, t1, cap, _ in intervals:
            cap_x.extend([t0, t1])
            cap_y.extend([cap, cap])
        ax_link.plot(cap_x, cap_y, color="black", linewidth=2.0, zorder=3)
        ax_link.fill_between(cap_x, 0, cap_y, color="black", alpha=0.08, zorder=1)
        link_y_max = max(cap for _, _, cap, _ in intervals) * 1.25
        ax_link.set_ylim(0, link_y_max)
        ax_link.grid(True, alpha=0.3)
        for t0, t1, _cap, txt in intervals:
            ax_link.text((t0 + t1) / 2, link_y_max * 0.92, txt,
                         ha="center", va="top", fontsize=9,
                         color="#222222",
                         bbox=dict(facecolor="white", edgecolor="#888888",
                                   alpha=0.9, pad=2.5, linewidth=0.5))
    else:
        ax_link.text(0.5, 0.5, "no network impairment declared",
                     transform=ax_link.transAxes, ha="center", va="center",
                     color="#888888", fontsize=9)
        ax_link.set_yticks([])
    ax_link.set_ylabel("link capacity\n(kbps)")

    # Configurations plot in spec order so colors are stable across re-runs.
    config_order = [str(c["id"]) for c in experiment.get("configurations", [])
                    if str(c["id"]) in bitrate_by_cfg]

    def _draw_ribbon(ax, by_cfg, sample_fn):
        """For each config, resample each run's trajectory to the grid via
        sample_fn, compute min/mean/max ribbons, plot. Returns the global
        max across all means (for Y-axis sizing)."""
        global_max = 0.0
        for cid in config_order:
            if cid not in by_cfg:
                continue
            color = color_for(cid)
            label = label_for(cid)
            resampled = [sample_fn(t, t_grid) for t in by_cfg[cid]]
            mn, mean, mx = _ribbon(resampled)
            # Ribbon: min-max envelope.
            ax.fill_between(t_grid, mn, mx, color=color, alpha=0.18,
                            linewidth=0, zorder=2,
                            label=f"{label} min/max (n={len(resampled)})")
            # Mean line: bold.
            ax.plot(t_grid, mean, color=color, linewidth=2.4, zorder=4,
                    label=f"{label} mean")
            valid = mx[~np.isnan(mx)]
            if valid.size:
                global_max = max(global_max, float(valid.max()))
        return global_max

    # ----- MIDDLE: bitrate (camera target + viewer wire) -----
    br_max = _draw_ribbon(ax_br, bitrate_by_cfg, _resample)
    # Receiver-side wire rate as dashed lines (no ribbon — just per-run
    # mean across runs to avoid clutter). Reveals the gap between
    # algorithm intent and what actually reached the viewer.
    rx_max = 0.0
    for cid in config_order:
        if cid not in rxwire_by_cfg:
            continue
        color = color_for(cid)
        label = label_for(cid)
        resampled = np.stack([_resample(t, t_grid)
                              for t in rxwire_by_cfg[cid]])
        mean = resampled.mean(axis=0)
        ax_br.plot(t_grid, mean, color=color, linewidth=1.6,
                   linestyle="--", alpha=0.85, zorder=4,
                   label=f"{label} rx wire")
        rx_max = max(rx_max, float(mean.max()))
    ax_br.set_ylabel("bitrate (kbps)")
    ax_br.set_ylim(0, max(2800.0, br_max, rx_max) * 1.05)
    ax_br.grid(True, alpha=0.3)

    # ----- BOTTOM: end-to-end latency -----
    if latency_by_cfg:
        lat_max = _draw_ribbon(ax_lat, latency_by_cfg, _bin_to_grid)
        ax_lat.set_ylabel("end-to-end\nlatency (ms)")
        ax_lat.set_ylim(0, max(200.0, lat_max) * 1.05)
        ax_lat.grid(True, alpha=0.3)
        ax_lat.legend(loc="center left", bbox_to_anchor=(1.01, 0.5),
                  framealpha=0.95, fontsize=8.5, borderaxespad=0)
    else:
        ax_lat.text(0.5, 0.5, "no latency data\n(frame_latency metric not enabled)",
                    transform=ax_lat.transAxes, ha="center", va="center",
                    color="#888888")
        ax_lat.set_ylabel("end-to-end\nlatency (ms)")
        ax_lat.set_yticks([])

    # ----- BOTTOM: frames received per second (smoothness / stalls) -----
    if arrivals_by_cfg:
        # Sender source is 15 fps; expected line is 15 frames/sec when
        # the link can carry every frame. Drops below 15 = stalls / loss.
        ax_fps.axhline(15.0, color="#888888", linewidth=1.0, linestyle=":",
                       label="source (15 fps)", zorder=2)
        fps_max = _draw_ribbon(ax_fps, arrivals_by_cfg, _count_to_grid)
        ax_fps.set_ylabel("frames received\nper second")
        ax_fps.set_ylim(0, max(18.0, fps_max) * 1.05)
        ax_fps.grid(True, alpha=0.3)
        ax_fps.legend(loc="center left", bbox_to_anchor=(1.01, 0.5),
                      framealpha=0.95, fontsize=8.5, borderaxespad=0)
    else:
        ax_fps.text(0.5, 0.5, "no viewer-arrival data",
                    transform=ax_fps.transAxes, ha="center", va="center",
                    color="#888888")
        ax_fps.set_ylabel("frames received\nper second")
        ax_fps.set_yticks([])

    ax_fps.set_xlabel("time since camera start (s)")
    ax_fps.set_xlim(0, x_max)

    fig.suptitle(name, y=0.995)
    # Leave ~22% of width on the right for legends placed outside the
    # data area, and a sliver at the top for the suptitle.
    fig.tight_layout(rect=(0, 0, 0.78, 0.985))

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else (FIG_DIR / f"{name}.png")
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    print(f"[plot] wrote {out_path}")
    print("[plot] bitrate runs: " + ", ".join(
        f"{label_for(c)}={len(v)}" for c, v in bitrate_by_cfg.items()))
    if latency_by_cfg:
        print("[plot] latency runs: " + ", ".join(
            f"{label_for(c)}={len(v)}" for c, v in latency_by_cfg.items()))
    if skipped:
        print(f"[plot] skipped {skipped} runs (failed or missing trajectory)")


if __name__ == "__main__":
    main()
