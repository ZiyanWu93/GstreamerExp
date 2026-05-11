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
content, not before it.

### 1.6 Use standard empirical-research labels
Prefer familiar labels over project-specific coinages:
**Claim**, **Prediction**, **Experimental Setup**, **Results**,
**Supporting Evidence**, **Findings**, and **Limitations**. Use
**Threats to Validity** only for measurement-design risks that could
invalidate a result, such as confounding, instrumentation error, or
generalization beyond the tested population. Ordinary limits on what a
claim covers are **Limitations**.

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

### 5.1 The page is a single self-contained HTML file
Lives at `analysis/hypotheses/<hid>.html`. Plotly via CDN. Reads
JSON snapshots from sibling files. Snapshots are emitted by a sibling
Python script. No build step beyond `python3 -m http.server`.

### 5.2 The verifier and the page share the snapshot
The CLI verifier and the page consume the same JSON snapshot, so
numbers agree by construction. If they disagree, the snapshot is
wrong, not the page.

### 5.3 New tests don't require new experiments when the data is already there
Add a new aggregation to the snapshot script. Re-runs only when a
new metric requires a new probe in the pipeline.
