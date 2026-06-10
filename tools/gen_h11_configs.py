"""Generate the H11 jitter-boundary sweep configurations.

Third and final §7.8 boundary axis (H9 delay, H10 loss). On a fixed 5 Mbps /
20 ms link, sweep netem jitter {0, 5, 10, 20, 50, 100} ms (delay 20 ms +/- J),
no loss, recovery off. Jitter reorders and delays packets: within the receiver
jitter buffer they are reabsorbed (latency cost); beyond it they arrive late
and are dropped (delivery/quality cost). SCReAM is also delay-based, so jitter
may perturb its delay estimate.

Single arm: the boundary is the jitter level at which SCReAM's delivered video
degrades. 0 ms jitter reuses the existing fixed-5mbps-20ms network.

  config ids 313-318 (id_base 313; one per jitter level).
"""
from __future__ import annotations
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "specs" / "configurations"

# (jitter_ms, network_name)
LEVELS = [
    (0, "fixed-5mbps-20ms"),
    (5, "fixed-5mbps-20ms-jitter5ms"),
    (10, "fixed-5mbps-20ms-jitter10ms"),
    (20, "fixed-5mbps-20ms-jitter20ms"),
    (50, "fixed-5mbps-20ms-jitter50ms"),
    (100, "fixed-5mbps-20ms-jitter100ms"),
]
ID_BASE = 313
CEILING = {"init": 1500, "min": 200, "max": 4000}
METRICS = [
    "frame_count", "encoder_target_kbps", "wire_bytes", "encoded_bitrate",
    "frame_latency", "decoder_errors", "decoded_psnr",
]


def build(jitter_ms: int, network: str) -> dict:
    return {
        "meta": {
            "name": f"h11_jitter_{jitter_ms}ms",
            "description": (
                f"H11 cell — 20 ms +/- {jitter_ms} ms jitter on fixed 5 Mbps, VP8 + "
                f"SCReAM loose {CEILING['max']} kbps ceiling, realmotion-avi. "
                "Jitter-boundary sweep, recovery off."
            ),
            "flags": ["scream"],
        },
        "video": "realmotion-avi",
        "network": network,
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


def cell_mapping() -> list[tuple[int, int, str]]:
    return [(ID_BASE + i, lvl[0], lvl[1]) for i, lvl in enumerate(LEVELS)]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for cid, j, net in cell_mapping():
        (OUT_DIR / f"{cid}.yaml").write_text(yaml.safe_dump(build(j, net), sort_keys=False))
    print(f"wrote {len(LEVELS)} H11 configs ({ID_BASE}-{ID_BASE + len(LEVELS) - 1})")
    for cid, j, net in cell_mapping():
        print(f"  {cid}: jitter={j}ms ({net})")


if __name__ == "__main__":
    main()
