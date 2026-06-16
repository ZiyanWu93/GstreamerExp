"""Per-run summary computation and pretty-printing.

build_summary() merges camera.json + viewer.json into the canonical
summary.json shape (verdict, camera block, viewer block, optional
latency block). print_report() formats one summary for the terminal.
read_result() reads a worker's result file with a tolerable fallback
when it's missing.

Latency correlation joins camera and viewer frame_latency samples by
RTP timestamp — the same value on both sides because it travels in the
packet header, so the join is robust to packet loss.
"""

from __future__ import annotations

import json
from pathlib import Path


def read_result(path: Path) -> dict:
    if not path.exists():
        return {"missing": True, "errors": []}
    return json.loads(path.read_text())


def _metric_summary(role_data: dict, name: str) -> dict:
    return role_data.get("metrics", {}).get(name, {}).get("summary", {})


def _correlate_latency(camera: dict, viewer: dict,
                       camera_skew: float = 0.0,
                       viewer_skew: float = 0.0) -> dict:
    """Join camera + viewer frame_latency samples by RTP timestamp.

    Each sample is [rtp_timestamp, host_wall_time]. Each side's wall
    time is corrected by its host clock skew (host_clock - controller_clock);
    deltas are then in the controller's timeframe and reflect actual
    end-to-end latency."""
    s_samples = camera.get("metrics", {}).get("frame_latency", {}).get("samples", [])
    r_samples = viewer.get("metrics", {}).get("frame_latency", {}).get("samples", [])
    if not s_samples or not r_samples:
        return {}
    s_by_ts = {ts: t - camera_skew for ts, t in s_samples}
    r_by_ts = {ts: t - viewer_skew for ts, t in r_samples}
    deltas = sorted(
        (r_by_ts[ts] - s_by_ts[ts]) * 1000.0  # ms
        for ts in r_by_ts if ts in s_by_ts
    )
    if not deltas:
        return {}

    def pct(p):
        return deltas[min(int(len(deltas) * p / 100), len(deltas) - 1)]

    return {
        "samples_count": len(deltas),
        "min_ms":        round(deltas[0], 2),
        "median_ms":     round(pct(50), 2),
        "p95_ms":        round(pct(95), 2),
        "p99_ms":        round(pct(99), 2),
        "max_ms":        round(deltas[-1], 2),
    }


def _stream_block(camera: dict, viewer: dict,
                  camera_skew: float, viewer_skew: float) -> dict:
    """Summarize one stream from its (camera, viewer) result pair —
    the single-stream summary shape, now nested under summary.streams[]."""
    errors = []
    if camera.get("missing"):
        errors.append("camera result file missing")
    if viewer.get("missing"):
        errors.append("viewer result file missing")
    if camera.get("errors"):
        errors.append(f"camera errors: {camera['errors']}")
    if viewer.get("errors"):
        errors.append(f"viewer errors: {viewer['errors']}")

    sent = _metric_summary(camera, "frame_count").get("frames", 0)
    recv = _metric_summary(viewer, "frame_count").get("frames", 0)
    if recv == 0:
        errors.append("no frames received")
    # Frame loss alone is not a failure — under impairment it is expected.

    camera_summary = {
        "frames_sent":      sent,
        "duration_seconds": camera.get("duration_seconds", 0),
        "exit_reason":      camera.get("exit_reason", "UNKNOWN"),
    }
    encoder_target = _metric_summary(camera, "encoder_target_kbps")
    if encoder_target:
        camera_summary["encoder_target"] = encoder_target
    encoded = _metric_summary(camera, "encoded_bitrate")
    if encoded:
        camera_summary["encoded_bitrate"] = encoded
    camera_wire = _metric_summary(camera, "wire_bytes")
    if camera_wire:
        camera_summary["wire_bytes"] = camera_wire

    viewer_summary = {
        "frames_depayloaded": recv,
        "duration_seconds":   viewer.get("duration_seconds", 0),
        "exit_reason":        viewer.get("exit_reason", "UNKNOWN"),
    }
    viewer_wire = _metric_summary(viewer, "wire_bytes")
    if viewer_wire:
        viewer_summary["wire_bytes"] = viewer_wire
    decoder = _metric_summary(viewer, "decoder_errors")
    if decoder:
        viewer_summary["decoder_errors"] = decoder

    block = {
        "verdict": "PASS" if not errors else "FAIL",
        "errors": errors,
        "camera": camera_summary,
        "viewer": viewer_summary,
    }
    latency = _correlate_latency(camera, viewer,
                                 camera_skew=camera_skew,
                                 viewer_skew=viewer_skew)
    if latency:
        block["latency"] = latency
    return block


_RTP_HZ = 90000.0   # RTP video clock rate (RTP timestamp units per second)


def _correlate_sync_error(streams_meta: list, viewer_data: list) -> dict:
    """Cross-stream presentation skew: how far apart, in ms, the N cameras'
    same-instant frames arrive at the viewer.

    For each stream, normalize its viewer-side frame_latency samples to a
    capture-time t = (rtp_timestamp - base) / 90000 (base = the stream's
    first rtp_timestamp), bin t to its frame index via the stream's fps, and
    for every frame index present in >= 2 streams take the spread of arrival
    wall-clocks, max(wall) - min(wall).

    The walls are NOT skew-corrected on purpose: every viewer worker runs on
    the SAME viewer host and shares one clock, so any host-vs-controller skew
    is a common additive constant that cancels EXACTLY in max - min. So the
    skew is trustworthy in absolute terms even though end-to-end latency is
    only relatively trustworthy. (This holds only because multi-camera is
    multi-stream on one viewer host, not multi-host.)

    Returns {} when fewer than two streams carry usable frame_latency — e.g.
    single-stream configs — so those summaries are unchanged.
    """
    per_stream: list = []   # {frame_index: wall} for each usable stream
    names: list = []
    for i, viewer in enumerate(viewer_data):
        meta = streams_meta[i] if i < len(streams_meta) else {}
        fps = meta.get("fps")
        samples = (viewer.get("metrics", {}) or {}).get("frame_latency", {}).get("samples", [])
        if not fps or not samples:
            continue
        base = min(ts for ts, _ in samples)
        frames: dict = {}
        for ts, wall in samples:
            k = round((ts - base) / _RTP_HZ * fps)   # frame index since first
            frames[k] = wall                          # last wins (dups rare)
        per_stream.append(frames)
        names.append(meta.get("name", f"stream{i}"))

    if len(per_stream) < 2:
        return {}

    skews: list = []
    worst = None   # (skew_ms, lo_name, hi_name)
    for k in set().union(*(set(m) for m in per_stream)):
        present = [(m[k], names[j]) for j, m in enumerate(per_stream) if k in m]
        if len(present) < 2:
            continue
        lo, hi = min(present), max(present)
        skew_ms = (hi[0] - lo[0]) * 1000.0
        skews.append(skew_ms)
        if worst is None or skew_ms > worst[0]:
            worst = (skew_ms, lo[1], hi[1])
    if not skews:
        return {}

    skews.sort()

    def pct(p):
        return skews[min(int(len(skews) * p / 100), len(skews) - 1)]

    return {
        "samples_count": len(skews),
        "min_ms":        round(skews[0], 2),
        "median_ms":     round(pct(50), 2),
        "p95_ms":        round(pct(95), 2),
        "p99_ms":        round(pct(99), 2),
        "max_ms":        round(skews[-1], 2),
        "worst_pair":    [worst[1], worst[2]],
    }


def build_summary(streams_meta: list, camera_data: list, viewer_data: list,
                  camera_skew: float = 0.0,
                  viewer_skew: float = 0.0) -> dict:
    """Aggregate N (camera, viewer) result pairs into the multi-stream
    summary. Each stream gets its own verdict + camera/viewer/latency
    block under summary.streams[]; the overall verdict is strict — PASS
    iff every stream PASSes (the teleop contract). camera_data[i] /
    viewer_data[i] are stream i's two result files, aligned with
    streams_meta (one {name, priority, fps} per stream). For multi-stream
    runs a cross-stream sync_error block is added (same-host presentation
    skew); single-stream runs omit it."""
    streams = []
    for i, meta in enumerate(streams_meta):
        camera = camera_data[i] if i < len(camera_data) else {"missing": True, "errors": []}
        viewer = viewer_data[i] if i < len(viewer_data) else {"missing": True, "errors": []}
        block = _stream_block(camera, viewer, camera_skew, viewer_skew)
        block["stream_id"] = i
        block["name"] = meta.get("name", f"stream{i}")
        block["priority"] = meta.get("priority", 0)
        streams.append(block)

    failed = [s["name"] for s in streams if s["verdict"] != "PASS"]
    errors = [f"stream '{name}' FAILED" for name in failed]
    verdict = "PASS" if not failed else "FAIL"
    out = {"verdict": verdict, "errors": errors, "streams": streams}

    sync_error = _correlate_sync_error(streams_meta, viewer_data)
    if sync_error:
        out["sync_error"] = sync_error
    return out


def print_report(label: str, run_dir: Path, summary: dict) -> None:
    print()
    print(f"=== {label}: {summary['verdict']} ===")
    for st in summary["streams"]:
        s = st["camera"]
        r = st["viewer"]
        print(f"  [{st['stream_id']}] {st['name']} "
              f"(prio {st['priority']}): {st['verdict']}")
        print(f"    camera: {s['frames_sent']:>4d} frames  "
              f"{s['duration_seconds']:>5.1f}s  exit={s['exit_reason']}")
        if "encoder_target" in s:
            b = s["encoder_target"]
            print(f"            target  {b['first_kbps']} → {b['last_kbps']} kbps "
                  f"(min {b['min_kbps']}, max {b['max_kbps']})")
        if "encoded_bitrate" in s:
            e = s["encoded_bitrate"]
            print(f"            encoded {e['first_kbps']} → {e['last_kbps']} kbps "
                  f"(mean {e['mean_kbps']})")
        if "wire_bytes" in s:
            w = s["wire_bytes"]
            print(f"            wire    {w['first_kbps']} → {w['last_kbps']} kbps "
                  f"(mean {w['mean_kbps']}, total {w['total_bytes']/1000:.1f} KB)")
        print(f"    viewer: {r['frames_depayloaded']:>4d} frames  "
              f"{r['duration_seconds']:>5.1f}s  exit={r['exit_reason']}")
        if "wire_bytes" in r:
            w = r["wire_bytes"]
            print(f"            wire    {w['first_kbps']} → {w['last_kbps']} kbps "
                  f"(mean {w['mean_kbps']}, total {w['total_bytes']/1000:.1f} KB)")
        if "decoder_errors" in r:
            d = r["decoder_errors"]
            total = d['depay_warnings'] + d['decoder_warnings'] + d['other_warnings']
            if total:
                print(f"            warnings depay={d['depay_warnings']} "
                      f"decoder={d['decoder_warnings']} other={d['other_warnings']}")
        if "latency" in st:
            L = st["latency"]
            print(f"            latency median {L['median_ms']:.1f} ms  "
                  f"p95 {L['p95_ms']:.1f} ms  p99 {L['p99_ms']:.1f} ms  "
                  f"max {L['max_ms']:.1f} ms  (n={L['samples_count']})")
        for e in st["errors"]:
            print(f"    ! {e}")
    if "sync_error" in summary:
        sy = summary["sync_error"]
        print(f"  sync:     median {sy['median_ms']:.1f} ms  "
              f"p95 {sy['p95_ms']:.1f} ms  max {sy['max_ms']:.1f} ms  "
              f"(worst {sy['worst_pair'][0]}<->{sy['worst_pair'][1]}, "
              f"n={sy['samples_count']})")
    if summary["errors"]:
        for e in summary["errors"]:
            print(f"  ! {e}")
    print(f"  results: {run_dir}")
