"""Structured hypothesis registry builder.

The YAML files under specs/hypotheses/ are the stable research
bookkeeping layer. This module validates those files and resolves the
current execution state from specs/experiments/ plus runs/experiments/.
It does not decide scientific verdicts; verifier scripts remain the
executable truth for each hypothesis.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HYPOTHESIS_DIR = PROJECT_ROOT / "specs" / "hypotheses"
EXPERIMENT_DIR = PROJECT_ROOT / "specs" / "experiments"
CONFIG_DIR = PROJECT_ROOT / "specs" / "configurations"
RUN_EXPERIMENT_DIR = PROJECT_ROOT / "runs" / "experiments"

REQUIRED_TOP_LEVEL = {
    "id",
    "slug",
    "title",
    "claim",
    "status",
    "source",
    "page",
    "verifier",
    "prediction",
    "verdict_rule",
    "setup",
    "results",
    "conclusions",
}
VALID_STATUSES = {
    "untested",
    "incomplete",
    "analyzable",
    "supported",
    "refuted",
    "inconclusive",
    "regime-dependent",
    "superseded",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text()) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected mapping")
    return doc


def load_hypothesis_specs(root: Path = PROJECT_ROOT) -> list[dict[str, Any]]:
    specs = []
    for path in sorted((root / "specs" / "hypotheses").glob("*.yaml")):
        spec = _load_yaml(path)
        spec["_path"] = str(path.relative_to(root))
        validate_hypothesis_spec(spec, root=root)
        specs.append(spec)
    return specs


def validate_hypothesis_spec(spec: dict[str, Any], root: Path = PROJECT_ROOT) -> None:
    path = spec.get("_path", "<memory>")
    missing = sorted(REQUIRED_TOP_LEVEL - set(spec))
    if missing:
        raise ValueError(f"{path}: missing required keys: {', '.join(missing)}")

    if spec["status"] not in VALID_STATUSES:
        raise ValueError(f"{path}: invalid status {spec['status']!r}")

    setup = spec["setup"]
    if not isinstance(setup, dict):
        raise ValueError(f"{path}: setup must be a mapping")
    experiments = setup.get("experiments")
    if not isinstance(experiments, list):
        raise ValueError(f"{path}: setup.experiments must be a list")
    metrics = setup.get("required_metrics")
    if not isinstance(metrics, list):
        raise ValueError(f"{path}: setup.required_metrics must be a list")

    results = spec["results"]
    if not isinstance(results, dict) or not isinstance(results.get("expected_records"), list):
        raise ValueError(f"{path}: results.expected_records must be a list")
    report = results.get("report")
    if report is not None and not (root / report).is_file():
        raise ValueError(f"{path}: results.report does not exist: {report}")
    for figure in results.get("figures", []):
        if not (root / figure).is_file():
            raise ValueError(f"{path}: results.figure does not exist: {figure}")

    conclusions = spec["conclusions"]
    if not isinstance(conclusions, dict):
        raise ValueError(f"{path}: conclusions must be a mapping")
    for key in ("established_claims", "limits"):
        if not isinstance(conclusions.get(key), list):
            raise ValueError(f"{path}: conclusions.{key} must be a list")

    page = spec.get("page")
    if page and not (root / page).is_file():
        raise ValueError(f"{path}: page does not exist: {page}")
    verifier = spec.get("verifier")
    if verifier and not (root / verifier).is_file():
        raise ValueError(f"{path}: verifier does not exist: {verifier}")

    seen_records = set()
    for exp in experiments:
        if not isinstance(exp, dict):
            raise ValueError(f"{path}: each setup.experiments item must be a mapping")
        name = exp.get("name")
        if not name:
            raise ValueError(f"{path}: experiment entry missing name")
        exp_path = root / "specs" / "experiments" / f"{name}.yaml"
        if not exp_path.is_file():
            raise ValueError(f"{path}: experiment spec does not exist: {name}")
        configs = exp.get("configurations", [])
        if not isinstance(configs, list):
            raise ValueError(f"{path}: experiment {name} configurations must be a list")
        for cid in configs:
            cfg_path = root / "specs" / "configurations" / f"{cid}.yaml"
            if not cfg_path.is_file():
                raise ValueError(f"{path}: configuration does not exist: {cid}")
        seen_records.add(f"runs/experiments/{name}.json")

    expected_records = set(results.get("expected_records", []))
    if expected_records != seen_records:
        raise ValueError(
            f"{path}: results.expected_records must match setup.experiments records"
        )


def _load_experiment_spec(name: str, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    return _load_yaml(root / "specs" / "experiments" / f"{name}.yaml")


def _load_experiment_record(name: str, root: Path = PROJECT_ROOT) -> dict[str, Any] | None:
    path = root / "runs" / "experiments" / f"{name}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def summarize_experiment(
    name: str,
    root: Path = PROJECT_ROOT,
    configurations: list[int | str] | None = None,
    minimum_completed_reps: int | None = None,
) -> dict[str, Any]:
    exp_spec = _load_experiment_spec(name, root=root)
    if configurations is None:
        expected_configs = [str(c["id"]) for c in exp_spec.get("configurations", [])]
    else:
        expected_configs = [str(c) for c in configurations]
    expected_reps = int(
        minimum_completed_reps
        if minimum_completed_reps is not None
        else exp_spec.get("reps") or 0
    )
    record = _load_experiment_record(name, root=root)
    summary: dict[str, Any] = {
        "name": name,
        "spec_path": f"specs/experiments/{name}.yaml",
        "record_path": f"runs/experiments/{name}.json",
        "spec_exists": True,
        "record_exists": record is not None,
        "expected_reps": expected_reps,
        "configurations": {},
    }

    completed_total = 0
    failed_total = 0
    for cid in expected_configs:
        summary["configurations"][cid] = {
            "completed_runs": 0,
            "failed_runs": 0,
            "missing_summaries": 0,
            "run_ids": [],
        }

    if record is None:
        summary["execution_status"] = "untested"
        return summary

    for run in record.get("runs", []):
        cid = str(run.get("config"))
        if cid not in summary["configurations"]:
            continue
        cell = summary["configurations"][cid]
        if run.get("exit_code") == 0:
            run_id = run.get("run_id")
            completed_total += 1
            cell["completed_runs"] += 1
            cell["run_ids"].append(run_id)
            summary_path = root / "runs" / cid / str(run_id) / "summary.json"
            if not summary_path.is_file():
                cell["missing_summaries"] += 1
        else:
            failed_total += 1
            cell["failed_runs"] += 1

    expected_total = expected_reps * len(expected_configs)
    configs_ready = all(
        cell["completed_runs"] >= expected_reps and cell["missing_summaries"] == 0
        for cell in summary["configurations"].values()
    )
    allow_failed_attempts = minimum_completed_reps is not None
    if expected_total and configs_ready and (failed_total == 0 or allow_failed_attempts):
        status = "analyzable"
    elif completed_total > 0 or failed_total > 0:
        status = "incomplete"
    else:
        status = "untested"
    summary["completed_runs"] = completed_total
    summary["failed_runs"] = failed_total
    summary["expected_runs"] = expected_total
    summary["execution_status"] = status
    return summary


def build_registry(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    entries = []
    for spec in load_hypothesis_specs(root=root):
        experiments = [
            summarize_experiment(
                exp["name"],
                root=root,
                configurations=exp.get("configurations"),
                minimum_completed_reps=exp.get("minimum_completed_reps"),
            )
            for exp in spec["setup"]["experiments"]
        ]
        execution_statuses = {exp["execution_status"] for exp in experiments}
        if not experiments:
            execution_status = "untested"
        elif execution_statuses == {"analyzable"}:
            execution_status = "analyzable"
        elif "incomplete" in execution_statuses or "analyzable" in execution_statuses:
            execution_status = "incomplete"
        else:
            execution_status = "untested"

        entries.append({
            "id": spec["id"],
            "slug": spec["slug"],
            "title": spec["title"],
            "claim": spec["claim"],
            "declared_status": spec["status"],
            "execution_status": execution_status,
            "source": spec["source"],
            "page": spec.get("page"),
            "verifier": spec.get("verifier"),
            "prediction": spec["prediction"],
            "verdict_rule": spec["verdict_rule"],
            "setup": spec["setup"],
            "results": spec["results"],
            "execution": {
                "experiments": experiments,
            },
            "conclusions": spec["conclusions"],
            "spec_path": spec["_path"],
        })

    return {
        "schema_version": 1,
        "generated_from": "specs/hypotheses/*.yaml",
        "hypotheses": entries,
    }


def write_registry(out_path: Path, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    registry = build_registry(root=root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(registry, indent=2) + "\n")
    return registry
