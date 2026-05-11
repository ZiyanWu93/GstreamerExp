# Figure style principles

These are the project rules for making result figures feel closer to
SIGCOMM/NSDI systems-paper figures. They are not venue requirements;
they are local conventions for readable experiment evidence.

## Canvas

- Prefer paper ratios over dashboard ratios.
- Single-result summaries should be compact but not thumbnail-like:
  roughly 2:1 to 2.3:1 width to height, with enough bottom margin for
  group labels and ratio annotations.
- Use Matplotlib-generated SVG for paper-bound quantitative figures
  unless an interactive/web-native plot is explicitly needed.
- Multi-panel time-series figures should be wide and shallow, usually
  double-column compact rather than full-page dashboard width.
- Avoid tall explanatory figures unless the vertical dimension carries
  new information.

## Text

- Captions carry the prose. The plot should carry axes, units, labels,
  and at most one short result annotation.
- Keep plot titles short: metric name, trace name, or panel label.
- Use 8-11 pt equivalent labels in the SVG. If a label needs a sentence,
  it belongs in the caption or page text.
- Put units on axes, not in surrounding prose.
- Legends must sit in reserved whitespace and must not share a baseline
  with titles, ratio annotations, or panel labels. If a reference line is
  used in only one panel, label it directly instead of expanding the
  global legend.

## Comparison

- When the claim is A vs B, put A and B in the same coordinate system.
- Use a stable color mapping across all figures: SCReAM is blue, GCC is
  orange, shared capacity/reference lines are gray or black.
- Show the comparison ratio directly when it is the result the reader
  should remember. Use a short label such as `S/G=2.74x`.
- Do not make the reader infer the claim from two separate figures when
  one grouped or two-panel figure can show the contrast.

## Uncertainty

- Bars show means only when the individual repetitions or spread are
  also visible.
- With three repetitions, show dots for each run and whiskers for
  min-max. With many repetitions, prefer median plus percentile band.
- Never hide the sample count; put it in the caption or a small plot
  note.

## Time Series

- For one trace, use a two-panel wide layout when comparing mechanism
  and outcome: sender behavior on the left, delivered result on the
  right.
- Keep the trace fixed within a figure. If the trace changes, use a new
  figure or clearly separated panels.
- Reference lines should be visually lighter than algorithm results.

## Web Pages

- Do not wrap paper-style figures in decorative cards or rounded image
  borders. Let the figure whitespace and caption do the work.
- The web page can explain how to read the plot, but the SVG should
  remain exportable into a paper draft without redesign.
