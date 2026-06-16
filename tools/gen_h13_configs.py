"""Generate the H13 keyframe-interval sweep at a fixed 1% packet loss.

The loss-collapse diagnostic concluded the H10/H12 "delivery cliff" is a
receiver-side VP8 reference-chain desync: one early loss-induced gap breaks
inter-frame prediction and, with a 60-frame keyframe interval (GOP), the
stream cannot re-anchor before the next loss re-breaks it. The falsifiable
prediction: shorten the GOP and the cliff should SOFTEN — more frequent
keyframes bound the desync window, so delivery should rise as the keyframe
interval shrinks. If delivery stays collapsed regardless of GOP, the GOP is
not the cause and the diagnostic is wrong.

Hold everything fixed at H10's 1% recovery-off cell and vary ONLY
encoder.keyframe_interval_frames over {60, 30, 15, 8, 4, 2} (default 60).
fixed 5 Mbps / 20 ms / 1% uniform loss, SCReAM loose 4000 kbps, realmotion-avi.

  config ids 325-330 (id_base 325; one per keyframe interval).
"""
from __future__ import annotations
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "specs" / "configurations"

KEYFRAMES = [60, 30, 15, 8, 4, 2]
ID_BASE = 325
NETWORK = "fixed-5mbps-20ms-loss1pct"   # H10's 1% loss cell (recovery off)
CEILING = {"init": 1500, "min": 200, "max": 4000}
METRICS = [
    "frame_count", "encoder_target_kbps", "wire_bytes", "encoded_bitrate",
    "frame_latency", "decoder_errors", "decoded_psnr",
]


def build(kf: int) -> dict:
    return {
        "meta": {
            "name": f"h13_keyframe_{kf}",
            "description": (
                f"H13 cell — keyframe interval {kf} frames at 1% loss on fixed 5 Mbps "
                f"/ 20 ms, VP8 + SCReAM loose {CEILING['max']} kbps, realmotion-avi, "
                "recovery off. Tests whether a shorter GOP softens the loss cliff."
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
            "encoder": {"bitrate_kbps": CEILING["init"], "keyframe_interval_frames": kf},
            "congestion_control": {
                "algorithm": "scream",
                "init_bitrate_kbps": CEILING["init"],
                "min_bitrate_kbps": CEILING["min"],
                "max_bitrate_kbps": CEILING["max"],
            },
            "sink": {"backend": "fake", "sync": False},
            "recovery": {"nack": False, "pli": False, "fec": False},
            "latency_budget_ms": 0,
            "network": NETWORK,
        }],
    }


def cell_mapping() -> list[tuple[int, int]]:
    return [(ID_BASE + i, kf) for i, kf in enumerate(KEYFRAMES)]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for cid, kf in cell_mapping():
        (OUT_DIR / f"{cid}.yaml").write_text(yaml.safe_dump(build(kf), sort_keys=False))
    print(f"wrote {len(KEYFRAMES)} H13 configs ({ID_BASE}-{ID_BASE + len(KEYFRAMES) - 1})")
    for cid, kf in cell_mapping():
        print(f"  {cid}: keyframe_interval={kf} frames")


if __name__ == "__main__":
    main()
