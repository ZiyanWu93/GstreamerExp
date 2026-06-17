"""Scenario runners — local controller and remote workers.

run_local() spawns camera and viewer workers as child processes on
this host. run_distributed() treats this host as the controller: it
syncs the minimal worker payload to two remote workers, measures clock
skew, spawns workers via SSH, and fetches result files back. Both
runners honour the same hook contract (pre_run / during_run / post_run)
and the same env-injection rules.

This module also owns the SSH primitives (with ControlMaster caching),
the clock-skew TTL cache, and the scream_env helper that lets a local
child process find the gstscream plugin.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


# Cache SSH connections via ControlMaster — drastically reduces per-call
# latency (especially for clock-skew measurement, where SSH connection
# setup variance dominates).
_SSH_OPTS = [
    "-o", "ControlMaster=auto",
    "-o", "ControlPath=/tmp/gstexp-ssh-%C",
    "-o", "ControlPersist=120",
]


def _ssh(host: str, remote_cmd: str, **kwargs):
    """Run a command on `host` via SSH. The command is wrapped in
    `bash -lc '…'` and passed as a single arg, so the remote shell
    (which may be fish) doesn't split it."""
    invocation = f"bash -lc {shlex.quote(remote_cmd)}"
    return subprocess.run(["ssh", *_SSH_OPTS, host, invocation], **kwargs)


def _ssh_popen(host: str, remote_cmd: str):
    invocation = f"bash -lc {shlex.quote(remote_cmd)}"
    return subprocess.Popen(["ssh", *_SSH_OPTS, host, invocation])


_SKEW_CACHE_TTL = 300.0   # seconds; skew is stable enough over this window

# Cap on how long the runner waits for the camera worker to exit.
# A normal run is ~25 s (20 s pipeline + setup/drain delays + ssh
# overhead). vp8 / scream cleanup sometimes wedges in
# `set_state(NULL)` after EOS — without a timeout the runner sits
# in `wait()` forever, eating the rest of the experiment. 90 s
# leaves comfortable margin for normal runs and short-circuits
# wedged ones into the ExitStack cleanup path.
_WORKER_WAIT_TIMEOUT_S = 90.0


def _worker_payload_rsync_args(project_root: Path, host: str,
                               remote_project_root: str) -> list[str]:
    """Build the rsync command for the remote worker payload.

    The controller keeps specs, runs, analysis, docs, and working notes
    locally. Workers only need the Python runtime package plus setup
    scripts. SCReAM source/build output is intentionally not synced:
    scripts/setup_remote.sh owns that remote-local checkout and build
    cache, so repeated runs don't churn large artifacts over SSH.
    """
    include_rules = [
        "--include=/gstexp/***",
        "--include=/scripts/",
        "--include=/scripts/setup_remote.sh",
        "--include=/scripts/scream-eos-fix.patch",
        "--include=/scripts/build_gstreamer.sh",
        "--include=/scripts/fix_clock.sh",
        "--include=/requirements.txt",
        "--include=/pyproject.toml",
        "--exclude=*",
    ]
    return [
        "rsync", "-az", "--delete", "--prune-empty-dirs",
        *include_rules,
        f"{project_root}/", f"{host}:{remote_project_root}/",
    ]


def _sync_worker_payload(project_root: Path, host: str,
                         remote_project_root: str) -> None:
    print(f"[controller] sync worker payload → {host}", flush=True)
    subprocess.run(
        _worker_payload_rsync_args(project_root, host, remote_project_root),
        check=True,
    )


def _measure_clock_skew(host: str, samples: int = 7) -> float:
    """Estimate (host_clock - controller_clock) seconds.

    Pre-warms the SSH ControlMaster connection so subsequent measurement
    samples have minimal RTT variance. Then runs `date +%s.%N` N times,
    using the round-trip midpoint as the controller's estimate of when
    the remote measurement happened. Returns the sample with the
    SMALLEST round-trip time (least uncertain), not the median — the
    fastest sample is the most accurate because RTT can only inflate,
    never shrink, the per-sample uncertainty.

    Result is cached in /tmp with _SKEW_CACHE_TTL seconds of validity. A
    repeat experiment within that window (the common case for `experiment.py`)
    skips the SSH probes entirely.
    """
    cache = Path(f"/tmp/gstexp-skew-{host.replace('/', '_')}.json")
    if cache.exists() and time.time() - cache.stat().st_mtime < _SKEW_CACHE_TTL:
        try:
            return json.loads(cache.read_text())["skew_s"]
        except Exception:
            pass   # corrupt cache; fall through and re-measure

    _ssh(host, "true", check=False)   # warm ControlMaster
    best = None
    best_rtt = float("inf")
    for _ in range(samples):
        t0 = time.time()
        r = _ssh(host, "date +%s.%N", capture_output=True, check=True)
        t1 = time.time()
        rtt = t1 - t0
        try:
            host_t = float(r.stdout.decode().strip())
        except Exception:
            continue
        if rtt < best_rtt:
            best_rtt = rtt
            best = host_t - (t0 + t1) / 2

    skew = best if best is not None else 0.0
    cache.write_text(json.dumps({"skew_s": skew, "measured_at": time.time()}))
    return skew


def scream_env(project_root: Path) -> dict:
    """Build a child-process env that lets GStreamer find gstscream.

    Copies libgstscream.so to a clean dir (the scanner hangs otherwise on
    the deps/ subfolder) and prepends GST_PLUGIN_PATH and LD_LIBRARY_PATH.
    No-op if scream isn't built; child gets the unmodified parent env.
    """
    env = dict(os.environ)
    plugin_so = project_root / "scream" / "gstscream" / "target" / "debug" / "libgstscream.so"
    lib_dir = project_root / "scream" / "code" / "wrapper_lib"
    if not plugin_so.exists() or not lib_dir.exists():
        return env

    clean_dir = Path(f"/tmp/gst-scream-plugin-{os.geteuid()}")
    clean_dir.mkdir(exist_ok=True)
    target = clean_dir / "libgstscream.so"
    if not target.exists() or plugin_so.stat().st_mtime > target.stat().st_mtime:
        try:
            shutil.copy2(plugin_so, target)
        except PermissionError:
            pass   # existing copy is fine

    pp = env.get("GST_PLUGIN_PATH", "")
    env["GST_PLUGIN_PATH"] = str(clean_dir) + (":" + pp if pp else "")
    lp = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = str(lib_dir) + (":" + lp if lp else "")
    return env


# ---- hooks ----------------------------------------------------------------

def _run_hook_sync(name: str, hooks: list, *,
                   camera_host: str | None, viewer_host: str | None,
                   project_root: Path, check: bool) -> None:
    """Execute a list of typed hooks to completion, sequentially. Schema:
        host:    'local' | 'camera' | 'viewer' (default 'local')
        sudo:    bool (default false)
        script:  multi-line shell, fed to bash -s on stdin

    Empty list is a no-op. Each hook runs to completion before the next
    starts. Order = list order; for setup phases the camera/viewer hooks
    are independent (different NICs) so order doesn't matter functionally.
    """
    for i, hook in enumerate(hooks or []):
        where = hook.get("host", "local")
        sudo = bool(hook.get("sudo", False))
        script = hook.get("script", "")
        suffix = f"[{i}]" if len(hooks) > 1 else ""
        print(f"[scenario] {name}{suffix} ({where}{', sudo' if sudo else ''})",
              flush=True)
        if where == "local":
            cmd = (["sudo", "bash", "-s"] if sudo else ["bash", "-s"])
            subprocess.run(cmd, input=script, text=True,
                           cwd=str(project_root), check=check)
            continue
        target = camera_host if where == "camera" else viewer_host
        if not target:
            sys.exit(f"hook {name!r} has host={where!r} but no {where}_host configured")
        remote = "sudo bash -s" if sudo else "bash -s"
        subprocess.run(["ssh", *_SSH_OPTS, target, remote],
                       input=script, text=True, check=check)


def _spawn_hook_async(name: str, hooks: list, *,
                      camera_host: str | None, viewer_host: str | None,
                      project_root: Path) -> list:
    """Spawn a list of hooks in the background — used for during_run,
    which runs concurrently with the worker for its full duration. Each
    hook in the list spawns its own subprocess; returns the list of
    Popen objects. Empty list returns []."""
    procs: list = []
    for i, hook in enumerate(hooks or []):
        where = hook.get("host", "local")
        sudo = bool(hook.get("sudo", False))
        script = hook.get("script", "")
        suffix = f"[{i}]" if len(hooks) > 1 else ""
        print(f"[scenario] {name}{suffix} ({where}"
              f"{', sudo' if sudo else ''}, async)", flush=True)
        if where == "local":
            cmd = (["sudo", "bash", "-s"] if sudo else ["bash", "-s"])
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True,
                                 cwd=str(project_root))
        else:
            target = camera_host if where == "camera" else viewer_host
            if not target:
                sys.exit(f"hook {name!r} has host={where!r} but no {where}_host configured")
            remote = "sudo bash -s" if sudo else "bash -s"
            p = subprocess.Popen(["ssh", *_SSH_OPTS, target, remote],
                                 stdin=subprocess.PIPE, text=True)
        p.stdin.write(script)
        p.stdin.close()
        procs.append(p)
    return procs


# ---- cleanup helpers ------------------------------------------------------
#
# Each helper is best-effort: it tolerates the resource being already gone
# (worker exited normally, file already removed), wraps every subprocess
# call in `check=False`, and never raises. This is the contract the
# ExitStack-based cleanup in the runners depends on — every callback
# unwinds independently, and one failing teardown can't skip the rest.

def _stop_during_procs(procs: list) -> None:
    """SIGTERM each during_run process, then SIGKILL on 5s timeout. Idempotent."""
    for p in procs:
        if p.poll() is not None:
            continue
        try:
            p.terminate()
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
                p.wait(timeout=5)
            except Exception:
                pass
        except Exception:
            pass


def _kill_remote_worker(host: str, pid_file: str,
                        popen: subprocess.Popen, label: str) -> None:
    """Tear down a worker spawned via _ssh_popen.

    SIGTERM through the worker's PID file (it writes its own PID to that
    file at startup). SIGHUP propagation across SSH is unreliable on
    this setup, and the SSH parent dying without explicit-kill is what
    produces orphan processes — hence the PID-file path. Then wait on
    the local SSH popen with a timeout, escalating to SIGKILL through
    the same PID file if needed.
    """
    print(f"[scenario] stopping {label} on {host}", flush=True)
    try:
        _ssh(host,
             f"[ -f {pid_file} ] && kill -TERM $(cat {pid_file}) 2>/dev/null; true",
             check=False, capture_output=True)
    except Exception:
        pass
    try:
        popen.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            _ssh(host,
                 f"[ -f {pid_file} ] && kill -KILL $(cat {pid_file}) 2>/dev/null; true",
                 check=False, capture_output=True)
        except Exception:
            pass
        try:
            popen.kill()
            popen.wait(timeout=5)
        except Exception:
            pass


def _kill_local_worker(popen: subprocess.Popen, *,
                       wrap: list, spec_path: Path, label: str) -> None:
    """Tear down a worker spawned by subprocess.Popen on this host.

    When wrap=[sudo, ...] is non-empty, the popen handle is sudo, not
    the worker, and SIGTERM to sudo doesn't reliably forward to its
    child — so we pkill by spec-path match. When wrap is empty, popen
    is the worker directly and terminate() reaches it.
    """
    if popen.poll() is not None:
        return
    print(f"[scenario] stopping {label}", flush=True)
    try:
        if wrap:
            subprocess.run(["sudo", "pkill", "-TERM", "-f", str(spec_path)],
                           check=False, capture_output=True)
        else:
            popen.terminate()
    except Exception:
        pass
    try:
        popen.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            popen.kill()
            popen.wait(timeout=5)
        except Exception:
            pass
    except Exception:
        pass


def _fetch_and_cleanup_remote(host: str, run_id: str,
                              remote_result: str, local_result: Path) -> None:
    """Fetch the worker's result file back, then remove its remote tmp
    files (PID + spec + result). The cleanup runs even if the fetch
    failed — leaking remote tmp files would accumulate over many runs.

    The scp invocation passes _SSH_OPTS (ControlMaster) so it uses the
    same multiplexed connection the rest of the runner shares; without
    those opts, scp can race against an idling/expiring ControlMaster
    and block ~120 s before falling through. A 30 s timeout is the
    backstop for the case where scp truly can't reach the file.
    """
    try:
        subprocess.run(
            ["scp", *_SSH_OPTS, f"{host}:{remote_result}", str(local_result)],
            check=False, capture_output=True, timeout=30,
        )
    except Exception:
        pass
    # Remove ONLY this stream's remote tmp files (result + pid + spec),
    # derived from the result path. Globbing gstexp-<run_id>-*.pid here
    # would delete sibling streams' pid files before their own kill/fetch
    # runs — in a multi-stream run that strands the not-yet-torn-down
    # viewers: their `kill -TERM $(cat pidfile)` finds nothing, so they
    # never get the clean signal that makes them write their result, and
    # only the first-torn-down stream survives.
    base = remote_result[:-len(".json")] if remote_result.endswith(".json") else remote_result
    try:
        _ssh(host,
             f"rm -f {remote_result} {base}.pid {base}.spec.json",
             check=False, capture_output=True, timeout=15)
    except Exception:
        pass


def _run_post_run(post_hooks: list, *,
                  camera_host: str | None, viewer_host: str | None,
                  project_root: Path) -> None:
    """post_run wrapper for stack.callback — uses check=False so a
    teardown failure (rare; tc qdisc del with || true is robust) can't
    poison other callbacks running after it."""
    try:
        _run_hook_sync("post_run", post_hooks,
                       camera_host=camera_host, viewer_host=viewer_host,
                       project_root=project_root, check=False)
    except Exception:
        pass


# ---- runners --------------------------------------------------------------

def run_distributed(
    *, project_root: Path,
    camera_specs: list, viewer_specs: list,
    run_dir: Path, camera_results: list, viewer_results: list,
    camera_host: str, viewer_host: str, remote_project_root: str,
    view_display, view_xauthority,
    setup_delay: float, drain_delay: float,
    pre_hooks: list, during_hooks: list, post_hooks: list,
    metric_args: list,
) -> None:
    """Controller/worker runner for N streams: sync payload, spawn 2N
    workers via SSH (N cameras on one host, N viewers on the other),
    coordinate, and fetch every result file back.

    Each stream rides its own (rtp, rtcp) port pair, so the N flows never
    collide on the shared NIC; per-stream spec/result/pid paths keep their
    outputs separate. `camera_specs[i]` and `viewer_specs[i]` are the two
    ends of stream i, index-aligned with `camera_results` / `viewer_results`.
    Each worker still receives ONE single-stream spec and never knows N>1.
    """
    run_id = run_dir.name                          # e.g. "2026-04-30T..."
    n = len(camera_specs)

    def _remote(role: str, i: int, kind: str) -> str:
        return f"/tmp/gstexp-{run_id}-{role}-{i}.{kind}"

    print(f"[controller] workers: camera={camera_host}, viewer={viewer_host}, "
          f"streams={n}")
    print(f"[controller] remote_project_root={remote_project_root}")

    # 1) Sync the minimal runtime payload to both workers. The controller
    #    owns code/spec authoring and result storage; workers own their
    #    local GStreamer + SCReAM build caches.
    for host in (camera_host, viewer_host):
        _sync_worker_payload(project_root, host, remote_project_root)

    # 1b) Measure clock skew (host_clock - controller_clock) per host — one
    #     pair of scalars shared across that host's N streams. NTP is
    #     unreliable on these hosts, so we self-measure (cached).
    camera_skew = _measure_clock_skew(camera_host)
    viewer_skew = _measure_clock_skew(viewer_host)
    print(f"[scenario] clock skew: camera={camera_skew:+.3f}s "
          f"viewer={viewer_skew:+.3f}s", flush=True)
    (run_dir / "_clock_skew.json").write_text(json.dumps(
        {"camera_skew_s": camera_skew, "viewer_skew_s": viewer_skew},
        indent=2))

    # 2) Run setup_remote.sh on each host (idempotent, fast on repeat).
    for host in (camera_host, viewer_host):
        print(f"[scenario] setup → {host}", flush=True)
        _ssh(host, f"cd {remote_project_root} && bash scripts/setup_remote.sh", check=True)

    # 3) Build worker env / command strings. Pure string assembly, no
    #    side effects — done before the try/finally so the cleanup block
    #    doesn't have to know about partial-init states.
    plugin_path = f"{remote_project_root}/.scream-plugin"
    lib_path = f"{remote_project_root}/scream/code/wrapper_lib"
    common_env = [
        f"GST_PLUGIN_PATH={plugin_path}:$GST_PLUGIN_PATH",
        f"LD_LIBRARY_PATH={lib_path}:$LD_LIBRARY_PATH",
    ]
    # DISPLAY / XAUTHORITY only apply to the viewer, and only in expo mode.
    viewer_env = list(common_env)
    if view_display:
        viewer_env.append(f"DISPLAY={view_display}")
    if view_xauthority:
        viewer_env.append(f"XAUTHORITY={view_xauthority}")
    camera_env_str = " ".join(common_env)
    viewer_env_str = " ".join(viewer_env)

    extra_args = " ".join(metric_args)
    viewer_extra_args = extra_args
    if view_display:
        viewer_extra_args = f"{extra_args} --view-display {view_display}".strip()

    def _ssh_worker_cmd(spec_path, result_path, pid_file, env_str, args_extra):
        # Source env.sh for the locally-built GStreamer 1.24, then layer the
        # project-local plugin path on top. The worker writes its own PID to
        # pid_file so we don't need `& wait $!` plumbing across SSH.
        return (
            f"cd {remote_project_root} && "
            f"source $HOME/gst-1.24/env.sh && "
            f"exec env {env_str} python3 -u -m gstexp.worker {spec_path} "
            f"--result-out {result_path} --pid-file {pid_file} {args_extra}"
        )

    # Cleanup discipline: every state-changing allocation pushes its
    # teardown onto the ExitStack, which unwinds LIFO on any exit path.
    # Per stream we push fetch THEN kill, so for each worker kill (later)
    # runs before its fetch (earlier) — the worker is killed, then its
    # finalized result file is pulled back. Order across streams doesn't
    # matter; each callback is best-effort and never raises.
    with contextlib.ExitStack() as stack:
        try:
            stack.callback(_run_post_run, post_hooks,
                           camera_host=camera_host, viewer_host=viewer_host,
                           project_root=project_root)
            _run_hook_sync("pre_run", pre_hooks,
                           camera_host=camera_host, viewer_host=viewer_host,
                           project_root=project_root, check=True)

            # scp each stream's spec to its host. The remote tmp files are
            # cleaned up by _fetch_and_cleanup_remote on unwind.
            print(f"[scenario] scp {n} camera + {n} viewer spec(s)", flush=True)
            for i in range(n):
                subprocess.run(
                    ["scp", str(camera_specs[i]),
                     f"{camera_host}:{_remote('camera', i, 'spec.json')}"],
                    check=True)
                subprocess.run(
                    ["scp", str(viewer_specs[i]),
                     f"{viewer_host}:{_remote('viewer', i, 'spec.json')}"],
                    check=True)

            # Viewers first (viewer-before-camera startup invariant per
            # stream): spawn all N, registering fetch then kill for each.
            print(f"[controller] starting {n} viewer worker(s) on {viewer_host}",
                  flush=True)
            for i in range(n):
                vp = _ssh_popen(viewer_host, _ssh_worker_cmd(
                    _remote('viewer', i, 'spec.json'), _remote('viewer', i, 'json'),
                    _remote('viewer', i, 'pid'), viewer_env_str, viewer_extra_args))
                stack.callback(_fetch_and_cleanup_remote, viewer_host, run_id,
                               _remote('viewer', i, 'json'), viewer_results[i])
                stack.callback(_kill_remote_worker, viewer_host,
                               _remote('viewer', i, 'pid'), vp, f"viewer[{i}]")

            time.sleep(setup_delay)

            # Cameras: spawn all N, same registration shape.
            print(f"[controller] starting {n} camera worker(s) on {camera_host}",
                  flush=True)
            camera_procs = []
            for i in range(n):
                cp = _ssh_popen(camera_host, _ssh_worker_cmd(
                    _remote('camera', i, 'spec.json'), _remote('camera', i, 'json'),
                    _remote('camera', i, 'pid'), camera_env_str, extra_args))
                camera_procs.append(cp)
                stack.callback(_fetch_and_cleanup_remote, camera_host, run_id,
                               _remote('camera', i, 'json'), camera_results[i])
                stack.callback(_kill_remote_worker, camera_host,
                               _remote('camera', i, 'pid'), cp, f"camera[{i}]")

            # During hooks registered LAST so they stop FIRST in unwind —
            # the tc-transition ladder must stop touching the NIC before
            # the workers are killed.
            during_procs = _spawn_hook_async(
                "during_run", during_hooks,
                camera_host=camera_host, viewer_host=viewer_host,
                project_root=project_root,
            )
            stack.callback(_stop_during_procs, during_procs)

            # Completion gate (sync.termination = all): wait on every camera
            # proc to a single shared deadline. A wedged stream times out and
            # is killed by the ExitStack — it fails ALONE, leaving the other
            # streams' data intact, and lets experiment.py advance.
            deadline = time.monotonic() + _WORKER_WAIT_TIMEOUT_S
            for i, cp in enumerate(camera_procs):
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    cp.wait(timeout=remaining)
                    print(f"[scenario] camera[{i}] exited rc={cp.returncode}",
                          flush=True)
                except subprocess.TimeoutExpired:
                    print(f"[scenario] camera[{i}] did not exit within the "
                          f"{_WORKER_WAIT_TIMEOUT_S}s deadline — abandoning it "
                          f"(stack cleanup will kill the worker)", flush=True)
            time.sleep(drain_delay)
        except KeyboardInterrupt:
            # Cleanup runs on the way out of the with-block.
            print("\n[scenario] interrupted", flush=True)


def run_local(
    *, project_root: Path,
    camera_specs: list, viewer_specs: list,
    camera_results: list, viewer_results: list,
    base_cmd: list, child_env: dict,
    wrap_camera: list, wrap_viewer: list,
    setup_delay: float, drain_delay: float,
    pre_hooks: list, during_hooks: list, post_hooks: list,
    view_display=None, view_xauthority=None,
) -> None:
    """Local runner: spawn 2N workers as child processes on this host —
    N viewers, then (after setup_delay) N cameras. One single-stream spec
    per process; `camera_specs[i]` / `viewer_specs[i]` are the two ends of
    stream i, index-aligned with the result paths.

    `wrap_camera` / `wrap_viewer` let a configuration prepend a wrapper
    (e.g. `sudo ip netns exec ns1`); when that wrapper is `sudo`, the
    parent's GST_PLUGIN_PATH / DISPLAY are stripped by sudoers default
    policy, so we re-inject the relevant vars inside the wrapped command
    via `env`. When view_display is set (expo mode), DISPLAY (and
    optionally XAUTHORITY) are added to each viewer's environment and
    `--view-display` is passed so it adds the autovideosink branch.
    """
    _PASSED_ENV_KEYS = ("GST_PLUGIN_PATH", "LD_LIBRARY_PATH", "DISPLAY", "XAUTHORITY")
    n = len(camera_specs)

    viewer_env = dict(child_env)
    if view_display:
        viewer_env["DISPLAY"] = view_display
    if view_xauthority:
        viewer_env["XAUTHORITY"] = view_xauthority

    def _build_cmd(wrap, env, *cmd_extra):
        if wrap:
            kvs = [f"{k}={env[k]}" for k in _PASSED_ENV_KEYS if k in env]
            return list(wrap) + (["env"] + kvs if kvs else []) + base_cmd + list(cmd_extra)
        return base_cmd + list(cmd_extra)

    # Cleanup discipline mirrors run_distributed: ExitStack with each
    # allocation pushing its teardown. Local mode differs only in the kill
    # helpers (Popen on this host, no remote PID file or result fetch).
    with contextlib.ExitStack() as stack:
        try:
            stack.callback(_run_post_run, post_hooks,
                           camera_host=None, viewer_host=None,
                           project_root=project_root)
            _run_hook_sync("pre_run", pre_hooks,
                           camera_host=None, viewer_host=None,
                           project_root=project_root, check=True)

            print(f"[scenario] starting {n} viewer(s)", flush=True)
            for i in range(n):
                viewer_extras = [str(viewer_specs[i]),
                                 "--result-out", str(viewer_results[i])]
                if view_display:
                    viewer_extras.extend(["--view-display", view_display])
                vp = subprocess.Popen(
                    _build_cmd(wrap_viewer, viewer_env, *viewer_extras),
                    env=viewer_env,
                )
                stack.callback(_kill_local_worker, vp, wrap=wrap_viewer,
                               spec_path=viewer_specs[i], label=f"viewer[{i}]")

            time.sleep(setup_delay)
            print(f"[scenario] starting {n} camera(s)", flush=True)
            camera_procs = []
            for i in range(n):
                cp = subprocess.Popen(
                    _build_cmd(wrap_camera, child_env, str(camera_specs[i]),
                               "--result-out", str(camera_results[i])),
                    env=child_env,
                )
                camera_procs.append(cp)
                stack.callback(_kill_local_worker, cp, wrap=wrap_camera,
                               spec_path=camera_specs[i], label=f"camera[{i}]")

            during_procs = _spawn_hook_async(
                "during_run", during_hooks,
                camera_host=None, viewer_host=None,
                project_root=project_root,
            )
            stack.callback(_stop_during_procs, during_procs)

            # Completion gate (sync.termination = all): wait on every camera
            # to a shared deadline; a wedged stream is killed by the stack.
            deadline = time.monotonic() + _WORKER_WAIT_TIMEOUT_S
            for i, cp in enumerate(camera_procs):
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    cp.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    print(f"[scenario] camera[{i}] did not exit within the "
                          f"{_WORKER_WAIT_TIMEOUT_S}s deadline — abandoning it",
                          flush=True)
            time.sleep(drain_delay)
        except KeyboardInterrupt:
            print("\n[scenario] interrupted", flush=True)
