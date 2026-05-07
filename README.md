# GstreamerExp

Testbed for real-time video congestion control. Compares
**SCReAM** and **Google Congestion Control** (`rtpgccbwe` + TWCC) on
a GStreamer 1.24 / VP8 / RTP / UDP pipeline, with declarative
network impairment via `tc`.

Configurations run either locally over loopback or distributed
across two Linux hosts (camera + viewer) connected by SSH.

For the *philosophy* of the project (what gets added, how
experiments are structured, the five-dimension evaluation framework),
see [`DESIGN.md`](DESIGN.md). The rest of this file is **how to
get it running**.

## Prerequisites

- Linux (tested on Ubuntu 20.04). Distributed mode needs two hosts
  on the same LAN with SSH key-based access between them and to your
  local machine.
- Python 3.8+ (`python3`, `pip3`)
- `git`, `gcc`, `make`, `cmake`, `meson >= 1.3`, `ninja`
- A free network interface on each host that the testbed can shape
  with `tc` (this NIC must carry the camera→viewer flow; see
  `hosts.yaml` setup below).
- Sudo on each host (only `tc` and apt installs use it).

## Setup

```bash
# 1. Clone
git clone https://github.com/<user>/GstreamerExp.git
cd GstreamerExp

# 2. Local Python deps
pip install -r requirements.txt

# 3. Tell the testbed about your machines.
cp hosts.example.yaml hosts.yaml
# Then edit hosts.yaml:
#   - actors.camera.host       — IP/hostname of the camera machine
#   - actors.camera.network_env.NIC — interface name on that machine
#                                     that carries camera → viewer
#   - actors.viewer.host       — IP/hostname of the viewer machine
#   - both project_root        — path on each remote machine where
#                                the runner rsyncs this tree (default
#                                ~/gstexp works fine)

# 4. Build GStreamer 1.24 + clone & build SCReAM, on each host.
#    This downloads the SCReAM source tree from upstream
#    (EricssonResearch/scream) into ./scream/ — that directory is
#    gitignored. Compile takes ~30 minutes on a 24-core host.
scp build_gstreamer.sh setup_remote.sh scream-eos-fix.patch <camera-host>:/tmp/
ssh <camera-host> "bash /tmp/build_gstreamer.sh && cd ~/gstexp && bash /tmp/setup_remote.sh"
# Repeat for <viewer-host>.

# 5. Smoke test on loopback (config 1 doesn't need any of the above
#    — it runs camera and viewer on your local machine).
python3 cli.py 1
```

If the loopback run prints a `=== 1 (...): PASS ===` block, you're
set. Distributed runs (configs 11, 12, …) need both hosts set up.

## Running experiments

Single configuration:
```bash
python3 cli.py <config_id>     # e.g. python3 cli.py 11
```

Multi-rep, multi-arm experiment:
```bash
python3 experiment.py <experiment_name>
```

Configuration files live in `specs/configurations/` (one YAML per
config, identified by id), `specs/networks/` (impairment profiles),
`specs/videos/` (camera source profiles), and
`specs/experiments/` (multi-config sweeps with rep counts).

Run results write to `runs/<config_id>/<timestamp>/` (gitignored).

## Hypothesis pages

Open `analysis/hypotheses/h1.html` and `analysis/hypotheses/h4.html`
in a browser after running an experiment, served from a local HTTP
server (`fetch()` doesn't work over `file://`):

```bash
cd analysis/hypotheses && python3 -m http.server 8765
# then visit http://localhost:8765/h1.html
```

The presentation principles those pages follow are documented in
[`analysis/hypotheses/PRESENTATION_PRINCIPLES.md`](analysis/hypotheses/PRESENTATION_PRINCIPLES.md).

## Layout

```
.
├── cli.py              # run a single configuration
├── experiment.py       # run a multi-rep, multi-arm sweep
├── runner.py           # local + distributed worker spawn / cleanup
├── camera.py viewer.py # the GStreamer pipelines
├── pipeline_config.py  # typed config dataclasses
├── validation.py       # YAML schema validators + hosts.yaml merge
├── metrics.py          # per-rep instrumentation (frame_count, late_drops, …)
├── specs/              # YAML configs (configurations/, networks/, videos/, experiments/)
├── analysis/           # post-run analysis (dimensions/, hypotheses/)
├── runs/               # experiment outputs, gitignored
├── tests/              # validator + spec test suite
├── tools/              # ancillary scripts (network_probe, fetch_test_video, …)
├── build_gstreamer.sh  # builds GStreamer 1.24 + gst-plugins-rs
├── setup_remote.sh     # per-host bootstrap (clones SCReAM, applies patch, builds)
├── scream/             # SCReAM source — cloned by setup_remote.sh, gitignored
└── hosts.yaml          # YOUR machine config — gitignored, copy from .example
```

## License

MIT — see [LICENSE](LICENSE).
