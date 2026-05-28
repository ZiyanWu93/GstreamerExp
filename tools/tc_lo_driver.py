#!/usr/bin/env python3
"""Drive time-varying tc impairment on the loopback interface from a
mahimahi-translated network spec, for the GstreamerExp <-> UMN
comparison harness.

Both comparison stacks (ours and UMN's) run sender+receiver on the same
host over 127.0.0.1, so every packet crosses `lo`. This script shapes
`lo` with:

    root  : netem  (fixed one-way delay from the spec's first step)
    child : tbf    (token-bucket rate, CHANGED every step)

It reads specs/networks/<name>.yaml `camera_steps`, applies the first
step's rate, then walks the steps sleeping `duration` seconds between
`tc ... change` calls. When the steps run out before --duration, it
loops the trace. On exit (SIGTERM/SIGINT/duration reached) it removes
the qdisc so `lo` is clean.

Requires root (calls `tc` directly). Run under sudo.

Usage:
    sudo python3 tools/tc_lo_driver.py \
        --spec specs/networks/mahimahi-5g-ho-100ms-x0p33.yaml \
        --duration 65
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

DEV = "lo"


def _tc(*args: str, check: bool = True) -> None:
    subprocess.run(["tc", *args], check=check,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _teardown() -> None:
    # Best-effort: remove our root qdisc, restoring lo to noqueue default.
    _tc("qdisc", "del", "dev", DEV, "root", check=False)


def _set_rate(rate_kbps: int, *, first: bool, delay_ms: int) -> None:
    """Apply the rate. On the first call, build the netem+tbf tree;
    afterwards just `change` the tbf rate."""
    # tbf needs a positive rate; a 0-kbps trace step means "blackout".
    # Model it as a tiny but nonzero rate so the queue stalls rather
    # than erroring out.
    rate = max(int(rate_kbps), 1)
    burst = "32kbit"
    latency = "400ms"
    if first:
        _teardown()
        # root netem: fixed delay (traces carry ~constant 20ms).
        _tc("qdisc", "add", "dev", DEV, "root", "handle", "1:",
            "netem", "delay", f"{delay_ms}ms")
        # child tbf under netem: the rate knob we vary.
        _tc("qdisc", "add", "dev", DEV, "parent", "1:", "handle", "2:",
            "tbf", "rate", f"{rate}kbit", "burst", burst, "latency", latency)
    else:
        _tc("qdisc", "change", "dev", DEV, "parent", "1:", "handle", "2:",
            "tbf", "rate", f"{rate}kbit", "burst", burst, "latency", latency)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, type=Path,
                    help="path to a networks/<name>.yaml with camera_steps")
    ap.add_argument("--duration", type=float, default=65.0,
                    help="total seconds to drive before teardown (loops the trace)")
    args = ap.parse_args()

    doc = yaml.safe_load(args.spec.read_text())
    steps = doc.get("camera_steps") or []
    if not steps:
        print(f"no camera_steps in {args.spec}", file=sys.stderr)
        return 2

    delay_ms = int(steps[0].get("delay_ms", 20))

    signal.signal(signal.SIGTERM, lambda *_: (_teardown(), sys.exit(0)))
    signal.signal(signal.SIGINT, lambda *_: (_teardown(), sys.exit(0)))

    print(f"[tc-lo] driving {args.spec.name} on {DEV}: "
          f"{len(steps)} steps, delay={delay_ms}ms, duration={args.duration}s",
          flush=True)

    start = time.monotonic()
    first = True
    i = 0
    try:
        while time.monotonic() - start < args.duration:
            step = steps[i % len(steps)]
            rate = int(step.get("rate_kbps", 0))
            _set_rate(rate, first=first, delay_ms=delay_ms)
            first = False
            time.sleep(float(step.get("duration", 0.1)))
            i += 1
    finally:
        _teardown()
        print(f"[tc-lo] done; {DEV} qdisc removed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
