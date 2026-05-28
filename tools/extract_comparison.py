#!/usr/bin/env python3
"""Extract fair, controller-focused metrics from the SCReAM-vs-SCReAM
comparison sweep. Runs ON aum (reads ~/comparison-runs and the trace
specs). Emits:

  - comparison-runs/metrics.csv : one row per (arm, trace, rep) with
    mean sent rate, mean capacity, utilization, overshoot fraction,
    delivered-frame fraction, median latency (where available)
  - comparison-runs/timeseries/<arm>-<trace>-rep<N>.csv : t, sent_kbps
    for the bitrate-vs-capacity plots

Sent-rate time-series:
  ours : camera.json metrics.wire_bytes.samples  [[t, kbps], ...]
  umn  : transmitter-metrics/*.csv frame_sent rows binned to 1 s
Delivered frames:
  ours : summary.json viewer.frames_depayloaded  (clean depayloaded)
  umn  : received_metrics.csv fps integrated  (NOTE: partial-decode →
         includes corrupt frames; documented caveat)
Capacity(t):
  from specs/networks/mahimahi-5g-<trace>-100ms-x0p33.yaml camera_steps,
  looped to cover the run duration.
"""
from __future__ import annotations
import csv, glob, json, os
from pathlib import Path

GE = Path(os.path.expanduser("~/GstreamerExp"))
OUT = Path(os.path.expanduser("~/comparison-runs"))
TOTAL_FRAMES = 600


def capacity_curve(trace: str):
    """Return (step_dur_s, [rate_kbps per step]) for the trace."""
    spec = GE / "specs/networks" / f"mahimahi-5g-{trace}-100ms-x0p33.yaml"
    import yaml
    doc = yaml.safe_load(spec.read_text())
    steps = doc["camera_steps"]
    rates = [float(s.get("rate_kbps", 0)) for s in steps]
    dur = float(steps[0].get("duration", 0.1))
    return dur, rates


def cap_at(t: float, dur: float, rates: list[float]) -> float:
    idx = int(t / dur) % len(rates)
    return rates[idx]


def ours_sent_series(rep_dir: Path):
    cam = json.load(open(rep_dir / "camera.json"))
    return [(float(t), float(k)) for t, k in
            cam["metrics"]["wire_bytes"]["samples"]]


def ours_delivered(rep_dir: Path) -> int:
    s = json.load(open(rep_dir / "summary.json"))
    return int(s["viewer"]["frames_depayloaded"])


def ours_latency_med(rep_dir: Path):
    s = json.load(open(rep_dir / "summary.json"))
    return s.get("latency", {}).get("median_ms")


def umn_sent_series(rep_dir: Path):
    tx = glob.glob(str(rep_dir / "transmitter-metrics" / "*.csv"))
    if not tx:
        return []
    rows = [r for r in csv.DictReader(open(tx[0])) if r.get("event") == "frame_sent"]
    if not rows:
        return []
    t0 = min(float(r["tx_unix_ms"]) for r in rows) / 1000.0
    by_sec: dict[int, float] = {}
    for r in rows:
        t = float(r["tx_unix_ms"]) / 1000.0 - t0
        by_sec[int(t)] = by_sec.get(int(t), 0.0) + float(r["payload_bytes"])
    return [(float(s), bytes_ * 8 / 1000.0) for s, bytes_ in sorted(by_sec.items())]


def umn_delivered(rep_dir: Path) -> int:
    """Count UMN frame_decoded events from the receiver log. CAVEAT:
    UMN uses partial-frame decode for VP8 — it logs frame_decoded for
    EVERY frame fed to libvpx, including corrupt/incomplete ones, so
    this is ~always 600 and does NOT reflect clean delivery. Kept only
    to make the asymmetry explicit; not a quality metric."""
    logs = glob.glob(str(rep_dir / "receiver" / "receiver-*" / "stream0.log"))
    if not logs:
        return 0
    n = 0
    for line in open(logs[0]):
        if "frame_decoded" in line:
            n += 1
    return n


def derive(series, dur, rates):
    """utilization + overshoot from a sent-rate series vs capacity."""
    if not series:
        return None
    utils, overs = [], 0
    sent_vals = []
    for t, sent in series:
        cap = cap_at(t, dur, rates)
        sent_vals.append(sent)
        if cap > 0:
            utils.append(min(sent / cap, 2.0))
        if sent > cap:
            overs += 1
    return {
        "mean_sent_kbps": round(sum(sent_vals) / len(sent_vals), 1),
        "utilization": round(sum(utils) / len(utils), 3) if utils else 0,
        "overshoot_frac": round(overs / len(series), 3),
    }


def main():
    ts_dir = OUT / "timeseries"
    ts_dir.mkdir(parents=True, exist_ok=True)
    out_rows = []

    arms = [d.name for d in sorted(OUT.glob("*-scream*")) if d.is_dir()]
    for arm in arms:
        for trace_dir in sorted((OUT / arm).glob("*")):
            if not trace_dir.is_dir():
                continue
            trace = trace_dir.name
            dur, rates = capacity_curve(trace)
            mean_cap = round(sum(rates) / len(rates), 1)
            for rep_dir in sorted(trace_dir.glob("rep*")):
                rep = rep_dir.name
                if arm == "ours-scream":
                    series = ours_sent_series(rep_dir)
                    delivered = ours_delivered(rep_dir)
                    lat = ours_latency_med(rep_dir)
                else:
                    series = umn_sent_series(rep_dir)
                    delivered = umn_delivered(rep_dir)
                    lat = None
                d = derive(series, dur, rates) or {
                    "mean_sent_kbps": 0, "utilization": 0, "overshoot_frac": 0}
                out_rows.append({
                    "arm": arm, "trace": trace, "rep": rep,
                    "mean_cap_kbps": mean_cap,
                    "mean_sent_kbps": d["mean_sent_kbps"],
                    "utilization": d["utilization"],
                    "overshoot_frac": d["overshoot_frac"],
                    "delivered": delivered,
                    "delivered_frac": round(delivered / TOTAL_FRAMES, 3),
                    "lat_med_ms": lat if lat is not None else "",
                })
                # dump time-series
                with open(ts_dir / f"{arm}-{trace}-{rep}.csv", "w", newline="") as fh:
                    w = csv.writer(fh)
                    w.writerow(["t", "sent_kbps", "cap_kbps"])
                    for t, sent in series:
                        w.writerow([round(t, 2), round(sent, 1),
                                    round(cap_at(t, dur, rates), 1)])

    csv_path = OUT / "metrics.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    print(f"wrote {csv_path} ({len(out_rows)} rows)")

    # quick aggregate to stdout
    import statistics as st
    print(f"\n{'arm':12} {'trace':5} {'util':>6} {'oversh':>7} "
          f"{'sent':>7} {'cap':>6} {'deliv%':>7} {'lat_ms':>7}")
    agg = {}
    for r in out_rows:
        agg.setdefault((r["arm"], r["trace"]), []).append(r)
    for (arm, trace), rs in sorted(agg.items()):
        util = st.mean(x["utilization"] for x in rs)
        over = st.mean(x["overshoot_frac"] for x in rs)
        sent = st.mean(x["mean_sent_kbps"] for x in rs)
        cap = rs[0]["mean_cap_kbps"]
        dlv = st.mean(x["delivered_frac"] for x in rs) * 100
        lats = [x["lat_med_ms"] for x in rs if x["lat_med_ms"] != ""]
        lat = st.mean(lats) if lats else float("nan")
        print(f"{arm:12} {trace:5} {util:6.2f} {over:7.2f} "
              f"{sent:7.0f} {cap:6.0f} {dlv:7.1f} {lat:7.1f}")


if __name__ == "__main__":
    main()
