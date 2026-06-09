"""One-shot: collapse the index's Goals / Hypotheses / Status sections into
a unified per-goal narrative (Goals overview, Goal 1, Goal 2), moving the
existing content blocks rather than retyping them.

Writes index_new.html; the caller verifies, then swaps.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "index.html"
OUT = ROOT / "index_new.html"
text = SRC.read_text()


def between(start, end, whole=text):
    i = whole.index(start)
    j = whole.index(end, i)
    return whole[i:j]


def card(hid):
    """Extract the <a ...id="hypo-hID"...>...</a> hypothesis card."""
    anchor = f'id="{hid}"'
    i = text.index(anchor)
    start = text.rindex("<a ", 0, i)
    end = text.index("</a>", i) + len("</a>")
    return text[start:end]


# --- region boundaries ---
ROADMAP = '<section class="page-section" data-section="roadmap">'
HYPO = '<section class="page-section" data-section="hypotheses">'
REF = '<section class="page-section" data-section="reference">'

region_start = text.index(ROADMAP)
region_end = text.index(REF)
head = text[:region_start]
tail = text[region_end:]

# --- roadmap sub-blocks (by h3 id markers) ---
B_goals     = between('<h3 id="roadmap-goals">',     '<h3 id="roadmap-contracts">')
B_contracts = between('<h3 id="roadmap-contracts">', '<h3 id="roadmap-phases">')
B_phases    = between('<h3 id="roadmap-phases">',    '<h3 id="roadmap-goal2">')
B_g2intro   = between('<h3 id="roadmap-goal2">',     '<h3 id="roadmap-goal2-config">')
B_g2config  = between('<h3 id="roadmap-goal2-config">',       '<h3 id="roadmap-goal2-knobs">')
B_knobs     = between('<h3 id="roadmap-goal2-knobs">',        '<h3 id="roadmap-goal2-regimes">')
B_regimes   = between('<h3 id="roadmap-goal2-regimes">',      '<h3 id="roadmap-goal2-mechanism">')
B_mech      = between('<h3 id="roadmap-goal2-mechanism">',    '<h3 id="roadmap-goal2-interactions">')
B_inter     = between('<h3 id="roadmap-goal2-interactions">', '<h3 id="roadmap-goal2-boundary">')
B_bound     = between('<h3 id="roadmap-goal2-boundary">',     '<h3 id="roadmap-relate">')
B_relate    = between('<h3 id="roadmap-relate">',    '<h3 id="roadmap-tradeoffs">')
B_tradeoffs = between('<h3 id="roadmap-tradeoffs">', '<h3 id="roadmap-strategic">')
B_strategic = between('<h3 id="roadmap-strategic">', '</section>')

# --- todo (status) sub-blocks ---
B_g1status  = between('<h3 id="todo-goal1">', '<h3 id="todo-goal2">')

# --- hypothesis cards ---
H = {n: card(f"hypo-h{n}") for n in range(1, 8)}


def retitle(block, new_h3):
    """Replace the first <h3 ...>...</h3> text with new_h3 (keep the id)."""
    return re.sub(r'(<h3 id="[^"]+">)[^<]*(</h3>)',
                  lambda m: m.group(1) + new_h3 + m.group(2), block, count=1)


def strip_findings(block):
    """Remove the old 'Findings to date: ...' prose pointer (now redundant
    with the inline evidence cards)."""
    return re.sub(r'<p class="small"><strong>Findings to date:.*?</p>\s*',
                  '', block, flags=re.S)


def evidence(cards_html, note=""):
    note_html = f'<p class="small">{note}</p>' if note else ""
    return ('<h4 style="margin-top:18px;">Evidence</h4>' + note_html
            + '<div class="hypo-grid">' + cards_html + '</div>')


# =========================== compose new sections ===========================

goals = (
    '<section class="page-section" data-section="roadmap">\n'
    '<h2 id="roadmap">5 · Goals</h2>\n'
    '<p>GstreamerExp has two goals pulling together. <strong>Goal 1</strong> '
    '(§6) is an engineering goal: match the capabilities UMN\'s teleop stack '
    'ships, while keeping the standards transport and controller rigour we do '
    'better. <strong>Goal 2</strong> (§7) is a research goal: characterize how '
    'SCReAM behaves across its configuration space. Each goal below is told '
    'end to end — plan, status, and the hypotheses that are the evidence.</p>\n'
    + retitle(B_goals, "5.1 The two goals")
    + retitle(B_relate, "5.2 How the two goals relate")
    + retitle(B_tradeoffs, "5.3 What this drops, what it keeps")
    + retitle(B_strategic, "5.4 Open strategic question — the repo shape")
    + '\n</section>\n'
)

goal1 = (
    '<section class="page-section" data-section="goal1">\n'
    '<h2 id="goal1">6 · Goal 1 — Match UMN\'s stack, keep our advantages</h2>\n'
    '<p>The engineering goal: cover the capabilities UMN\'s Teleop-Gopher-Streamer '
    'ships (real cameras, hardware encode, multi-stream, dual-end recording, an '
    'external control surface), while keeping what we do better (standards '
    'RTP/RTCP/SRTP/DTLS, reference SCReAM and GCC, the full recovery toolbox, '
    'measurement rigour). Below: the plan (capabilities + phasing), the current '
    'status by contract, and the evidence that we already beat UMN where it '
    'counts.</p>\n'
    + retitle(B_contracts, "6.1 Plan — capabilities (the seven contracts)")
    + retitle(B_phases, "6.2 Plan — phasing (P1–P4)")
    + retitle(B_g1status, "6.3 Status — by contract")
    + '<h3 id="goal1-evidence">6.4 Evidence</h3>\n'
    + '<p>The headline result for Goal 1 — our SCReAM is more mature than UMN\'s '
      'reimplementation, and the cause is isolated to a wiring gap, not the '
      'algorithm.</p>\n'
    + '<div class="hypo-grid">' + H[5] + '</div>\n'
    + '</section>\n'
)

goal2 = (
    '<section class="page-section" data-section="goal2">\n'
    '<h2 id="goal2">7 · Goal 2 — Characterize SCReAM</h2>\n'
    + retitle(B_g2intro, "7.1 What we are characterizing")
    + retitle(B_g2config, "7.2 Configuration space")
    + retitle(strip_findings(B_knobs), "7.3 Knob sensitivity")
    + evidence(H[6] + H[7])
    + retitle(strip_findings(B_regimes), "7.4 Network conditions")
    + evidence(H[1] + H[3] + H[4])
    + retitle(strip_findings(B_mech), "7.5 Mechanism contributions")
    + '<h4 style="margin-top:18px;">Evidence</h4>'
    + '<p class="small">The mechanism here is the same controller-to-encoder '
      'coupling isolated in <a href="#hypo-h5">H5</a> — the Goal 1 maturity '
      'result (§6.4). It is the evidence for this axis too.</p>'
    + retitle(strip_findings(B_inter), "7.6 Interactions with other features")
    + evidence(H[2])
    + retitle(B_bound, "7.7 Boundary behaviour")
    + '<p class="small">No hypothesis tested yet on this axis.</p>\n'
    + '</section>\n'
)

new_text = head + goals + '\n' + goal1 + '\n' + goal2 + '\n' + tail
OUT.write_text(new_text)
print(f"wrote {OUT} ({new_text.count(chr(10))} lines)")
print("sections:", new_text.count('<section class="page-section"'),
      "closes:", new_text.count('</section>'))
