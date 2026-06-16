"""Generate the full inline hypothesis content for index.html.

Each hypothesis becomes a first-class block (claim, predictions, verdict,
findings, figures, tables, limitations) styled with index.html's own classes,
grouped under its Goal 2 characterization category. The blocks are written
between per-category markers:

    <!-- INLINE:knobs -->  ... generated ...  <!-- /INLINE:knobs -->

so re-running keeps index.html in sync with the specs/reports. The standalone
analysis/hypotheses/h*.html pages are unchanged (kept for direct links).

    python3 tools/build_inline.py
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "specs" / "hypotheses"
REPORTS = ROOT / "analysis" / "hypotheses" / "results"

# category marker -> ordered hypothesis slugs (the evidence mapping)
CATEGORIES = {
    "knobs":        ["h6", "h7", "h8"],
    "regimes":      ["h1", "h3", "h4"],
    "mechanism":    ["h5"],
    "interactions": ["h2"],
    "boundary":     ["h9", "h10", "h11", "h12"],
}
BADGE = {"supported": "supported", "refuted": "refuted",
         "regime-dependent": "regime", "inconclusive": "inconclusive",
         "untested": "untested", "incomplete": "untested"}
VERDICT_ROWS = [
    ("supported_when_all", "Supported when all"),
    ("refuted_when_any", "Refuted when any"),
    ("inconclusive_when_any", "Inconclusive when any"),
    ("untested_when_any", "Untested when any"),
]


def as_text(x) -> str:
    """A finding written with a ': ' parses as a YAML mapping ({k: v}); rejoin
    it back into the intended sentence so it renders as prose, not a dict."""
    if isinstance(x, dict):
        return "; ".join(f"{k}: {v}" for k, v in x.items())
    return str(x)


def esc(x) -> str:
    return html.escape(as_text(x), quote=True)


def fmt(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:,.1f}"
        if abs(v) >= 1:
            return f"{v:.2f}"
        return f"{v:.3f}"
    if v is None:
        return ""
    return esc(v)


def block(slug: str) -> str:
    spec = yaml.safe_load((SPECS / f"{slug}.yaml").read_text())
    rp = REPORTS / f"{slug}_report.json"
    report = json.loads(rp.read_text()) if rp.is_file() else {}

    hid = spec["id"]
    status = spec.get("status", "untested")
    badge = BADGE.get(status, "untested")
    out = [f'<div class="hypo-full" id="hypo-{slug}">']
    out.append(
        f'<h4 class="hypo-h">{esc(hid)} · {esc(spec.get("title", ""))}'
        f' <span class="badge {badge}">{esc(status)}</span></h4>')
    out.append(
        f'<p class="meta">{esc(spec.get("source", ""))} · '
        f'<a href="analysis/hypotheses/{slug}.html">standalone page →</a></p>')
    out.append(f'<p><strong>Claim.</strong> {esc(spec.get("claim", ""))}</p>')

    preds = spec.get("predictions") or []
    if preds:
        out.append('<p class="lbl">Predictions</p><ul>'
                   + "".join(f"<li><code>{esc(p)}</code></li>" for p in preds) + "</ul>")

    verdict = spec.get("verdict") or {}
    rows = []
    for key, label in VERDICT_ROWS:
        if verdict.get(key):
            cells = "<br>".join(f"<code>{esc(p)}</code>" for p in verdict[key])
            rows.append(f"<tr><td><strong>{label}</strong></td><td>{cells}</td></tr>")
    if rows:
        out.append('<p class="lbl">Verdict</p><table><thead>'
                   '<tr><th>Outcome</th><th>Predicates</th></tr></thead><tbody>'
                   + "".join(rows) + "</tbody></table>")

    conclusions = spec.get("conclusions") or {}
    established = conclusions.get("established") or []
    if established:
        out.append('<p class="lbl">Findings</p><ul>'
                   + "".join(f"<li>{esc(x)}</li>" for x in established) + "</ul>")

    for fig in report.get("figures") or []:
        out.append(f'<figure><img src="{esc(fig["path"])}" alt="" loading="lazy">'
                   f'<figcaption>{esc(fig.get("caption", ""))}</figcaption></figure>')

    for title, trows in (report.get("tables") or {}).items():
        if not trows:
            continue
        cols = list(trows[0].keys())
        thead = "".join(f"<th>{esc(c)}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td>{fmt(r.get(c))}</td>" for c in cols) + "</tr>"
                       for r in trows)
        out.append(f'<h5 class="tbl-h">{esc(title)}</h5>'
                   f'<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>')

    limits = conclusions.get("limits") or []
    if limits:
        out.append('<p class="lbl">Limitations</p><ul class="small">'
                   + "".join(f"<li>{esc(x)}</li>" for x in limits) + "</ul>")

    out.append("</div>")
    return "\n".join(out)


def fill(text: str, marker: str, content: str) -> str:
    pat = re.compile(rf"(<!-- INLINE:{marker} -->).*?(<!-- /INLINE:{marker} -->)", re.S)
    repl = r"\1\n" + content.replace("\\", "\\\\") + r"\n\2"
    new, n = pat.subn(repl, text)
    if n != 1:
        raise SystemExit(f"marker INLINE:{marker} found {n} times (expected 1)")
    return new


def main() -> None:
    idx = ROOT / "index.html"
    t = idx.read_text()
    for cat, slugs in CATEGORIES.items():
        content = "\n".join(block(s) for s in slugs)
        t = fill(t, cat, content)
    idx.write_text(t)
    (ROOT / "features.html").write_text(t)
    total = sum(len(v) for v in CATEGORIES.values())
    print(f"inlined {total} hypotheses into index.html + features.html")


if __name__ == "__main__":
    main()
