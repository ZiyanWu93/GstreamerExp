#!/usr/bin/env python3
"""Configuration runner — entry point for measurement runs.

Usage:
    python3 cli.py <id|path>     # run a configuration (measurement, headless)
    python3 cli.py --list        # list configurations

A configuration is `specs/configurations/<id>.yaml`. See the spec docs in
validation.py for what fields it carries. cli.py orchestrates: load + resolve
+ validate the configuration → project to per-role specs → run_local() or
run_distributed() → build_summary() → print_report().

Visual rendering is not part of cli.py — that's expo.py, which calls
run_configuration() with view_display set.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Optional

import yaml

from gstexp.validation import resolve_includes, validate_doc, project_to_roles
from gstexp.runner import run_distributed, run_local, scream_env
from gstexp.reporting import build_summary, print_report, read_result


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIGS_DIR = PROJECT_ROOT / "specs" / "configurations"


def _resolve_config(arg: str) -> tuple[str, Path]:
    """Map a CLI arg ('7' or 'specs/configurations/7.yaml') to (id, path)."""
    p = Path(arg)
    if p.is_file() and p.suffix in (".yaml", ".yml"):
        return p.stem, p.resolve()
    candidate = CONFIGS_DIR / f"{arg}.yaml"
    if candidate.is_file():
        return arg, candidate
    sys.exit(f"no configuration found for {arg!r} "
             f"(expected {candidate} or a .yaml path)")


def _list_configs() -> int:
    rows = []
    for p in sorted(CONFIGS_DIR.glob("*.yaml"),
                    key=lambda q: int(q.stem) if q.stem.isdigit() else 999):
        try:
            doc = yaml.safe_load(p.read_text())
        except Exception as e:
            rows.append((p.stem, "?", f"(parse error: {e})", ""))
            continue
        meta = doc.get("meta", {}) or {}
        flags = ",".join(meta.get("flags", []) or [])
        rows.append((p.stem, meta.get("name", ""),
                     meta.get("description", ""), flags))
    if not rows:
        print(f"no configurations under {CONFIGS_DIR}")
        return 1
    id_w = max(len("id"), max(len(r[0]) for r in rows))
    name_w = max(len("name"), max(len(r[1]) for r in rows))
    flag_w = max(len("flags"), max(len(r[3]) for r in rows))
    print(f"{'id':<{id_w}}  {'name':<{name_w}}  {'flags':<{flag_w}}  description")
    print(f"{'-'*id_w}  {'-'*name_w}  {'-'*flag_w}  -----------")
    for cid, name, desc, flags in rows:
        print(f"{cid:<{id_w}}  {name:<{name_w}}  {flags:<{flag_w}}  {desc}")
    return 0


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def run_configuration(config_arg: str, *,
                      view_display: Optional[str] = None,
                      view_xauthority: Optional[str] = None) -> int:
    """Run one configuration end-to-end and return its exit code.

    Default (cli.py): view_display=None — pure measurement, headless.
    Expo (expo.py):   view_display=":N" — adds a tee + autovideosink branch
                      to the viewer pipeline; sets DISPLAY (and optionally
                      XAUTHORITY) on the viewer worker's environment so
                      autovideosink can reach the X server.
    """
    config_id, config_path = _resolve_config(config_arg)
    doc = yaml.safe_load(config_path.read_text())
    doc = resolve_includes(doc, PROJECT_ROOT, config_path)
    validate_doc(doc, config_path)
    camera_dicts, viewer_dicts = project_to_roles(doc, config_id)
    n = len(camera_dicts)

    # Validation guarantees every required key is present — no .get
    # defaults below this line; missing keys would have failed earlier.
    meta = doc.get("meta") or {}
    scenario = doc["scenario"]
    hooks = doc.get("hooks") or {}
    streams_meta = [{"name": st["name"], "priority": st["priority"]}
                    for st in doc["streams"]]

    setup_delay = float(scenario["setup_delay_seconds"])
    drain_delay = float(scenario["drain_delay_seconds"])
    actors = scenario["actors"]
    camera_actor = actors["camera"]
    viewer_actor = actors["viewer"]
    camera_host = camera_actor["host"]
    viewer_host = viewer_actor["host"]
    camera_ssh_host = camera_actor.get("ssh_host") or camera_host
    viewer_ssh_host = viewer_actor.get("ssh_host") or viewer_host
    metrics_list = scenario["metrics"]
    wrap_camera = camera_actor.get("wrap", [])
    wrap_viewer = viewer_actor.get("wrap", [])
    pre_hooks = hooks.get("pre_run") or []
    during_hooks = hooks.get("during_run") or []
    post_hooks = hooks.get("post_run") or []

    # Controller/worker mode is gated on having a remote project_root for
    # both actors. In local mode (e.g. 127.0.0.1 hosts), project_root may
    # be absent because workers run as direct child processes from cwd.
    camera_project_root = camera_actor.get("project_root")
    viewer_project_root = viewer_actor.get("project_root")
    distributed = camera_project_root is not None and viewer_project_root is not None
    if distributed and camera_project_root != viewer_project_root:
        sys.exit(f"{config_path.name}: distributed mode requires identical "
                 f"project_root on both actors today; got camera="
                 f"{camera_project_root!r} viewer={viewer_project_root!r}")
    remote_project_root = camera_project_root if distributed else None

    run_dir = PROJECT_ROOT / "runs" / config_id / _timestamp()
    run_dir.mkdir(parents=True)

    metric_args = []
    for m in metrics_list:
        metric_args.extend(["--metric", m])
    # `-m gstexp.worker` puts the project root on sys.path automatically
    # (the directory containing the gstexp/ package, i.e. cwd) — no need
    # to pass the worker file path explicitly.
    base_cmd = [sys.executable, "-u", "-m", "gstexp.worker"] + metric_args
    child_env = scream_env(PROJECT_ROOT)

    # Per-stream effective specs + result paths. project_to_roles owns its
    # own host/port injection, so each dict is fully formed. The worker
    # sees ONE single-stream spec per process; results land in
    # camera_<i>.json / viewer_<i>.json so per-stream outputs stay separate.
    camera_specs, viewer_specs = [], []
    camera_results, viewer_results = [], []
    for i in range(n):
        cspec = run_dir / f"_camera_spec_{i}.json"
        vspec = run_dir / f"_viewer_spec_{i}.json"
        cspec.write_text(json.dumps(camera_dicts[i], indent=2))
        vspec.write_text(json.dumps(viewer_dicts[i], indent=2))
        camera_specs.append(cspec)
        viewer_specs.append(vspec)
        camera_results.append(run_dir / f"camera_{i}.json")
        viewer_results.append(run_dir / f"viewer_{i}.json")

    print(f"[scenario] config: {config_id} ({meta.get('name', '')})")
    print(f"[scenario] run:    {run_dir}")
    print(f"[scenario] streams: {n} "
          f"({', '.join(s['name'] for s in streams_meta)})")
    if view_display:
        print(f"[scenario] expo:   view_display={view_display}")

    if distributed:
        run_distributed(
            project_root=PROJECT_ROOT,
            camera_specs=camera_specs,
            viewer_specs=viewer_specs,
            run_dir=run_dir,
            camera_results=camera_results,
            viewer_results=viewer_results,
            camera_host=camera_ssh_host,
            viewer_host=viewer_ssh_host,
            remote_project_root=remote_project_root,
            view_display=view_display,
            view_xauthority=view_xauthority,
            setup_delay=setup_delay,
            drain_delay=drain_delay,
            pre_hooks=pre_hooks,
            during_hooks=during_hooks,
            post_hooks=post_hooks,
            metric_args=metric_args,
        )
    else:
        run_local(
            project_root=PROJECT_ROOT,
            camera_specs=camera_specs,
            viewer_specs=viewer_specs,
            camera_results=camera_results,
            viewer_results=viewer_results,
            base_cmd=base_cmd,
            child_env=child_env,
            wrap_camera=wrap_camera,
            wrap_viewer=wrap_viewer,
            setup_delay=setup_delay,
            drain_delay=drain_delay,
            pre_hooks=pre_hooks,
            during_hooks=during_hooks,
            post_hooks=post_hooks,
            view_display=view_display,
            view_xauthority=view_xauthority,
        )

    skew_file = run_dir / "_clock_skew.json"
    camera_skew = viewer_skew = 0.0
    if skew_file.exists():
        skew = json.loads(skew_file.read_text())
        camera_skew = skew.get("camera_skew_s", 0.0)
        viewer_skew = skew.get("viewer_skew_s", 0.0)

    camera_data = [read_result(p) for p in camera_results]
    viewer_data = [read_result(p) for p in viewer_results]
    summary = build_summary(streams_meta, camera_data, viewer_data,
                            camera_skew=camera_skew,
                            viewer_skew=viewer_skew)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print_report(f"{config_id} ({meta.get('name', '')})".strip(),
                 run_dir, summary)
    return 0 if summary["verdict"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description="Configuration runner")
    parser.add_argument("config", nargs="?",
                        help="Configuration id (e.g. '7') or path to a .yaml")
    parser.add_argument("--list", action="store_true",
                        help="List configurations and exit")
    args = parser.parse_args()

    if args.list:
        sys.exit(_list_configs())
    if not args.config:
        parser.error("config id required (or pass --list)")

    sys.exit(run_configuration(args.config))


if __name__ == "__main__":
    main()
