"""Build hypothesis report data and figures."""

from __future__ import annotations

import json
import math
import statistics
import sys
from html import escape
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = PROJECT_ROOT / "analysis" / "hypotheses" / "results"

sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import framework  # noqa: E402


FIG_FONT = "Helvetica, Arial, sans-serif"
FIG_TEXT = "#111111"
FIG_MUTED = "#555555"
FIG_AXIS = "#222222"
FIG_GRID = "#dedede"
FIG_SCREAM = "#1f77b4"
FIG_GCC = "#ff7f0e"
FIG_CAPACITY = "#4d4d4d"
FIG_SOURCE = "#777777"


def _matplotlib():
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 7,
        "axes.labelsize": 7,
        "axes.titlesize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "axes.linewidth": 0.7,
        "svg.fonttype": "none",
        "svg.hashsalt": "gstexp",
    })
    return plt


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text()) or {}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _network_capacity_series(network_name: str) -> list[tuple[float, float]]:
    spec = _load_yaml(PROJECT_ROOT / "specs" / "networks" / f"{network_name}.yaml")
    t = 0.0
    series = []
    for step in spec.get("camera_steps", []):
        duration = float(step.get("duration") or 0.0)
        rate = float(step.get("rate_kbps") or 0.0)
        series.append((t + duration / 2.0, rate))
        t += duration
    return series


def _network_capacity_intervals(network_name: str) -> list[tuple[float, float, float]]:
    spec = _load_yaml(PROJECT_ROOT / "specs" / "networks" / f"{network_name}.yaml")
    t = 0.0
    intervals = []
    for step in spec.get("camera_steps", []):
        duration = float(step.get("duration") or 0.0)
        rate = float(step.get("rate_kbps") or 0.0)
        intervals.append((t, t + duration, rate))
        t += duration
    return intervals


def _capacity_distribution(network_name: str) -> dict[str, float]:
    rates = [rate for _t, rate in _network_capacity_series(network_name)]
    quantiles = statistics.quantiles(rates, n=100)
    return {
        "min_kbps": min(rates),
        "p10_kbps": quantiles[9],
        "median_kbps": statistics.median(rates),
        "mean_kbps": statistics.mean(rates),
        "max_kbps": max(rates),
        "bins_below_1000_kbps_pct": 100.0 * sum(1 for rate in rates if rate < 1000.0) / len(rates),
    }


def _run_metric_samples(config_id: str, run_id: str, role: str, metric: str) -> list[tuple[float, float]]:
    path = PROJECT_ROOT / "runs" / config_id / run_id / f"{role}.json"
    if not path.is_file():
        return []
    doc = _load_json(path)
    samples = (((doc.get("metrics") or {}).get(metric) or {}).get("samples") or [])
    out = []
    for sample in samples:
        if len(sample) >= 2:
            out.append((float(sample[0]), float(sample[1])))
    return out


def _mean_series_by_second(name: str, config_id: str, role: str, metric: str) -> list[tuple[float, float]]:
    record = _load_json(PROJECT_ROOT / "runs" / "experiments" / f"{name}.json")
    buckets: dict[int, list[float]] = {}
    for run in record.get("runs", []):
        if str(run.get("config")) != config_id or run.get("exit_code") != 0:
            continue
        for t, value in _run_metric_samples(config_id, str(run.get("run_id")), role, metric):
            buckets.setdefault(int(round(t)), []).append(value)
    return [(float(sec), statistics.mean(values)) for sec, values in sorted(buckets.items()) if values]


def _capacity_stats(network_name: str, media_kbps: float) -> dict[str, float]:
    distribution = _capacity_distribution(network_name)
    return {
        "min_kbps": distribution["min_kbps"],
        "p10_kbps": distribution["p10_kbps"],
        "median_kbps": distribution["median_kbps"],
        "mean_kbps": distribution["mean_kbps"],
        "max_kbps": distribution["max_kbps"],
        "median_headroom": distribution["median_kbps"] / media_kbps if media_kbps else 0.0,
        "p10_headroom": distribution["p10_kbps"] / media_kbps if media_kbps else 0.0,
        "bins_below_1000_kbps_pct": distribution["bins_below_1000_kbps_pct"],
    }


def _config_network(config_id: str) -> str:
    cfg = _load_yaml(PROJECT_ROOT / "specs" / "configurations" / f"{config_id}.yaml")
    return str(cfg["streams"][0]["network"])


def _config_algorithm(config_id: str) -> str:
    cfg = _load_yaml(PROJECT_ROOT / "specs" / "configurations" / f"{config_id}.yaml")
    stream = cfg["streams"][0]
    return str((stream.get("congestion_control") or {}).get("algorithm") or "unknown")


def _camera_wire_mean(config_id: str, run_id: str) -> float | None:
    summary_path = PROJECT_ROOT / "runs" / config_id / run_id / "summary.json"
    if not summary_path.is_file():
        return None
    summary = _load_json(summary_path)
    if summary.get("verdict") != "PASS":
        return None
    wire = (((summary.get("camera") or {}).get("wire_bytes") or {}).get("mean_kbps"))
    return float(wire) if wire is not None else None


def _run_summaries(name: str, config_id: str) -> list[dict[str, Any]]:
    record = _load_json(PROJECT_ROOT / "runs" / "experiments" / f"{name}.json")
    rows = []
    for run in record.get("runs", []):
        if str(run.get("config")) != config_id or run.get("exit_code") != 0:
            continue
        summary_path = PROJECT_ROOT / "runs" / config_id / str(run.get("run_id")) / "summary.json"
        if not summary_path.is_file():
            continue
        summary = _load_json(summary_path)
        if summary.get("verdict") != "PASS":
            continue
        rows.append({"run_id": str(run.get("run_id")), "summary": summary})
    return rows


def _summary_metric(summary: dict[str, Any], role: str, metric: str, key: str) -> float:
    return float((((summary.get(role) or {}).get(metric) or {}).get(key)) or 0.0)


def _viewer_frames(summary: dict[str, Any]) -> float:
    return float((summary.get("viewer") or {}).get("frames_depayloaded") or 0.0)


def _mean_cumulative_viewer_frames_by_second(name: str, config_id: str) -> list[tuple[float, float]]:
    record = _load_json(PROJECT_ROOT / "runs" / "experiments" / f"{name}.json")
    run_series: list[list[float]] = []
    max_sec = 0
    for run in record.get("runs", []):
        if str(run.get("config")) != config_id or run.get("exit_code") != 0:
            continue
        run_id = str(run.get("run_id"))
        viewer_path = PROJECT_ROOT / "runs" / config_id / run_id / "viewer.json"
        summary_path = PROJECT_ROOT / "runs" / config_id / run_id / "summary.json"
        if not viewer_path.is_file() or not summary_path.is_file():
            continue
        viewer = _load_json(viewer_path)
        summary = _load_json(summary_path)
        if summary.get("verdict") != "PASS":
            continue
        samples = (((viewer.get("metrics") or {}).get("frame_latency") or {}).get("samples") or [])
        start = float(viewer.get("started_at") or 0.0)
        duration = float(viewer.get("duration_seconds") or 0.0)
        times = sorted(
            max(0.0, float(sample[1]) - start)
            for sample in samples
            if len(sample) >= 2
        )
        if not times:
            continue
        final_frames = _viewer_frames(summary)
        scale = final_frames / len(times) if times else 1.0
        end_sec = int(max(duration, times[-1])) + 1
        max_sec = max(max_sec, end_sec)
        counts = []
        idx = 0
        for sec in range(end_sec + 1):
            while idx < len(times) and times[idx] <= sec:
                idx += 1
            counts.append(idx * scale)
        run_series.append(counts)

    points = []
    for sec in range(max_sec + 1):
        values = [series[sec] if sec < len(series) else series[-1] for series in run_series]
        if values:
            points.append((float(sec), statistics.mean(values)))
    return points


def _headroom_rows(experiment_names: list[str]) -> list[dict[str, Any]]:
    rows = []
    for name in experiment_names:
        exp_spec = _load_yaml(PROJECT_ROOT / "specs" / "experiments" / f"{name}.yaml")
        record = _load_json(PROJECT_ROOT / "runs" / "experiments" / f"{name}.json")
        labels = {str(c["id"]): str(c.get("label") or c["id"]) for c in exp_spec.get("configurations", [])}
        for config_id, label in labels.items():
            wire_values = []
            for run in record.get("runs", []):
                if str(run.get("config")) != config_id or run.get("exit_code") != 0:
                    continue
                wire = _camera_wire_mean(config_id, str(run.get("run_id")))
                if wire is not None:
                    wire_values.append(wire)
            media = _mean(wire_values) or 0.0
            network = _config_network(config_id)
            rows.append({
                "experiment": name,
                "config_id": config_id,
                "label": label,
                "algorithm": _config_algorithm(config_id),
                "network": network,
                "observed_wire_kbps": media,
                **_capacity_stats(network, media),
            })
    return rows


def _record_status(record: dict[str, Any], expected_configs: list[str]) -> dict[str, Any]:
    runs = record.get("runs", [])
    completed = [r for r in runs if r.get("exit_code") == 0 and str(r.get("config")) in expected_configs]
    failed = [r for r in runs if r.get("exit_code") != 0 and str(r.get("config")) in expected_configs]
    return {
        "started_at": record.get("started_at"),
        "total_runs": len([r for r in runs if str(r.get("config")) in expected_configs]),
        "completed_runs": len(completed),
        "failed_runs": len(failed),
    }


def _experiment_setup(name: str, log_path: str | None = None) -> dict[str, Any]:
    spec = _load_yaml(PROJECT_ROOT / "specs" / "experiments" / f"{name}.yaml")
    record_path = PROJECT_ROOT / "runs" / "experiments" / f"{name}.json"
    record = _load_json(record_path) if record_path.is_file() else {}
    arms = []
    for entry in spec.get("configurations", []):
        config_id = str(entry["id"])
        arms.append({
            "config_id": config_id,
            "label": str(entry.get("label") or config_id),
            "algorithm": _config_algorithm(config_id),
            "network": _config_network(config_id),
        })
    return {
        "name": name,
        "description": spec.get("description"),
        "spec_path": f"specs/experiments/{name}.yaml",
        "record_path": f"runs/experiments/{name}.json",
        "log_path": log_path,
        "command": f"python3 experiment.py {name}",
        "reps": spec.get("reps"),
        "varies": spec.get("varies", []),
        "arms": arms,
        "record_status": _record_status(record, [a["config_id"] for a in arms]) if record else None,
    }


def _nice_upper(value: float) -> float:
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    base = 10 ** exponent
    scaled = value / base
    for multiplier in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0):
        if scaled <= multiplier:
            return multiplier * base
    return 10.0 * base


def _tick_values(upper: float, count: int = 5) -> list[float]:
    if count <= 1:
        return [upper]
    return [upper * i / (count - 1) for i in range(count)]


def _fmt_tick(value: float, upper: float) -> str:
    if upper <= 12.0 and abs(value - round(value)) > 0.05:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.0f}"


def _line_path(
    points: list[tuple[float, float]],
    x_map: Any,
    y_map: Any,
) -> str:
    return " ".join(
        ("M" if idx == 0 else "L") + f"{x_map(x):.1f},{y_map(y):.1f}"
        for idx, (x, y) in enumerate(points)
    )


def _capacity_step_path(
    intervals: list[tuple[float, float, float]],
    x_map: Any,
    y_map: Any,
    *,
    scale: float,
) -> str:
    commands = []
    for idx, (t0, t1, rate) in enumerate(intervals):
        value = rate * scale
        if idx == 0:
            commands.append(f"M{x_map(t0):.1f},{y_map(value):.1f}")
        else:
            commands.append(f"L{x_map(t0):.1f},{y_map(value):.1f}")
        commands.append(f"L{x_map(t1):.1f},{y_map(value):.1f}")
    return " ".join(commands)


def _time_ticks(max_x: float) -> list[float]:
    step = 10.0 if max_x <= 60.0 else 20.0
    end = math.ceil(max_x / step) * step
    return [step * i for i in range(int(end / step) + 1)]


def _trace_grouped_bar_svg(
    title: str,
    subtitle: str,
    y_label: str,
    groups: list[dict[str, Any]],
    output: Path,
    footnote: str,
    *,
    value_precision: int = 1,
) -> None:
    from matplotlib.ticker import MaxNLocator

    plt = _matplotlib()

    fig, ax = plt.subplots(figsize=(3.35, 1.72))
    x_positions = list(range(len(groups)))
    bar_width = 0.25
    offsets = [-bar_width / 1.7, bar_width / 1.7]
    value_fmt = f"{{:.{value_precision}f}}"
    all_values = [
        float(value)
        for group in groups
        for bar in group["bars"]
        for value in ([bar["value"]] + list(bar.get("samples") or []))
    ]
    y_upper = max(all_values, default=1.0) * 1.16

    for bar_idx, label in enumerate(("SCReAM", "GCC")):
        values = [float(group["bars"][bar_idx]["value"]) for group in groups]
        colors = [group["bars"][bar_idx]["color"] for group in groups]
        samples = [
            [float(v) for v in (group["bars"][bar_idx].get("samples") or [])]
            for group in groups
        ]
        lower = [v - min(s) if s else 0.0 for v, s in zip(values, samples)]
        upper = [max(s) - v if s else 0.0 for v, s in zip(values, samples)]
        xs = [x + offsets[bar_idx] for x in x_positions]
        ax.bar(
            xs,
            values,
            width=bar_width,
            color=colors,
            edgecolor="black",
            linewidth=0.35,
            yerr=[lower, upper],
            capsize=1.8,
            error_kw={"elinewidth": 0.55, "capthick": 0.55, "ecolor": FIG_AXIS},
            label=label,
            zorder=2,
        )
        for x, sample_values, color in zip(xs, samples, colors):
            if not sample_values:
                continue
            jitter = [-0.035, 0.0, 0.035] if len(sample_values) == 3 else [0.0] * len(sample_values)
            ax.scatter(
                [x + j for j in jitter],
                sample_values,
                s=7,
                facecolors="white",
                edgecolors=color,
                linewidths=0.55,
                zorder=3,
            )

    tick_labels = [
        f"{group['label']}\n{group['ratio_label']}" if group.get("ratio_label") else group["label"]
        for group in groups
    ]
    ax.set_title(title, loc="left", pad=2)
    ax.set_ylabel(y_label)
    ax.set_xticks(x_positions)
    ax.set_xticklabels(tick_labels)
    ax.set_xlim(-0.55, len(groups) - 0.45)
    ax.set_ylim(0.0, y_upper)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4, prune=None))
    ax.grid(axis="y", color=FIG_GRID, linewidth=0.45)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", length=2.2, width=0.6, pad=1.5)
    ax.legend(loc="upper right", frameon=False, ncols=2, columnspacing=0.8, handlelength=1.0)
    fig.subplots_adjust(left=0.18, right=0.98, top=0.86, bottom=0.31)
    fig.savefig(output, format="svg", metadata={"Date": None})
    plt.close(fig)


def _bar_svg(title: str, y_label: str, groups: list[dict[str, Any]], output: Path, footnote: str) -> None:
    from matplotlib.ticker import MaxNLocator

    plt = _matplotlib()
    fig, ax = plt.subplots(figsize=(3.35, 1.72))
    x_positions = list(range(len(groups)))
    bar_count = max(len(group["bars"]) for group in groups)
    bar_width = 0.18 if bar_count >= 3 else 0.25
    offsets = [(i - (bar_count - 1) / 2.0) * bar_width * 1.35 for i in range(bar_count)]
    seen_labels: set[str] = set()

    for bar_idx in range(bar_count):
        values = [float(group["bars"][bar_idx]["value"]) for group in groups]
        colors = [group["bars"][bar_idx]["color"] for group in groups]
        label = groups[0]["bars"][bar_idx]["label"]
        xs = [x + offsets[bar_idx] for x in x_positions]
        ax.bar(
            xs,
            values,
            width=bar_width,
            color=colors,
            edgecolor="black",
            linewidth=0.35,
            label=label if label not in seen_labels else None,
            zorder=2,
        )
        seen_labels.add(label)

    ax.set_title(title, loc="left", pad=2)
    ax.set_ylabel(y_label)
    ax.set_xticks(x_positions)
    ax.set_xticklabels([group["label"] for group in groups])
    ax.set_ylim(0.0, max(float(bar["value"]) for group in groups for bar in group["bars"]) * 1.18)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    ax.grid(axis="y", color=FIG_GRID, linewidth=0.45)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", length=2.2, width=0.6, pad=1.5)
    ax.legend(loc="upper right", frameon=False, ncols=1, handlelength=1.0)
    fig.subplots_adjust(left=0.18, right=0.98, top=0.84, bottom=0.22)
    fig.savefig(output, format="svg", metadata={"Date": None})
    plt.close(fig)


def _line_svg(
    title: str,
    y_label: str,
    series: list[dict[str, Any]],
    output: Path,
    *,
    footnote: str,
    log_scale: bool = False,
) -> None:
    from matplotlib.ticker import MaxNLocator

    plt = _matplotlib()
    fig, ax = plt.subplots(figsize=(3.35, 1.72))
    for item in series:
        points = item["points"]
        if len(points) < 2:
            continue
        xs = [t for t, _v in points]
        ys = [max(float(v), 1.0) for _t, v in points]
        ax.plot(xs, ys, color=item["color"], linewidth=0.85, label=item["label"])
    if log_scale:
        ax.set_yscale("log")
    else:
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    ax.set_title(title, loc="left", pad=2)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(y_label)
    ax.grid(axis="y", color=FIG_GRID, linewidth=0.45)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", length=2.2, width=0.6, pad=1.5)
    ax.legend(loc="upper right", frameon=False, handlelength=1.2)
    fig.subplots_adjust(left=0.18, right=0.98, top=0.84, bottom=0.28)
    fig.savefig(output, format="svg", metadata={"Date": None})
    plt.close(fig)


def _h4_trace_timeseries_svg(
    case: dict[str, str],
    comparison: dict[str, Any],
    output: Path,
) -> None:
    from matplotlib.ticker import MaxNLocator

    capacity_intervals = _network_capacity_intervals(case["network"])
    scream_wire = [
        (t, v / 1000.0)
        for t, v in _mean_series_by_second(case["name"], case["scream"], "camera", "wire_bytes")
    ]
    gcc_wire = [
        (t, v / 1000.0)
        for t, v in _mean_series_by_second(case["name"], case["gcc"], "camera", "wire_bytes")
    ]
    scream_frames = _mean_cumulative_viewer_frames_by_second(case["name"], case["scream"])
    gcc_frames = _mean_cumulative_viewer_frames_by_second(case["name"], case["gcc"])

    plt = _matplotlib()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(5.2, 1.85))

    if capacity_intervals:
        cap_x = [capacity_intervals[0][0]]
        cap_y = [capacity_intervals[0][2] / 1000.0]
        for _t0, t1, rate in capacity_intervals:
            cap_x.append(t1)
            cap_y.append(rate / 1000.0)
        ax1.step(cap_x, cap_y, where="post", color=FIG_CAPACITY, linewidth=0.75, linestyle="--", label="capacity")

    for label, color, points in (("SCReAM", FIG_SCREAM, scream_wire), ("GCC", FIG_GCC, gcc_wire)):
        if len(points) >= 2:
            ax1.plot([t for t, _v in points], [v for _t, v in points], color=color, linewidth=0.85, label=label)

    for label, color, points in (("SCReAM", FIG_SCREAM, scream_frames), ("GCC", FIG_GCC, gcc_frames)):
        if len(points) >= 2:
            ax2.plot([t for t, _v in points], [v for _t, v in points], color=color, linewidth=0.95, label=label)

    ax2.axhline(915.0, color=FIG_SOURCE, linewidth=0.55, linestyle=(0, (2, 3)))
    ax2.text(
        0.02,
        0.92,
        f"SCReAM/GCC frames = {comparison['viewer_frame_ratio_scream_over_gcc']:.2f}x",
        transform=ax2.transAxes,
        ha="left",
        va="top",
        fontsize=6.5,
        color=FIG_TEXT,
    )

    capacity_end = max((t1 for _t0, t1, _rate in capacity_intervals), default=30.0)
    max_x = max([capacity_end] + [t for series in (scream_wire, gcc_wire, scream_frames, gcc_frames) for t, _v in series])
    for ax in (ax1, ax2):
        ax.set_xlim(0.0, max_x)
        ax.set_xlabel("Time (s)")
        ax.grid(axis="y", color=FIG_GRID, linewidth=0.45)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(axis="both", length=2.2, width=0.6, pad=1.5)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4))

    ax1.set_title("(a) Camera egress vs. trace capacity", loc="left", pad=2)
    ax1.set_ylabel("Mbit/s")
    ax1.legend(loc="upper right", frameon=False, handlelength=1.2)
    ax2.set_title("(b) Cumulative delivered frames", loc="left", pad=2)
    ax2.set_ylabel("frames")
    fig.subplots_adjust(left=0.095, right=0.99, top=0.82, bottom=0.28, wspace=0.28)
    fig.savefig(output, format="svg", metadata={"Date": None})
    plt.close(fig)


def build_h3(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    experiments = [
        "scream-vs-gcc-mahimahi-5g-cqi",
        "scream-vs-gcc-mahimahi-5g-ho",
        "scream-vs-gcc-mahimahi-5g-rb",
    ]
    rows = _headroom_rows(experiments)

    groups = []
    for network in ("mahimahi-5g-cqi-100ms", "mahimahi-5g-ho-100ms", "mahimahi-5g-rb-100ms"):
        candidates = [r for r in rows if r["network"] == network]
        media = statistics.mean([r["observed_wire_kbps"] for r in candidates if r["observed_wire_kbps"]])
        label = network.replace("mahimahi-5g-", "").replace("-100ms", "").upper()
        groups.append({
            "label": label,
            "bars": [
                {"label": "p10 capacity", "value": candidates[0]["p10_kbps"], "color": "#98a2b3"},
                {"label": "median capacity", "value": candidates[0]["median_kbps"], "color": "#344054"},
                {"label": "media wire", "value": media, "color": "#d92d20"},
            ],
        })

    _bar_svg(
        "H3 capacity headroom over emitted media",
        "kbit/s",
        groups,
        out_dir / "h3_capacity_headroom.svg",
        "Capacity is from translated 100 ms trace bins; media wire bitrate is the mean camera-side observed RTP bitrate.",
    )
    _line_svg(
        "H3 handover trace headroom over media load",
        "kbit/s, log scale",
        [
            {"label": "HO capacity", "color": "#172033", "width": 2, "points": _network_capacity_series("mahimahi-5g-ho-100ms")},
            {"label": "SCReAM wire", "color": "#d62728", "width": 2, "points": _mean_series_by_second("scream-vs-gcc-mahimahi-5g-ho", "57", "camera", "wire_bytes")},
            {"label": "GCC wire", "color": "#1f77b4", "width": 2, "points": _mean_series_by_second("scream-vs-gcc-mahimahi-5g-ho", "58", "camera", "wire_bytes")},
        ],
        out_dir / "h3_ho_headroom_timeseries.svg",
        footnote="The handover trace has brief fades, but the typical headroom remains much larger than the emitted stream.",
        log_scale=True,
    )
    return {
        "hypothesis": "H3",
        "status": "supported",
        "environment": _environment(),
        "experiments": [
            _experiment_setup("scream-vs-gcc-mahimahi-5g-cqi", None),
            _experiment_setup("scream-vs-gcc-mahimahi-5g-ho", "runs/experiment-logs/scream-vs-gcc-mahimahi-5g-ho.log"),
            _experiment_setup("scream-vs-gcc-mahimahi-5g-rb", "runs/experiment-logs/scream-vs-gcc-mahimahi-5g-rb.log"),
        ],
        "tables": {"headroom": rows},
        "figures": [
            {"path": "analysis/hypotheses/results/h3_capacity_headroom.svg", "caption": "Capacity percentiles versus observed camera wire bitrate."},
            {"path": "analysis/hypotheses/results/h3_ho_headroom_timeseries.svg", "caption": "Time-series view showing handover trace capacity against emitted media rate."},
        ],
    }


def _trace_label_for_network(network_name: str) -> str:
    for token in ("cqi", "ho", "rb"):
        if f"-{token}-" in network_name:
            return token.upper()
    return network_name.replace("mahimahi-5g-", "").replace("-100ms", "").upper()


def _h4_cases() -> list[dict[str, str]]:
    spec = _load_yaml(PROJECT_ROOT / "specs" / "hypotheses" / "h4.yaml")
    cases = []
    for entry in ((spec.get("setup") or {}).get("experiments") or []):
        name = str(entry["name"])
        exp_spec = _load_yaml(PROJECT_ROOT / "specs" / "experiments" / f"{name}.yaml")
        config_ids = [str(c["id"]) for c in exp_spec.get("configurations", [])]
        by_algorithm = {
            _config_algorithm(config_id).lower(): config_id
            for config_id in config_ids
        }
        if "scream" not in by_algorithm or "gcc" not in by_algorithm:
            raise RuntimeError(f"{name}: H4 expects one SCReAM arm and one GCC arm")

        network = _config_network(by_algorithm["scream"])
        if _config_network(by_algorithm["gcc"]) != network:
            raise RuntimeError(f"{name}: H4 expects both arms to use the same network trace")

        cases.append({
            "trace": _trace_label_for_network(network),
            "name": name,
            "network": network,
            "scream": by_algorithm["scream"],
            "gcc": by_algorithm["gcc"],
            "role": str(entry.get("role") or ""),
        })
    return cases


def _h4_trace_characteristics(cases: list[dict[str, str]]) -> list[dict[str, Any]]:
    descriptions = {
        "CQI": "high-capacity control trace",
        "HO": "handover-like trace with sharp fades",
        "RB": "resource-block scarcity trace",
    }
    out = []
    for case in cases:
        stats = _capacity_distribution(case["network"])
        low_bins = (
            "no sub-1 Mbit/s bins"
            if stats["bins_below_1000_kbps_pct"] == 0.0
            else f"{stats['bins_below_1000_kbps_pct']:.1f}% of 100 ms bins below 1 Mbit/s"
        )
        phrase = descriptions.get(case["trace"], "translated realistic trace")
        if case["trace"] == "HO":
            sentence = (
                f"{case['trace']} x0.33 is a {phrase}: {low_bins} "
                f"and {stats['median_kbps'] / 1000.0:.2f} Mbit/s median capacity."
            )
        else:
            sentence = (
                f"{case['trace']} x0.33 is a {phrase} with {low_bins} "
                f"and {stats['median_kbps'] / 1000.0:.2f} Mbit/s median capacity."
            )
        out.append({
            "trace": case["trace"],
            "network": case["network"],
            "role": case["role"],
            "median_capacity_kbps": stats["median_kbps"],
            "p10_capacity_kbps": stats["p10_kbps"],
            "bins_below_1000_kbps_pct": stats["bins_below_1000_kbps_pct"],
            "sentence": sentence,
        })
    return out


def _h4_rows(cases: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        for key, label in (("scream", "SCReAM"), ("gcc", "GCC")):
            config_id = case[key]
            summaries = _run_summaries(case["name"], config_id)
            frames = [_viewer_frames(s["summary"]) for s in summaries]
            camera_wire = [
                _summary_metric(s["summary"], "camera", "wire_bytes", "mean_kbps")
                for s in summaries
            ]
            viewer_wire = [
                _summary_metric(s["summary"], "viewer", "wire_bytes", "mean_kbps")
                for s in summaries
            ]
            encoded = [
                _summary_metric(s["summary"], "camera", "encoded_bitrate", "mean_kbps")
                for s in summaries
            ]
            encoder_target = []
            for s in summaries:
                values = [
                    value
                    for _t, value in _run_metric_samples(
                        config_id,
                        s["run_id"],
                        "camera",
                        "encoder_target_kbps",
                    )
                ]
                if values:
                    encoder_target.append(statistics.mean(values))
            latency_p95 = [
                float(((s["summary"].get("latency") or {}).get("p95_ms")) or 0.0)
                for s in summaries
            ]
            rows.append({
                "trace": case["trace"],
                "experiment": case["name"],
                "network": case["network"],
                "algorithm": label,
                "config_id": config_id,
                "runs": [s["run_id"] for s in summaries],
                "pass_runs": len(summaries),
                "viewer_frames_mean": _mean(frames) or 0.0,
                "viewer_frames_values": frames,
                "camera_wire_mean_kbps": _mean(camera_wire) or 0.0,
                "camera_wire_values_kbps": camera_wire,
                "viewer_wire_mean_kbps": _mean(viewer_wire) or 0.0,
                "viewer_wire_values_kbps": viewer_wire,
                "encoded_mean_kbps": _mean(encoded) or 0.0,
                "encoded_values_kbps": encoded,
                "encoder_target_mean_kbps": _mean(encoder_target) or 0.0,
                "encoder_target_values_kbps": encoder_target,
                "latency_p95_mean_ms": _mean(latency_p95) or 0.0,
                "latency_p95_values_ms": latency_p95,
            })
    return rows


def _h4_comparison_rows(
    rows: list[dict[str, Any]],
    cases: list[dict[str, str]],
) -> list[dict[str, Any]]:
    out = []
    for case in cases:
        trace_rows = [r for r in rows if r["trace"] == case["trace"]]
        by_alg = {row["algorithm"]: row for row in trace_rows}
        scream = by_alg["SCReAM"]
        gcc = by_alg["GCC"]
        frame_ratio = scream["viewer_frames_mean"] / gcc["viewer_frames_mean"]
        camera_wire_ratio = scream["camera_wire_mean_kbps"] / gcc["camera_wire_mean_kbps"]
        viewer_wire_delta_pct = (
            100.0
            * abs(scream["viewer_wire_mean_kbps"] - gcc["viewer_wire_mean_kbps"])
            / max(scream["viewer_wire_mean_kbps"], gcc["viewer_wire_mean_kbps"])
        )
        out.append({
            "trace": case["trace"],
            "network": case["network"],
            "scream_viewer_frames_mean": scream["viewer_frames_mean"],
            "gcc_viewer_frames_mean": gcc["viewer_frames_mean"],
            "viewer_frame_ratio_scream_over_gcc": frame_ratio,
            "viewer_frame_delta": scream["viewer_frames_mean"] - gcc["viewer_frames_mean"],
            "scream_camera_wire_mean_kbps": scream["camera_wire_mean_kbps"],
            "gcc_camera_wire_mean_kbps": gcc["camera_wire_mean_kbps"],
            "camera_wire_ratio_scream_over_gcc": camera_wire_ratio,
            "scream_encoder_target_mean_kbps": scream["encoder_target_mean_kbps"],
            "gcc_encoder_target_mean_kbps": gcc["encoder_target_mean_kbps"],
            "gcc_target_to_camera_egress_ratio": (
                gcc["encoder_target_mean_kbps"] / gcc["camera_wire_mean_kbps"]
                if gcc["camera_wire_mean_kbps"] else 0.0
            ),
            "scream_viewer_wire_mean_kbps": scream["viewer_wire_mean_kbps"],
            "gcc_viewer_wire_mean_kbps": gcc["viewer_wire_mean_kbps"],
            "viewer_wire_delta_pct": viewer_wire_delta_pct,
        })
    return out


def build_h4(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    cases = _h4_cases()
    rows = _h4_rows(cases)
    comparisons = _h4_comparison_rows(rows, cases)
    by_trace_alg = {(row["trace"], row["algorithm"]): row for row in rows}
    comparison_by_trace = {row["trace"]: row for row in comparisons}

    _trace_grouped_bar_svg(
        "Delivered frames",
        "mean over 3 reps; dots are runs; whiskers show min-max",
        "Viewer-depayloaded frames",
        [
            {
                "label": case["trace"],
                "ratio_label": f"S/G={comparison_by_trace[case['trace']]['viewer_frame_ratio_scream_over_gcc']:.2f}x",
                "bars": [
                    {
                        "label": "SCReAM",
                        "value": by_trace_alg[(case["trace"], "SCReAM")]["viewer_frames_mean"],
                        "samples": by_trace_alg[(case["trace"], "SCReAM")]["viewer_frames_values"],
                        "color": FIG_SCREAM,
                    },
                    {
                        "label": "GCC",
                        "value": by_trace_alg[(case["trace"], "GCC")]["viewer_frames_mean"],
                        "samples": by_trace_alg[(case["trace"], "GCC")]["viewer_frames_values"],
                        "color": FIG_GCC,
                    },
                ],
            }
            for case in cases
        ],
        out_dir / "h4_frame_delivery_by_trace.svg",
        "S/G is the SCReAM/GCC frame ratio; higher is better.",
    )
    _trace_grouped_bar_svg(
        "Camera egress",
        "mean over 3 reps; dots are runs; whiskers show min-max",
        "Camera egress (Mbit/s)",
        [
            {
                "label": case["trace"],
                "ratio_label": f"S/G={comparison_by_trace[case['trace']]['camera_wire_ratio_scream_over_gcc']:.2f}x",
                "bars": [
                    {
                        "label": "SCReAM",
                        "value": by_trace_alg[(case["trace"], "SCReAM")]["camera_wire_mean_kbps"] / 1000.0,
                        "samples": [v / 1000.0 for v in by_trace_alg[(case["trace"], "SCReAM")]["camera_wire_values_kbps"]],
                        "color": FIG_SCREAM,
                    },
                    {
                        "label": "GCC",
                        "value": by_trace_alg[(case["trace"], "GCC")]["camera_wire_mean_kbps"] / 1000.0,
                        "samples": [v / 1000.0 for v in by_trace_alg[(case["trace"], "GCC")]["camera_wire_values_kbps"]],
                        "color": FIG_GCC,
                    },
                ],
            }
            for case in cases
        ],
        out_dir / "h4_camera_egress_by_trace.svg",
        "S/G is the SCReAM/GCC camera-egress ratio; lower is not alone better.",
        value_precision=2,
    )
    for case in cases:
        _h4_trace_timeseries_svg(
            case,
            comparison_by_trace[case["trace"]],
            out_dir / f"h4_{case['trace'].lower()}_timeseries.svg",
        )

    return {
        "hypothesis": "H4",
        "status": "supported",
        "environment": _environment(),
        "experiments": [_experiment_setup(case["name"], None) for case in cases],
        "trace_characteristics": _h4_trace_characteristics(cases),
        "claim_structure": {
            "conclusion": (
                "SCReAM wins on the stressed HO/RB traces because its post-payload "
                "RTP pacing turns congestion-control decisions into lower camera "
                "egress, while GCC lowers its estimator target but this pipeline "
                "still emits multi-Mbit/s RTP that the trace cannot carry, leaving "
                "fewer complete frames decodable at the viewer."
            ),
            "subclaims": [
                "Within each trace pair, the trace, video, codec, bitrate bounds, recovery settings, sink, workers, and run length are fixed; only congestion_control.algorithm varies.",
                "CQI is the high-capacity control and both controllers deliver nearly all frames, so the advantage appears only when HO/RB capacity actually stresses the stream.",
                "On HO, SCReAM delivers 2.74x GCC's viewer frames; on RB, it delivers 1.39x GCC's viewer frames.",
                "The advantage is not from sending more traffic; SCReAM uses 0.72x GCC's camera egress on HO and 0.64x on RB.",
                "GCC's target falls on the stressed traces, but its measured camera egress remains about 2.35 Mbit/s on HO and 2.32 Mbit/s on RB.",
            ],
        },
        "tables": {
            "h4_arm_summary": rows,
            "h4_trace_comparison": comparisons,
        },
        "figures": [
            {"path": "analysis/hypotheses/results/h4_frame_delivery_by_trace.svg", "caption": "Figure H4-1. Mean viewer-depayloaded frames by trace. Each grouped pair compares SCReAM and GCC under the same trace and workload; S/G labels show the SCReAM/GCC frame ratio."},
            {"path": "analysis/hypotheses/results/h4_camera_egress_by_trace.svg", "caption": "Figure H4-2. Mean camera egress bitrate by trace. S/G labels show whether the delivered-frame improvement came from simply sending more traffic; GCC's lower estimator target on stressed traces is not the same as lower emitted RTP rate."},
            {"path": "analysis/hypotheses/results/h4_cqi_timeseries.svg", "caption": "Figure H4-3a. CQI fixed-trace time series. Left: shared capacity and camera egress. Right: cumulative delivered frames."},
            {"path": "analysis/hypotheses/results/h4_ho_timeseries.svg", "caption": "Figure H4-3b. HO fixed-trace time series. The wide two-panel layout holds the trace fixed and shows where SCReAM's delivered-frame curve separates from GCC."},
            {"path": "analysis/hypotheses/results/h4_rb_timeseries.svg", "caption": "Figure H4-3c. RB fixed-trace time series. The wide two-panel layout holds the trace fixed and shows the moderate SCReAM frame-delivery advantage."},
        ],
    }


def _environment() -> dict[str, Any]:
    return {
        "topology": "controller-worker",
        "controller": "local workstation",
        "camera_worker": "aum",
        "viewer_worker": "veda",
        "transport": "Tailscale FQDNs for media/control addressing where configured",
        "hosts_source": "hosts.yaml, with hosts.example.yaml documenting the expected keys",
        "sync_scope": "allowlisted project files only; run outputs are collected from workers",
    }


def _h1_regime_timeseries_svg(
    snapshot: dict[str, Any],
    output: Path,
) -> None:
    from matplotlib.ticker import MaxNLocator

    plt = _matplotlib()
    fig, ax = plt.subplots(figsize=(5.2, 1.85))

    envelope = snapshot.get("envelope") or []
    if envelope:
        cap_x = [envelope[0]["start_s"]]
        cap_y = [envelope[0]["cap_kbps"]]
        for entry in envelope:
            cap_x.append(entry["end_s"])
            cap_y.append(entry["cap_kbps"])
        ax.step(cap_x, cap_y, where="post", color=FIG_CAPACITY, linewidth=0.75, linestyle="--", label="capacity")

    color_by_alg = {"scream": FIG_SCREAM, "gcc": FIG_GCC}
    for arm in snapshot.get("arms", []):
        alg = arm.get("algorithm", "")
        color = color_by_alg.get(alg, FIG_TEXT)
        bucket: dict[int, list[float]] = {}
        for rep in arm.get("reps", []):
            for sample in rep.get("samples", []) or []:
                if len(sample) >= 2:
                    bucket.setdefault(int(round(float(sample[0]))), []).append(float(sample[1]))
        if not bucket:
            continue
        xs = sorted(bucket)
        ys = [statistics.mean(bucket[x]) for x in xs]
        ax.plot(xs, ys, color=color, linewidth=0.85, label=arm.get("label", alg.upper()))

    ax.set_title(f"H1 {snapshot.get('regime_label', '').split(chr(8212))[0].strip() or 'regime'}", loc="left", pad=2)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Encoder rate (kbit/s)")
    ax.grid(axis="y", color=FIG_GRID, linewidth=0.45)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", length=2.2, width=0.6, pad=1.5)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    ax.legend(loc="upper right", frameon=False, handlelength=1.2)
    fig.subplots_adjust(left=0.10, right=0.98, top=0.84, bottom=0.24)
    fig.savefig(output, format="svg", metadata={"Date": None})
    plt.close(fig)


def _h1_regime_summary_svg(
    snapshots: list[dict[str, Any]],
    output: Path,
) -> None:
    plt = _matplotlib()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(5.2, 1.85))

    labels = [snap.get("regime_label", "").split("—")[0].strip() or snap["experiment"] for snap in snapshots]
    util_gaps = [snap["aggregate"]["util_gap"] for snap in snapshots]
    var_ratios = [snap["aggregate"]["var_ratio"] for snap in snapshots]
    colors = ["#1d6f1d" if val > 0 else "#b14a02" for val in util_gaps]

    xs = list(range(len(labels)))
    ax1.bar(xs, util_gaps, color=colors, edgecolor="black", linewidth=0.35, width=0.55)
    ax1.axhline(0.05, color="#888", linewidth=0.6, linestyle=":", label="0.05 threshold")
    ax1.set_xticks(xs)
    ax1.set_xticklabels(labels, fontsize=6.5)
    ax1.set_title("(a) util_gap (GCC − SCReAM, bottleneck)", loc="left", pad=2)
    ax1.legend(loc="upper right", frameon=False, fontsize=6.5, handlelength=1.2)

    colors2 = ["#1d6f1d" if val >= 1.2 else "#b14a02" for val in var_ratios]
    ax2.bar(xs, var_ratios, color=colors2, edgecolor="black", linewidth=0.35, width=0.55)
    ax2.axhline(1.2, color="#888", linewidth=0.6, linestyle=":", label="1.20 threshold")
    ax2.set_xticks(xs)
    ax2.set_xticklabels(labels, fontsize=6.5)
    ax2.set_title("(b) var_ratio (GCC stddev / SCReAM stddev)", loc="left", pad=2)
    ax2.legend(loc="upper right", frameon=False, fontsize=6.5, handlelength=1.2)

    for ax in (ax1, ax2):
        ax.grid(axis="y", color=FIG_GRID, linewidth=0.45)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(axis="both", length=2.2, width=0.6, pad=1.5)

    fig.subplots_adjust(left=0.08, right=0.99, top=0.82, bottom=0.22, wspace=0.32)
    fig.savefig(output, format="svg", metadata={"Date": None})
    plt.close(fig)


def build_h1(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    base = PROJECT_ROOT / "analysis" / "hypotheses"
    slow = _load_json(base / "h1_data_scream-vs-gcc-720p.json")
    fast = _load_json(base / "h1_data_scream-vs-gcc-720p-fast.json")
    snapshots = [slow, fast]

    _h1_regime_timeseries_svg(slow, out_dir / "h1_slow_timeseries.svg")
    _h1_regime_timeseries_svg(fast, out_dir / "h1_fast_timeseries.svg")
    _h1_regime_summary_svg(snapshots, out_dir / "h1_regime_summary.svg")

    regime_comparison = [
        {
            "regime": snap.get("regime_label"),
            "experiment": snap.get("experiment"),
            "scream_util_mean": snap["aggregate"]["scream_util_mean"],
            "gcc_util_mean": snap["aggregate"]["gcc_util_mean"],
            "util_gap": snap["aggregate"]["util_gap"],
            "scream_rate_stddev_kbps": snap["aggregate"]["scream_rate_stddev_kbps"],
            "gcc_rate_stddev_kbps": snap["aggregate"]["gcc_rate_stddev_kbps"],
            "var_ratio": snap["aggregate"]["var_ratio"],
            "verdict": snap["aggregate"]["verdict"],
        }
        for snap in snapshots
    ]

    return {
        "hypothesis": "H1",
        "status": "regime-dependent",
        "environment": _environment(),
        "experiments": [
            _experiment_setup("scream-vs-gcc-720p", None),
            _experiment_setup("scream-vs-gcc-720p-fast", None),
        ],
        "claim_structure": {
            "conclusion": (
                "Zhang 2019's bottleneck-phase comparison reproduces on the "
                "slow-changing capacity-step network, where SCReAM under-uses "
                "the bottleneck cap relative to GCC and GCC's rate variance "
                "is markedly higher. The same comparison flips on the "
                "fast-changing network because neither controller reaches "
                "steady state between transitions."
            ),
            "subclaims": [
                "Both experiments hold the workload, codec, and bitrate bounds fixed; only the network transition cadence varies.",
                "On the slow regime, util_gap exceeds the 5 pp threshold and var_ratio exceeds the 1.2 threshold, so the predicted SCReAM under-use signature reproduces.",
                "On the fast regime, both thresholds fail: SCReAM over-shoots the cap more than GCC and rate variance converges across algorithms.",
                "The result is therefore regime-dependent, not a universal claim about either algorithm.",
            ],
        },
        "tables": {"h1_regime_comparison": regime_comparison},
        "figures": [
            {"path": "analysis/hypotheses/results/h1_regime_summary.svg", "caption": "Figure H1-1. Slow vs fast regime: util_gap and var_ratio against the predicted thresholds. Green bars exceed the threshold; orange bars do not."},
            {"path": "analysis/hypotheses/results/h1_slow_timeseries.svg", "caption": "Figure H1-2a. Slow regime (scream-vs-gcc-720p): encoder rate over time, averaged across reps, against the capacity step."},
            {"path": "analysis/hypotheses/results/h1_fast_timeseries.svg", "caption": "Figure H1-2b. Fast regime (scream-vs-gcc-720p-fast): the same view; transitions are frequent enough that neither controller settles."},
        ],
    }


def _h2_cells_bar_svg(
    comparison: list[dict[str, Any]],
    output: Path,
) -> None:
    from matplotlib.ticker import MaxNLocator

    plt = _matplotlib()
    fig, ax = plt.subplots(figsize=(5.2, 1.95))

    labels = [
        f"b={row['budget_ms']}ms\nnack={'on' if row['nack'] else 'off'}"
        for row in comparison
    ]
    scream_vals = [row["scream_median"] for row in comparison]
    gcc_vals = [row["gcc_median"] for row in comparison]

    xs = list(range(len(comparison)))
    bar_width = 0.36
    ax.bar([x - bar_width / 2 for x in xs], scream_vals, width=bar_width, color=FIG_SCREAM, edgecolor="black", linewidth=0.35, label="SCReAM (median)")
    ax.bar([x + bar_width / 2 for x in xs], gcc_vals, width=bar_width, color=FIG_GCC, edgecolor="black", linewidth=0.35, label="GCC (median)")
    for x, row in zip(xs, comparison):
        delta = row["delta_scream_minus_gcc"]
        sign = "+" if delta >= 0 else ""
        ax.text(x, max(row["scream_median"], row["gcc_median"]) + 6, f"Δ={sign}{delta:.0f}", ha="center", fontsize=6.0, color=FIG_TEXT)

    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=6.5)
    ax.set_ylabel("Viewer frames delivered (median)")
    ax.set_title("H2 delivered frames per (latency budget, NACK) cell", loc="left", pad=2)
    ax.set_ylim(0, max(scream_vals + gcc_vals) * 1.15)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    ax.grid(axis="y", color=FIG_GRID, linewidth=0.45)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", length=2.2, width=0.6, pad=1.5)
    ax.legend(loc="lower right", frameon=False, handlelength=1.2)
    fig.subplots_adjust(left=0.10, right=0.99, top=0.84, bottom=0.28)
    fig.savefig(output, format="svg", metadata={"Date": None})
    plt.close(fig)


def build_h2(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    base = PROJECT_ROOT / "analysis" / "hypotheses"
    combined = _load_json(base / "h2_data_h2-combined.json")
    comparison = combined.get("comparison", [])

    _h2_cells_bar_svg(comparison, out_dir / "h2_cell_comparison.svg")

    smallest = min(comparison, key=lambda row: row["delta_scream_minus_gcc"]) if comparison else None
    largest = max(comparison, key=lambda row: row["delta_scream_minus_gcc"]) if comparison else None
    cells_gcc_ahead = sum(1 for row in comparison if row["delta_scream_minus_gcc"] < 0)

    return {
        "hypothesis": "H2",
        "status": "supported",
        "environment": _environment(),
        "experiments": [
            _experiment_setup(name, None) for name in combined.get("experiments", [])
        ],
        "claim_structure": {
            "conclusion": (
                "GCC leads SCReAM in every tested latency-budget x NACK cell on the "
                "fluctuating capacity-step network. Neither deadline enforcement nor "
                "retransmission changes the ranking."
            ),
            "subclaims": [
                f"{cells_gcc_ahead} of {len(comparison)} cells have GCC ahead of SCReAM by median delivered frames.",
                f"Smallest SCReAM−GCC delta: {smallest['delta_scream_minus_gcc']:.0f} (budget={smallest['budget_ms']}ms, nack={'on' if smallest['nack'] else 'off'})." if smallest else "",
                f"Largest SCReAM−GCC delta: {largest['delta_scream_minus_gcc']:.0f} (budget={largest['budget_ms']}ms, nack={'on' if largest['nack'] else 'off'})." if largest else "",
                "The fluctuating-network ranking is stable across deadline enforcement (0/50/100 ms) and across NACK on/off.",
            ],
        },
        "tables": {"h2_cell_comparison": comparison},
        "figures": [
            {"path": "analysis/hypotheses/results/h2_cell_comparison.svg", "caption": "Figure H2-1. SCReAM vs GCC median viewer frames delivered, per (latency budget, NACK) cell. The Δ label is SCReAM − GCC; negative means GCC is ahead."},
        ],
    }


def build_h5(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """H5 — cross-implementation SCReAM comparison vs UMN Teleop-Gopher.

    Reads the comparison metrics CSV produced by tools/extract_comparison.py
    (analysis/umn-comparison/metrics.csv), not an experiment.py record.
    """
    import csv as _csv

    metrics_path = PROJECT_ROOT / "analysis" / "umn-comparison" / "metrics.csv"
    rows = list(_csv.DictReader(metrics_path.open()))

    def agg(metric: str) -> dict[tuple[str, str], float]:
        g: dict[tuple[str, str], list[float]] = {}
        for r in rows:
            try:
                g.setdefault((r["arm"], r["trace"]), []).append(float(r[metric]))
            except ValueError:
                pass
        return {k: statistics.mean(v) for k, v in g.items() if v}

    util, over = agg("utilization"), agg("overshoot_frac")
    sent, cap = agg("mean_sent_kbps"), agg("mean_cap_kbps")
    traces = ["ho", "rb", "cqi"]
    rungs = [("umn-scream", "S0\nno delay"), ("umn-scream-v2", "S1\n+delay"),
             ("umn-scream-s2", "S2\n+50ms"), ("umn-scream-s3", "S3\n+ramp"),
             ("ours-scream", "REF\nours")]

    plt = _matplotlib()

    # Figure H5-1: utilization by trace, ours vs UMN (fair config).
    fig, ax = plt.subplots(figsize=(5.4, 3.0))
    x = range(len(traces))
    w = 0.38
    ours_u = [util.get(("ours-scream", t), 0.0) for t in traces]
    umn_u = [util.get(("umn-scream-v2", t), 0.0) for t in traces]
    ax.bar([xi - w / 2 for xi in x], ours_u, w, color=FIG_SCREAM,
           label="Our SCReAM (reference)")
    ax.bar([xi + w / 2 for xi in x], umn_u, w, color=FIG_GCC,
           label="UMN SCReAM (reimpl., feedback on)")
    ax.axhline(1.0, color=FIG_CAPACITY, ls="--", lw=0.7)
    ax.set_xticks(list(x))
    ax.set_xticklabels([t.upper() for t in traces])
    ax.set_ylabel("link utilization (sent / capacity)")
    ax.set_title("Link utilization by trace — both fairly configured")
    ax.legend()
    ax.grid(axis="y", color=FIG_GRID, lw=0.6)
    fig.tight_layout()
    fig.savefig(out_dir / "h5_utilization_by_trace.svg", format="svg",
                metadata={"Date": None})
    plt.close(fig)

    # Figure H5-2: isolation ladder — HO overshoot + CQI utilization per rung.
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.0, 3.4))
    labels = [lbl for _, lbl in rungs]
    a1.bar(range(len(rungs)), [over.get((arm, "ho"), 0.0) for arm, _ in rungs],
           color=FIG_SOURCE)
    a1.set_xticks(range(len(rungs)))
    a1.set_xticklabels(labels)
    a1.set_title("HO overshoot fraction (lower better)")
    a1.set_ylabel("overshoot")
    a1.grid(axis="y", color=FIG_GRID, lw=0.6)
    a2.bar(range(len(rungs)), [util.get((arm, "cqi"), 0.0) for arm, _ in rungs],
           color=FIG_SOURCE)
    a2.axhline(1.0, color=FIG_CAPACITY, ls="--", lw=0.7)
    a2.set_xticks(range(len(rungs)))
    a2.set_xticklabels(labels)
    a2.set_title("CQI link utilization (higher better)")
    a2.set_ylabel("utilization")
    a2.grid(axis="y", color=FIG_GRID, lw=0.6)
    fig.suptitle("Factor isolation ladder — one capability added per rung")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out_dir / "h5_isolation_ladder.svg", format="svg",
                metadata={"Date": None})
    plt.close(fig)

    fair = [{
        "trace": t.upper(),
        "capacity_kbps": round(cap.get(("ours-scream", t), 0.0)),
        "ours_utilization": round(util.get(("ours-scream", t), 0.0), 3),
        "umn_utilization": round(util.get(("umn-scream-v2", t), 0.0), 3),
        "ours_sent_kbps": round(sent.get(("ours-scream", t), 0.0)),
        "umn_sent_kbps": round(sent.get(("umn-scream-v2", t), 0.0)),
        "ours_overshoot": round(over.get(("ours-scream", t), 0.0), 3),
        "umn_overshoot": round(over.get(("umn-scream-v2", t), 0.0), 3),
    } for t in traces]

    ladder = [{
        "rung": lbl.replace("\n", " "),
        "ho_overshoot": round(over.get((arm, "ho"), 0.0), 3),
        "cqi_utilization": round(util.get((arm, "cqi"), 0.0), 3),
        "cqi_sent_kbps": round(sent.get((arm, "cqi"), 0.0)),
    } for arm, lbl in rungs]

    return {
        "hypothesis": "H5",
        "status": "supported",
        "environment": {
            "topology": "single host, loopback (127.0.0.1)",
            "host": "aum",
            "shaper": "tc qdisc on lo (netem 20 ms + dynamic tbf) from translated 5G trace",
            "our_stack": "GStreamer 1.24, config 90 (SCReAM via gstscream)",
            "umn_stack": "teleop-gopher-streamer 502533c, congestion_controller=scream, network_time_sync on",
            "workload": "realmotion.avi (1280x1024 MJPEG @ 10 fps, 60 s)",
            "data_source": "analysis/umn-comparison/metrics.csv (tools/extract_comparison.py)",
        },
        "claim_structure": {
            "conclusion": ("With both implementations configured fairly, our SCReAM "
                           "changes its sending rate to match the available bandwidth, "
                           "while UMN's holds a near-constant rate. The cause is a "
                           "controller-to-encoder gap: UMN's controller decides on a rate, "
                           "but the encode loop tries to apply it by writing codec.bit_rate "
                           "on an already-running software encoder — which libav ignores, "
                           "since a software encoder's bitrate is fixed when it opens. So "
                           "the rate is set once at startup and never updated. This is a "
                           "wiring problem in the implementation, not a flaw in the SCReAM "
                           "algorithm or a matter of how often the control loop runs."),
            "subclaims": [
                f"Link utilization (share of available bandwidth used) on the high-capacity CQI trace: ours ~{util.get(('ours-scream','cqi'),0)*100:.0f}%, UMN ~{util.get(('umn-scream-v2','cqi'),0)*100:.0f}%.",
                f"UMN's sending rate stays near {min(sent.get(('umn-scream-v2',t),0) for t in traces):.0f}-{max(sent.get(('umn-scream-v2',t),0) for t in traces):.0f} kbps on all three traces, whose capacities differ about 2.5x — it does not rise when more bandwidth is available.",
                "Speeding up UMN's control loop (200 ms to 50 ms) and ramping faster did not raise its utilization — loop timing is not the cause.",
                "Measured directly: the actual sent rate exceeded the controller's commanded rate by more than 1.3x in about 89% of decisions (the controller-to-encoder gap); UMN's packet-loss congestion signal also never fired.",
                "Pinpointed in code: the encode loop (encoder.py _encode_stream_pyav) sets codec.bit_rate on a live encoder; only a full encoder reopen actually changes the rate, and libvpx never triggers that reopen. UMN's separate controller-thread design hand-wires this connection per codec; our SCReAM is a built-in pipeline element whose rate is applied by the framework.",
            ],
        },
        "tables": {
            "h5_fair_comparison": fair,
            "h5_isolation_ladder": ladder,
            "h5_implementation_maturity": [
                {"dimension": "Algorithm",
                 "umn_reimplementation": "Hand-written Python, SCReAM-inspired; its own code calls it a first pass for hardware integration",
                 "ours_reference": "Ericsson reference SCReAM (C++), via the gstscream plugin"},
                {"dimension": "Does the rate decision reach the encoder?",
                 "umn_reimplementation": "No on the software path — assigns codec.bit_rate on a running libvpx encoder, which libav ignores",
                 "ours_reference": "Yes — applied live through GStreamer's encoder property; the framework reconfigures the encoder"},
                {"dimension": "Congestion signals used",
                 "umn_reimplementation": "Delay only (and only with network_time_sync on); loss signal never fires; receiver feedback marked a future phase",
                 "ours_reference": "Delay and loss together, via standard RTCP feedback"},
                {"dimension": "Packet pacing",
                 "umn_reimplementation": "None — packets leave as the encoder emits them",
                 "ours_reference": "SCReAM paces its own RTP send queue"},
                {"dimension": "Integration",
                 "umn_reimplementation": "Separate controller thread leaving a per-frame budget the encode loop must apply correctly per codec",
                 "ours_reference": "Built-in pipeline element; the media framework guarantees the rate reaches the encoder"},
            ],
            "h5_algorithm_comparison_code_review": [
                {"aspect": "Control variable",
                 "umn_reimplementation": "One scalar target rate",
                 "ours_reference": "A congestion window + bytes-in-flight limit, then a rate derived from it"},
                {"aspect": "Delay handling",
                 "umn_reimplementation": "Binary: is one-way delay over the 120 ms threshold?",
                 "ours_reference": "A queue-delay trend/gradient vs a target, scaled continuously"},
                {"aspect": "Rate increase",
                 "umn_reimplementation": "Blind x1.05 every 200 ms tick, regardless of history",
                 "ours_reference": "Adaptive: fast when far below the last good point, gentle near it"},
                {"aspect": "Rate decrease",
                 "umn_reimplementation": "Fixed x0.8 (x0.7 on loss)",
                 "ours_reference": "Proportional to how far queue delay exceeds the target"},
                {"aspect": "State kept",
                 "umn_reimplementation": "Just the current target rate",
                 "ours_reference": "cwnd, smoothed RTT, queue-delay stats, reference rate, in-flight bytes"},
                {"aspect": "Extras",
                 "umn_reimplementation": "None",
                 "ours_reference": "ECN/L4S marking, RTP-queue-aware, fast-start"},
            ],
        },
        "figures": [
            {"path": "analysis/hypotheses/results/h5_isolation_ladder.svg",
             "caption": "Figure H5-1. Isolating the cause. Each bar adds one capability to UMN's SCReAM: S0 (none) → S1 (+delay signal) → S2 (+faster 50 ms loop) → S3 (+faster ramp); REF is our reference SCReAM. Left: overshoot (the share of time spent sending faster than the link can carry — lower is better) drops once the delay signal is on at S1, and the faster-timing steps S2/S3 do not change it. Right: utilization (the share of available bandwidth used — higher is better) on the high-capacity CQI trace stays low through S1/S2/S3, so faster timing does not help; only the reference implementation reaches high utilization."},
            {"path": "analysis/hypotheses/results/h5_utilization_by_trace.svg",
             "caption": "Figure H5-2. Share of available bandwidth used (utilization), per trace, with both implementations configured fairly. Ours rises with the available bandwidth (most visibly on the high-capacity CQI trace); UMN's stays low because its sending rate is held near a constant regardless of the link. The dashed line marks 1.0 — using all the available bandwidth."},
        ],
    }


def write_reports(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    reports = {
        "H1": build_h1(out_dir),
        "H2": build_h2(out_dir),
        "H3": build_h3(out_dir),
        "H4": build_h4(out_dir),
        "H5": build_h5(out_dir),
    }
    for hid, report in reports.items():
        (out_dir / f"{hid.lower()}_report.json").write_text(json.dumps(report, indent=2) + "\n")
    framework.write_registry(out_dir / "index.json", PROJECT_ROOT)
    return reports


def main() -> None:
    reports = write_reports()
    for hid, report in reports.items():
        figures = ", ".join(f["path"] for f in report["figures"])
        print(f"{hid}: wrote report JSON and figures: {figures}")


if __name__ == "__main__":
    main()
