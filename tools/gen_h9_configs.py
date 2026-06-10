"""Generate the H9 RTT-boundary sweep configurations.

H6/H7/H8 found SCReAM's tuning knobs do not move picture quality for this
workload. H9 opens the boundary axis (§7.8): where does SCReAM's delay-based
control degenerate? SCReAM is a delay-based controller, so base path delay is
the most controller-specific stressor — higher delay means slower feedback and
more data in flight before the loop reacts.

Sweeps the one-way netem delay {20, 50, 100, 200, 400, 800} ms on a fixed
5 Mbps link (no loss, so the codec is not confounded), SCReAM at the loose
4000 kbps ceiling streaming realmotion-avi. Single arm: the degeneration point
is the delay at which SCReAM's own decoded quality / latency cliffs.

  config ids 301-306 (id_base 301; one per delay level).
"""
from __future__ import annotations
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "specs" / "configurations"

DELAY_MS = [20, 50, 100, 200, 400, 800]
ID_BASE = 301
CEILING = {"init": 1500, "min": 200, "max": 4000}   # loose, same as H6/H8
METRICS = [
    "frame_count", "encoder_target_kbps", "wire_bytes", "encoded_bitrate",
    "frame_latency", "decoder_errors", "decoded_psnr",
]


def build(delay_ms: int) -> dict:
    return {
        "meta": {
            "name": f"h9_rtt_{delay_ms}ms",
            "description": (
                f"H9 cell — base delay {delay_ms} ms on fixed 5 Mbps, VP8 + SCReAM "
                f"loose {CEILING['max']} kbps ceiling, streaming realmotion-avi. "
                "RTT-boundary sweep."
            ),
            "flags": ["scream"],
        },
        "video": "realmotion-avi",
        "network": f"fixed-5mbps-{delay_ms}ms",
        "scenario": {
            "actors": {"camera": {}, "viewer": {}},
            "setup_delay_seconds": 3.0,
            "drain_delay_seconds": 1.0,
            "metrics": METRICS,
        },
        "codec": "vp8",
        "encoder": {"bitrate_kbps": CEILING["init"], "keyframe_interval_frames": 60},
        "congestion_control": {
            "algorithm": "scream",
            "init_bitrate_kbps": CEILING["init"],
            "min_bitrate_kbps": CEILING["min"],
            "max_bitrate_kbps": CEILING["max"],
        },
        "sink": {"backend": "fake", "sync": False},
        "recovery": {"nack": False, "pli": False, "fec": False},
        "latency_budget_ms": 0,
    }


def cell_mapping() -> list[tuple[int, int]]:
    return [(ID_BASE + i, d) for i, d in enumerate(DELAY_MS)]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for cid, d in cell_mapping():
        (OUT_DIR / f"{cid}.yaml").write_text(yaml.safe_dump(build(d), sort_keys=False))
    print(f"wrote {len(DELAY_MS)} H9 configs ({ID_BASE}-{ID_BASE + len(DELAY_MS) - 1})")
    for cid, d in cell_mapping():
        print(f"  {cid}: delay={d}ms")


if __name__ == "__main__":
    main()
