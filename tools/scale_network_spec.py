#!/usr/bin/env python3
"""Scale a step-based network spec while preserving its time-series shape."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _scale_steps(
    steps: list[dict],
    *,
    scale: float,
    min_rate_kbps: int,
) -> list[dict]:
    out: list[dict] = []
    for step in steps:
        scaled = dict(step)
        rate = step.get("rate_kbps")
        if not isinstance(rate, int):
            sys.exit("each step must have integer rate_kbps")
        scaled["rate_kbps"] = max(min_rate_kbps, int(round(rate * scale)))
        out.append(scaled)
    return out


def _mean_rate(steps: list[dict]) -> int:
    duration = sum(float(s["duration"]) for s in steps)
    if duration <= 0:
        sys.exit("network spec has no positive-duration steps")
    return round(
        sum(float(s["duration"]) * int(s["rate_kbps"]) for s in steps)
        / duration
    )


def scale_spec(
    doc: dict,
    *,
    name: str,
    scale: float,
    min_rate_kbps: int,
) -> dict:
    if scale <= 0:
        sys.exit("--scale must be positive")
    if min_rate_kbps <= 0:
        sys.exit("--min-rate-kbps must be positive")
    if not isinstance(doc.get("camera_steps"), list):
        sys.exit("input spec must contain camera_steps")

    camera_steps = _scale_steps(
        doc["camera_steps"],
        scale=scale,
        min_rate_kbps=min_rate_kbps,
    )
    viewer_steps = _scale_steps(
        doc.get("viewer_steps", []),
        scale=scale,
        min_rate_kbps=min_rate_kbps,
    )
    original_mean = _mean_rate(doc["camera_steps"])
    scaled_mean = _mean_rate(camera_steps)
    source_name = doc.get("name", "unnamed")
    description = doc.get("description", "").rstrip()
    suffix = (
        f" Scaled from {source_name} by factor {scale:g}, preserving step "
        f"durations, delay, loss model, and labels. Original camera mean rate: "
        f"{original_mean} kbit/s. Scaled camera mean rate: {scaled_mean} kbit/s."
    )
    return {
        **doc,
        "name": name,
        "description": f"{description}{suffix}",
        "camera_steps": camera_steps,
        "viewer_steps": viewer_steps,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path, help="input specs/networks/*.yaml")
    ap.add_argument("--scale", type=float, required=True,
                    help="multiplicative rate factor")
    ap.add_argument("--name", required=True, help="output network spec name")
    ap.add_argument("--out", type=Path,
                    help="output YAML path; default specs/networks/<name>.yaml")
    ap.add_argument("--min-rate-kbps", type=int, default=1,
                    help="floor for scaled rates")
    args = ap.parse_args()

    doc = yaml.safe_load(args.spec.read_text())
    if not isinstance(doc, dict):
        sys.exit(f"{args.spec}: expected mapping")
    out_doc = scale_spec(
        doc,
        name=args.name,
        scale=args.scale,
        min_rate_kbps=args.min_rate_kbps,
    )
    out = args.out or PROJECT_ROOT / "specs" / "networks" / f"{args.name}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(out_doc, sort_keys=False, width=88))
    print(f"wrote {out} ({len(out_doc['camera_steps'])} camera steps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
