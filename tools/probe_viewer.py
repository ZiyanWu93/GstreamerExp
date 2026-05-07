"""UDP probe — runs on the viewer actor (pair to probe_camera).

Binds to bind_host:port, recvs UDP packets until --stop-after-s seconds
elapse with no packet (or --max-duration-s total), unpacks seq + send_ts
from the payload, captures the local CLOCK_REALTIME arrival timestamp,
and writes one JSON line per packet to --output. Output schema:

    {"seq": <int>, "send_ts_ns": <int>, "recv_ts_ns": <int>}

Clock skew correction happens in the driver, not here.

Self-contained — no project imports — so the script can be piped over
SSH stdin without deployment.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import time


_HEADER = struct.Struct(">QQ")   # seq, send_ts_ns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind-host", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--max-duration-s", type=float, required=True,
                    help="hard deadline — viewer always exits by this point")
    ap.add_argument("--stop-after-s", type=float, default=2.0,
                    help="exit early after this many idle seconds with no "
                         "packets (lets the viewer wrap up cleanly when "
                         "the camera has finished)")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    sock.bind((args.bind_host, args.port))
    sock.settimeout(args.stop_after_s)

    out = open(args.output, "w")
    start = time.monotonic()
    last_pkt_t = start
    n = 0
    while True:
        if time.monotonic() - start > args.max_duration_s:
            break
        try:
            pkt, _ = sock.recvfrom(2048)
        except socket.timeout:
            if time.monotonic() - last_pkt_t > args.stop_after_s:
                break
            continue
        recv_ts_ns = time.clock_gettime_ns(time.CLOCK_REALTIME)
        if len(pkt) < _HEADER.size:
            continue
        seq, send_ts_ns = _HEADER.unpack_from(pkt)
        out.write(json.dumps({
            "seq": seq, "send_ts_ns": send_ts_ns, "recv_ts_ns": recv_ts_ns,
        }) + "\n")
        last_pkt_t = time.monotonic()
        n += 1

    out.close()
    print(f"probe_viewer: captured {n} packets in {time.monotonic() - start:.2f}s")


if __name__ == "__main__":
    main()
