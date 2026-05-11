#!/usr/bin/env python3
"""Build the structured hypothesis registry JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import framework


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default="analysis/hypotheses/results/index.json",
        help="Output JSON path relative to project root.",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="Validate and print the registry without writing it.",
    )
    args = ap.parse_args()

    root = framework.PROJECT_ROOT
    if args.check:
        registry = framework.build_registry(root=root)
        print(json.dumps({
            "hypotheses": len(registry["hypotheses"]),
            "ids": [h["id"] for h in registry["hypotheses"]],
        }, indent=2))
        return

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = root / out_path
    registry = framework.write_registry(out_path, root=root)
    rel = out_path.relative_to(root)
    print(f"wrote {rel} with {len(registry['hypotheses'])} hypotheses")


if __name__ == "__main__":
    main()
