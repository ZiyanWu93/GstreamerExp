"""Generate the H12 loss sweep with recovery ON (the H10 follow-up).

H10 found that with recovery OFF the loss boundary is a cliff at zero: delivery
falls from 96% to 36% at just 0.1% loss. H12 re-runs the identical loss sweep
with NACK/RTX retransmission ON (nack + rtx_buffer_ms 500), changing ONLY the
recovery setting, so the contrast isolates exactly what loss recovery buys.

Same loss levels and networks as H10 {0, 0.1, 0.25, 0.5, 1, 2} %, fixed 5 Mbps /
20 ms, SCReAM loose 4000 kbps ceiling, realmotion-avi.

  config ids 319-324 (id_base 319; one per loss level).
"""
from __future__ import annotations
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "specs" / "configurations"

# Same (loss_pct, network) set as H10 — only recovery differs.
LEVELS = [
    (0.0, "fixed-5mbps-20ms"),
    (0.1, "fixed-5mbps-20ms-loss0p1pct"),
    (0.25, "fixed-5mbps-20ms-loss0p25pct"),
    (0.5, "fixed-5mbps-20ms-loss0p5pct"),
    (1.0, "fixed-5mbps-20ms-loss1pct"),
    (2.0, "fixed-5mbps-20ms-loss2pct"),
]
ID_BASE = 319
CEILING = {"init": 1500, "min": 200, "max": 4000}
RECOVERY = {"nack": True, "pli": True, "fec": False, "rtx_buffer_ms": 500}
METRICS = [
    "frame_count", "encoder_target_kbps", "wire_bytes", "encoded_bitrate",
    "frame_latency", "decoder_errors", "decoded_psnr",
]


def build(loss_pct: float, network: str) -> dict:
    return {
        "meta": {
            "name": f"h12_lossrec_{loss_pct}pct",
            "description": (
                f"H12 cell — {loss_pct}% uniform loss on fixed 5 Mbps / 20 ms, VP8 + "
                f"SCReAM loose {CEILING['max']} kbps ceiling, realmotion-avi, "
                "RECOVERY ON (NACK/RTX). Loss sweep, the H10 recovery-off twin."
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
            "recovery": dict(RECOVERY),
            # NACK needs a receiver budget to wait for retransmissions; 0 neuters
            # it (the buffer outputs frames before RTX can deliver). 100 ms matches
            # the proven nack-latency-budget config (config 32). Part of "recovery
            # on": a budget without NACK has nothing to wait for, so the H10
            # (recovery off, budget 0) contrast stays fair.
            "latency_budget_ms": 100,
            "network": network,
        }],
    }


def cell_mapping() -> list[tuple[int, float, str]]:
    return [(ID_BASE + i, lvl[0], lvl[1]) for i, lvl in enumerate(LEVELS)]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for cid, loss, net in cell_mapping():
        (OUT_DIR / f"{cid}.yaml").write_text(yaml.safe_dump(build(loss, net), sort_keys=False))
    print(f"wrote {len(LEVELS)} H12 configs ({ID_BASE}-{ID_BASE + len(LEVELS) - 1})")
    for cid, loss, net in cell_mapping():
        print(f"  {cid}: loss={loss}% recovery=NACK/RTX ({net})")


if __name__ == "__main__":
    main()
