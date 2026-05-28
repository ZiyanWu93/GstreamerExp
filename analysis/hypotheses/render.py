"""Render hypothesis pages (HTML + Jupyter notebook) from the canonical
YAML spec at specs/hypotheses/<slug>.yaml plus the structured report JSON
at analysis/hypotheses/results/<slug>_report.json.

Single source of truth: the YAML spec. Numeric tables come from the report
JSON. Figures are SVG paths already on disk under results/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import nbformat
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text()) or {}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _fmt_cell(value: Any) -> tuple[str, bool]:
    if isinstance(value, bool):
        return ("yes" if value else "no", False)
    if isinstance(value, int):
        return (f"{value:,}", True)
    if isinstance(value, float):
        if abs(value) >= 100:
            return (f"{value:,.1f}", True)
        if abs(value) >= 1:
            return (f"{value:.2f}", True)
        return (f"{value:.3f}", True)
    if value is None:
        return ("", False)
    return (str(value), False)


def _column_is_renderable(col: str) -> bool:
    """Hide raw per-rep arrays from the rendered tables; keep them in the JSON."""
    if col.endswith("_values"):
        return False
    if col.endswith("_values_kbps") or col.endswith("_values_ms"):
        return False
    if col == "runs":
        return False
    return True


def _flatten_table(table_name: str, rows: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    """Convert a list-of-dicts (or dict-of-rows) into a (columns, rows) table.

    Numeric cells are tagged so the template can right-align them. Per-rep
    raw-value columns are filtered out — they remain available in the
    underlying report JSON.
    """
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return {"title": table_name, "columns": [], "rows": []}
    columns = [col for col in rows[0].keys() if _column_is_renderable(col)]
    out_rows = []
    for row in rows:
        cells = []
        for col in columns:
            display, is_num = _fmt_cell(row.get(col))
            cells.append({"value": display, "numeric": is_num})
        out_rows.append(cells)
    return {"title": table_name, "columns": columns, "rows": out_rows}


def _figure_descriptors(report: dict[str, Any]) -> list[dict[str, str]]:
    figures = []
    for fig in report.get("figures", []) or []:
        path = fig.get("path") or ""
        src = "results/" + Path(path).name
        figures.append({"src": src, "caption": fig.get("caption", "")})
    return figures


def _table_descriptors(report: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name, rows in (report.get("tables") or {}).items():
        out.append(_flatten_table(name, rows))
    return out


def _required_metrics(spec: dict[str, Any]) -> list[str]:
    return list((spec.get("setup") or {}).get("required_metrics") or [])


def _experiment_blocks(report: dict[str, Any]) -> list[dict[str, Any]]:
    return list(report.get("experiments") or [])


def _trace_characteristics(report: dict[str, Any]) -> list[dict[str, Any]]:
    return list(report.get("trace_characteristics") or [])


def _claim_structure(report: dict[str, Any]) -> tuple[str | None, list[str]]:
    cs = report.get("claim_structure") or {}
    return cs.get("conclusion"), list(cs.get("subclaims") or [])


def _conclusions(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    conclusions = spec.get("conclusions") or {}
    return (
        list(conclusions.get("established_claims") or []),
        list(conclusions.get("limits") or []),
    )


def render_html(spec_path: Path, report_path: Path) -> str:
    spec = _load_yaml(spec_path)
    report = _load_json(report_path)
    mechanism, subclaims = _claim_structure(report)
    established, limits = _conclusions(spec)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        keep_trailing_newline=True,
    )
    template = env.get_template("page.html.j2")
    return template.render(
        id=spec.get("id"),
        slug=spec.get("slug"),
        title=spec.get("title"),
        status=spec.get("status"),
        source=spec.get("source"),
        claim=spec.get("claim"),
        prediction=spec.get("prediction"),
        verdict_rule=spec.get("verdict_rule"),
        verifier=spec.get("verifier"),
        mechanism=mechanism,
        supporting_subclaims=subclaims,
        established_claims=established,
        limits=limits,
        figures=_figure_descriptors(report),
        tables=_table_descriptors(report),
        trace_characteristics=_trace_characteristics(report),
        experiments=_experiment_blocks(report),
        required_metrics=_required_metrics(spec),
        glossary=spec.get("glossary") or [],
    )


def _md_cell(text: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_markdown_cell(text.strip() + "\n")


def _md_table(columns: list[str], rows: list[list[dict[str, Any]]]) -> str:
    if not columns:
        return ""
    head = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body_lines = []
    for row in rows:
        body_lines.append("| " + " | ".join(cell["value"] for cell in row) + " |")
    return "\n".join([head, sep, *body_lines])


def render_notebook(spec_path: Path, report_path: Path) -> nbformat.NotebookNode:
    spec = _load_yaml(spec_path)
    report = _load_json(report_path)
    mechanism, subclaims = _claim_structure(report)
    established, limits = _conclusions(spec)

    nb = nbformat.v4.new_notebook()
    nb.metadata.update({
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {"name": "python"},
    })

    cells: list[nbformat.NotebookNode] = []

    cells.append(_md_cell(
        f"[← GstreamerExp hub](../../index.html) · "
        f"[README](../../README.md) · "
        f"[Hypothesis catalog](../../docs/HYPOTHESES.md)\n\n"
        f"# {spec.get('id')} — {spec.get('title')}\n\n"
        f"**Status:** `{spec.get('status')}` · "
        f"**Source:** {spec.get('source')}"
    ))

    cells.append(_md_cell(
        f"## Claim\n\n{spec.get('claim', '')}\n\n"
        f"## Prediction\n\n{spec.get('prediction', '')}\n\n"
        f"## Verdict rule\n\n{spec.get('verdict_rule', '')}"
    ))

    if mechanism:
        cells.append(_md_cell(f"## Main claim\n\n{mechanism}"))

    if subclaims:
        body = "## Supporting evidence\n\n" + "\n".join(f"- {item}" for item in subclaims)
        cells.append(_md_cell(body))

    trace = _trace_characteristics(report)
    if trace:
        body = "## Trace Characteristics\n\n" + "\n".join(f"- {entry.get('sentence', '')}" for entry in trace)
        cells.append(_md_cell(body))

    if established or limits:
        parts = ["## Findings and Limitations"]
        if established:
            parts.append("")
            parts.append("**Findings**")
            parts.append("")
            parts.extend(f"- {item}" for item in established)
        if limits:
            parts.append("")
            parts.append("**Limitations**")
            parts.append("")
            parts.extend(f"- {item}" for item in limits)
        cells.append(_md_cell("\n".join(parts)))

    figures = _figure_descriptors(report)
    if figures:
        parts = ["## Figures"]
        for fig in figures:
            parts.append("")
            parts.append(f"![{fig['caption']}]({fig['src']})")
            parts.append("")
            parts.append(f"*{fig['caption']}*")
        cells.append(_md_cell("\n".join(parts)))

    tables = _table_descriptors(report)
    if tables:
        parts = ["## Tables"]
        for tbl in tables:
            parts.append("")
            parts.append(f"### `{tbl['title']}`")
            parts.append("")
            parts.append(_md_table(tbl["columns"], tbl["rows"]))
        cells.append(_md_cell("\n".join(parts)))

    setup_parts = ["## Experimental setup"]
    for exp in _experiment_blocks(report):
        setup_parts.append("")
        setup_parts.append(f"### `{exp.get('name')}`")
        if exp.get("description"):
            setup_parts.append("")
            setup_parts.append(exp["description"])
        arms = ", ".join(
            f"`{arm.get('config_id')}` ({arm.get('algorithm')})"
            for arm in exp.get("arms", [])
        )
        meta_bits = [f"**Configurations:** {arms}"]
        if exp.get("reps"):
            meta_bits.append(f"**Reps:** {exp['reps']}")
        setup_parts.append("")
        setup_parts.append(" · ".join(meta_bits))
        setup_parts.append("")
        setup_parts.append(
            f"Spec: `{exp.get('spec_path')}` · "
            f"Record: `{exp.get('record_path')}` · "
            f"Run: `{exp.get('command')}`"
        )
        status = exp.get("record_status")
        if status:
            setup_parts.append("")
            setup_parts.append(
                f"**Status:** {status.get('completed_runs')} of "
                f"{status.get('total_runs')} runs completed."
            )
    cells.append(_md_cell("\n".join(setup_parts)))

    metrics = _required_metrics(spec)
    if metrics:
        body = "## Required metrics\n\n" + "\n".join(f"- `{m}`" for m in metrics)
        cells.append(_md_cell(body))

    glossary = spec.get("glossary") or []
    if glossary:
        body = "## Glossary\n\n" + "\n\n".join(
            f"**{g.get('term')}** — {g.get('definition')}" for g in glossary)
        cells.append(_md_cell(body))

    cells.append(_md_cell(
        "## Reproducibility\n\n"
        f"This notebook is generated from `specs/hypotheses/{spec.get('slug')}.yaml` "
        f"and `analysis/hypotheses/results/{spec.get('slug')}_report.json`. To regenerate:\n\n"
        "```sh\n"
        "python3 analysis/hypotheses/build_reports.py\n"
        "python3 analysis/hypotheses/build_pages.py\n"
        f"python3 {spec.get('verifier')}\n"
        "```\n\n"
        f"Source: {spec.get('source')}"
    ))

    nb["cells"] = cells
    return nb


def write_outputs(slug: str, out_dir: Path | None = None) -> tuple[Path, Path]:
    """Render and write h{slug}.html and h{slug}.ipynb. Returns the two paths."""
    spec_path = PROJECT_ROOT / "specs" / "hypotheses" / f"{slug}.yaml"
    report_path = RESULTS_DIR / f"{slug}_report.json"
    pages_dir = out_dir if out_dir is not None else Path(__file__).resolve().parent

    html_path = pages_dir / f"{slug}.html"
    nb_path = pages_dir / f"{slug}.ipynb"

    html_path.write_text(render_html(spec_path, report_path))
    nbformat.write(render_notebook(spec_path, report_path), str(nb_path))
    return html_path, nb_path
