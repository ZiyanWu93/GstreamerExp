"""Throughput dimension.

Question: Did the data get there, and at what byte cost?

For a single run:
  - frames_sent (camera) vs frames_received (viewer)
  - delivery ratio
  - on-wire bytes total (camera egress vs viewer ingress)
  - per-phase delivery breakdown when network steps are available

For an experiment: per-arm aggregate of the same.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is importable when run as a module.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis import _common as C   # noqa: E402


name = "throughput"


def analyze_run(run: C.RunFiles) -> dict:
    summary = C.load_json(run.summary_path)
    camera = summary.get("camera") or {}
    viewer = summary.get("viewer") or {}
    sent = int(camera.get("frames_sent", 0))
    received = int(viewer.get("frames_depayloaded", 0))
    delivery = (received / sent) if sent > 0 else None
    cam_bytes = (camera.get("wire_bytes") or {}).get("total_bytes")
    rx_bytes  = (viewer.get("wire_bytes") or {}).get("total_bytes")

    return {
        "dimension": name,
        "frames_sent":     sent,
        "frames_received": received,
        "delivery":        delivery,
        "camera_total_bytes": cam_bytes,
        "viewer_total_bytes": rx_bytes,
        "exit_reason":     {
            "camera": camera.get("exit_reason"),
            "viewer": viewer.get("exit_reason"),
        },
    }


def analyze_experiment(record: dict) -> dict:
    """Per-arm aggregate. Each arm contributes one analyze_run report
    per PASS run; we aggregate the per-run scalars into mean ± stdev."""
    arms = {c["id"]: c["label"] for c in record["configurations"]}
    per_arm: dict[str, dict] = {}
    for cid, label in arms.items():
        runs = [r for r in record["runs"]
                if r["config"] == cid and r["exit_code"] == 0]
        sent_xs: list = []
        recv_xs: list = []
        delivery_xs: list = []
        for r in runs:
            run_dir = PROJECT_ROOT / "runs" / cid / r["run_id"]
            rep = analyze_run(C.RunFiles(
                run_dir, run_dir / "summary.json",
                run_dir / "camera.json", run_dir / "viewer.json", cid,
            ))
            sent_xs.append(rep["frames_sent"])
            recv_xs.append(rep["frames_received"])
            if rep["delivery"] is not None:
                delivery_xs.append(rep["delivery"])
        per_arm[cid] = {
            "label": label,
            "n":     len(runs),
            "frames_sent_xs":     sent_xs,
            "frames_received_xs": recv_xs,
            "delivery_xs":        delivery_xs,
        }
    return {"dimension": name, "per_arm": per_arm}


def render(report: dict) -> str:
    """Human-readable rendering. Auto-detects run vs experiment shape."""
    if "per_arm" in report:
        return _render_experiment(report)
    return _render_run(report)


def _render_run(report: dict) -> str:
    lines = ["[throughput] Did the data get there, and at what byte cost?"]
    sent = report["frames_sent"]
    recv = report["frames_received"]
    deliv = report["delivery"]
    deliv_s = f"{deliv*100:.1f}%" if deliv is not None else "—"
    lines.append(f"  frames     sent={sent}  received={recv}  "
                 f"delivery={deliv_s}")
    cam_kb = (report["camera_total_bytes"] or 0) / 1024
    rx_kb = (report["viewer_total_bytes"] or 0) / 1024
    lines.append(f"  bytes      camera_egress={cam_kb:.1f} KB  "
                 f"viewer_ingress={rx_kb:.1f} KB")
    er = report["exit_reason"]
    lines.append(f"  exit       camera={er['camera']}  viewer={er['viewer']}")
    return "\n".join(lines)


def _render_experiment(report: dict) -> str:
    lines = ["[throughput] Did the data get there, and at what byte cost?"]
    lines.append(f"  {'arm':<14} {'n':>3} "
                 f"{'rx_frames µ±σ':>16} {'delivery µ±σ':>16}")
    for cid, arm in report["per_arm"].items():
        delivery_pct = [d * 100 for d in arm["delivery_xs"]]
        lines.append(f"  {arm['label']:<14} {arm['n']:>3} "
                     f"{C.msd(arm['frames_received_xs'], '{:.1f}'):>16} "
                     f"{C.msd(delivery_pct, '{:.1f}'):>16}")
    return "\n".join(lines)
