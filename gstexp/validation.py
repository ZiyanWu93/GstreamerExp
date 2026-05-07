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
_TOP_LEVEL_REQUIRED = {"meta", "scenario", "source", "codec", "encoder",
                       "sink", "recovery", "latency_budget_ms"}
_TOP_LEVEL_OPTIONAL = {"congestion_control", "hooks"}
_TOP_LEVEL_KEYS = _TOP_LEVEL_REQUIRED | _TOP_LEVEL_OPTIONAL

# `scenario` carries run orchestration. The `remote:` block is optional —
# its presence triggers distributed mode. `wrap_sender`/`wrap_receiver`
# are optional because most configurations don't use a wrapper.
# Scenario carries run orchestration. The `actors:` block is required
# and keyed by role; today the role set is exactly {camera, viewer}.
# All per-host configuration (host, project_root, network_env, wrap)
# lives inside the actor block — there is no top-level `network_env` or
# `remote.project_root` anymore. Per-actor scope is what unlocks
# asymmetric path impairment (each actor's NIC shapes its own egress).
_SCENARIO_REQUIRED = {"actors", "setup_delay_seconds",
                      "drain_delay_seconds", "metrics"}
_SCENARIO_OPTIONAL: set = set()
_SCENARIO_KEYS = _SCENARIO_REQUIRED | _SCENARIO_OPTIONAL

_VALID_ROLES = {"camera", "viewer"}
_ACTOR_REQUIRED = {"host"}
_ACTOR_OPTIONAL = {"project_root", "network_env", "wrap"}
_ACTOR_KEYS = _ACTOR_REQUIRED | _ACTOR_OPTIONAL


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
        unknown = set(s) - _STEP_KEYS
        if unknown:
            sys.exit(f"{where}: {label}[{i}] unknown key(s) {sorted(unknown)}; "
                     f"expected {sorted(_STEP_KEYS)}")
        _validate_step_loss(s["loss"], f"{where}: {label}[{i}]",
                            lambda m: sys.exit(m))


def _compile_role_steps(role: str, steps: list) -> dict | None:
    """Compile one direction's step list into pre_run / during_run /
    post_run hooks tagged with `host: <role>`. Returns None when steps
    is empty (no impairment on that direction → no hooks needed).

    The pre_run hook installs the qdiscs for step 1. The during_run hook
    (only generated when there is more than one step) sleeps for the
    previous step's duration, then transitions into the next. Transitions
    that touch a `bursty` (Gilbert-Elliott) step on either side use
    `tc qdisc del`+`add` — netem doesn't document whether GE Markov state
    survives a `tc qdisc change`, so a hard reset guarantees the bursty
    regime starts from a known initial state on every entry. State-free
    transitions (no bursty on either side) use the atomic `change` form.
    The post_run hook tears down the qdisc tree.

    Each generated script references "$NIC" — the configuration must set
    the interface name via the actor's network_env (resolve_includes
    pairs each hook with its actor's env).
    """
    if not steps:
        return None

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
        clauses = [f"delay {s['delay_ms']}ms"]
        loss = _loss_clause(s)
        if loss is not None:
            clauses.append(loss)
        return " ".join(clauses)

    def _burst_kbit(s):
        # ~10 ms of headroom at the configured rate; floor at 8 kbit so
        # even sub-Mbps rates can dequeue a single MTU.
        return max(8, s["rate_kbps"] // 100)

    # Shaping is scoped to peer-bound traffic only — the prio root is a
    # passthrough for everything else. Without this scope, `tc ... root`
    # shapes every packet leaving $NIC, including SSH, DNS, and internet
    # traffic when $NIC is the host's default-route interface (which it
    # is on aum/veda — the USB ethernet is the only active link). Band
    # 1:1 carries traffic the filter classifies in (peer-bound IPv4);
    # band 1:2 is the priomap default and stays at prio's implicit pfifo.
    # IPv6 traffic falls through to band 1:2 unshaped — all our test
    # flows are IPv4 (see resolve_includes for the PEER_IP injection).
    def _install_shaper(s, action):
        return [
            f"tc qdisc {action} dev \"$NIC\" parent 1:1 handle 10: "
            f"netem {_netem_clause(s)}",
            f"tc qdisc {action} dev \"$NIC\" parent 10:1 handle 20: "
            f"tbf rate {s['rate_kbps']}kbit burst {_burst_kbit(s)}kbit latency 50ms",
        ]

    def _install_root():
        # Idempotent: del any prior root, then install prio + filter.
        return [
            'tc qdisc del dev "$NIC" root 2>/dev/null || true',
            'tc qdisc add dev "$NIC" root handle 1: prio bands 2 '
            'priomap 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1',
            'tc filter add dev "$NIC" parent 1:0 protocol ip prio 1 u32 '
            'match ip dst "$PEER_IP"/32 flowid 1:1',
        ]

    def _reinstall(s):
        # Hard reset: del+add the shaper subtree (netem+tbf). Used at any
        # transition involving bursty loss to guarantee GE Markov state
        # starts fresh. The prio root + filter are preserved.
        return [
            'tc qdisc del dev "$NIC" parent 1:1 handle 10: 2>/dev/null || true',
            *_install_shaper(s, "add"),
        ]

    def _loss_descr(s):
        loss = s["loss"]
        if loss["model"] == "none":
            return "loss=none"
        if loss["model"] == "uniform":
            return f"loss={loss['pct']}% uniform"
        p, r = _gemodel_p_r(loss["pct"], loss["burst"])
        return (f"loss={loss['pct']}% bursty "
                f"(B={loss['burst']}, p={p:.2f}%, r={r:.1f}%)")

    def _step_log(idx: int, s: dict) -> str:
        return (f"{role} step {idx+1}: rate={s['rate_kbps']}kbit "
                f"delay={s['delay_ms']}ms {_loss_descr(s)} — {s['label']}")

    s0 = steps[0]
    pre_lines = [
        "set -e",
        *_install_root(),
        *_install_shaper(s0, "add"),
        f'echo "[net/{role}] $NIC initial — {_step_log(0, s0)}"',
    ]
    hooks = {
        "pre_run": {
            "host": role, "sudo": True,
            "script": "\n".join(pre_lines) + "\n",
        },
    }

    if len(steps) > 1:
        during_lines = ["set -e"]
        for i, s in enumerate(steps[1:], start=1):
            during_lines.append(f"sleep {steps[i-1]['duration']}")
            prev_bursty = steps[i-1]["loss"]["model"] == "bursty"
            this_bursty = s["loss"]["model"] == "bursty"
            if prev_bursty or this_bursty:
                during_lines.extend(_reinstall(s))
            else:
                during_lines.extend(_install_shaper(s, "change"))
            during_lines.append(
                f'echo "[net/{role}] $(date +%H:%M:%S) — {_step_log(i, s)}"'
            )
        hooks["during_run"] = {
            "host": role, "sudo": True,
            "script": "\n".join(during_lines) + "\n",
        }

    hooks["post_run"] = {
        "host": role, "sudo": True,
        "script": (
            'tc qdisc del dev "$NIC" root 2>/dev/null || true\n'
            f'echo "[net/{role}] $NIC qdiscs removed"\n'
        ),
    }
    return hooks


def _compile_network_steps(spec_path: Path, spec: dict) -> dict:
    """Compile a network spec's per-direction step lists into hook lists.

    Each network spec declares two step sequences:
      camera_steps:  shaping on the camera actor's egress (forward path)
      viewer_steps:  shaping on the viewer actor's egress (return path)

    Both required, both may be empty. The two compile independently — they
    can have different step counts and different step durations; the
    runner spawns each phase's hook list per actor and waits for all.

    Returns a dict where each phase value is a LIST of hooks (possibly
    empty). The runner iterates the list per phase.
    """
    where = spec_path.name
    camera_steps = spec.get("camera_steps", [])
    viewer_steps = spec.get("viewer_steps", [])
    _validate_steps_block(camera_steps, "camera_steps", where)
    _validate_steps_block(viewer_steps, "viewer_steps", where)

    camera_hooks = _compile_role_steps("camera", camera_steps)
    viewer_hooks = _compile_role_steps("viewer", viewer_steps)

    out: dict = {"pre_run": [], "during_run": [], "post_run": []}
    for role_hooks in (camera_hooks, viewer_hooks):
        if role_hooks is None:
            continue
        for phase, hook in role_hooks.items():
            out[phase].append(hook)
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

    video_ref = doc.pop("video", None)
    if video_ref is not None:
        if doc.get("source") is not None:
            sys.exit(f"{where}: cannot set both `video: {video_ref}` "
                     f"and inline `source`")
        spec_path = project_root / "specs" / "videos" / f"{video_ref}.yaml"
        if not spec_path.is_file():
            sys.exit(f"{where}: video spec not found at {spec_path}")
        spec = yaml.safe_load(spec_path.read_text()) or {}
        if "source" not in spec:
            sys.exit(f"{spec_path.name}: missing `source` block")
        doc["source"] = spec["source"]

    network_ref = doc.pop("network", None)
    if network_ref is not None:
        if doc.get("hooks"):
            sys.exit(f"{where}: cannot set both `network: {network_ref}` "
                     f"and inline `hooks`")
        spec_path = project_root / "specs" / "networks" / f"{network_ref}.yaml"
        if not spec_path.is_file():
            sys.exit(f"{where}: network spec not found at {spec_path}")
        spec = yaml.safe_load(spec_path.read_text()) or {}
        unknown_spec_keys = set(spec) - _NETWORK_SPEC_KEYS
        if unknown_spec_keys:
            sys.exit(f"{spec_path.name}: unknown key(s) "
                     f"{sorted(unknown_spec_keys)}; expected "
                     f"{sorted(_NETWORK_SPEC_KEYS)}")
        hooks = _compile_network_steps(spec_path, spec)
        # Pull the per-actor network_env from the scenario block. Each
        # hook's `host` field names the actor whose env should prefix
        # the script. Hooks are list-valued per phase, so iterate one
        # level deeper. We do basic shape checks here (validate_doc runs
        # later for the deep checks) — just enough to hand the right
        # dict to the right hook.
        actors = (doc.get("scenario") or {}).get("actors") or {}
        # PEER_IP is auto-injected from actor topology: the camera's peer
        # is the viewer's host and vice versa. The shaping scripts use it
        # in a `tc filter ... match ip dst $PEER_IP` clause so only
        # peer-bound traffic gets impaired (not SSH / DNS / internet on
        # the host's default-route NIC).
        peer_role = {"camera": "viewer", "viewer": "camera"}
        for hook_list in hooks.values():
            for hook in hook_list:
                role = hook.get("host")
                if role not in actors:
                    continue   # local or non-actor hosts get no env prefix
                env = dict((actors[role] or {}).get("network_env") or {})
                peer = peer_role.get(role)
                if peer and peer in actors:
                    peer_host = (actors[peer] or {}).get("host")
                    if peer_host:
                        env.setdefault("PEER_IP", peer_host)
                if hook["script"] and not env:
                    sys.exit(f"{where}: network `{network_ref}` runs `tc` "
                             f"against $NIC for the {role} actor, but "
                             f"`scenario.actors.{role}.network_env` is "
                             f"unset — set NIC there.")
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
        if "source" in missing_top:
            fail("`source` not set — declare inline or pull in via `video: <name>`")
        fail(f"missing required block(s): {sorted(missing_top)}")

    if not isinstance(doc.get("codec"), str):
        fail("`codec` must be a string (e.g. 'vp8')")
    if doc["codec"] not in _IMPLEMENTED_CODECS:
        fail(f"`codec` must be one of {sorted(_IMPLEMENTED_CODECS)}, "
             f"got {doc['codec']!r}. Adding a new codec means defining a "
             f"new dataclass in pipeline_config.py mirroring Vp8Codec "
             f"and registering it here + in worker.py's loader.")

    for key, (required, optional) in _BLOCK_SCHEMAS.items():
        block = doc.get(key)
        if block is None:
            continue   # presence/absence already checked at the top level
        if not isinstance(block, dict):
            fail(f"`{key}` must be a mapping")
        missing = required - set(block)
        if missing:
            fail(f"{key}: missing key(s) {sorted(missing)}")
        unknown = set(block) - required - optional
        if unknown:
            fail(f"{key}: unknown key(s) {sorted(unknown)}; "
                 f"expected {sorted(required | optional)}")

    sink = doc["sink"]
    if sink["backend"] not in _VALID_SINK_BACKENDS:
        fail(f"sink.backend must be one of {sorted(_VALID_SINK_BACKENDS)}, "
             f"got {sink['backend']!r}. Visual rendering moved to expo specs "
             f"(see expo.py); configurations carry only measurement sinks.")
    # sink.path is required iff sink.backend == "file"; for any other
    # backend it must be absent so the spec doesn't carry a dead field.
    if sink["backend"] == "file" and "path" not in sink:
        fail("sink: `path` is required when backend=='file'")
    if sink["backend"] != "file" and "path" in sink:
        fail(f"sink: `path` only applies when backend=='file' "
             f"(got backend={sink['backend']!r})")

    if "congestion_control" in doc:
        _validate_cc(doc["congestion_control"], fail)

    _validate_source(doc["source"], fail)
    _validate_recovery(doc["recovery"], fail)
    _validate_scenario(doc["scenario"], fail)

    # latency_budget_ms — viewer-side enforcement budget for frames'
    # operational freshness. 0 disables enforcement (frames are
    # delivered regardless of age); any positive int is a hard cap in
    # milliseconds: any frame older than the budget at convert.src is
    # dropped and counted by the late_drops metric. Required because
    # operational policy must be set explicitly per the project's
    # no-silent-defaults rule.
    budget = doc["latency_budget_ms"]
    if not isinstance(budget, int) or budget < 0:
        fail("latency_budget_ms must be a non-negative integer "
             "(0 disables enforcement; any positive int caps frame age "
             "in milliseconds)")

    # Cross-block constraints for the decoded_psnr metric. The metric
    # reproduces the source on the viewer side to pair each received
    # frame with its ground-truth counterpart, so the source must be:
    #   1. Reproducible at the viewer — synthetic patterns (deterministic
    #      from backend+dims+fps) or file-backed (the same file staged
    #      at the same path on the viewer host).
    #   2. Without clock_overlay — the overlay burns wall-clock text
    #      into the Y plane, and the viewer's reproduced source can't
    #      match the camera's clock exactly. Pixel-exact comparison
    #      would attribute overlay drift to network corruption.
    #   3. For file backend: loop=false. Looping makes frame N (N >
    #      file_frame_count) refer to the (N mod file_frame_count)-th
    #      frame of the next iteration, but the camera-side seek and
    #      the viewer-side seek aren't synchronized, so the two streams
    #      drift out of phase. Require single-pass playback for now.
    if "decoded_psnr" in doc["scenario"].get("metrics", []):
        backend = doc["source"].get("backend")
        if backend not in ("synthetic", "file"):
            fail(f"scenario.metrics: decoded_psnr requires "
                 f"source.backend in {{synthetic, file}}, got {backend!r}")
        if doc["source"].get("clock_overlay"):
            fail("scenario.metrics: decoded_psnr requires "
                 "source.clock_overlay == false (overlay burns wall-clock "
                 "text that the viewer can't reproduce pixel-exactly)")
        if backend == "file":
            if (doc["source"].get("file") or {}).get("loop"):
                fail("scenario.metrics: decoded_psnr with source.backend "
                     "== 'file' requires source.file.loop == false "
                     "(camera and viewer seek-on-EOS aren't synchronized)")


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
    # Cross-check against the metric registry so a typo (e.g. frame_count_kbps)
    # fails at config load instead of silently dropping a metric.
    from gstexp.metrics import METRIC_CLASSES
    for m in metrics:
        if m not in METRIC_CLASSES:
            fail(f"scenario.metrics: unknown metric {m!r}; "
                 f"expected one of {sorted(METRIC_CLASSES)}")


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


def _port_for_config_id(config_id: str) -> int:
    """Derive the RTP port for a configuration from its id.

    Convention: port = _PORT_BASE + 2 * int(config_id), so config 7's
    port pair is (30014, 30015) and config 11's is (30022, 30023).
    Each configuration gets a unique pair by construction; concurrent
    runs can't collide on bind. Configurations don't carry a `transport:`
    block — the port choice was the only field there, and it's mechanical.

    Numeric config ids only today. A name like 'scream-baseline' would
    need a hashing scheme + load-time uniqueness check, added here when
    the first non-numeric configuration appears.
    """
    if not config_id.isdigit():
        sys.exit(f"non-numeric config id {config_id!r}: port derivation "
                 f"expects numeric ids. Either rename the configuration or "
                 f"extend validation._port_for_config_id with a hashing scheme.")
    return _PORT_BASE + 2 * int(config_id)


def project_to_roles(doc: dict, config_id: str) -> tuple[dict, dict]:
    """Project the unified configuration into per-role specs.

    Returns (camera_dict, viewer_dict) shaped to the Camera / Viewer
    dataclasses in pipeline_config — fully formed and ready for the
    worker's loaders. The mirror fields (port, codec, transport
    protocol, CC algorithm) are derived once here, eliminating the
    silent-bug surface where a config could disagree with itself.

    Topology fields (egress.host / egress.bind_host / ingress.host /
    ingress.peer_host) are read from `scenario.actors.{camera,viewer}.host`
    and injected here. Earlier, this injection lived in cli.main() —
    that left project_to_roles producing incomplete dicts, so any caller
    outside cli.py would hit a TypeError when constructing the Egress /
    Ingress dataclasses. Owning the injection here makes the function's
    contract honest: its output is consumable as-is.
    """
    codec = doc["codec"]
    enc = doc["encoder"]
    cc = doc.get("congestion_control")
    sink = doc["sink"]
    source = doc["source"]
    actors = doc["scenario"]["actors"]
    camera_host = actors["camera"]["host"]
    viewer_host = actors["viewer"]["host"]

    # Port and rtcp_port are mechanical — derived from the config id with
    # the rtp/rtcp pair following RFC 3550 convention (rtcp = rtp + 1).
    # Configurations don't carry a transport block; the user's job is to
    # pick the experimental dimensions, not coordinate sockets.
    port = _port_for_config_id(config_id)
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
        },
    }

    viewer = {
        "ingress": {
            "transport": "udp",
            "port": port,
            "rtcp_port": rtcp_port,
            "host": viewer_host,           # local bind
            "peer_host": camera_host,      # where RTCP feedback goes
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
    # fec_percentage (rtpulpfecenc overhead) when fec is on. RTX and FEC
    # PT mappings are hardcoded in the pipeline — implementation detail,
    # not user knobs.
    rec = doc["recovery"]
    camera["recovery"] = {"nack": rec["nack"], "pli": rec["pli"], "fec": rec["fec"]}
    if rec["nack"]:
        camera["recovery"]["rtx_buffer_ms"] = rec["rtx_buffer_ms"]
    if rec["fec"]:
        camera["recovery"]["fec_percentage"] = rec["fec_percentage"]
    viewer["recovery"] = {"nack": rec["nack"], "pli": rec["pli"], "fec": rec["fec"]}

    # The decoded_psnr metric on the viewer needs the camera's source
    # to reproduce ground-truth frames. Only attach when the metric is
    # enabled — otherwise the viewer spec stays minimal. Cross-block
    # validation in validate_doc has already confirmed source.backend
    # is synthetic when this branch fires.
    if "decoded_psnr" in doc["scenario"].get("metrics", []):
        viewer["ground_truth"] = dict(source)

    # The viewer enforces the latency budget — a hard cap on per-frame
    # operational age, applied at convert.src by the late_drops metric.
    # Camera doesn't see it.
    viewer["latency_budget_ms"] = doc["latency_budget_ms"]

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
