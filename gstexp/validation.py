"""Configuration loader, validator, and per-role projector.

A configuration declares one pipeline once, at the top level — codec,
encoder, transport, congestion_control, sink — instead of mirroring
between camera and viewer halves. resolve_includes() inlines the
`video:` and `network:` refs (source from videos/<name>.yaml; tc hooks
from networks/<name>.yaml). validate_doc() checks the unified shape.
project_to_roles() then derives the per-role specs the worker needs,
so the duality stays an implementation detail.

A scenario declares actors keyed by role: today there are exactly two
roles — `camera` (the actor that holds the source of video — a real
camera or a synthetic test pattern) and `viewer` (the actor that
consumes it, whether to a display or a measurement sink). Both actors are full-duplex
network endpoints; the role names describe their position in the media
flow, not the network direction (every actor both sends and receives
packets). The actor abstraction makes future roles natural — e.g. a
`relay` for SFU-style RTP forwarding — without re-spelling vocabulary.

All three functions exit on error with a message that names the
offending key, so typos surface at load time instead of inside a
worker subprocess.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import yaml


# The user-authored configuration is one pipeline declared once. Every
# field that affects behavior must be set explicitly — there are no
# dataclass defaults or schema-level fallbacks. Each entry is
# (required, optional); `optional` only holds fields that depend on
# other fields (e.g. sink.path matters iff sink.backend=="file") and
# are checked further down.
# Bitrate bounds are required for any CC algorithm (we always pick them);
# every other algorithm-specific knob is optional, with absence meaning
# "use the algorithm's documented default."
_CC_REQUIRED = {"algorithm", "init_bitrate_kbps", "min_bitrate_kbps", "max_bitrate_kbps"}
_CC_OPTIONAL_BLOCKS = {"scream", "gcc"}     # algorithm-specific overrides

# Knob sets per algorithm. Keys map to flags / properties consumed by
# camera.py / viewer.py (see _build_scream_params and the rtpgccbwe /
# rtpbin set_property calls). The split between camera and viewer
# inside `gcc:` happens during projection.
_SCREAM_CAMERA_KNOBS = {
    "delay_target_seconds", "ect", "pacing_headroom", "adaptive_pacing_headroom",
    "inflight_headroom", "max_window_headroom", "min_packets_in_flight",
    "microburst_interval_ms", "reorder_time_seconds", "mul_increase",
    "relaxed_pacing", "no_pacing",
}
_SCREAM_VIEWER_KNOBS: set = set()           # screamrx exposes nothing today
_GCC_CAMERA_KNOBS = {"estimator"}
_GCC_VIEWER_KNOBS = {"twcc_feedback_interval_ms"}

# Codec discriminator. The string here picks a Codec class in
# pipeline_config (Vp8Codec today). The validator rejects unknown
# values rather than letting a stale string slip through to a confusing
# GStreamer "no element vp9enc" error at pipeline-build time.
_IMPLEMENTED_CODECS = {"vp8"}

_VALID_ECT = {-1, 0, 1, 3}                  # SCReAM accepts only these
_VALID_GCC_ESTIMATORS = {"kalman", "linear-regression"}
# Configurations declare measurement sinks only. `autovideo` is no longer
# a configuration choice — visual rendering is an expo concern (see expo.py)
# that adds an autovideosink branch alongside the measurement sink at
# runtime, leaving the configuration's measurement intent intact.
_VALID_SINK_BACKENDS = {"fake", "file"}

_BLOCK_SCHEMAS = {
    "encoder":   ({"bitrate_kbps", "keyframe_interval_frames"}, set()),
    "sink":      ({"backend", "sync"}, {"path"}),
    # `source` is validated by _validate_source — backend-discriminated.
    # `congestion_control` is validated by _validate_cc — algorithm-discriminated.
}

# Source backend discriminator. The string here picks a Source backend
# class in pipeline_config (SyntheticSource today). The validator
# rejects unknown values rather than letting them slip through to a
# confusing GStreamer "no element <foo>" error at pipeline-build time.
_IMPLEMENTED_SOURCE_BACKENDS = {"synthetic", "file"}

# Source has shared fields (apply to every backend) plus a discriminator
# and an optional sub-block per backend with backend-specific knobs.
# Mirror the congestion_control / codec discriminator pattern.
_SOURCE_REQUIRED = {"backend", "width", "height", "fps",
                    "num_frames", "clock_overlay"}
_SOURCE_OPTIONAL_BLOCKS = {"synthetic", "file"}     # one entry per real backend

# Per-backend allowed knob sets. When a new backend lands, register its
# sub-block keys here AND in the _IMPLEMENTED_SOURCE_BACKENDS registry
# AND in pipeline_config (new dataclass) AND in worker.py (loader).
_SYNTHETIC_KNOBS = {"pattern"}
_FILE_KNOBS = {"path", "loop"}
_TOP_LEVEL_REQUIRED = {"meta", "scenario", "streams", "sync"}
_TOP_LEVEL_OPTIONAL = {"hooks"}
_TOP_LEVEL_KEYS = _TOP_LEVEL_REQUIRED | _TOP_LEVEL_OPTIONAL

# Each entry of the top-level `streams:` list is one stream's full
# payload. The single-stream fields that used to live at top level
# (source/codec/encoder/sink/recovery/latency_budget_ms + optional
# congestion_control) re-home under each stream, joined by a `name` and a
# `priority` — the teleop degradation order, a non-negative int where 0 is
# most important (shed last) and larger sheds first. `video:` and
# `network:` are per-stream *references* (a video stem; a network profile
# for this stream's own shaped lane on the shared NIC); resolve_includes
# pops them — `video:` inlines `source`, `network:` folds into the
# combined top-level `hooks` — so neither survives to validate_doc, which
# sees the inlined `source` instead.
_STREAM_REQUIRED = {"name", "source", "codec", "encoder", "sink",
                    "recovery", "latency_budget_ms", "priority"}
_STREAM_OPTIONAL = {"congestion_control"}
_STREAM_KEYS = _STREAM_REQUIRED | _STREAM_OPTIONAL
_STREAM_REFS = {"video", "network"}   # resolve-time, popped before validate
_MAX_STREAMS = 16                     # hard cap; gates the port stride

# `sync:` declares how the N streams are cross-synchronized. Discriminated
# by `mode`: shared_epoch (controller-issued T0 + a software start-barrier;
# the implemented default), ptp (a deferred seam for true multi-host
# capture timestamps), none (legacy independent start). `termination`
# decides when a run ends relative to the N source frame-caps: `all` waits
# for every stream, `first` ends when the first stream finishes.
_SYNC_REQUIRED = {"mode", "termination"}
_SYNC_MODES = {"shared_epoch", "ptp", "none"}
_SYNC_TERMINATIONS = {"all", "first"}

# `scenario` carries run orchestration. The `remote:` block is optional —
# its presence triggers distributed mode. `wrap_sender`/`wrap_receiver`
# are optional because most configurations don't use a wrapper.
# Scenario carries run orchestration. The `actors:` block is required
# and keyed by role; today the role set is exactly {camera, viewer}.
# All per-host configuration (host, ssh_host, media_host, project_root,
# network_env, wrap) lives inside the actor block — there is no top-level
# `network_env` or `remote.project_root` anymore. Per-actor scope is what
# unlocks asymmetric path impairment (each actor's NIC shapes its own egress).
_SCENARIO_REQUIRED = {"actors", "setup_delay_seconds",
                      "drain_delay_seconds", "metrics"}
_SCENARIO_OPTIONAL: set = set()
_SCENARIO_KEYS = _SCENARIO_REQUIRED | _SCENARIO_OPTIONAL

_VALID_ROLES = {"camera", "viewer"}
_ACTOR_REQUIRED = {"host"}
_ACTOR_OPTIONAL = {"ssh_host", "media_host", "project_root", "network_env", "wrap"}
_ACTOR_KEYS = _ACTOR_REQUIRED | _ACTOR_OPTIONAL

# Keep validation independent of the local GStreamer installation. The
# worker imports gstexp.metrics and therefore needs gi/Gst at runtime; the
# spec validator should still be able to run on a controller that only
# prepares remote jobs.
_VALID_METRICS = {
    "frame_count",
    "encoder_target_kbps",
    "frame_latency",
    "stage_latency",
    "wire_bytes",
    "encoded_bitrate",
    "decoder_errors",
    "decoded_psnr",
    "late_drops",
}


# A network spec is purely declarative: a list of steps, each with
# rate / delay / loss / duration / label. resolve_includes() compiles
# the steps into the imperative tc invocations the runner consumes.
#
# Loss is the one impairment dimension where multiple models matter for
# the recovery study (NACK / PLI / FEC differentiate under bursty loss,
# not under uniform iid). It's modeled as a discriminated block keyed by
# `model`; each model has its own required parameter set. Rate and delay
# stay flat scalars because they have one model today (fixed-rate via
# tbf, fixed-delay via netem); when a research question demands a second
# model on either, that's a localized rewrite.
_NETWORK_SPEC_KEYS = {"name", "description", "camera_steps", "viewer_steps"}
_STEP_KEYS = {"duration", "rate_kbps", "delay_ms", "loss", "label"}
_STEP_OPTIONAL_KEYS = {"jitter_ms"}   # netem delay jitter; absent means 0
_LOSS_MODELS = {"none", "uniform", "bursty"}
_LOSS_REQUIRED_BY_MODEL = {
    "none":    {"model"},
    "uniform": {"model", "pct"},
    "bursty":  {"model", "pct", "burst"},
}


def _gemodel_p_r(pct: float, burst: int) -> tuple[float, float]:
    """Convert friendly (avg loss %, avg burst length in packets) to
    Gilbert-Elliott (p, r) percentages that netem's `loss gemodel` takes.

    avg loss rate = p / (p + r) ; avg burst length = 1 / r packets.
    Solving: r = 100 / burst, p = (pct * r) / (100 - pct).
    """
    r = 100.0 / burst
    p = (pct * r) / (100.0 - pct)
    return p, r


def _validate_step_loss(loss, where: str, fail) -> None:
    """Validate one step's loss block — model-discriminated."""
    if not isinstance(loss, dict):
        fail(f"{where}.loss must be a mapping with a `model` key")
    if "model" not in loss:
        fail(f"{where}.loss: missing `model` key; "
             f"expected one of {sorted(_LOSS_MODELS)}")
    model = loss["model"]
    if model not in _LOSS_MODELS:
        fail(f"{where}.loss.model must be one of {sorted(_LOSS_MODELS)}, "
             f"got {model!r}")
    required = _LOSS_REQUIRED_BY_MODEL[model]
    missing = required - set(loss)
    if missing:
        fail(f"{where}.loss[{model}]: missing key(s) {sorted(missing)}")
    unknown = set(loss) - required
    if unknown:
        fail(f"{where}.loss[{model}]: unknown key(s) {sorted(unknown)}; "
             f"expected {sorted(required)}")
    if model in ("uniform", "bursty"):
        pct = loss["pct"]
        if not isinstance(pct, (int, float)) or pct <= 0 or pct > 100:
            fail(f"{where}.loss.pct must be a number in (0, 100] "
                 f"(use model: none for zero loss)")
    if model == "bursty":
        burst = loss["burst"]
        if not isinstance(burst, int) or burst < 2:
            fail(f"{where}.loss.burst must be an integer >= 2 "
                 f"(use model: uniform for B=1 — Bernoulli iid is the "
                 f"limit case of Gilbert-Elliott at burst=1)")


def _validate_steps_block(steps, label: str, where: str) -> None:
    """Validate a step list (camera_steps or viewer_steps). Empty is OK."""
    if not isinstance(steps, list):
        sys.exit(f"{where}: `{label}` must be a list")
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            sys.exit(f"{where}: {label}[{i}] must be a mapping")
        missing = _STEP_KEYS - set(s)
        if missing:
            sys.exit(f"{where}: {label}[{i}] missing key(s) {sorted(missing)}")
        unknown = set(s) - _STEP_KEYS - _STEP_OPTIONAL_KEYS
        if unknown:
            sys.exit(f"{where}: {label}[{i}] unknown key(s) {sorted(unknown)}; "
                     f"expected {sorted(_STEP_KEYS | _STEP_OPTIONAL_KEYS)}")
        if "jitter_ms" in s and not isinstance(s["jitter_ms"], (int, float)):
            sys.exit(f"{where}: {label}[{i}].jitter_ms must be a number")
        _validate_step_loss(s["loss"], f"{where}: {label}[{i}]",
                            lambda m: sys.exit(m))


# ---- per-step netem/tbf clause builders (shared across all lanes) -------

def _loss_clause(s):
    loss = s["loss"]
    model = loss["model"]
    if model == "none":
        return None
    if model == "uniform":
        return f"loss {loss['pct']}%"
    # bursty
    p, r = _gemodel_p_r(loss["pct"], loss["burst"])
    return f"loss gemodel {p:.4f} {r:.4f}"


def _netem_clause(s):
    delay = f"delay {s['delay_ms']}ms"
    if s.get("jitter_ms"):
        delay += f" {s['jitter_ms']}ms"   # netem: delay <mean> <jitter>
    clauses = [delay]
    loss = _loss_clause(s)
    if loss is not None:
        clauses.append(loss)
    return " ".join(clauses)


def _burst_kbit(s):
    # ~10 ms of headroom at the configured rate; floor at 8 kbit so even
    # sub-Mbps rates can dequeue a single MTU.
    return max(8, s["rate_kbps"] // 100)


def _loss_descr(s):
    loss = s["loss"]
    if loss["model"] == "none":
        return "loss=none"
    if loss["model"] == "uniform":
        return f"loss={loss['pct']}% uniform"
    p, r = _gemodel_p_r(loss["pct"], loss["burst"])
    return (f"loss={loss['pct']}% bursty "
            f"(B={loss['burst']}, p={p:.2f}%, r={r:.1f}%)")


def _step_log(role: str, name: str, idx: int, s: dict) -> str:
    jit = f" jitter={s['jitter_ms']}ms" if s.get("jitter_ms") else ""
    return (f"{role}[{name}] step {idx+1}: rate={s['rate_kbps']}kbit "
            f"delay={s['delay_ms']}ms{jit} {_loss_descr(s)} — {s['label']}")


# ---- per-stream tc lanes ------------------------------------------------
#
# Multi-stream impairment shapes N flows independently on one NIC. The
# root is an `htb` qdisc; each stream rides its own leaf class (filtered
# by destination port) carrying a netem (delay/loss) -> tbf (rate)
# subtree. Everything the per-port filters don't match — SSH, DNS, the
# host's internet traffic on the default-route NIC — falls to the htb
# default class (1:ffff), unshaped, exactly as the old single-lane `prio`
# root left band 1:2 untouched. The handles below are derived from the
# 0-based stream index so up to _MAX_STREAMS lanes coexist without
# colliding. (The old single-stream scheme was this with one lane on a
# `prio` root; htb is needed because `prio` caps at 16 bands < N+passthrough.)

def _lane_classid(i: int) -> str:
    return f"1:{i + 1}"            # htb leaf class for stream i


def _lane_netem(i: int) -> str:
    return f"{(i + 1) * 10}:"      # netem qdisc handle (10:, 20:, ...)


def _lane_tbf(i: int) -> str:
    return f"{(i + 1) * 10 + 1}:"  # tbf qdisc handle (11:, 21:, ...)


def _install_lane_shaper(i, s, action):
    # netem (delay/loss) then tbf (rate) under stream i's htb leaf class.
    return [
        f"tc qdisc {action} dev \"$NIC\" parent {_lane_classid(i)} "
        f"handle {_lane_netem(i)} netem {_netem_clause(s)}",
        f"tc qdisc {action} dev \"$NIC\" parent {_lane_netem(i)}1 "
        f"handle {_lane_tbf(i)} tbf rate {s['rate_kbps']}kbit "
        f"burst {_burst_kbit(s)}kbit latency 50ms",
    ]


def _install_lane(i, rtp_port, s0):
    # Build stream i's lane: leaf class, shaper subtree (step 0), and the
    # destination-port filter that routes the flow into it. The 0xfffe
    # mask catches both rtp_port (even, RFC 3550) and rtcp = rtp_port + 1.
    return [
        f"tc class add dev \"$NIC\" parent 1: classid {_lane_classid(i)} "
        f"htb rate 10gbit ceil 10gbit",
        *_install_lane_shaper(i, s0, "add"),
        f"tc filter add dev \"$NIC\" parent 1:0 protocol ip prio 1 u32 "
        f"match ip dst \"$PEER_IP_RESOLVED\"/32 "
        f"match ip dport {rtp_port} 0xfffe flowid {_lane_classid(i)}",
    ]


def _install_htb_root():
    # Idempotent: del any prior root, then install the htb root + a
    # non-limiting passthrough class for unmatched traffic.
    return [
        'PEER_IP_RESOLVED="$(getent ahostsv4 "$PEER_IP" | awk \'NR==1 {print $1}\')"',
        '[ -n "$PEER_IP_RESOLVED" ] || { echo "could not resolve PEER_IP=$PEER_IP" >&2; exit 1; }',
        'tc qdisc del dev "$NIC" root 2>/dev/null || true',
        'tc qdisc add dev "$NIC" root handle 1: htb default ffff',
        'tc class add dev "$NIC" parent 1: classid 1:ffff htb rate 10gbit ceil 10gbit',
    ]


def _reinstall_lane(i, s):
    # Hard reset stream i's shaper subtree (bursty GE state starts fresh);
    # the htb class + filter are preserved.
    return [
        f"tc qdisc del dev \"$NIC\" parent {_lane_classid(i)} "
        f"handle {_lane_netem(i)} 2>/dev/null || true",
        *_install_lane_shaper(i, s, "add"),
    ]


def _compile_role_lanes(role: str, lanes: list) -> dict:
    """Compile one actor role's lanes into pre/during/post hooks.

    lanes: list of (stream_idx, name, rtp_port, steps) — one entry per
    stream that shapes THIS direction. Returns
    {pre_run: hook, during_run: [hook,...], post_run: hook}: a single
    pre_run that installs the htb root + every lane (step 0), one
    independent during_run hook per multi-step lane (each walks its own
    trace, touching only its own subtree), and a single post_run that
    tears the root down.
    """
    pre_lines = ["set -e", *_install_htb_root()]
    for i, name, rtp_port, steps in lanes:
        pre_lines += _install_lane(i, rtp_port, steps[0])
        pre_lines.append(
            f'echo "[net/{role}] $NIC lane — {_step_log(role, name, 0, steps[0])}"')
    pre_hook = {"host": role, "sudo": True, "script": "\n".join(pre_lines) + "\n"}

    during = []
    for i, name, rtp_port, steps in lanes:
        if len(steps) <= 1:
            continue
        lines = ["set -e"]
        for k, s in enumerate(steps[1:], start=1):
            lines.append(f"sleep {steps[k-1]['duration']}")
            prev_bursty = steps[k-1]["loss"]["model"] == "bursty"
            this_bursty = s["loss"]["model"] == "bursty"
            if prev_bursty or this_bursty:
                lines += _reinstall_lane(i, s)
            else:
                lines += _install_lane_shaper(i, s, "change")
            lines.append(
                f'echo "[net/{role}] $(date +%H:%M:%S) — {_step_log(role, name, k, s)}"')
        during.append({"host": role, "sudo": True,
                       "script": "\n".join(lines) + "\n"})

    post_hook = {"host": role, "sudo": True,
                 "script": ('tc qdisc del dev "$NIC" root 2>/dev/null || true\n'
                            f'echo "[net/{role}] $NIC qdiscs removed"\n')}
    return {"pre_run": pre_hook, "during_run": during, "post_run": post_hook}


def _compile_streams_network(stream_nets: list, where: str) -> dict:
    """Compile every stream's network profile into one combined hook set.

    stream_nets: list of {idx, name, rtp_port, spec} where `spec` is the
    parsed network YAML (camera_steps / viewer_steps) for that stream, or
    None when the stream declares no `network:` (it rides the unshaped
    passthrough lane). For each actor role, the streams that shape that
    direction become htb lanes on one root; the hook lists are the union
    across roles. Each phase value is a LIST of hooks (possibly empty);
    the runner iterates per phase.
    """
    out: dict = {"pre_run": [], "during_run": [], "post_run": []}
    for role, steps_key in (("camera", "camera_steps"), ("viewer", "viewer_steps")):
        lanes = []
        for sn in stream_nets:
            spec = sn["spec"]
            if spec is None:
                continue
            steps = spec.get(steps_key, [])
            _validate_steps_block(steps, steps_key, where)
            if steps:
                lanes.append((sn["idx"], sn["name"], sn["rtp_port"], steps))
        if not lanes:
            continue
        role_hooks = _compile_role_lanes(role, lanes)
        out["pre_run"].append(role_hooks["pre_run"])
        out["during_run"].extend(role_hooks["during_run"])
        out["post_run"].append(role_hooks["post_run"])
    return out


def resolve_includes(doc: dict, project_root: Path, config_path: Path,
                     hosts_override: dict | None = None) -> dict:
    """Inline `video:` and `network:` references before validation.

    The video spec contributes a `source:` block that lands at the
    top-level `source`; the network spec contributes a `hooks:` block at
    the top-level `hooks`. Bash variables for the network spec (e.g.
    each actor's NIC) come from per-actor `network_env:` mappings under
    `scenario.actors.<role>.network_env`; their values are prepended to
    each hook script as `export VAR=...` lines, scoped to the actor that
    will run that hook (a hook with `host: "camera"` picks up the camera
    actor's network_env, and so on).

    Defining both a reference and the corresponding inline block (e.g.
    `video: foo` plus inline `source`) is an error — pick one.
    """
    where = config_path.name

    # Merge per-actor defaults from hosts.yaml (machine-specific values
    # — IPs, NIC names, project_root paths) into scenario.actors so the
    # per-config files don't need to embed them. Per-config values take
    # precedence; hosts.yaml fills in only what's missing or empty.
    # `hosts_override` lets callers (e.g. tests) inject a custom mapping
    # instead of reading hosts.yaml — pass {} to disable the merge.
    if hosts_override is not None:
        hosts_doc = hosts_override
    else:
        hosts_path = project_root / "hosts.yaml"
        hosts_doc = {}
        if hosts_path.is_file():
            try:
                hosts_doc = yaml.safe_load(hosts_path.read_text()) or {}
            except Exception as e:
                sys.exit(f"{hosts_path.name}: parse error: {e}")
    if hosts_doc:
        defaults = (hosts_doc.get("actors") or {})
        scenario = doc.setdefault("scenario", {})
        cfg_actors = scenario.setdefault("actors", {})
        # Loopback hosts (127.0.0.1, localhost) are configured for local
        # in-process runs and don't need rsync/SSH paths from hosts.yaml.
        # Filling project_root would flip the distributed-detection in
        # cli.py and trigger an unnecessary remote copy to the loopback.
        _LOOPBACK = {"127.0.0.1", "localhost", "::1"}
        for role, default in defaults.items():
            if not isinstance(default, dict):
                continue
            existing = cfg_actors.get(role)
            if isinstance(existing, dict) and existing.get("host") in _LOOPBACK:
                continue
            if existing is None or existing == {}:
                # Role not yet populated in this config — use defaults.
                cfg_actors[role] = dict(default)
            elif isinstance(existing, dict):
                # Role partially populated — fill in missing fields from
                # defaults without overwriting per-config values.
                for k, v in default.items():
                    if existing.get(k) in (None, "", {}):
                        existing[k] = v
                    elif k == "network_env" and isinstance(existing.get(k), dict):
                        # Merge nested network_env mapping.
                        for ek, ev in v.items():
                            if existing[k].get(ek) in (None, ""):
                                existing[k][ek] = ev

    streams = doc.get("streams")
    config_id = config_path.stem

    # Per-stream references: each stream's `video:` inlines its own
    # `source`; each stream's `network:` names the profile for its own
    # shaped lane. Both are resolve-time refs popped before validate_doc.
    # The network refs are gathered with each stream's derived rtp_port
    # (so the tc filters can classify by destination port) and compiled
    # together into one combined hook set per actor NIC.
    stream_nets = []
    if isinstance(streams, list):
        for idx, st in enumerate(streams):
            if not isinstance(st, dict):
                continue   # validate_doc rejects the malformed entry
            video_ref = st.pop("video", None)
            if video_ref is not None:
                if st.get("source") is not None:
                    sys.exit(f"{where}: streams[{idx}] sets both "
                             f"`video: {video_ref}` and inline `source` — "
                             f"pick one")
                spec_path = project_root / "specs" / "videos" / f"{video_ref}.yaml"
                if not spec_path.is_file():
                    sys.exit(f"{where}: streams[{idx}] video spec not found "
                             f"at {spec_path}")
                spec = yaml.safe_load(spec_path.read_text()) or {}
                if "source" not in spec:
                    sys.exit(f"{spec_path.name}: missing `source` block")
                st["source"] = spec["source"]

            network_ref = st.pop("network", None)
            net_spec = None
            if network_ref is not None:
                spec_path = project_root / "specs" / "networks" / f"{network_ref}.yaml"
                if not spec_path.is_file():
                    sys.exit(f"{where}: streams[{idx}] network spec not found "
                             f"at {spec_path}")
                net_spec = yaml.safe_load(spec_path.read_text()) or {}
                unknown_spec_keys = set(net_spec) - _NETWORK_SPEC_KEYS
                if unknown_spec_keys:
                    sys.exit(f"{spec_path.name}: unknown key(s) "
                             f"{sorted(unknown_spec_keys)}; expected "
                             f"{sorted(_NETWORK_SPEC_KEYS)}")
            stream_nets.append({
                "idx": idx,
                "name": st.get("name", f"stream{idx}"),
                "rtp_port": _port_for_stream(config_id, idx),
                "spec": net_spec,
            })

    if any(sn["spec"] is not None for sn in stream_nets):
        if doc.get("hooks"):
            sys.exit(f"{where}: cannot set both per-stream `network:` refs "
                     f"and inline top-level `hooks` — pick one")
        hooks = _compile_streams_network(stream_nets, where)
        # Prefix each hook script with its actor's network_env (NIC) and
        # the auto-injected PEER_IP, scoped by the hook's `host` role.
        # PEER_IP is read from actor topology (the camera's peer is the
        # viewer's media endpoint and vice versa) so the `tc filter ...
        # match ip dst $PEER_IP_RESOLVED` clauses only impair peer-bound
        # traffic, not SSH / DNS / internet on the default-route NIC.
        actors = (doc.get("scenario") or {}).get("actors") or {}
        peer_role = {"camera": "viewer", "viewer": "camera"}
        for hook_list in hooks.values():
            for hook in hook_list:
                role = hook.get("host")
                if role not in actors:
                    continue   # local or non-actor hosts get no env prefix
                env = dict((actors[role] or {}).get("network_env") or {})
                peer = peer_role.get(role)
                if peer and peer in actors:
                    peer_actor = actors[peer] or {}
                    peer_host = peer_actor.get("media_host") or peer_actor.get("host")
                    if peer_host:
                        env.setdefault("PEER_IP", peer_host)
                if hook["script"] and not env:
                    sys.exit(f"{where}: per-stream networks run `tc` against "
                             f"$NIC for the {role} actor, but "
                             f"`scenario.actors.{role}.network_env` is unset "
                             f"— set NIC there.")
                if env:
                    prefix = "".join(f"export {k}={shlex.quote(str(v))}\n"
                                     for k, v in env.items())
                    hook["script"] = prefix + hook["script"]
        doc["hooks"] = hooks

    return doc


def validate_doc(doc: dict, config_path: Path) -> None:
    """Raise SystemExit with a useful message for missing or unknown keys.

    Every behavior-affecting field is required — there are no silent
    fallbacks. Typos at any nesting level surface here with the expected
    key set listed.
    """
    where = config_path.name

    def fail(msg: str):
        sys.exit(f"{where}: {msg}")

    if not isinstance(doc, dict):
        fail("top-level YAML must be a mapping")
    unknown_top = set(doc) - _TOP_LEVEL_KEYS
    if unknown_top:
        fail(f"unknown top-level key(s): {sorted(unknown_top)}; "
             f"expected one of {sorted(_TOP_LEVEL_KEYS)}")

    missing_top = _TOP_LEVEL_REQUIRED - set(doc)
    if missing_top:
        fail(f"missing required block(s): {sorted(missing_top)}")

    _validate_scenario(doc["scenario"], fail)
    _validate_sync(doc["sync"], fail)

    metrics = doc["scenario"].get("metrics", [])

    streams = doc["streams"]
    if not isinstance(streams, list) or not streams:
        fail("`streams` must be a non-empty list of per-stream blocks")
    if len(streams) > _MAX_STREAMS:
        fail(f"too many streams: {len(streams)} > _MAX_STREAMS "
             f"({_MAX_STREAMS}); raise _MAX_STREAMS (and the port stride) "
             f"to allow more.")

    names: list = []
    for idx, st in enumerate(streams):
        if not isinstance(st, dict):
            fail(f"streams[{idx}] must be a mapping")
        _validate_stream(idx, st, metrics, fail)
        names.append(st["name"])
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        fail(f"streams: duplicate name(s) {dupes}; each stream needs a "
             f"unique name")


def _validate_sync(sync, fail) -> None:
    """Validate the `sync:` block — the cross-stream synchronization
    contract, discriminated by mode."""
    if not isinstance(sync, dict):
        fail("`sync` must be a mapping")
    missing = _SYNC_REQUIRED - set(sync)
    if missing:
        fail(f"sync: missing key(s) {sorted(missing)}")
    unknown = set(sync) - _SYNC_REQUIRED
    if unknown:
        fail(f"sync: unknown key(s) {sorted(unknown)}; expected "
             f"{sorted(_SYNC_REQUIRED)}")
    if sync["mode"] not in _SYNC_MODES:
        fail(f"sync.mode must be one of {sorted(_SYNC_MODES)}, got "
             f"{sync['mode']!r}. shared_epoch is implemented; ptp is a "
             f"deferred seam; none disables cross-stream start alignment.")
    if sync["termination"] not in _SYNC_TERMINATIONS:
        fail(f"sync.termination must be one of {sorted(_SYNC_TERMINATIONS)}, "
             f"got {sync['termination']!r}")


def _validate_stream(idx: int, st: dict, metrics: list, fail) -> None:
    """Validate one streams[idx] block — the per-stream payload that used
    to be the whole configuration top level. Every error is index-tagged
    with `streams[idx].` so a typo in one camera's spec points at it."""
    def sfail(msg: str):
        fail(f"streams[{idx}].{msg}")

    unknown = set(st) - _STREAM_KEYS
    if unknown:
        sfail(f" unknown key(s) {sorted(unknown)}; expected "
              f"{sorted(_STREAM_KEYS)}")
    missing = _STREAM_REQUIRED - set(st)
    if missing:
        if "source" in missing:
            sfail("source not set — declare inline or pull in via "
                  "`video: <name>`")
        sfail(f" missing key(s) {sorted(missing)}")

    if not isinstance(st["name"], str) or not st["name"]:
        sfail("name must be a non-empty string")
    pr = st["priority"]
    if isinstance(pr, bool) or not isinstance(pr, int) or pr < 0:
        sfail("priority must be a non-negative integer (0 = most important, "
              "shed last; larger sheds first)")

    if not isinstance(st.get("codec"), str):
        sfail("codec must be a string (e.g. 'vp8')")
    if st["codec"] not in _IMPLEMENTED_CODECS:
        sfail(f"codec must be one of {sorted(_IMPLEMENTED_CODECS)}, "
              f"got {st['codec']!r}. Adding a new codec means defining a new "
              f"dataclass in pipeline_config.py mirroring Vp8Codec and "
              f"registering it here + in worker.py's loader.")

    for key, (required, optional) in _BLOCK_SCHEMAS.items():
        block = st.get(key)
        if block is None:
            continue   # presence already checked above
        if not isinstance(block, dict):
            sfail(f"{key} must be a mapping")
        bmissing = required - set(block)
        if bmissing:
            sfail(f"{key}: missing key(s) {sorted(bmissing)}")
        bunknown = set(block) - required - optional
        if bunknown:
            sfail(f"{key}: unknown key(s) {sorted(bunknown)}; expected "
                  f"{sorted(required | optional)}")

    sink = st["sink"]
    if sink["backend"] not in _VALID_SINK_BACKENDS:
        sfail(f"sink.backend must be one of {sorted(_VALID_SINK_BACKENDS)}, "
              f"got {sink['backend']!r}. Visual rendering moved to expo specs "
              f"(see expo.py); configurations carry only measurement sinks.")
    # sink.path is required iff sink.backend == "file"; absent otherwise.
    if sink["backend"] == "file" and "path" not in sink:
        sfail("sink: `path` is required when backend=='file'")
    if sink["backend"] != "file" and "path" in sink:
        sfail(f"sink: `path` only applies when backend=='file' "
              f"(got backend={sink['backend']!r})")

    if "congestion_control" in st:
        _validate_cc(st["congestion_control"], sfail)
    _validate_source(st["source"], sfail)
    _validate_recovery(st["recovery"], sfail)

    # latency_budget_ms — viewer-side per-frame freshness budget. 0
    # disables enforcement; a positive int caps frame age in ms (frames
    # older than the budget at convert.src are dropped and counted by the
    # late_drops metric). Required per the no-silent-defaults rule.
    budget = st["latency_budget_ms"]
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        sfail("latency_budget_ms must be a non-negative integer (0 disables "
              "enforcement; any positive int caps frame age in milliseconds)")

    # Cross-block constraints for decoded_psnr. The metric reproduces this
    # stream's source on the viewer to pair each received frame with its
    # ground truth, so the source must be reproducible (synthetic or file),
    # carry no clock_overlay (wall-clock text the viewer can't reproduce
    # pixel-exactly), and — for the file backend — not loop (camera/viewer
    # seek-on-EOS aren't synchronized).
    if "decoded_psnr" in metrics:
        backend = st["source"].get("backend")
        if backend not in ("synthetic", "file"):
            sfail(f"source: decoded_psnr metric requires source.backend in "
                  f"{{synthetic, file}}, got {backend!r}")
        if st["source"].get("clock_overlay"):
            sfail("source: decoded_psnr metric requires clock_overlay == "
                  "false (overlay burns wall-clock text the viewer can't "
                  "reproduce pixel-exactly)")
        if backend == "file" and (st["source"].get("file") or {}).get("loop"):
            sfail("source: decoded_psnr with backend=='file' requires "
                  "file.loop == false (camera/viewer seek-on-EOS aren't "
                  "synchronized)")


def _validate_source(source, fail) -> None:
    """Validate the source block — backend-discriminated.

    Mirrors _validate_cc: shared required fields, then per-backend
    sub-block validation. Unknown backends (e.g. `backend: camera`)
    are rejected with a precise pointer to the missing SourceBackend
    dataclass + worker registry — not a confusing GStreamer error
    at pipeline-build time.
    """
    if not isinstance(source, dict):
        fail("`source` must be a mapping")
    missing = _SOURCE_REQUIRED - set(source)
    if missing:
        fail(f"source: missing key(s) {sorted(missing)}")
    unknown = set(source) - _SOURCE_REQUIRED - _SOURCE_OPTIONAL_BLOCKS
    if unknown:
        fail(f"source: unknown key(s) {sorted(unknown)}; "
             f"expected {sorted(_SOURCE_REQUIRED | _SOURCE_OPTIONAL_BLOCKS)}")

    backend = source["backend"]
    if backend not in _IMPLEMENTED_SOURCE_BACKENDS:
        fail(f"source.backend `{backend}` is not implemented. Valid "
             f"backends today: {sorted(_IMPLEMENTED_SOURCE_BACKENDS)}. "
             f"Adding a new backend means defining a new dataclass in "
             f"pipeline_config.py mirroring SyntheticSource and registering "
             f"it here + in worker.py's _SOURCE_BACKENDS.")

    # Reject sub-blocks for any other backend than the chosen one.
    others = (_SOURCE_OPTIONAL_BLOCKS - {backend}) & set(source)
    if others:
        fail(f"source: `{sorted(others)[0]}` block set but backend is "
             f"{backend!r}")

    # The chosen backend's sub-block must be present and well-shaped.
    sub = source.get(backend)
    if sub is None:
        fail(f"source: missing `{backend}` block — required when "
             f"backend={backend!r}")
    if not isinstance(sub, dict):
        fail(f"source.{backend} must be a mapping")

    if backend == "synthetic":
        missing_sub = _SYNTHETIC_KNOBS - set(sub)
        if missing_sub:
            fail(f"source.synthetic: missing key(s) {sorted(missing_sub)}")
        unknown_sub = set(sub) - _SYNTHETIC_KNOBS
        if unknown_sub:
            fail(f"source.synthetic: unknown key(s) {sorted(unknown_sub)}; "
                 f"expected {sorted(_SYNTHETIC_KNOBS)}")
        if not isinstance(sub["pattern"], str) or not sub["pattern"]:
            fail("source.synthetic.pattern must be a non-empty string")
    elif backend == "file":
        missing_sub = _FILE_KNOBS - set(sub)
        if missing_sub:
            fail(f"source.file: missing key(s) {sorted(missing_sub)}")
        unknown_sub = set(sub) - _FILE_KNOBS
        if unknown_sub:
            fail(f"source.file: unknown key(s) {sorted(unknown_sub)}; "
                 f"expected {sorted(_FILE_KNOBS)}")
        if not isinstance(sub["path"], str) or not sub["path"]:
            fail("source.file.path must be a non-empty string")
        if not isinstance(sub["loop"], bool):
            fail("source.file.loop must be a bool")
        # We deliberately don't check that the path EXISTS — it's a path
        # on the camera host, not the controller. The pipeline fails
        # loudly at run time if the file is missing or unreadable.


def _validate_recovery(rec, fail) -> None:
    if not isinstance(rec, dict):
        fail("`recovery` must be a mapping")
    required = {"nack", "pli", "fec"}
    optional = {"rtx_buffer_ms", "fec_percentage"}
    missing = required - set(rec)
    if missing:
        fail(f"recovery: missing key(s) {sorted(missing)}")
    unknown = set(rec) - required - optional
    if unknown:
        fail(f"recovery: unknown key(s) {sorted(unknown)}; "
             f"expected {sorted(required | optional)}")
    for k in ("nack", "pli", "fec"):
        if not isinstance(rec[k], bool):
            fail(f"recovery.{k} must be a bool")
    # Conditional: rtx_buffer_ms required iff nack=true; forbidden otherwise.
    # Same conditional pattern as sink.path with backend == 'file'.
    if rec["nack"] and "rtx_buffer_ms" not in rec:
        fail("recovery: `rtx_buffer_ms` is required when nack: true")
    if not rec["nack"] and "rtx_buffer_ms" in rec:
        fail("recovery: `rtx_buffer_ms` only applies when nack: true "
             "(got nack: false)")
    if "rtx_buffer_ms" in rec:
        v = rec["rtx_buffer_ms"]
        if not isinstance(v, int) or v <= 0:
            fail("recovery.rtx_buffer_ms must be a positive integer")
    # Same conditional shape: fec_percentage required iff fec=true.
    if rec["fec"] and "fec_percentage" not in rec:
        fail("recovery: `fec_percentage` is required when fec: true")
    if not rec["fec"] and "fec_percentage" in rec:
        fail("recovery: `fec_percentage` only applies when fec: true "
             "(got fec: false)")
    if "fec_percentage" in rec:
        v = rec["fec_percentage"]
        if not isinstance(v, int) or v < 1 or v > 100:
            fail("recovery.fec_percentage must be an integer in [1, 100]")


def _validate_cc(cc, fail) -> None:
    if not isinstance(cc, dict):
        fail("`congestion_control` must be a mapping")
    missing = _CC_REQUIRED - set(cc)
    if missing:
        fail(f"congestion_control: missing key(s) {sorted(missing)}")
    unknown = set(cc) - _CC_REQUIRED - _CC_OPTIONAL_BLOCKS
    if unknown:
        fail(f"congestion_control: unknown key(s) {sorted(unknown)}; "
             f"expected {sorted(_CC_REQUIRED | _CC_OPTIONAL_BLOCKS)}")

    algo = cc["algorithm"]
    if algo not in ("scream", "gcc"):
        fail(f"congestion_control.algorithm must be 'scream' or 'gcc', got {algo!r}")

    other = "gcc" if algo == "scream" else "scream"
    if other in cc:
        fail(f"congestion_control: `{other}` block set but algorithm is {algo!r}")

    sub = cc.get(algo)
    if sub is not None:
        if not isinstance(sub, dict):
            fail(f"congestion_control.{algo} must be a mapping")
        if algo == "scream":
            allowed = _SCREAM_CAMERA_KNOBS | _SCREAM_VIEWER_KNOBS
        else:
            allowed = _GCC_CAMERA_KNOBS | _GCC_VIEWER_KNOBS
        unknown_sub = set(sub) - allowed
        if unknown_sub:
            fail(f"congestion_control.{algo}: unknown key(s) {sorted(unknown_sub)}; "
                 f"expected (any subset of) {sorted(allowed)}")
        if algo == "scream" and "ect" in sub and sub["ect"] not in _VALID_ECT:
            fail(f"congestion_control.scream.ect must be one of "
                 f"{sorted(_VALID_ECT)}, got {sub['ect']!r}")
        if algo == "gcc" and "estimator" in sub and sub["estimator"] not in _VALID_GCC_ESTIMATORS:
            fail(f"congestion_control.gcc.estimator must be one of "
                 f"{sorted(_VALID_GCC_ESTIMATORS)}, got {sub['estimator']!r}")


def _validate_scenario(scenario, fail) -> None:
    if not isinstance(scenario, dict):
        fail("`scenario` must be a mapping")

    missing = _SCENARIO_REQUIRED - set(scenario)
    if missing:
        fail(f"scenario: missing key(s) {sorted(missing)}")
    unknown = set(scenario) - _SCENARIO_KEYS
    if unknown:
        fail(f"scenario: unknown key(s) {sorted(unknown)}; "
             f"expected {sorted(_SCENARIO_KEYS)}")

    _validate_actors(scenario["actors"], fail)

    for k in ("setup_delay_seconds", "drain_delay_seconds"):
        if not isinstance(scenario[k], (int, float)):
            fail(f"scenario.{k} must be a number")

    metrics = scenario["metrics"]
    if not isinstance(metrics, list) or not all(isinstance(m, str) for m in metrics):
        fail("scenario.metrics must be a list of strings")
    # Cross-check against the metric registry's names so a typo (e.g.
    # frame_count_kbps) fails at config load instead of silently dropping
    # a metric. This deliberately avoids importing gstexp.metrics because
    # that module imports gi/Gst, which is only required on execution hosts.
    for m in metrics:
        if m not in _VALID_METRICS:
            fail(f"scenario.metrics: unknown metric {m!r}; "
                 f"expected one of {sorted(_VALID_METRICS)}")


def _validate_actors(actors, fail) -> None:
    if not isinstance(actors, dict):
        fail("scenario.actors must be a mapping keyed by role")
    missing_roles = _VALID_ROLES - set(actors)
    if missing_roles:
        fail(f"scenario.actors: missing role(s) {sorted(missing_roles)}; "
             f"every scenario must declare {sorted(_VALID_ROLES)}")
    unknown_roles = set(actors) - _VALID_ROLES
    if unknown_roles:
        fail(f"scenario.actors: unknown role(s) {sorted(unknown_roles)}; "
             f"valid roles are {sorted(_VALID_ROLES)}")
    for role, actor in actors.items():
        if not isinstance(actor, dict):
            fail(f"scenario.actors.{role} must be a mapping")
        missing = _ACTOR_REQUIRED - set(actor)
        if missing:
            fail(f"scenario.actors.{role}: missing key(s) {sorted(missing)}")
        unknown = set(actor) - _ACTOR_KEYS
        if unknown:
            fail(f"scenario.actors.{role}: unknown key(s) {sorted(unknown)}; "
                 f"expected {sorted(_ACTOR_KEYS)}")
        if not isinstance(actor["host"], str) or not actor["host"]:
            fail(f"scenario.actors.{role}.host must be a non-empty string")
        if "ssh_host" in actor and not isinstance(actor["ssh_host"], str):
            fail(f"scenario.actors.{role}.ssh_host must be a string")
        if "media_host" in actor and not isinstance(actor["media_host"], str):
            fail(f"scenario.actors.{role}.media_host must be a string")
        if "project_root" in actor and not isinstance(actor["project_root"], str):
            fail(f"scenario.actors.{role}.project_root must be a string")
        if "network_env" in actor:
            env = actor["network_env"]
            if not isinstance(env, dict):
                fail(f"scenario.actors.{role}.network_env must be a mapping")
            for k, v in env.items():
                if not isinstance(k, str) or not isinstance(v, (str, int, float)):
                    fail(f"scenario.actors.{role}.network_env: keys and values "
                         f"must be strings/numbers")
        if "wrap" in actor:
            w = actor["wrap"]
            if not isinstance(w, list) or not all(isinstance(x, str) for x in w):
                fail(f"scenario.actors.{role}.wrap must be a list of strings")


_PORT_BASE = 30000
_PORT_STRIDE = 2 * _MAX_STREAMS    # ports reserved per config (32) — one
                                   # (rtp, rtcp) pair per possible stream


def _port_for_stream(config_id: str, stream_idx: int) -> int:
    """Derive the RTP port for stream `stream_idx` of a configuration.

    Each config owns a contiguous block of _PORT_STRIDE ports starting at
    _PORT_BASE + _PORT_STRIDE * int(config_id); stream i takes the pair
    (base + 2*i, base + 2*i + 1) (rtp, rtcp — RFC 3550 adjacency). So
    config 11's stream 0 is (30352, 30353), stream 1 (30354, 30355), and
    config 12 starts cleanly at 30384. Every pair is unique by
    construction across the whole (config, stream) map, so concurrent
    runs and co-located streams never collide on bind. Configurations
    don't carry a `transport:` block — the port choice is mechanical.

    Numeric config ids only today; a named config would need a hashing
    scheme + load-time uniqueness check, added here when the first
    non-numeric configuration appears.
    """
    if not config_id.isdigit():
        sys.exit(f"non-numeric config id {config_id!r}: port derivation "
                 f"expects numeric ids. Either rename the configuration or "
                 f"extend validation._port_for_stream with a hashing scheme.")
    if not 0 <= stream_idx < _MAX_STREAMS:
        sys.exit(f"stream index {stream_idx} out of range "
                 f"[0, {_MAX_STREAMS}); raise _MAX_STREAMS to widen the "
                 f"per-config port block.")
    return _PORT_BASE + _PORT_STRIDE * int(config_id) + 2 * stream_idx


def project_to_roles(doc: dict, config_id: str) -> tuple[list, list]:
    """Project a multi-stream configuration into per-role, per-stream specs.

    Returns (camera_dicts, viewer_dicts): one Camera dict and one Viewer
    dict per entry in doc["streams"], index-aligned (camera_dicts[i] and
    viewer_dicts[i] are the two ends of stream i). Each dict is shaped to
    the Camera / Viewer dataclasses in pipeline_config and is fully formed
    — host topology and the per-stream (rtp, rtcp) port pair are injected
    here so each of the N flows binds a unique socket on the shared NIC and
    the worker loaders can consume the dict as-is. Multiplicity lives only
    here and in orchestration; each worker still receives one single-stream
    spec and never knows N > 1 exists.
    """
    actors = doc["scenario"]["actors"]
    camera_host = actors["camera"].get("media_host") or actors["camera"]["host"]
    viewer_host = actors["viewer"].get("media_host") or actors["viewer"]["host"]
    metrics = doc["scenario"].get("metrics", [])

    camera_dicts: list = []
    viewer_dicts: list = []
    for idx, st in enumerate(doc["streams"]):
        camera, viewer = _project_stream(
            idx, st, config_id, camera_host, viewer_host, metrics)
        camera_dicts.append(camera)
        viewer_dicts.append(viewer)
    return camera_dicts, viewer_dicts


def _project_stream(idx: int, st: dict, config_id: str,
                    camera_host: str, viewer_host: str,
                    metrics: list) -> tuple[dict, dict]:
    """Project one streams[idx] entry into (camera_dict, viewer_dict).

    The mirror fields (port, codec, CC algorithm) are derived once here so
    a stream can't disagree with itself; the (rtp, rtcp) pair follows
    RFC 3550 (rtcp = rtp + 1) and is unique per (config, stream) by
    construction via _port_for_stream.
    """
    codec = st["codec"]
    enc = st["encoder"]
    cc = st.get("congestion_control")
    sink = st["sink"]
    source = st["source"]

    port = _port_for_stream(config_id, idx)
    rtcp_port = port + 1

    camera = {
        "source": dict(source),
        "encoder": {
            "codec": codec,
            "bitrate_kbps": enc["bitrate_kbps"],
            "keyframe_interval_frames": enc["keyframe_interval_frames"],
        },
        "packetizer": {"protocol": "rtp"},
        "egress": {
            "transport": "udp",
            "port": port,
            "rtcp_port": rtcp_port,
            "host": viewer_host,           # destination = the other actor
            "bind_host": camera_host,      # local bind for RTCP listener
            "stream_id": idx,
        },
    }

    viewer = {
        "ingress": {
            "transport": "udp",
            "port": port,
            "rtcp_port": rtcp_port,
            "host": viewer_host,           # local bind
            "peer_host": camera_host,      # where RTCP feedback goes
            "stream_id": idx,
        },
        "depacketizer": {"protocol": "rtp"},
        "decoder": {"codec": codec},
        "sink": dict(sink),
    }

    if cc:
        camera_cc, viewer_cc = _project_cc(cc)
        camera["congestion_control"] = camera_cc
        viewer["congestion_control"] = viewer_cc

    # Recovery: both roles need nack/pli/fec flags. The camera additionally
    # gets rtx_buffer_ms (rtprtxsend ring depth) when nack is on, and
    # fec_percentage (rtpulpfecenc overhead) when fec is on.
    rec = st["recovery"]
    camera["recovery"] = {"nack": rec["nack"], "pli": rec["pli"], "fec": rec["fec"]}
    if rec["nack"]:
        camera["recovery"]["rtx_buffer_ms"] = rec["rtx_buffer_ms"]
    if rec["fec"]:
        camera["recovery"]["fec_percentage"] = rec["fec_percentage"]
    viewer["recovery"] = {"nack": rec["nack"], "pli": rec["pli"], "fec": rec["fec"]}

    # decoded_psnr reproduces this stream's source on the viewer to score
    # delivered frames against ground truth; attach it only when enabled.
    if "decoded_psnr" in metrics:
        viewer["ground_truth"] = dict(source)

    # The viewer enforces this stream's latency budget at convert.src.
    viewer["latency_budget_ms"] = st["latency_budget_ms"]

    return camera, viewer


def _project_cc(cc: dict) -> tuple[dict, dict]:
    """Split the unified CC block into per-role dicts.

    The bitrate bounds always go to the camera. The algorithm-specific
    sub-block (`scream:` or `gcc:`) is dispatched per-knob: SCReAM knobs
    are all camera-side; GCC `estimator` is camera-side, while
    `twcc_feedback_interval_ms` is viewer-side.
    """
    algo = cc["algorithm"]
    bitrate = {
        "init_bitrate_kbps": cc["init_bitrate_kbps"],
        "min_bitrate_kbps":  cc["min_bitrate_kbps"],
        "max_bitrate_kbps":  cc["max_bitrate_kbps"],
    }
    sub = cc.get(algo, {})
    if algo == "scream":
        camera = {"algorithm": "scream", **bitrate, **sub}
        viewer = {"algorithm": "scream"}       # screamrx exposes nothing
    else:                                      # algo == "gcc"
        camera_sub = {k: v for k, v in sub.items() if k in _GCC_CAMERA_KNOBS}
        viewer_sub = {k: v for k, v in sub.items() if k in _GCC_VIEWER_KNOBS}
        camera = {"algorithm": "gcc", **bitrate, **camera_sub}
        viewer = {"algorithm": "gcc", **viewer_sub}
    return camera, viewer
