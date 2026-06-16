#!/usr/bin/env python3
"""Spec-driven experiment runner.

Usage:
    python3 experiment.py <spec>          # spec name (file stem) or path
    python3 experiment.py --list          # list available experiment specs

A spec is `specs/experiments/<name>.yaml` and defines:
    name           : record base name
    description    : free text
    reps           : repetitions per configuration
    varies         : configuration paths that intentionally differ
    configurations : list of {id, label, color}

The runner interleaves runs round-robin across the listed configurations
(reps × len(configurations) total runs) so cross-traffic noise distributes
evenly across the algorithms being compared. After each rep, the experiment record
at runs/experiments/<name>.json is rewritten so a partial experiment is
still analyzable.

Interleaving matters: cross-traffic on the LAN drifts over the ~minutes
of a multi-rep experiment, so back-to-back runs of the same configuration
see correlated noise. Round-robin distributes the noise.
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

import yaml

from gstexp.validation import resolve_includes, validate_doc


PROJECT_ROOT = Path(__file__).resolve().parent
RUNS_ROOT = PROJECT_ROOT / "runs"
EXPERIMENTS_DIR = PROJECT_ROOT / "specs" / "experiments"
CONFIGURATIONS_DIR = PROJECT_ROOT / "specs" / "configurations"

# Paths that may differ between configurations in the same experiment
# without affecting comparison validity. `meta.*` is documentation.
# `hooks.*` is DERIVED output: resolve_includes compiles each stream's
# `network:` ref into per-(peer, dport) tc lanes, so the compiled script
# embeds each config's stream ports and legitimately differs config to
# config. The authored difference that matters — the `network:` ref
# itself — is compared on the RAW (pre-resolve) doc instead (see below),
# so ignoring the compiled hooks loses no signal. Anything else that
# differs must be in the experiment spec's `varies` list.
_COMPARE_IGNORE = {"meta.name", "meta.description"}
_COMPARE_IGNORE_PREFIXES = ("meta.flags", "hooks")


def _resolve_spec_path(arg: str) -> Path:
    """Map a CLI arg ('scream-vs-gcc-720p' or a path) to a spec file."""
    p = Path(arg)
    if p.is_file() and p.suffix in (".yaml", ".yml"):
        return p.resolve()
    candidate = EXPERIMENTS_DIR / f"{arg}.yaml"
    if candidate.is_file():
        return candidate
    sys.exit(f"no experiment spec found for {arg!r} "
             f"(expected {candidate} or a .yaml path)")


def _flatten(value, prefix: str = "") -> dict:
    """Walk a nested dict/list, return {dotted.path: leaf_value}."""
    out: dict = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = value
    return out


def _path_in(path: str, allowed: set) -> bool:
    """A path matches an allow-set entry exactly or as a sub-path
    (so listing 'congestion_control' permits 'congestion_control.algorithm')."""
    return any(path == a or path.startswith(a + ".") or path.startswith(a + "[")
               for a in allowed)


def _load_and_validate_spec(spec_path: Path) -> dict:
    """Load the spec and assert structural integrity + comparison validity.

    Configurations listed in the experiment must agree on every field
    that affects what's being measured — only the paths in `varies` (plus
    a small built-in ignore list for documentation fields) are allowed
    to differ. Each listed configuration is fully resolved (video/network
    refs inlined) and validated, then the resolved docs are deep-compared.
    Anything that differs and isn't declared as varied fails the experiment
    at load time.
    """
    where = spec_path.name
    spec = yaml.safe_load(spec_path.read_text()) or {}

    required = {"name", "reps", "varies", "configurations"}
    missing = required - set(spec)
    if missing:
        sys.exit(f"{where}: missing required key(s) {sorted(missing)}")

    if not isinstance(spec["reps"], int) or spec["reps"] < 1:
        sys.exit(f"{where}: reps must be a positive integer")

    if not isinstance(spec["varies"], list) or not all(
            isinstance(v, str) for v in spec["varies"]):
        sys.exit(f"{where}: varies must be a list of dotted paths "
                 f"(e.g. ['congestion_control.algorithm'])")

    cfgs = spec["configurations"]
    if not isinstance(cfgs, list) or not cfgs:
        sys.exit(f"{where}: configurations must be a non-empty list")
    seen_ids = set()
    for i, c in enumerate(cfgs):
        if not isinstance(c, dict):
            sys.exit(f"{where}: configurations[{i}] must be a mapping")
        for k in ("id", "label", "color"):
            if k not in c:
                sys.exit(f"{where}: configurations[{i}] missing key {k!r}")
        cid = str(c["id"])
        c["id"] = cid                      # normalize to string for downstream
        if cid in seen_ids:
            sys.exit(f"{where}: configurations[{i}] duplicate id {cid!r}")
        seen_ids.add(cid)

    # Resolve+validate each configuration for correctness, but compare the
    # RAW (pre-resolve) authored docs: resolve_includes mutates the doc in
    # place (popping the per-stream `video:`/`network:` refs and compiling
    # port-bearing `hooks`), and the authored `streams[i].network` ref is
    # the difference that matters — not the compiled tc script. So deep-copy
    # the raw doc before resolving, then flatten the raw copies.
    raw_docs: list = []
    for c in cfgs:
        cid = c["id"]
        cfg_path = CONFIGURATIONS_DIR / f"{cid}.yaml"
        if not cfg_path.is_file():
            sys.exit(f"{where}: configuration {cid!r} not found at {cfg_path}")
        doc = yaml.safe_load(cfg_path.read_text()) or {}
        raw_docs.append(copy.deepcopy(doc))
        doc = resolve_includes(doc, PROJECT_ROOT, cfg_path)
        validate_doc(doc, cfg_path)

    varies = set(spec["varies"])
    flats = [_flatten(d) for d in raw_docs]
    all_paths = set().union(*flats)
    diffs: list = []
    for path in sorted(all_paths):
        if path in _COMPARE_IGNORE:
            continue
        if any(path.startswith(p + ".") or path == p for p in _COMPARE_IGNORE_PREFIXES):
            continue
        if any(path.startswith(p + "[") for p in _COMPARE_IGNORE_PREFIXES):
            continue
        values = [f.get(path) for f in flats]
        if any(v != values[0] for v in values[1:]):
            if not _path_in(path, varies):
                diffs.append((path, values))

    if diffs:
        ids = [c["id"] for c in cfgs]
        details = "\n".join(
            f"    {path}: " + ", ".join(f"{cid}={v!r}" for cid, v in zip(ids, vals))
            for path, vals in diffs
        )
        sys.exit(f"{where}: configurations differ on paths not declared in `varies`:\n"
                 f"{details}\n"
                 f"  add the path to `varies` (if intentional) or change the "
                 f"configurations to agree.")

    return spec


def _runs_for_config(config_id: str) -> set[str]:
    d = RUNS_ROOT / config_id
    if not d.is_dir():
        return set()
    return {p.name for p in d.iterdir() if p.is_dir()}


def _new_run_for_config(config_id: str, before: set[str]) -> str | None:
    """Find the run timestamp added since `before`."""
    after = _runs_for_config(config_id)
    new = sorted(after - before)
    if not new:
        return None
    return new[-1]


def _network_record_for_config(config_id: str) -> dict | None:
    """Return the directional network spec copied into experiment records."""
    cfg_path = CONFIGURATIONS_DIR / f"{config_id}.yaml"
    if not cfg_path.is_file():
        return None
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    # The network ref is now per-stream; experiment records are built from
    # single-stream experiment configs, so read stream 0's ref.
    streams = cfg.get("streams") or []
    net_ref = streams[0].get("network") if streams else None
    if not net_ref:
        return None
    net_path = PROJECT_ROOT / "specs" / "networks" / f"{net_ref}.yaml"
    if not net_path.is_file():
        return None
    net_spec = yaml.safe_load(net_path.read_text()) or {}
    return {
        "name": str(net_ref),
        "spec_path": str(net_path.relative_to(PROJECT_ROOT)),
        "camera_steps": net_spec.get("camera_steps") or [],
        "viewer_steps": net_spec.get("viewer_steps") or [],
    }


def _list_specs() -> int:
    rows = []
    for p in sorted(EXPERIMENTS_DIR.glob("*.yaml")):
        try:
            doc = yaml.safe_load(p.read_text()) or {}
        except Exception as e:
            rows.append((p.stem, f"(parse error: {e})", "?", ""))
            continue
        ids = ",".join(str(c.get("id", "?")) for c in doc.get("configurations", []))
        rows.append((p.stem, doc.get("name", ""), str(doc.get("reps", "?")),
                     ids, (doc.get("description", "") or "").splitlines()[0]
                          if doc.get("description") else ""))
    if not rows:
        print(f"no experiment specs under {EXPERIMENTS_DIR}")
        return 1
    name_w = max(len("name"), max(len(r[0]) for r in rows))
    reps_w = max(len("reps"), max(len(r[2]) for r in rows))
    cfgs_w = max(len("configs"), max(len(r[3]) for r in rows))
    print(f"{'name':<{name_w}}  {'reps':<{reps_w}}  {'configs':<{cfgs_w}}  description")
    print(f"{'-'*name_w}  {'-'*reps_w}  {'-'*cfgs_w}  -----------")
    for stem, _name, reps, ids, desc in rows:
        print(f"{stem:<{name_w}}  {reps:<{reps_w}}  {ids:<{cfgs_w}}  {desc}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Spec-driven experiment runner")
    parser.add_argument("spec", nargs="?",
                        help="Experiment spec name (file stem under experiments/) "
                             "or a path to a .yaml")
    parser.add_argument("--list", action="store_true",
                        help="List available experiment specs and exit")
    parser.add_argument("--resume", action="store_true",
                        help="Skip (config, rep) cells already completed with "
                             "exit_code 0 in the existing record, and append new "
                             "runs to it. Safe to re-run after a kill/failure.")
    args = parser.parse_args()

    if args.list:
        sys.exit(_list_specs())
    if not args.spec:
        parser.error("spec required (or pass --list)")

    spec_path = _resolve_spec_path(args.spec)
    spec = _load_and_validate_spec(spec_path)

    name = spec["name"]
    reps = spec["reps"]
    configurations = spec["configurations"]
    config_ids = [c["id"] for c in configurations]

    # Copy the directional network ref into the record so analysis
    # tools can read the self-contained artifact instead of re-opening
    # the specs. The validator guarantees the network is shared unless
    # the experiment explicitly declares otherwise.
    network_record = _network_record_for_config(config_ids[0])

    expt_dir = RUNS_ROOT / "experiments"
    expt_dir.mkdir(parents=True, exist_ok=True)
    record_path = expt_dir / f"{name}.json"

    runs: list[dict] = []
    done_cells: set[tuple] = set()
    started_at = _dt.datetime.now().isoformat()

    # Resume: load completed (config, rep) cells from the existing record so a
    # killed/failed sweep continues instead of redoing finished work. Only
    # exit_code 0 cells count as done; failed/partial cells are re-run.
    if args.resume and record_path.is_file():
        try:
            prior = json.loads(record_path.read_text())
            for r in prior.get("runs", []):
                if r.get("exit_code") == 0 and r.get("run_id"):
                    runs.append(r)
                    done_cells.add((str(r["config"]), int(r["rep"])))
            started_at = prior.get("started_at", started_at)
            print(f"[experiment] resume: {len(done_cells)} completed cells "
                  f"loaded from {record_path}")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"[experiment] resume: could not read prior record ({e}); "
                  f"starting fresh")

    print(f"[experiment] {name}: configs={config_ids} reps={reps}")
    print(f"[experiment] record will land at {record_path}")

    total = reps * len(config_ids)
    n = 0
    for rep in range(reps):
        for cid in config_ids:
            n += 1
            if (str(cid), rep) in done_cells:
                print(f"\n[experiment] rep={rep} config={cid} ({n}/{total}) — "
                      f"already done, skipping", flush=True)
                continue
            before = _runs_for_config(cid)
            print(f"\n[experiment] rep={rep} config={cid} ({n}/{total})", flush=True)
            r = subprocess.run(
                ["python3", str(PROJECT_ROOT / "cli.py"), cid],
                cwd=str(PROJECT_ROOT),
            )
            run_id = _new_run_for_config(cid, before)
            runs.append({
                "config":     cid,
                "rep":        rep,
                "run_id":     run_id,
                "exit_code":  r.returncode,
            })
            # Persist after every run so a partial experiment is still
            # analyzable. Configurations + the directional network spec
            # are copied here so plot.py and analyze.py don't need to
            # re-read the specs.
            record_path.write_text(json.dumps({
                "name":           name,
                "spec_path":      str(spec_path.relative_to(PROJECT_ROOT)),
                "configurations": configurations,
                "network":        network_record,
                "reps":           reps,
                "started_at":     started_at,
                "runs":           runs,
            }, indent=2))

    print(f"\n[experiment] done. record: {record_path}")
    fails = [r for r in runs if r["exit_code"] != 0]
    if fails:
        print(f"[experiment] {len(fails)}/{total} runs FAILED")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
