"""H3 - Media workload calibration gates realistic 5G trace comparisons.

This is a validity check before running SCReAM-vs-GCC claims on
translated 5G traces. If the video stream is far below trace capacity,
then frame delivery near-ties are expected and do not say much about
the congestion-control algorithms.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import _verifier as V  # noqa: E402

HID = "H3"
CLAIM = "Media workload calibration gates realistic 5G trace comparisons"
SOURCE = "Project-internal validity check for realistic-trace SCReAM/GCC experiments"
DEFAULT_EXPERIMENTS = (
    "scream-vs-gcc-mahimahi-5g-cqi",
    "scream-vs-gcc-mahimahi-5g-ho",
    "scream-vs-gcc-mahimahi-5g-rb",
)
MEDIAN_HEADROOM_THRESHOLD = 10.0
LOW_CAPACITY_BINS_THRESHOLD_PCT = 15.0


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def _network_for_config(config_id: str) -> str:
    cfg = _load_yaml(PROJECT_ROOT / "specs" / "configurations" / f"{config_id}.yaml")
    return str(cfg["network"])


def _rates(network: str) -> list[float]:
    spec = _load_yaml(PROJECT_ROOT / "specs" / "networks" / f"{network}.yaml")
    return [float(step["rate_kbps"]) for step in spec.get("camera_steps", [])]


def _camera_wire_mean(config_id: str, run_id: str) -> float | None:
    path = PROJECT_ROOT / "runs" / config_id / run_id / "summary.json"
    if not path.is_file():
        return None
    summary = json.loads(path.read_text())
    if summary.get("verdict") != "PASS":
        return None
    wire = (((summary.get("camera") or {}).get("wire_bytes") or {}).get("mean_kbps"))
    return float(wire) if wire is not None else None


def _experiment_wire_by_network(record: dict) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for run in record.get("runs", []):
        if run.get("exit_code") != 0:
            continue
        cid = str(run.get("config"))
        network = _network_for_config(cid)
        wire = _camera_wire_mean(cid, str(run.get("run_id")))
        if wire is not None:
            out.setdefault(network, []).append(wire)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiments", nargs="+", default=list(DEFAULT_EXPERIMENTS))
    args = ap.parse_args()

    V.print_header(HID, CLAIM, SOURCE)

    by_network: dict[str, list[float]] = {}
    missing = []
    for experiment in args.experiments:
        record = V.try_load_experiment(experiment)
        if record is None:
            missing.append(experiment)
            continue
        for network, values in _experiment_wire_by_network(record).items():
            by_network.setdefault(network, []).extend(values)

    if missing:
        return V.print_untested("missing records: " + ", ".join(missing))
    if not by_network:
        return V.print_untested("no observed camera wire bitrate summaries found")

    numbers: dict[str, str] = {}
    supported_networks = 0
    for network, wire_values in sorted(by_network.items()):
        rates = _rates(network)
        if not rates or not wire_values:
            return V.print_untested(f"missing rate or wire data for {network}")
        median_capacity = statistics.median(rates)
        mean_wire = statistics.mean(wire_values)
        headroom = median_capacity / mean_wire
        low_bins_pct = 100.0 * sum(1 for rate in rates if rate < 1000.0) / len(rates)
        if headroom >= MEDIAN_HEADROOM_THRESHOLD and low_bins_pct < LOW_CAPACITY_BINS_THRESHOLD_PCT:
            supported_networks += 1
        numbers[network] = (
            f"median_capacity={median_capacity:.0f} kbps, "
            f"mean_wire={mean_wire:.1f} kbps, "
            f"median_headroom={headroom:.1f}x, "
            f"bins_below_1mbps={low_bins_pct:.1f}%"
        )

    if supported_networks == len(by_network):
        verdict = "supported"
        note = "current workload under-drives all tested realistic traces"
    elif supported_networks:
        verdict = "inconclusive"
        note = "some traces are under-driven, but not all meet the headroom rule"
    else:
        verdict = "refuted"
        note = "the traces appear to stress the emitted media workload under the configured rule"

    return V.print_verdict(verdict, numbers, note)


if __name__ == "__main__":
    main()
