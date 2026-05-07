# GStreamer Experiment

Testbed for video congestion control — SCReAM and Google CC (rtpgccbwe
+ TWCC) — built on GStreamer 1.24 with VP8/RTP/UDP. Configurations run
locally over loopback or distributed across two hosts via SSH, with
declarative `tc` impairment driven by named network specs.

## Operating principles

These rules govern what gets added, what gets rejected, and how to
read what's here.

### 1. Pipeline first, experiments second

The project exists to **build this teleoperation pipeline and
characterize its performance under realistic stress**. Particular
comparisons — recovery × loss regime, content type, congestion
controller — are *instruments* used to measure the pipeline, not
destinations the project is trying to reach. New work is judged on
whether it makes the pipeline more capable or its behavior more
visible, not on whether it completes a planned comparison.

### 2. Insights are functions of data, not frozen text

Anything worth claiming about the pipeline ("PLI dominates by N
frames at 300 kbps," "decoded PSNR drops to 10 dB during the
impaired phase, recovers over 6 seconds") must be **regenerable by
running a script against `runs/`**. Conversation-level insights
either get encoded as a script that reproduces them, or they
evaporate. The five-dimension evaluation framework (see
"Evaluation framework" below) is the structural form of this rule
for descriptive measurement: every metric we compute and every
comparison we run lives in the framework, and the framework's code
runs against `runs/` to produce the report.

The same rule for *predictive* claims lives in
[`HYPOTHESES.md`](HYPOTHESES.md) and `analysis/hypotheses/`. Each
hypothesis catalogues a falsifiable prediction (the question, frozen
in prose with its source citation and quantitative threshold), and
each one has a verifier script that derives the current verdict —
supported / refuted / inconclusive / untested — by reading the
relevant experiment record. `python3 analysis/hypotheses/<id>.py`
prints the current status; the markdown never carries it.

### 3. Declarative specs, validated at load time

Every behavior-affecting field lives in a YAML under `specs/` and
passes through `validation.py` before any pipeline starts. Typos at
any nesting level, missing fields, and contradictions between
fields all fail with precise error messages at config load — never
silently mid-run. The four spec layers (videos, networks,
configurations, experiments) compose hierarchically, each adding
one orthogonal axis.

### 4. No silent fallbacks

Required fields are required; absence is rejected, not defaulted.
If the code needs a value, the spec must declare it. The exception
is algorithm-specific knobs whose default is documented by the
algorithm itself (SCReAM's CLI flags, vp8enc's properties); for
those, absence in the spec means "use the algorithm's documented
default," and the validator allows omission. Defaults never live in
this project's code.

### 5. Two implementations make a pattern; one implementation just uses one shape

Don't add a "kind" tag (`algorithm:`, `model:`, `backend:`) to a
schema field until two different things actually exist for it to
tag. One thing doesn't need a tag — it's just that thing. A
vending machine selling one product doesn't need a "select
beverage" interface; the same logic applies to specs.

| field | shape | why |
|---|---|---|
| `congestion_control.algorithm: scream \| gcc` | tagged | two real algorithms with disjoint parameters |
| `source.backend: synthetic \| file` | tagged | two real backends today |
| `loss.model: none \| uniform \| bursty` | tagged | three real loss generators |
| `rate_kbps: 5000` | flat scalar | one rate model today (TBF cap); `model: fixed` everywhere would be ceremony |
| `delay_ms: 100` | flat scalar | one delay model today; same reason |

When a second variant lands (e.g. a `rate.model: variable_trace`
that replays a real bandwidth log), the migration is a 10-minute
rewrite of the four or five specs that use the field — same cost
as adopting the tag pre-emptively, paid only when there's a real
reason. Pre-emptive flexibility loses both ways: every spec
carries dead ceremony today, AND the guessed shape is usually
wrong when the real second variant arrives, so you rewrite anyway.

Bolting a flag onto a flat field to encode the second variant
(`loss_burst: true` on top of `loss_pct: 2`) is the same mistake
in disguise — it's a tag pretending to be a flag. When two real
variants exist, the field gets the proper tagged shape; when one
exists, it stays flat.

### 6. Cleanup is structural, not customary

Every allocation that changes state outside the process — `tc`
qdiscs, remote workers, RTP sockets, file fixtures — registers its
teardown atomically at the moment of allocation (`ExitStack`
pattern in `runner.py`). The `finally` block is gone; cleanup runs
in LIFO order on every exit path, and each step is best-effort so
one failure can't skip the rest. State leaks (stranded `tc`
qdiscs, orphaned worker processes) are bugs in the cleanup
structure, not in operator hygiene.

### 7. Reproducibility over convenience

Specs are in git; results live in `runs/`. The same code on the
same hosts with the same spec produces the same record. Knobs are
explicit (no env-var magic, no auto-tuning, no defaults outside
the spec). `experiment.py` interleaves runs round-robin so
cross-traffic noise distributes evenly across configurations
rather than biasing one arm. Stochastic elements (Gilbert-Elliott
loss, jitter) are honest about their variance and verified
empirically by `tools/network_probe.py`.

## Evaluation framework

A teleoperation video pipeline's performance is fully described by
**five dimensions**. They're irreducible — each captures a property
that the others can't see — and exhaustive — every measurement we
make, every research question we ask, fits in exactly one. The
documentation here, the metric tags in `metrics.py`, and the modules
in `analysis/dimensions/` share the same five-element skeleton. New
measurements that don't belong anywhere either fit into one of the
five (where they're justified) or get rejected (where they're noise).

| Dimension       | Question it answers                                | Metrics that serve it                          |
|-----------------|----------------------------------------------------|------------------------------------------------|
| **throughput**  | Did the data get there, and at what byte cost?     | `frame_count`, `wire_bytes`, `encoded_bitrate` |
| **quality**     | How visually intelligible was what arrived?        | `decoded_psnr`, `decoder_errors`               |
| **latency**     | How fresh was what arrived?                        | `frame_latency`                                |
| **adaptation**  | How well did the controller match output to capacity? | `encoder_target_kbps` × the network spec      |
| **stability**   | How predictably did the pipeline behave over time? | derived from the above (variance, jitter)     |

Each `Metric` subclass declares its dimension via a class attribute,
so the analysis layer reads `Metric.dimension` rather than
hardcoding metric names. Adding a metric without a dimension is a
structural bug; adding a dimension without a corresponding analysis
module is the same.

### What "good" and "bad" look like, per dimension

- **Throughput**: high delivery ratio (frames received / sent),
  on-wire byte rate close to encoded rate (low overhead). Bad: low
  delivery, large gap between encoded and on-wire (excessive
  retransmission or FEC overhead).
- **Quality**: high mean PSNR with a tight distribution; small p10
  (the worst frames) close to the median. Bad: high mean but heavy
  tail (occasional severely-corrupted frames the average hides).
- **Latency**: low p99 and max relative to median. Bad: median is
  fine but max is multi-second (rare freeze events).
- **Adaptation**: utilization near 100% during steady phases (uses
  the available bandwidth), settling time short after transitions
  (reacts quickly to network change), small overshoot. Bad:
  chronic over-shoot during low-capacity phases (drives more loss),
  long settling after recovery (under-utilizes bandwidth).
- **Stability**: low coefficient of variation in encoder rate
  within steady phases, low PSNR jitter, run-to-run reps cluster
  tightly. Bad: same nominal mean but high run-to-run variance
  (pipeline isn't predictable; results are sensitive to noise).

### Running the evaluation

```sh
python3 analysis/evaluate.py <config-id>           # most recent run of that config
python3 analysis/evaluate.py <config-id> <ts>      # specific run
python3 analysis/evaluate.py <experiment-name>     # whole experiment (per-arm)
```

`evaluate.py` is a small driver — it loops over the dimension
modules in `analysis/dimensions/` and renders each one's section.
The driver doesn't know what each dimension measures; that lives
in the dimension module's docstring. A new dimension would land
as a new module in that directory plus an addition to
`DIMENSION_MODULES` in `analysis/dimensions/__init__.py`. The
five sections in the report mirror the five entries in the table
above one-to-one.

## Run a configuration

```sh
python3 cli.py --list           # available configurations
python3 cli.py 8                # run configuration 8
```

A configuration is a YAML at `specs/configurations/<id>.yaml` that
declares the pipeline once at the top level — codec, encoder, transport,
congestion_control, sink — instead of mirroring sender/receiver halves.
It may reference a named video spec (`video: <name>`) for the source
block and a named network spec (`network: <name>`) for impairment hooks.

`cli.py` validates the document strictly (every behavior-affecting field
must be set; typos in any block are caught at load time), projects the
unified spec into per-role specs for the worker, runs the typed
`pre_run` / `during_run` / `post_run` hooks, and writes a merged
`summary.json` to `runs/<id>/<timestamp>/`.

## Run an expo (visual demo)

```sh
python3 expo.py --list
python3 expo.py scream-720p-fluctuation
```

An expo spec lives at `specs/expos/<name>.yaml` and pairs one configuration
with display info. The runner adds a `tee + autovideosink` branch to the
receiver pipeline alongside the measurement sink, so the run produces the
same `summary.json` as `cli.py <id>` does *and* renders the decoded video
on the configured display. Visual rendering never affects experiments
(`experiment.py` has no expo equivalent — experiments are headless by
construction).

## Run an experiment

```sh
python3 experiment.py --list             # available experiment specs
python3 experiment.py scream-vs-gcc-720p
python3 plot.py --latest
```

An experiment is `specs/experiments/<name>.yaml` and names a comparison:
which configurations to pair, how many reps each, how to label and color
them in the plot, and which paths in the configurations are *allowed*
to differ (`varies`). The validator deep-compares the listed
configurations and rejects the experiment if any path outside `varies`
diverges between them — so a pair of configurations claimed to compare
"SCReAM vs GCC" can't silently differ in encoder bitrate or any other
field that would invalidate the comparison.

`experiment.py` interleaves runs round-robin so cross-traffic noise
distributes evenly across configurations, and writes the experiment
record (spec contents + completed runs) to `runs/experiments/<name>.json`.
`plot.py` reads that record and renders bitrate / latency / arrival-rate
trajectories per configuration, with labels and colors carried through
from the spec and the impairment shading derived from the network spec's
`steps` block.

## What's in the repo

| File / folder | Purpose |
|---|---|
| `cli.py` | configuration runner — entry point |
| `runner.py` | local + distributed run orchestration, SSH primitives, hooks |
| `validation.py` | spec resolution, validation, projection to per-role dataclasses |
| `reporting.py` | summary computation + terminal report |
| `worker.py` | per-role process that builds the pipeline and runs the GLib main loop |
| `pipeline_config.py` | dataclasses for the per-role pipeline specs |
| `sender.py`, `receiver.py` | three pipeline shapes each: bare / SCReAM / GCC |
| `metrics.py` | metric plugins that attach pad probes and finalize JSON |
| `experiment.py` | spec-driven repeat runner; writes the experiment record |
| `expo.py` | spec-driven visual demo runner; same measurement plus a render branch |
| `analyze.py` | cross-run summary table |
| `plot.py` | overlay plots from an experiment record |
| `specs/experiments/` | named experiments — see `experiment.py --list` |
| `specs/expos/` | named visual demos — see `expo.py --list` |
| `specs/configurations/` | per-run configurations — see `cli.py --list` |
| `specs/videos/` | named source specs (`video: <name>` in a configuration) |
| `specs/networks/` | named impairment specs (`network: <name>` in a configuration) |
| `scream/` | vendored gstscream (built once via `build_gstreamer.sh`) |
| `scream-eos-fix.patch` | applied to gstscream during build; prevents an EOS panic |
| `build_gstreamer.sh` | one-shot build of GStreamer 1.24 + `gst-plugins-rs` into `~/gst-1.24` |
| `setup_remote.sh` | idempotent per-host setup; `cli.py` runs it before distributed runs |
| `fix_clock.sh` | sync host clock via chrony — needed for absolute end-to-end latency reporting |
| `runs/` | per-run output, plus `experiments/` records and `figures/` plots |
| `reference/` | concept notes and diagrams that mirror the GStreamer cluster in the graph |
| `TODO.md` | open gaps for an actual teleoperation video stack |

## The four spec types

```
specs/
  videos/         <- source presets       (video: <name>     pulls in `source:`)
  networks/       <- impairment presets   (network: <name>   pulls in compiled hooks)
  configurations/ <- per-run scenarios    (cli.py --list)
  experiments/    <- named comparisons    (experiment.py --list)
```

Each layer composes the one before it. A configuration declares a
pipeline plus optional video/network refs. An experiment declares a
comparison of multiple configurations plus the paths that may differ.

## Network impairment is declarative

A network spec is a list of steps; the runner compiles each step into
the `tc qdisc` invocations that produce that impairment.

```yaml
# specs/networks/fluctuating-5mbps-300kbps.yaml
steps:
  - { duration: 7, rate_kbps: 5000, delay_ms:  20, loss_pct: 0, label: "5 Mbps · 20 ms" }
  - { duration: 6, rate_kbps:  300, delay_ms: 100, loss_pct: 2, label: "300 kbps · 100 ms · 2% loss" }
  - { duration: 7, rate_kbps: 5000, delay_ms:  20, loss_pct: 0, label: "5 Mbps · 20 ms" }
```

Configurations using this spec set `network_env: { NIC: <ifname> }` to
specify which interface the bash should target. `plot.py` reads the
same `steps` block from the experiment record and uses it to shade the
impaired window and draw the link-capacity panel.

## Metrics

`scenario.metrics` lists which metrics to enable for the run. The list
is required and validated against the metric registry — typos fail at
config load. Available metrics:

| Metric | Side | What it samples |
|---|---|---|
| `frame_count`         | both     | buffers leaving `encoder.src` (sender) or `depay.src` (receiver) |
| `encoder_target_kbps` | sender   | `vp8enc.target-bitrate` once per second; whichever CC is active drives this |
| `frame_latency`       | both     | `(rtp_timestamp, wall_time)` on the marker-bit packet at the network boundary; joined by RTP timestamp |
| `wire_bytes`          | both     | bytes through the egress / ingress UDP element + per-second kbps |
| `encoded_bitrate`     | sender   | actual byte-rate emitted by `vp8enc` |
| `decoder_errors`      | receiver | bus warnings from `rtpvp8depay` and `vp8dec` |

## Distributed runs

A configuration runs in distributed mode when its `scenario` block
contains a `remote:` sub-block (with `project_root` and optional
`display` / `xauthority`). `cli.py` then:

1. `rsync`'s the project to `sender_host` and `receiver_host`,
2. measures clock skew per host (minimum-RTT sample, cached for 5 minutes),
3. runs `setup_remote.sh` on each host (idempotent; rebuilds gstscream
   against 1.24 if the cache is stale),
4. runs the configuration's hooks where each one specifies
   (`local` / `sender` / `receiver`),
5. spawns `worker.py` over SSH with PID files for clean teardown,
6. fetches result files back, writes `summary.json`.

## Tests

The validator surface is under unit test in `tests/`. Stdlib `unittest`,
no extra dependencies, runs in well under a second:

```sh
python3 -m unittest discover tests -v
```

The suite has three pieces:
- `test_specs_validate.py` — every spec file in `specs/` resolves and
  validates clean (positive cases).
- `test_validator.py` — the configuration validator catches realistic
  mistakes (negative cases) — top-level typos, dead fields, conditional
  rules, scenario typos, CC algorithm dispatch.
- `test_cross_spec_validators.py` — the experiment `varies` deep-compare
  catches undeclared divergences, and the expo validator rejects unknown
  configuration references / missing display / unknown keys.

Run before any change to `validation.py`, `experiment.py`, `expo.py`,
or the spec files.

## First-time setup

```sh
bash build_gstreamer.sh         # ~30 min; installs to ~/gst-1.24
bash setup_remote.sh            # verifies env, builds gstscream
python3 cli.py 1                # smoke: bare loopback baseline
python3 cli.py 3                # smoke: SCReAM over loopback
```

For distributed runs, run `build_gstreamer.sh` on each participating
host, make passwordless SSH work in both directions, and run
`fix_clock.sh` on each host so end-to-end latency reports aren't biased
by clock skew.
