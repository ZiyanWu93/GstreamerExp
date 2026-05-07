"""UDP probe — runs on the camera actor.

Sends `rate_pps × duration_s` packets at a steady cadence to the target
host:port. Each 64-byte packet carries an 8-byte big-endian sequence
number and an 8-byte big-endian send timestamp (CLOCK_REALTIME ns).
The viewer-side probe pairs the seq with its arrival time; the driver
corrects the cross-host clock skew downstream.

This script must stay self-contained — no project imports — so it can
be piped over SSH stdin without deploying anything.
"""

from __future__ import annotations

import argparse
import socket
import struct
import time


_PAYLOAD_SIZE = 32
_HEADER = struct.Struct(">QQ")   # seq, send_ts_ns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-host", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--rate-pps", type=int, required=True,
                    help="packets per second")
    ap.add_argument("--duration-s", type=float, required=True)
    args = ap.parse_args()

    pad = b"\x00" * (_PAYLOAD_SIZE - _HEADER.size)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / args.rate_pps
    total = int(args.rate_pps * args.duration_s)
    start = time.monotonic()
    sent = 0
    for seq in range(total):
        # Steady cadence: target_t is the absolute instant at which seq#
        # `seq` should be released. Sleep until then; if we're already
        # late, drop the sleep but keep marching.
        target_t = start + seq * interval
        now = time.monotonic()
        if now < target_t:
            time.sleep(target_t - now)
        send_ts_ns = time.clock_gettime_ns(time.CLOCK_REALTIME)
        pkt = _HEADER.pack(seq, send_ts_ns) + pad
        try:
            sock.sendto(pkt, (args.target_host, args.port))
            sent += 1
        except OSError:
            pass   # buffer full, count as a local-side drop and continue
    print(f"probe_camera: sent {sent}/{total} packets "
          f"over {time.monotonic() - start:.2f}s")


if __name__ == "__main__":
    main()
