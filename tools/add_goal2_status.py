"""Add a '7.3 Status - by axis' scoreboard to Goal 2 (mirroring Goal 1's
6.3 Status), and renumber the five characterization axes to 7.4-7.8.
Idempotent-ish: asserts each anchor is found exactly once."""
import pathlib

p = pathlib.Path("index.html")
t = p.read_text()

STATUS = '''<h3 id="goal2-status">7.3 Status — by axis</h3>

<p>
  Where each characterization axis stands. The five axes mirror §7.4–§7.8
  below. <span class="small">"Runnable now" means the sweep needs only
  <code>tc</code>/<code>netem</code> and the existing framework; "blocked"
  means it waits on a Goal 1 capability (§6).</span>
</p>

<div class="feature-block" id="status-knobs">
  <h3>Knob sensitivity <span class="small">→ <a href="#roadmap-goal2-knobs">§7.4</a></span></h3>
  <ul>
    <li class="done">Queue-delay-target swept across three networks at two bitrate ceilings (<a href="#hypo-h6">H6</a>, <a href="#hypo-h7">H7</a>)</li>
    <li class="todo">Loss-backoff multiplier — not isolated · <strong>runnable now</strong></li>
    <li class="todo">Bitrate bounds (init / min / max) sensitivity surface — not mapped · <strong>runnable now</strong></li>
    <li class="todo">ECN / L4S — never run · may need GStreamer ≥ 1.20</li>
    <li class="todo">Pacing and the ~10 remaining SCReAM knobs — at default, untouched · <strong>runnable now</strong></li>
  </ul>
</div>

<div class="feature-block" id="status-regimes">
  <h3>Network conditions <span class="small">→ <a href="#roadmap-goal2-regimes">§7.5</a></span></h3>
  <ul>
    <li class="done">Link capacity sampled via 5G slices (<a href="#hypo-h1">H1</a>, <a href="#hypo-h4">H4</a>); workload-validity guardrail (<a href="#hypo-h3">H3</a>)</li>
    <li class="todo">RTT — not yet a clean independent axis · <strong>runnable now</strong></li>
    <li class="todo">Loss pattern — no matched-mean comparison (iid vs Gilbert-Elliott) · <strong>runnable now</strong></li>
    <li class="todo">Jitter — not isolated · <strong>runnable now</strong></li>
    <li class="todo">Workload — flagged by H3, not yet a swept axis · <strong>runnable now</strong></li>
  </ul>
</div>

<div class="feature-block" id="status-mechanism">
  <h3>Mechanism contributions <span class="small">→ <a href="#roadmap-goal2-mechanism">§7.6</a></span></h3>
  <ul>
    <li class="done">Controller → encoder coupling gap isolated at UMN (<a href="#hypo-h5">H5</a>)</li>
    <li class="todo">Pacing vs encoder-rate split — needs a gstscream flag to disable pacing</li>
    <li class="todo">Coupling-fidelity curve — needs an instrumented delay / drop knob on the bitrate-notify path</li>
    <li class="todo">Feedback cadence (RTCP interval) sweep · <strong>runnable now</strong></li>
  </ul>
</div>

<div class="feature-block" id="status-interactions">
  <h3>Interactions <span class="small">→ <a href="#roadmap-goal2-interactions">§7.7</a></span></h3>
  <ul>
    <li class="done">Recovery does not change the SCReAM-vs-GCC winner on a fluctuating link (<a href="#hypo-h2">H2</a>)</li>
    <li class="todo">SCReAM × recovery cross-product (NACK/RTX × PLI × FEC) · <strong>runnable now</strong></li>
    <li class="todo">SCReAM × codec (VP8 vs H.264) — <strong>blocked on</strong> the <a href="#contract-encoder">§6.1 Encoder</a> contract</li>
    <li class="todo">SCReAM × multi-stream — <strong>blocked on</strong> the multi-stream refactor</li>
  </ul>
</div>

<div class="feature-block" id="status-boundary">
  <h3>Boundary behaviour <span class="small">→ <a href="#roadmap-goal2-boundary">§7.8</a></span></h3>
  <ul>
    <li class="todo">RTT degeneration threshold — none run · <strong>runnable now</strong></li>
    <li class="todo">Loss-rate degeneration threshold — none run · <strong>runnable now</strong></li>
    <li class="todo">Jitter degeneration threshold — none run · <strong>runnable now</strong></li>
  </ul>
</div>

'''

# 1) insert the status block immediately before the first axis (knobs)
knobs = '<h3 id="roadmap-goal2-knobs">'
assert t.count(knobs) == 1, knobs
t = t.replace(knobs, STATUS + knobs, 1)

# 2) renumber the five axes 7.3-7.7 -> 7.4-7.8
renum = {
 '<h3 id="roadmap-goal2-knobs">7.3 Knob sensitivity</h3>':
 '<h3 id="roadmap-goal2-knobs">7.4 Knob sensitivity</h3>',
 '<h3 id="roadmap-goal2-regimes">7.4 Network conditions</h3>':
 '<h3 id="roadmap-goal2-regimes">7.5 Network conditions</h3>',
 '<h3 id="roadmap-goal2-mechanism">7.5 Mechanism contributions</h3>':
 '<h3 id="roadmap-goal2-mechanism">7.6 Mechanism contributions</h3>',
 '<h3 id="roadmap-goal2-interactions">7.6 Interactions with other features</h3>':
 '<h3 id="roadmap-goal2-interactions">7.7 Interactions with other features</h3>',
 '<h3 id="roadmap-goal2-boundary">7.7 Boundary behaviour</h3>':
 '<h3 id="roadmap-goal2-boundary">7.8 Boundary behaviour</h3>',
}
for a, b in renum.items():
    assert t.count(a) == 1, a
    t = t.replace(a, b, 1)

# 3) update the Goal 2 lead-paragraph cross-refs (§7.3-§7.7 shift to §7.4-§7.8)
lead = {
 '<strong>knob sensitivity</strong> (§7.3 → H6, H7),':
 '<strong>status by axis</strong> (§7.3), then <strong>knob sensitivity</strong> (§7.4 → H6, H7),',
 '<strong>network conditions</strong> (§7.4 → H1, H3, H4),':
 '<strong>network conditions</strong> (§7.5 → H1, H3, H4),',
 '<strong>mechanism</strong> (§7.5 → H5),':
 '<strong>mechanism</strong> (§7.6 → H5),',
 '<strong>interactions</strong> (§7.6 → H2), and':
 '<strong>interactions</strong> (§7.7 → H2), and',
 '<strong>boundary behaviour</strong> (§7.7, not yet tested).':
 '<strong>boundary behaviour</strong> (§7.8, not yet tested).',
}
for a, b in lead.items():
    assert t.count(a) == 1, ("LEAD", a)
    t = t.replace(a, b, 1)

# 4) sidebar: add a 'Status - by axis' sub under Goal 2, after Configuration space
side = '<li><a class="sub" href="#roadmap-goal2-config">Configuration space</a></li>'
assert t.count(side) == 1, side
t = t.replace(side, side + '\n        <li><a class="sub" href="#goal2-status">Status — by axis</a></li>', 1)

p.write_text(t)
print("done. sections:", t.count('<section class="page-section"'),
      "closes:", t.count('</section>'))
