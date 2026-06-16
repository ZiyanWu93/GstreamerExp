"""Generate the H10 loss-boundary sweep configurations.

H9 found SCReAM's delay boundary (~200 ms one-way). H10 is the second of the
three §7.8 boundary axes: where does packet loss break SCReAM? On a fixed
5 Mbps link at 20 ms delay, sweep uniform iid loss {0, 0.5, 1, 2, 5, 10} %.
Recovery is off (nack/pli/fec false, matching H6-H9), so loss is unmasked:
a lost packet corrupts VP8 until the next keyframe (60 frames = 6 s here).

Single arm: the degeneration point is the loss rate at which SCReAM's own
decoded quality cliffs. 0% loss reuses the existing fixed-5mbps-20ms network.

  config ids 307-312 (id_base 307; one per loss level).
"""
from __future__ import annotations
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "specs" / "configurations"

# (loss_pct, network_name)
LEVELS = [
    (0.0, "fixed-5mbps-20ms"),
    (0.1, "fixed-5mbps-20ms-loss0p1pct"),
    (0.25, "fixed-5mbps-20ms-loss0p25pct"),
    (0.5, "fixed-5mbps-20ms-loss0p5pct"),
    (1.0, "fixed-5mbps-20ms-loss1pct"),
    (2.0, "fixed-5mbps-20ms-loss2pct"),
]
ID_BASE = 307
CEILING = {"init": 1500, "min": 200, "max": 4000}
METRICS = [
    "frame_count", "encoder_target_kbps", "wire_bytes", "encoded_bitrate",
    "frame_latency", "decoder_errors", "decoded_psnr",
]


def build(loss_pct: float, network: str) -> dict:
    return {
        "meta": {
            "name": f"h10_loss_{loss_pct}pct",
            "description": (
                f"H10 cell — {loss_pct}% uniform loss on fixed 5 Mbps / 20 ms, VP8 + "
                f"SCReAM loose {CEILING['max']} kbps ceiling, realmotion-avi. "
                "Loss-boundary sweep, recovery off."
            ),
            "flags": ["scream"],
        },
        "scenario": {
            "actors": {"camera": {}, "viewer": {}},
            "setup_delay_seconds": 3.0,
            "drain_delay_seconds": 1.0,
            "metrics": METRICS,
        },
        "sync": {"mode": "shared_epoch", "termination": "all"},
        "streams": [{
            "name": "main",
            "priority": 0,
            "video": "realmotion-avi",
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
            "network": network,
        }],
    }


def cell_mapping() -> list[tuple[int, float, str]]:
    return [(ID_BASE + i, lvl[0], lvl[1]) for i, lvl in enumerate(LEVELS)]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for cid, loss, net in cell_mapping():
        (OUT_DIR / f"{cid}.yaml").write_text(yaml.safe_dump(build(loss, net), sort_keys=False))
    print(f"wrote {len(LEVELS)} H10 configs ({ID_BASE}-{ID_BASE + len(LEVELS) - 1})")
    for cid, loss, net in cell_mapping():
        print(f"  {cid}: loss={loss}% ({net})")


if __name__ == "__main__":
    main()
