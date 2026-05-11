"""H4 - SCReAM preserves more decodable video on stressed realistic 5G traces."""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import _verifier as V  # noqa: E402

HID = "H4"
CLAIM = "SCReAM preserves more decodable video than GCC on calibrated stressed realistic 5G traces"
SOURCE = "Project-internal realistic-trace SCReAM/GCC experiment"
CASES = [
    {
        "trace": "CQI",
        "experiment": "scream-vs-gcc-mahimahi-5g-cqi-x0p33-snow-384x216",
        "scream": "81",
        "gcc": "82",
        "role": "control",
    },
    {
        "trace": "HO",
        "experiment": "scream-vs-gcc-mahimahi-5g-ho-x0p33-snow-384x216",
        "scream": "79",
        "gcc": "80",
        "role": "stress",
    },
    {
        "trace": "RB",
        "experiment": "scream-vs-gcc-mahimahi-5g-rb-x0p33-snow-384x216",
        "scream": "83",
        "gcc": "84",
        "role": "stress",
    },
]
MIN_STRESS_FRAME_RATIO = 1.25
MIN_STRONG_FRAME_RATIO = 2.0
MAX_STRESS_CAMERA_WIRE_RATIO = 0.85
MAX_VIEWER_WIRE_DELTA_PCT = 10.0


def _summary(config_id: str, run_id: str) -> dict | None:
    path = PROJECT_ROOT / "runs" / config_id / run_id / "summary.json"
    if not path.is_file():
        return None
    doc = json.loads(path.read_text())
    if doc.get("verdict") != "PASS":
        return None
    return doc


def _summaries(record: dict, config_id: str) -> list[dict]:
    out = []
    for run in record.get("runs", []):
        if str(run.get("config")) != config_id or run.get("exit_code") != 0:
            continue
        summary = _summary(config_id, str(run.get("run_id")))
        if summary is not None:
            out.append(summary)
    return out


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _frames(summary: dict) -> float:
    return float((summary.get("viewer") or {}).get("frames_depayloaded") or 0.0)


def _camera_wire(summary: dict) -> float:
    return float((((summary.get("camera") or {}).get("wire_bytes") or {}).get("mean_kbps")) or 0.0)


def _viewer_wire(summary: dict) -> float:
    return float((((summary.get("viewer") or {}).get("wire_bytes") or {}).get("mean_kbps")) or 0.0)


def _encoder_target(summary: dict) -> float:
    target = ((summary.get("camera") or {}).get("encoder_target") or {})
    values = [float(v) for v in (target.get("last_kbps"), target.get("min_kbps"), target.get("max_kbps")) if v is not None]
    return statistics.mean(values) if values else 0.0


def _load_case(case: dict) -> dict | None:
    record = V.try_load_experiment(case["experiment"])
    if record is None:
        V.print_untested(f"missing experiment record: {case['experiment']}")
        return None

    scream = _summaries(record, case["scream"])
    gcc = _summaries(record, case["gcc"])
    expected = int(record.get("reps") or 0)
    if len(scream) < expected or len(gcc) < expected:
        V.print_untested(
            f"{case['trace']}: need {expected} PASS summaries for each arm; "
            f"found scream={len(scream)} gcc={len(gcc)}")
        return None

    scream_frames = _mean([_frames(s) for s in scream])
    gcc_frames = _mean([_frames(s) for s in gcc])
    scream_camera_wire = _mean([_camera_wire(s) for s in scream])
    gcc_camera_wire = _mean([_camera_wire(s) for s in gcc])
    scream_viewer_wire = _mean([_viewer_wire(s) for s in scream])
    gcc_viewer_wire = _mean([_viewer_wire(s) for s in gcc])
    scream_encoder_target = _mean([_encoder_target(s) for s in scream])
    gcc_encoder_target = _mean([_encoder_target(s) for s in gcc])
    frame_ratio = scream_frames / gcc_frames if gcc_frames else 0.0
    camera_wire_ratio = scream_camera_wire / gcc_camera_wire if gcc_camera_wire else 0.0
    viewer_wire_delta_pct = (
        100.0 * abs(scream_viewer_wire - gcc_viewer_wire)
        / max(scream_viewer_wire, gcc_viewer_wire)
        if max(scream_viewer_wire, gcc_viewer_wire) else 0.0
    )

    return {
        "trace": case["trace"],
        "role": case["role"],
        "scream_frames": scream_frames,
        "gcc_frames": gcc_frames,
        "frame_delta": scream_frames - gcc_frames,
        "frame_ratio": frame_ratio,
        "scream_camera_wire": scream_camera_wire,
        "gcc_camera_wire": gcc_camera_wire,
        "camera_wire_ratio": camera_wire_ratio,
        "scream_viewer_wire": scream_viewer_wire,
        "gcc_viewer_wire": gcc_viewer_wire,
        "scream_encoder_target": scream_encoder_target,
        "gcc_encoder_target": gcc_encoder_target,
        "viewer_wire_delta_pct": viewer_wire_delta_pct,
    }


def main() -> None:
    V.print_header(HID, CLAIM, SOURCE)
    rows = []
    for case in CASES:
        row = _load_case(case)
        if row is None:
            return None
        rows.append(row)

    stress = [row for row in rows if row["role"] == "stress"]
    stress_supported = all(
        row["frame_ratio"] >= MIN_STRESS_FRAME_RATIO
        and row["camera_wire_ratio"] <= MAX_STRESS_CAMERA_WIRE_RATIO
        and row["viewer_wire_delta_pct"] <= MAX_VIEWER_WIRE_DELTA_PCT
        for row in stress
    )
    strong_stress = any(row["frame_ratio"] >= MIN_STRONG_FRAME_RATIO for row in stress)
    supported = stress_supported and strong_stress
    if supported:
        verdict = "supported"
        note = (
            "CQI acts as the high-capacity control; on HO/RB stress traces "
            "SCReAM delivers more decodable video with lower camera egress. "
            "GCC's estimator target can still fall; camera egress is the measured output."
        )
    else:
        verdict = "refuted"
        note = "the stressed-trace frame delivery / efficiency thresholds were not met"

    numbers = {
        f"{row['trace']}_viewer_frames": (
            f"scream={row['scream_frames']:.1f}, gcc={row['gcc_frames']:.1f}, "
            f"ratio={row['frame_ratio']:.2f}x, delta={row['frame_delta']:.1f}"
        )
        for row in rows
    }
    numbers.update(
        {
            f"{row['trace']}_camera_wire_mean_kbps": (
                f"scream={row['scream_camera_wire']:.1f}, gcc={row['gcc_camera_wire']:.1f}, "
                f"ratio={row['camera_wire_ratio']:.2f}x"
            )
            for row in rows
        }
    )
    numbers.update(
        {
            f"{row['trace']}_encoder_target_summary_kbps": (
                f"scream={row['scream_encoder_target']:.1f}, gcc={row['gcc_encoder_target']:.1f}"
            )
            for row in rows
        }
    )
    return V.print_verdict(verdict, numbers, note)


if __name__ == "__main__":
    main()
