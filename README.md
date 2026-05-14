# GstreamerExp

A testbed that builds a real-time video pipeline (GStreamer 1.24 ·
VP8 · RTP · UDP), runs it under declarative `tc` impairment, and
treats every claim about the pipeline as a falsifiable hypothesis
backed by a verifier script. Two congestion-control algorithms —
**SCReAM** and **Google Congestion Control** — are wired in side-by-side
so the comparison is a matter of one spec field. The five-dimension
evaluation framework (throughput, quality, latency, adaptation,
stability) is the structural shape every metric and hypothesis fits in.

## 5-minute quickstart

The smoke test runs camera and viewer on this machine over loopback.
No SSH, no `tc`, no remote workers.

```sh
pip install -r requirements.txt
python3 cli.py 1
```

If you see `=== 1 (bare): PASS ===`, the testbed works. The run wrote
its record to `runs/1/<timestamp>/summary.json`. Try `python3 cli.py 3`
next for the SCReAM-over-loopback variant.

For real network impairment between two hosts, see [Distributed
setup](#distributed-setup) below.

## Where to go from here

| I want to… | Read |
|---|---|
| Understand the project's operating principles | [`docs/DESIGN.md`](docs/DESIGN.md) |
| Browse the architecture, pipeline shapes, recovery features | [`features.html`](features.html) (serve locally) |
| Read a hypothesis result with figures and tables | [`analysis/hypotheses/h1.ipynb`](analysis/hypotheses/h1.ipynb) – [`h4.ipynb`](analysis/hypotheses/h4.ipynb) (render on GitHub) |
| See the catalog of claims and their sources | [`docs/HYPOTHESES.md`](docs/HYPOTHESES.md) |
| See the gap list against a real teleoperation stack | [`docs/TODO.md`](docs/TODO.md) |
| Add a new hypothesis | [`specs/hypotheses/`](specs/hypotheses/) + [`docs/HYPOTHESES.md`](docs/HYPOTHESES.md) + a verifier script |
| Add a new configuration / network / video / experiment | [`specs/`](specs/) (validated at load time; see [`docs/DESIGN.md`](docs/DESIGN.md) §3) |
| Tune the figure conventions | [`docs/FIGURE_STYLE_PRINCIPLES.md`](docs/FIGURE_STYLE_PRINCIPLES.md), [`analysis/hypotheses/PRESENTATION_PRINCIPLES.md`](analysis/hypotheses/PRESENTATION_PRINCIPLES.md) |

## Hypothesis results

Each hypothesis is rendered as a Jupyter notebook (GitHub displays
it directly) and an HTML page (open it via a local HTTP server).
Both come from `specs/hypotheses/<slug>.yaml`; do not hand-edit the
generated files. Rebuild after the underlying specs or runs change:

```sh
python3 analysis/hypotheses/build_reports.py    # report JSON + figures
python3 analysis/hypotheses/build_pages.py      # HTML + notebooks
```

| Id | Verdict | Question | Notebook | HTML page |
|---|---|---|---|---|
| **H1** | regime-dependent | Does SCReAM under-use the bottleneck cap vs GCC on capacity-step networks? | [h1.ipynb](analysis/hypotheses/h1.ipynb) | [h1.html](analysis/hypotheses/h1.html) |
| **H2** | supported | Does GCC stay ahead across latency-deadline and retransmission cells? | [h2.ipynb](analysis/hypotheses/h2.ipynb) | [h2.html](analysis/hypotheses/h2.html) |
| **H3** | supported | Does the original realistic-trace workload under-drive the network? | [h3.ipynb](analysis/hypotheses/h3.ipynb) | [h3.html](analysis/hypotheses/h3.html) |
| **H4** | supported | Does SCReAM preserve more decodable video on stressed 5G traces? | [h4.ipynb](analysis/hypotheses/h4.ipynb) | [h4.html](analysis/hypotheses/h4.html) |

## Running experiments

A configuration is a YAML at [`specs/configurations/<id>.yaml`](specs/configurations/)
that declares the whole pipeline (codec, encoder, transport,
congestion control, sink) at the top level. An experiment at
[`specs/experiments/<name>.yaml`](specs/experiments/) names a comparison
of configurations with a rep count and a `varies` allowlist.

```sh
python3 cli.py --list                # one-line per configuration
python3 cli.py 11                    # one configuration, one run

python3 experiment.py --list         # one-line per experiment
python3 experiment.py scream-vs-gcc-720p
```

Run results write to `runs/<config_id>/<timestamp>/` (gitignored).
`experiment.py` interleaves reps round-robin so cross-traffic noise
distributes evenly across arms.

## Distributed setup

Loopback (config 1) needs none of this. The distributed configs
(11, 12, …) run camera and viewer on two Linux workers over SSH.

```sh
# 1. Tell the testbed about your machines.
cp hosts.example.yaml hosts.yaml
# Then edit hosts.yaml:
#   actors.camera.host       — default camera endpoint
#   actors.camera.ssh_host   — SSH/rsync endpoint; defaults to host
#   actors.camera.media_host — RTP/RTCP endpoint; defaults to host
#   actors.camera.network_env.NIC — interface name carrying camera→viewer
#   actors.viewer.host / ssh_host / media_host — same shape on the viewer side
#   both project_root        — path on each worker where the runner
#                              syncs the minimal runtime payload

# 2. Build GStreamer 1.24 + SCReAM on each worker.
#    setup_remote.sh clones EricssonResearch/scream into ./scream/
#    (gitignored). Compile takes ~30 minutes on a 24-core host.
scp scripts/build_gstreamer.sh scripts/setup_remote.sh scripts/scream-eos-fix.patch <camera-host>:/tmp/
ssh <camera-host> "bash /tmp/build_gstreamer.sh && cd ~/gstexp && bash /tmp/setup_remote.sh"
# Repeat for <viewer-host>.

# 3. Verify by running a distributed config.
python3 cli.py 11
```

Prerequisites: Linux on each host (tested on Ubuntu 20.04), Python 3.8+,
`git`, `gcc`, `make`, `cmake`, `meson >= 1.3`, `ninja`, sudo on each
host (only `tc` and apt installs use it).

## Layout

```
.
├── cli.py              # run a single configuration
├── experiment.py       # run a multi-rep, multi-arm sweep
├── gstexp/             # runner, pipeline builders, validators, metrics, plotting
├── specs/              # YAML configs (configurations/, networks/, videos/, experiments/, hypotheses/)
├── analysis/           # post-run analysis (dimensions/, hypotheses/)
├── runs/               # experiment outputs, gitignored
├── tests/              # validator + spec test suite
├── tools/              # ancillary scripts (network_probe, fetch_test_video, …)
├── scripts/            # build/setup shell scripts + scream-eos-fix.patch
├── docs/               # design notes, hypothesis catalog, todo
├── reference/          # GStreamer concept notes
├── scream/             # SCReAM source — cloned by setup_remote.sh, gitignored
└── hosts.yaml          # YOUR machine config — gitignored, copy from .example
```

## License

MIT — see [LICENSE](LICENSE).
