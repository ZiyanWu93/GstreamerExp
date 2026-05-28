#!/usr/bin/env python3
"""Extract a SCReAM-vs-SCReAM comparison: our reference gstscream
(from runs/<cfg>/summary.json) vs UMN's reimplementation (from a
preserved received_metrics.csv). Run on aum where the data lives."""
import json, csv, glob, sys


def ours(cfg):
    d = sorted(glob.glob(f"runs/{cfg}/*/summary.json"))[-1]
    s = json.load(open(d))
    v, c = s["viewer"], s["camera"]
    et = c["encoder_target"]
    lat = s.get("latency", {})
    print("=== OUR SCReAM (reference / Ericsson gstscream) ===")
    print(f"  delivered frames : {v['frames_depayloaded']} / 600")
    print(f"  encoder target   : {et['first_kbps']} -> {et['last_kbps']} kbps "
          f"(min {et['min_kbps']}, max {et['max_kbps']})")
    print(f"  tx wire mean     : {c['wire_bytes']['mean_kbps']:.0f} kbps")
    print(f"  rx wire mean     : {v['wire_bytes']['mean_kbps']:.0f} kbps")
    if lat:
        print(f"  latency          : median {lat['median_ms']:.1f} ms  "
              f"p95 {lat['p95_ms']:.1f}  max {lat['max_ms']:.1f}")


def umn(outdir):
    f = glob.glob(f"{outdir}/receiver/receiver-*/received_metrics.csv")
    if not f:
        print(f"=== UMN SCReAM: no csv in {outdir} ===")
        return
    rows = list(csv.DictReader(open(f[0])))
    if len(rows) < 2:
        print(f"=== UMN SCReAM: only {len(rows)} rows (collapsed?) ===")
        return
    ts = [float(r["timestamp"]) for r in rows]
    fps = [float(r["fps"]) for r in rows]
    br = [float(r["bitrate_mbps"]) * 1000 for r in rows]
    frames = sum(fps[i] * (ts[i] - ts[i - 1]) for i in range(1, len(rows)))
    nz = [b for b in br if b > 0]
    print("=== UMN SCReAM (Rushi reimplementation / Python AIMD) ===")
    print(f"  delivered frames : {round(frames)} / 600  (integrated from fps)")
    print(f"  rx bitrate mean  : {sum(br)/len(br):.0f} kbps  "
          f"(active-only {sum(nz)/len(nz) if nz else 0:.0f})")
    print(f"  rx bitrate range : {min(br):.0f} - {max(br):.0f} kbps")
    print(f"  metric rows      : {len(rows)} over {ts[-1]-ts[0]:.1f}s")
    tx = glob.glob(f"{outdir}/transmitter-metrics/*.csv")
    if tx:
        tr = list(csv.DictReader(open(tx[0])))
        if tr:
            print(f"  tx-metrics cols  : {list(tr[0].keys())[:14]}")


if __name__ == "__main__":
    ours(sys.argv[1] if len(sys.argv) > 1 else "90")
    print()
    umn(sys.argv[2] if len(sys.argv) > 2 else "/tmp/arm-umn-scream-out")
