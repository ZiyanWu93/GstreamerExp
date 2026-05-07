#!/usr/bin/env python3
"""Spec-driven expo runner — visual demonstration of one configuration.

Usage:
    python3 expo.py <name|path>     # run an expo
    python3 expo.py --list          # list available expos

An expo spec lives at `specs/expos/<name>.yaml` and declares:

    name           : (required) identity
    description    : (optional) free text
    configuration  : (required) which configuration id to run
    display        : (required) X display string (e.g. ":0", ":20")
    xauthority     : (optional) path to the X authority file

The expo runner asks cli.py's run_configuration() to add a tee +
autovideosink branch to the viewer pipeline, with DISPLAY (and
optionally XAUTHORITY) set in the viewer worker's environment.
The measurement side is unchanged — running an expo also produces
runs/<id>/<ts>/summary.json, identical to a plain `cli.py <id>` run.

experiment.py (multi-config statistical comparisons) does not have
an --expo equivalent: experiments are headless by construction.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from cli import run_configuration


PROJECT_ROOT = Path(__file__).resolve().parent
EXPOS_DIR = PROJECT_ROOT / "specs" / "expos"
CONFIGURATIONS_DIR = PROJECT_ROOT / "specs" / "configurations"


_SPEC_REQUIRED = {"name", "configuration", "display"}
_SPEC_OPTIONAL = {"description", "xauthority"}
_SPEC_KEYS = _SPEC_REQUIRED | _SPEC_OPTIONAL


def _resolve_spec_path(arg: str) -> Path:
    p = Path(arg)
    if p.is_file() and p.suffix in (".yaml", ".yml"):
        return p.resolve()
    candidate = EXPOS_DIR / f"{arg}.yaml"
    if candidate.is_file():
        return candidate
    sys.exit(f"no expo spec found for {arg!r} "
             f"(expected {candidate} or a .yaml path)")


def _load_and_validate_spec(spec_path: Path) -> dict:
    where = spec_path.name
    spec = yaml.safe_load(spec_path.read_text()) or {}

    if not isinstance(spec, dict):
        sys.exit(f"{where}: top-level must be a mapping")
    missing = _SPEC_REQUIRED - set(spec)
    if missing:
        sys.exit(f"{where}: missing required key(s) {sorted(missing)}")
    unknown = set(spec) - _SPEC_KEYS
    if unknown:
        sys.exit(f"{where}: unknown key(s) {sorted(unknown)}; "
                 f"expected {sorted(_SPEC_KEYS)}")

    cid = str(spec["configuration"])
    cfg_path = CONFIGURATIONS_DIR / f"{cid}.yaml"
    if not cfg_path.is_file():
        sys.exit(f"{where}: configuration {cid!r} not found at {cfg_path}")
    spec["configuration"] = cid

    for k in ("display", "name"):
        if not isinstance(spec[k], str) or not spec[k]:
            sys.exit(f"{where}: {k} must be a non-empty string")
    if "xauthority" in spec and not isinstance(spec["xauthority"], str):
        sys.exit(f"{where}: xauthority must be a string")

    return spec


def _list_specs() -> int:
    rows = []
    for p in sorted(EXPOS_DIR.glob("*.yaml")):
        try:
            doc = yaml.safe_load(p.read_text()) or {}
        except Exception as e:
            rows.append((p.stem, "?", "?", f"(parse error: {e})"))
            continue
        rows.append((p.stem,
                     str(doc.get("configuration", "?")),
                     str(doc.get("display", "?")),
                     (doc.get("description", "") or "").splitlines()[0]
                       if doc.get("description") else ""))
    if not rows:
        print(f"no expo specs under {EXPOS_DIR}")
        return 1
    name_w = max(len("name"),    max(len(r[0]) for r in rows))
    cfg_w  = max(len("config"),  max(len(r[1]) for r in rows))
    disp_w = max(len("display"), max(len(r[2]) for r in rows))
    print(f"{'name':<{name_w}}  {'config':<{cfg_w}}  {'display':<{disp_w}}  description")
    print(f"{'-'*name_w}  {'-'*cfg_w}  {'-'*disp_w}  -----------")
    for n, c, d, desc in rows:
        print(f"{n:<{name_w}}  {c:<{cfg_w}}  {d:<{disp_w}}  {desc}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Spec-driven expo runner")
    parser.add_argument("spec", nargs="?",
                        help="Expo spec name (file stem under specs/expos/) "
                             "or a path to a .yaml")
    parser.add_argument("--list", action="store_true",
                        help="List available expo specs and exit")
    args = parser.parse_args()

    if args.list:
        sys.exit(_list_specs())
    if not args.spec:
        parser.error("spec required (or pass --list)")

    spec_path = _resolve_spec_path(args.spec)
    spec = _load_and_validate_spec(spec_path)

    print(f"[expo] {spec['name']}: configuration={spec['configuration']} "
          f"display={spec['display']}")
    sys.exit(run_configuration(
        spec["configuration"],
        view_display=spec["display"],
        view_xauthority=spec.get("xauthority"),
    ))


if __name__ == "__main__":
    main()
