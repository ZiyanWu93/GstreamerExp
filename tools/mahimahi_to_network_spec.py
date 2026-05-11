#!/usr/bin/env python3
"""Convert Mahimahi packet-opportunity traces to network specs.

Input traces use the standard Mahimahi format: one integer millisecond
timestamp per packet delivery opportunity. The output is this project's
step-based `specs/networks/*.yaml` shape, where each bin becomes one
`tc tbf` rate step. This is an approximation of packet-level Mahimahi
replay, but it preserves the measured capacity envelope at the selected
time resolution.
"""

from __future__ import annotations

import argparse
import math
import sys
import zipfile
from collections import Counter
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PACKET_BYTES = 1500


def _read_trace(path: Path, member: str | None) -> list[int]:
    if path.suffix == ".zip":
        if member is None:
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if not n.endswith("/")]
            sys.exit(f"{path}: zip input requires --member; members: {names}")
        with zipfile.ZipFile(path) as z:
            text = z.read(member).decode()
    else:
        if member is not None:
            sys.exit("--member only applies to .zip inputs")
        text = path.read_text()

    out: list[int] = []
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s:
            continue
        try:
            out.append(int(s))
        except ValueError:
            sys.exit(f"{path}:{i}: expected integer millisecond timestamp, got {s!r}")
    if not out:
        sys.exit(f"{path}: trace is empty")
    if out != sorted(out):
        sys.exit(f"{path}: trace timestamps must be nondecreasing")
    return out


def _steps_from_trace(
    timestamps_ms: list[int],
    *,
    bin_ms: int,
    packet_bytes: int,
    delay_ms: int,
    min_rate_kbps: int,
    merge_equal: bool,
) -> list[dict]:
    start = timestamps_ms[0]
    end_exclusive = timestamps_ms[-1] + 1
    bins = math.ceil((end_exclusive - start) / bin_ms)
    counts = Counter((t - start) // bin_ms for t in timestamps_ms)

    steps: list[dict] = []
    for i in range(bins):
        bin_start = start + i * bin_ms
        bin_end = min(start + (i + 1) * bin_ms, end_exclusive)
        duration_s = (bin_end - bin_start) / 1000.0
        packets = counts.get(i, 0)
        rate_kbps = max(
            min_rate_kbps,
            round((packets * packet_bytes * 8) / duration_s / 1000),
        )
        step = {
            "duration": round(duration_s, 6),
            "rate_kbps": int(rate_kbps),
            "delay_ms": delay_ms,
            "loss": {"model": "none"},
            "label": f"{bin_start - start}-{bin_end - start} ms, {packets} packets",
        }
        if (
            merge_equal
            and steps
            and steps[-1]["rate_kbps"] == step["rate_kbps"]
            and steps[-1]["delay_ms"] == step["delay_ms"]
            and steps[-1]["loss"] == step["loss"]
        ):
            steps[-1]["duration"] = round(steps[-1]["duration"] + step["duration"], 6)
            steps[-1]["label"] = f"{steps[-1]['label'].split(',')[0]}-{bin_end - start} ms, merged"
        else:
            steps.append(step)
    return steps


def _spec(name: str, source: str, steps: list[dict], *,
          bin_ms: int, packet_bytes: int, delay_ms: int) -> dict:
    total_duration = round(sum(s["duration"] for s in steps), 6)
    mean_rate = round(sum(s["rate_kbps"] * s["duration"] for s in steps) / total_duration)
    return {
        "name": name,
        "description": (
            f"Mahimahi trace converted to step-based tc shaping. Source: {source}. "
            f"Bin: {bin_ms} ms. Packet size assumption: {packet_bytes} bytes. "
            f"Delay: {delay_ms} ms. Duration: {total_duration}s. "
            f"Time-weighted mean rate: {mean_rate} kbit/s. "
            "Each step has loss model none; capacity variation is represented "
            "by changing tbf rate per bin."
        ),
        "camera_steps": steps,
        "viewer_steps": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace", type=Path,
                    help="Mahimahi .trace file or .zip containing one")
    ap.add_argument("--member",
                    help="member name when TRACE is a zip, e.g. cqi.trace")
    ap.add_argument("--name", required=True,
                    help="network spec name")
    ap.add_argument("--out", type=Path,
                    help="output YAML path; default specs/networks/<name>.yaml")
    ap.add_argument("--bin-ms", type=int, default=100,
                    help="aggregation bin size in milliseconds")
    ap.add_argument("--packet-bytes", type=int, default=DEFAULT_PACKET_BYTES,
                    help="packet size represented by one trace line")
    ap.add_argument("--delay-ms", type=int, default=20,
                    help="netem delay to pair with the rate trace")
    ap.add_argument("--min-rate-kbps", type=int, default=1,
                    help="rate used for bins with zero packet opportunities")
    ap.add_argument("--merge-equal", action="store_true",
                    help="merge adjacent bins with identical rounded rate")
    args = ap.parse_args()

    if args.bin_ms <= 0:
        sys.exit("--bin-ms must be positive")
    if args.packet_bytes <= 0:
        sys.exit("--packet-bytes must be positive")
    if args.min_rate_kbps <= 0:
        sys.exit("--min-rate-kbps must be positive")

    timestamps = _read_trace(args.trace, args.member)
    steps = _steps_from_trace(
        timestamps,
        bin_ms=args.bin_ms,
        packet_bytes=args.packet_bytes,
        delay_ms=args.delay_ms,
        min_rate_kbps=args.min_rate_kbps,
        merge_equal=args.merge_equal,
    )
    source = str(args.trace)
    if args.member:
        source = f"{source}:{args.member}"
    doc = _spec(
        args.name, source, steps,
        bin_ms=args.bin_ms,
        packet_bytes=args.packet_bytes,
        delay_ms=args.delay_ms,
    )
    out = args.out or PROJECT_ROOT / "specs" / "networks" / f"{args.name}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(doc, sort_keys=False, width=88))
    print(f"wrote {out} ({len(steps)} steps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
