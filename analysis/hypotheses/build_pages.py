"""Generate per-hypothesis HTML pages + Jupyter notebooks from the
canonical YAML spec at specs/hypotheses/<slug>.yaml.

Reads:
  specs/hypotheses/*.yaml
  analysis/hypotheses/results/<slug>_report.json   (produced by build_reports.py)
  analysis/hypotheses/results/*.svg                (produced by build_reports.py)

Writes:
  analysis/hypotheses/<slug>.html
  analysis/hypotheses/<slug>.ipynb
  analysis/hypotheses/results/index.json           (via build_registry)
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import render, framework  # noqa: E402


def main() -> None:
    specs_dir = PROJECT_ROOT / "specs" / "hypotheses"
    slugs = sorted(p.stem for p in specs_dir.glob("h*.yaml"))
    if not slugs:
        print("No hypothesis specs found under specs/hypotheses/", file=sys.stderr)
        sys.exit(1)

    for slug in slugs:
        html_path, nb_path = render.write_outputs(slug, out_dir=HERE)
        rel_html = html_path.relative_to(PROJECT_ROOT)
        rel_nb = nb_path.relative_to(PROJECT_ROOT)
        print(f"{slug}: wrote {rel_html} and {rel_nb}")

    registry_path = HERE / "results" / "index.json"
    framework.write_registry(registry_path, PROJECT_ROOT)
    print(f"registry: wrote {registry_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
