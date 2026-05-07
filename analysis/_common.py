"""Shared helpers for the dimension modules.

Each dimension module reads the same kind of inputs (run summary,
viewer/camera result files, the network spec for phase boundaries) and
emits the same structural shape (dict with `dimension`, `findings`,
optional `per_phase` block). These helpers keep that boilerplate in
one place so dimension modules stay short and focused.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---- run / experiment input resolution ------------------------------------

@dataclass
class RunFiles:
    """Pointer bundle for one run's on-disk artifacts.

    config_id is None when the run dir doesn't conform to runs/<cid>/<ts>/
    (e.g. local one-off runs). Phase analysis is skipped in that case."""
    run_dir:        Path
    summary_path:   Path
    camera_path:    Path
    viewer_path:    Path
    config_id:      str | None


def resolve_run(arg: str) -> RunFiles:
    """Accept either a config id (most-recent run) or a full path."""
    p = Path(arg)
    if p.is_dir():
        run_dir = p
    else:
        cdir = PROJECT_ROOT / "runs" / arg
        if cdir.is_dir():
            runs = sorted(cdir.iterdir(), key=lambda x: x.name)
            if not runs:
                raise FileNotFoundError(f"no runs under {cdir}")
            run_dir = runs[-1]
        else:
            raise FileNotFoundError(f"can't resolve run input: {arg}")

    config_id = None
    if run_dir.parent.parent.name == "runs":
        config_id = run_dir.parent.name

    return RunFiles(
        run_dir=run_dir,
        summary_path=run_dir / "summary.json",
        camera_path=run_dir / "camera.json",
        viewer_path=run_dir / "viewer.json",
        config_id=config_id,
    )


def resolve_experiment(arg: str) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    p = PROJECT_ROOT / "runs" / "experiments" / f"{arg}.json"
    if p.is_file():
        return p
    raise FileNotFoundError(f"can't resolve experiment record: {arg}")


def load_json(p: Path) -> dict:
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def load_camera_steps_for_config(config_id: str) -> list[dict] | None:
    """Read a configuration's referenced network spec, return its
    camera_steps. Returns None when the config doesn't reference a
    network (loopback) or when paths don't resolve.

    The phase-aware reports use these steps to bin samples by network
    regime; without them we still produce overall summaries, but the
    per-phase columns are dropped."""
    cfg_path = PROJECT_ROOT / "specs" / "configurations" / f"{config_id}.yaml"
    if not cfg_path.is_file():
        return None
    try:
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        return None
    network_ref = cfg.get("network")
    if not network_ref:
        return None
    net_path = PROJECT_ROOT / "specs" / "networks" / f"{network_ref}.yaml"
    if not net_path.is_file():
        return None
    try:
        net = yaml.safe_load(net_path.read_text()) or {}
    except Exception:
        return None
    steps = net.get("camera_steps") or []
    if not steps:
        return None
    return steps


def phase_boundaries_ns(steps: list[dict]) -> list[tuple[int, int, str]]:
    """Convert camera_steps into [(start_ns, end_ns, label), ...] tuples
    keyed off the run's t=0. duration=0 means "holds for the whole run"
    — we cap at 24 hours so binning never excludes a sample."""
    out = []
    cum_ns = 0
    for s in steps:
        dur = int(s.get("duration") or 0)
        end_ns = cum_ns + (dur * 10**9 if dur > 0 else 24 * 3600 * 10**9)
        label = s.get("label") or f"step{len(out)+1}"
        out.append((cum_ns, end_ns, label))
        cum_ns = end_ns
    return out


# ---- statistics helpers ---------------------------------------------------

def msd(xs: list, fmt: str = "{:.1f}") -> str:
    if not xs:
        return "—"
    if len(xs) == 1:
        return fmt.format(xs[0])
    return f"{fmt.format(statistics.mean(xs))} ± {fmt.format(statistics.stdev(xs))}"


def percentile(sorted_xs: list, p: float) -> float:
    if not sorted_xs:
        return float("nan")
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    k = (len(sorted_xs) - 1) * (p / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


def fmt(value, kind: str = "float", precision: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and math.isnan(value):
        return "—"
    if kind == "int":
        return f"{int(value)}"
    if kind == "pct":
        return f"{value:.{precision}f}%"
    return f"{value:.{precision}f}"
