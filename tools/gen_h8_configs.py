"""Generate the H8 mul_increase (ramp-aggressiveness) sweep configurations.

H6/H7 closed the queue-delay-target knob: it moves latency, not quality, and
on the capacity-step link a looser target slightly HURT quality (data buffered
through the 300 kbps trough arrives stale). H8 asks the follow-up: does a more
aggressive multiplicative-increase ramp recover picture quality faster after
capacity returns?

Sweeps SCReAM's mul_increase over {0.02, 0.05, 0.1, 0.2, 0.3, 0.5} across the
same three networks (static / fluct / 5g), holding the queue-delay-target at
its 0.06 s default and the bitrate ceiling LOOSE (init/min/max = 1500/200/4000
kbps) so the encoder has room to ramp when capacity returns.

  config ids 201-218 (id_base 201; regime order [static, fluct, 5g];
  mul order [0.02, 0.05, 0.1, 0.2, 0.3, 0.5]).

The fixed-5 Mbps link is the control: with no capacity change, ramp speed
should not matter, so PSNR there should stay flat. The capacity-step link is
the treatment, where a faster ramp should recover quality sooner.
"""
from __future__ import annotations
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "specs" / "configurations"

MUL_INCREASE = [0.02, 0.05, 0.1, 0.2, 0.3, 0.5]   # default is 0.05
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
ID_BASE = 201   # 201-218: a clean block clear of H6/H7 (101-136) and the
                # psnr-sanity outlier at 140.
CEILING = {"init": 1500, "min": 200, "max": 4000}   # loose, room to ramp
DELAY_TARGET_S = 0.06                                # SCReAM default, held fixed


def build(mul: float, regime_key: str, network_name: str) -> dict:
    return {
        "meta": {
            "name": f"h8_mulinc_{mul}_{regime_key}",
            "description": (
                f"H8 cell — mul_increase {mul} on {network_name}, VP8 + SCReAM "
                f"loose {CEILING['max']} kbps ceiling, queue-delay-target "
                f"{DELAY_TARGET_S}s, streaming realmotion-avi."
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
            "encoder": {
                "bitrate_kbps": CEILING["init"],
                "keyframe_interval_frames": 60,
            },
            "congestion_control": {
                "algorithm": "scream",
                "init_bitrate_kbps": CEILING["init"],
                "min_bitrate_kbps": CEILING["min"],
                "max_bitrate_kbps": CEILING["max"],
                "scream": {
                    "delay_target_seconds": DELAY_TARGET_S,
                    "mul_increase": mul,
                },
            },
            "sink": {"backend": "fake", "sync": False},
            "recovery": {"nack": False, "pli": False, "fec": False},
            "latency_budget_ms": 0,
            "network": network_name,
        }],
    }


def cell_id(mul: float, regime_key: str) -> int:
    return (ID_BASE
            + REGIME_ORDER.index(regime_key) * len(MUL_INCREASE)
            + MUL_INCREASE.index(mul))


def cell_mapping() -> list[tuple[int, float, str, str]]:
    out = []
    for regime_key in REGIME_ORDER:
        for mul in MUL_INCREASE:
            out.append((cell_id(mul, regime_key), mul, regime_key, REGIMES[regime_key]))
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for cid, mul, regime_key, network_name in cell_mapping():
        spec = build(mul, regime_key, network_name)
        (OUT_DIR / f"{cid}.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
        n += 1
    print(f"wrote {n} H8 configurations ({ID_BASE}-{ID_BASE + n - 1}) under {OUT_DIR}")
    for cid, mul, regime_key, _ in cell_mapping():
        print(f"  {cid}: mul_increase={mul} {regime_key}")


if __name__ == "__main__":
    main()
