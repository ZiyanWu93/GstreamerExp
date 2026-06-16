"""Generate the H6 and H7 queue-delay-target sweep configurations.

Both sweeps vary SCReAM's queue-delay-target over {10, 30, 60, 100, 200,
500} ms across three networks (static / fluct / 5g). They differ only in
the encoder's bitrate ceiling:

  H6 (config ids 101–118): loose ceiling, init/min/max = 1500/200/4000 kbps.
      The encoder has room to spare. (Finding: knob moves latency, not quality.)

  H7 (config ids 119–136): tight ceiling, init/min/max = 500/200/800 kbps.
      The encoder is bandwidth-starved, so loosening the knob actually gives
      VP8 more bytes to spend. Tests whether the knob becomes a quality knob
      once the encoder is hungry.

All other fields are fixed: realmotion-avi, VP8, recovery off, distributed
camera (aum) → viewer (veda) via hosts.yaml. Numeric ids are required
because per-run UDP ports are derived from them.
"""
from __future__ import annotations
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "specs" / "configurations"

QDT_MS = [10, 30, 60, 100, 200, 500]
REGIME_ORDER = ["static", "fluct", "5g"]
REGIMES = {
    "static": "fixed-5mbps-20ms",
    "fluct": "fluctuating-5mbps-300kbps",
    "5g": "mahimahi-5g-cqi-100ms-x0p33",
}
METRICS = [
    "frame_count",
    "encoder_target_kbps",
    "wire_bytes",
    "encoded_bitrate",
    "frame_latency",
    "decoder_errors",
    "decoded_psnr",
]

# Each tier: (hypothesis id, id_base, init_kbps, min_kbps, max_kbps).
TIERS = {
    "h6": {"id_base": 101, "init": 1500, "min": 200, "max": 4000},
    "h7": {"id_base": 119, "init": 500, "min": 200, "max": 800},
}


def build(tier: str, qdt_ms: int, regime_key: str, network_name: str) -> dict:
    cfg = TIERS[tier]
    return {
        "meta": {
            "name": f"{tier}_qdt_{qdt_ms}_{regime_key}",
            "description": (
                f"{tier.upper()} cell — queue-delay-target {qdt_ms} ms on {network_name}, "
                f"VP8 + SCReAM ceiling {cfg['max']} kbps, streaming realmotion-avi."
            ),
            "flags": ["scream"],
        },
        "scenario": {
            # Empty actor blocks defer to hosts.yaml (aum=camera, veda=viewer
            # over the wired LAN). tc shaping applies on the NIC named in
            # hosts.yaml.network_env.NIC.
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
            "encoder": {
                "bitrate_kbps": cfg["init"],
                "keyframe_interval_frames": 60,
            },
            "congestion_control": {
                "algorithm": "scream",
                "init_bitrate_kbps": cfg["init"],
                "min_bitrate_kbps": cfg["min"],
                "max_bitrate_kbps": cfg["max"],
                "scream": {"delay_target_seconds": qdt_ms / 1000.0},
            },
            "sink": {"backend": "fake", "sync": False},
            "recovery": {"nack": False, "pli": False, "fec": False},
            "latency_budget_ms": 0,
            "network": network_name,
        }],
    }


def cell_id(tier: str, qdt_ms: int, regime_key: str) -> int:
    return (
        TIERS[tier]["id_base"]
        + REGIME_ORDER.index(regime_key) * len(QDT_MS)
        + QDT_MS.index(qdt_ms)
    )


def cell_mapping(tier: str) -> list[tuple[int, int, str, str]]:
    out = []
    for regime_key in REGIME_ORDER:
        for qdt_ms in QDT_MS:
            out.append((cell_id(tier, qdt_ms, regime_key), qdt_ms, regime_key, REGIMES[regime_key]))
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for tier in TIERS:
        for cid, qdt_ms, regime_key, network_name in cell_mapping(tier):
            spec = build(tier, qdt_ms, regime_key, network_name)
            (OUT_DIR / f"{cid}.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
            total += 1
    print(f"wrote {total} configurations under {OUT_DIR}")
    for tier in TIERS:
        print(f"  {tier} ceiling={TIERS[tier]['max']}kbps:")
        for cid, qdt_ms, regime_key, _ in cell_mapping(tier):
            print(f"    {cid}: qdt={qdt_ms}ms {regime_key}")


if __name__ == "__main__":
    main()
