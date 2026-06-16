"""One-shot migration: fold every single-stream configuration into the
multi-stream schema (a 1-element `streams:` list + a `sync:` block).

The single-stream top-level fields (video|source, codec, encoder,
congestion_control?, sink, recovery, latency_budget_ms, network?) re-home
under streams[0]; `meta` and `scenario` stay top-level; a default
`sync: {mode: shared_epoch, termination: all}` is added. Inline top-level
`hooks` (a couple of configs) are preserved as-is (the stream then carries
no `network:` ref, so resolve_includes won't try to also compile one).

No backward compatibility — this rewrites the corpus in place. Run once:

    python3 tools/migrate_to_streams.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "specs" / "configurations"

# Per-stream payload keys, in the order they should appear under streams[0].
_STREAM_ORDER = ["name", "priority", "video", "source", "codec", "encoder",
                 "congestion_control", "sink", "recovery", "latency_budget_ms",
                 "network"]


def migrate(doc: dict) -> dict:
    if "streams" in doc:
        return doc   # already migrated — idempotent

    stream: dict = {"name": "main", "priority": 0}
    for key in _STREAM_ORDER:
        if key in ("name", "priority"):
            continue
        if key in doc:
            stream[key] = doc[key]

    out: dict = {"meta": doc["meta"], "scenario": doc["scenario"]}
    out["sync"] = {"mode": "shared_epoch", "termination": "all"}
    out["streams"] = [stream]
    if "hooks" in doc:                       # preserve inline hooks top-level
        out["hooks"] = doc["hooks"]
    return out


def main() -> int:
    files = sorted(CONFIG_DIR.glob("*.yaml"))
    migrated = 0
    for f in files:
        doc = yaml.safe_load(f.read_text()) or {}
        new = migrate(doc)
        if new is doc:
            continue
        f.write_text(yaml.safe_dump(new, sort_keys=False, allow_unicode=True))
        migrated += 1
    print(f"migrated {migrated}/{len(files)} configs to streams[]+sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
