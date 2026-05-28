# Presentation principles for hypothesis result pages

These rules guide every active hypothesis page in this directory. The
goal: a page where a cold reader can land
anywhere and understand it — no assumed familiarity with the
literature, no jargon without a referent, no buried conclusions.

---

## 1. Page structure

### 1.1 Lead with the main result
The first thing on the page is what was found, not what was tested.
A reader skimming should know the answer in five seconds.

### 1.2 Order claims: main result → mechanism → caveat
If the page reframes or partially refutes a literature claim, lead
with what's true, follow with why, end with the caveat. Caveats go
last; they should not be the reader's first impression.

### 1.3 Per-section order
Inside every panel and every plot block:
**result → setup → claim being tested → plot → how to read → numerical detail.**
The plot comes before the how-to-read so the reader sees the picture
immediately.

### 1.4 Each section is self-contained
Headings, callouts, and captions are intelligible if the reader
scrolled directly to them. No orphan jargon ("supported," "refuted")
that depends on context elsewhere.

### 1.5 Glossary at the end
Definitions are reference material, not narrative. They go after the
content, not before it. The spec supports a `glossary:` list (each entry
is `term` + `definition`); the template renders it as the final content
section (after Required metrics, before Reproducibility). Every page that
uses a domain term should populate it — see §1.7.

### 1.6 Use standard empirical-research labels
Prefer familiar labels over project-specific coinages:
**Claim**, **Prediction**, **Experimental Setup**, **Results**,
**Supporting Evidence**, **Findings**, and **Limitations**. Use
**Threats to Validity** only for measurement-design risks that could
invalidate a result, such as confounding, instrumentation error, or
generalization beyond the tested population. Ordinary limits on what a
claim covers are **Limitations**.

### 1.7 Define every domain term — inline on first use; glossary is the backstop
A cold reader must never hit a term they can't resolve on the page, and
should rarely have to *leave the main thread* to resolve one. So the
default is to define a term the first time it appears, inline:

- A short parenthetical — "utilization (the share of available bandwidth
  actually used)".
- Or, better, replace the jargon with plain words and introduce the
  coined term afterward — describe the behavior ("the encoder keeps
  sending at the same rate no matter what the controller decides"), then
  name it ("a controller-to-encoder gap"). The reader understands the
  thing before they have to carry the label.

The end glossary (§1.5) is a **backstop**, not the primary mechanism:
use it for terms that recur many times or need a longer definition than
fits inline. A page where the reader must scroll to the glossary to
follow the opening argument has front-loaded its jargon and deferred its
meaning — fix the prose, don't lean on the glossary.

This is the principle an author close to the work violates most easily,
because the jargon feels self-evident. A concrete failure we hit: an H5
draft used "controller-to-encoder command-following gap," "utilization,"
"overshoot," "delay signal," "network_time_sync," and "x0.33" with no
definition anywhere — each obvious to the author, opaque to everyone
else.

Before publishing, list every noun a non-specialist wouldn't know and
confirm each has a referent on the page. The usual offenders:
- **Project coinages** — a named "gap," a multi-letter mechanism. Define
  what it *is*, not just that it exists.
- **Metric names** — utilization, overshoot, goodput. State what the
  number measures and which direction is good.
- **Bare acronyms** — HO, RB, CQI, NACK, PLI, RTP, tc. Expand on first
  use or in the glossary.

---

## 2. Writing the conclusion

### 2.1 Lead with a unifying thesis
Every conclusion section opens with one sentence stating the property
the bullets collectively support. Without it the bullets read as
disconnected facts.

### 2.2 Describe the phenomenon, not the figure
"GCC pushes harder" — not "GCC's box sits higher." Conclusions must
be intelligible without seeing the figure.

### 2.3 Conclusions stay qualitative; numbers go in a "numerical detail" block
The headline says what happened. Numbers (percentages, ratios, σ)
verify it but should not carry it.

### 2.4 Make scope explicit in the claim itself
If a measurement is restricted to a window or phase, name that scope
in the claim. "Inside the bottleneck," "across the full run." Metrics
inherit their scope; conclusions should too.

### 2.5 Declare positively, not defensively
"X stays higher than Y" — not "X is not universally lower than Y."
Don't phrase findings as counters to claims the reader hasn't yet
read.

### 2.6 Cite literature fully on first reference
Authors, title, venue, year. Restate the claim in plain English.
Don't assume the reader recognizes the citation.

### 2.7 Strip value-laden adjectives
"Conservative," "aggressive," "good," "bad" pre-frame the data with
judgment. Describe behavior, not character.

### 2.8 Bullets, not paragraphs
A reader scanning should absorb the result in two seconds.

### 2.9 Don't number bullets unless their order is fixed
Numbering implies a strict sequence. If you reorder, you renumber.
Drop the numbers and use headings within each bullet instead.

---

## 3. Figure design

### 3.1 Every visual element gets a one-liner
Name in plain English: x-axis (with units), y-axis (with units),
every line color, every shaded region, every reference line. If a
thing exists in the figure, the reader should know what it represents.

### 3.2 Add a "what good looks like" note
What shape signals a healthy or expected outcome? Tell the reader;
don't make them derive it.

### 3.3 Median + percentile band, not per-rep spaghetti
With many runs, draw one bold median line per arm with a lighter
band for the 10th–90th percentile spread. Multiple thin overlapping
lines are visual noise.

### 3.4 Shade where the result lives
If the conclusion is about a specific time window, tint that window.
The reader's eye lands there without reading the legend.

### 3.5 Color-code callouts redundantly
Green = matches the claim being tested; orange = diverges; blue =
neutral / overview. Color is a redundant cue; the text inside also
says it. Color alone is not the signal.

---

## 4. Cross-section coherence

### 4.1 Compare contrasts side by side
When the finding is a contrast (slow vs fast, A vs B), put both
panels in one image so the contrast is anchored at a glance.

### 4.2 Add a companion measurement when scope is narrow
If a literature claim is scoped to a window or phase, also report
the full-run version. The two views often answer different questions
and rank algorithms differently.

### 4.3 The page-level thesis names the property
Not "X reverses in regime B." Name what determines the outcome.
The reader walks away knowing the property, not just that prior
claims were incomplete.

---

## 5. Mechanics

### 5.1 Pages are generated from a declarative spec
Each page lives at `analysis/hypotheses/<slug>.html` (served locally)
and `analysis/hypotheses/<slug>.ipynb` (browsable on GitHub). Both
come from `specs/hypotheses/<slug>.yaml` plus the report JSON at
`analysis/hypotheses/results/<slug>_report.json` plus the SVG figures
in the same directory. The orchestrator is
`python3 analysis/hypotheses/build_pages.py`; the renderer module is
`analysis/hypotheses/render.py`; the HTML template is
`analysis/hypotheses/templates/page.html.j2`. **Do not hand-edit the
generated `<slug>.html` or `<slug>.ipynb` files** — the next build
will overwrite them. Modify the YAML (for prose), `build_reports.py`
(for figures and tables), or the template (for layout).

### 5.2 The verifier and the page share the report
The CLI verifier and the page consume the same report JSON, so
numbers agree by construction. If they disagree, the report JSON
is wrong, not the page.

### 5.3 The principles in §1–§4 are enforced by the template
Section labels, ordering, and styling live in `page.html.j2`. Adding
a new principle ideally lands as a template change, so every page
picks it up at the next `build_pages.py` run rather than drifting
page by page.

### 5.4 New tests don't require new experiments when the data is already there
Add a new aggregation to `build_reports.py`. Re-runs only when a new
metric requires a new probe in the pipeline.
