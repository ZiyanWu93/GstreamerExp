"""Scenario runners — local and distributed.

run_local() spawns camera and viewer workers as child processes on
this host. run_distributed() rsyncs to two hosts, measures clock skew,
spawns workers via SSH, and fetches result files back. Both runners
honour the same hook contract (pre_run / during_run / post_run) and
the same env-injection rules.

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
    try:
        _ssh(host,
             f"rm -f {remote_result} /tmp/gstexp-{run_id}-*.pid "
             f"/tmp/gstexp-{run_id}-*-spec.json",
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
    effective_camera_local: Path, effective_viewer_local: Path,
    run_dir: Path, camera_result: Path, viewer_result: Path,
    camera_host: str, viewer_host: str, remote_project_root: str,
    view_display, view_xauthority,
    setup_delay: float, drain_delay: float,
    pre_hooks: list, during_hooks: list, post_hooks: list,
    metric_args: list,
) -> None:
    """Distributed runner: sync to both hosts, spawn workers via SSH,
    coordinate, fetch result files back."""
    run_id = run_dir.name                          # e.g. "2026-04-30T..."
    remote_camera_pid = f"/tmp/gstexp-{run_id}-camera.pid"
    remote_viewer_pid = f"/tmp/gstexp-{run_id}-viewer.pid"
    remote_camera_result = f"/tmp/gstexp-{run_id}-camera.json"
    remote_viewer_result = f"/tmp/gstexp-{run_id}-viewer.json"
    remote_camera_spec = f"/tmp/gstexp-{run_id}-camera-spec.json"
    remote_viewer_spec = f"/tmp/gstexp-{run_id}-viewer-spec.json"

    print(f"[scenario] distributed: camera={camera_host}, viewer={viewer_host}")
    print(f"[scenario] remote_project_root={remote_project_root}")

    # 1) rsync project to both hosts. The exclude list keeps build
    #    outputs host-local: pushing the controller's stale (or absent)
    #    target/ + wrapper_lib build files would invalidate the remote
    #    rebuild detection on every run, which previously cost ~20s of
    #    incremental cargo compile per host per run.
    rsync_excludes = [
        "--exclude=runs/", "--exclude=__pycache__/",
        "--exclude=*.pyc", "--exclude=_archive/",
        "--exclude=reference/",
        "--exclude=scream/gstscream/target/",
        "--exclude=scream/code/wrapper_lib/build/",
        "--exclude=scream/code/wrapper_lib/CMakeFiles/",
        "--exclude=scream/code/wrapper_lib/CMakeCache.txt",
        "--exclude=scream/code/wrapper_lib/cmake_install.cmake",
        "--exclude=scream/code/wrapper_lib/Makefile",
        "--exclude=scream/code/wrapper_lib/*.so",
        "--exclude=scream/code/wrapper_lib/*.a",
        "--exclude=.scream-plugin/",
    ]
    for host in (camera_host, viewer_host):
        print(f"[scenario] rsync → {host}", flush=True)
        subprocess.run(
            ["rsync", "-az", "--delete", *rsync_excludes,
             f"{project_root}/", f"{host}:{remote_project_root}/"],
            check=True,
        )

    # 1b) Measure clock skew (host_clock - controller_clock) for each host
    #     so we can subtract it from latency raw deltas. NTP is unreliable
    #     on these hosts, so we self-measure (cached for _SKEW_CACHE_TTL).
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
        _ssh(host, f"cd {remote_project_root} && bash setup_remote.sh", check=True)

    # 3) Build worker env / command strings. Pure string assembly, no
    #    side effects — done before the try/finally so the cleanup block
    #    doesn't have to know about partial-init states.
    # Project-local plugin paths layer on top of the GStreamer 1.24 env that
    # env.sh sets up. Order matters: project-local plugins (gstscream in
    # .scream-plugin) come first so they override anything similarly-named
    # in the system path.
    plugin_path = f"{remote_project_root}/.scream-plugin"
    lib_path = f"{remote_project_root}/scream/code/wrapper_lib"
    # No quotes around the values: bash won't expand ~ inside double quotes,
    # and the paths have no whitespace so quoting buys us nothing.
    common_env = [
        f"GST_PLUGIN_PATH={plugin_path}:$GST_PLUGIN_PATH",
        f"LD_LIBRARY_PATH={lib_path}:$LD_LIBRARY_PATH",
    ]
    # DISPLAY / XAUTHORITY only apply to the viewer, and only when expo
    # mode is on (view_display is set). The camera never renders.
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
        # Source ~/gst-1.24/env.sh first to point at the locally-built
        # GStreamer 1.24 (system has 1.16 which is too old for rtpgccbwe);
        # then layer the project-local plugin path on top. Worker writes
        # its own PID to pid_file at startup so we don't need shell-level
        # `& wait $!` plumbing across SSH.
        return (
            f"cd {remote_project_root} && "
            f"source $HOME/gst-1.24/env.sh && "
            f"exec env {env_str} python3 -u worker.py {spec_path} "
            f"--result-out {result_path} --pid-file {pid_file} {args_extra}"
        )

    # Cleanup discipline: every state-changing allocation pushes its
    # teardown onto the ExitStack at the moment of allocation. The stack
    # unwinds in LIFO order on any exit path (success, exception,
    # KeyboardInterrupt). Each callback is best-effort and never raises,
    # so one failing teardown can't skip the rest. Exceptions raised
    # inside the with-block (other than KeyboardInterrupt, which we swallow
    # to preserve the prior CLI semantic) propagate to the caller after
    # cleanup completes.
    with contextlib.ExitStack() as stack:
        try:
            # post_run runs LAST in unwind: register first. It's gated on
            # pre_run being attempted, so we register it just BEFORE
            # invoking pre_run — that way a pre_run failure mid-script
            # still triggers post_run on the way out.
            stack.callback(_run_post_run, post_hooks,
                           camera_host=camera_host, viewer_host=viewer_host,
                           project_root=project_root)
            _run_hook_sync("pre_run", pre_hooks,
                           camera_host=camera_host, viewer_host=viewer_host,
                           project_root=project_root, check=True)

            # scp effective spec files to each remote. The remote tmp
            # files are cleaned up by _fetch_and_cleanup_remote on
            # unwind, so no explicit teardown for them here.
            print("[scenario] scp effective specs", flush=True)
            subprocess.run(
                ["scp", str(effective_camera_local), f"{camera_host}:{remote_camera_spec}"],
                check=True,
            )
            subprocess.run(
                ["scp", str(effective_viewer_local), f"{viewer_host}:{remote_viewer_spec}"],
                check=True,
            )

            # Viewer: spawn, then register kill + result fetch. Order
            # matters — fetch is registered AFTER kill so it runs FIRST
            # in LIFO unwind, but _kill_remote_worker waits for the SSH
            # parent (and therefore the worker) to exit before returning,
            # so by the time fetch runs, the worker has finalized its
            # result file. Wait — that's the WRONG order if we want
            # fetch to happen AFTER the worker exits. Re-stating: kill
            # registered SECOND runs FIRST in LIFO (correct), fetch
            # registered FIRST runs LAST (correct).
            print(f"[scenario] starting viewer on{viewer_host}", flush=True)
            viewer_proc = _ssh_popen(
                viewer_host,
                _ssh_worker_cmd(remote_viewer_spec, remote_viewer_result, remote_viewer_pid,
                                viewer_env_str, viewer_extra_args),
            )
            stack.callback(_fetch_and_cleanup_remote,
                           viewer_host, run_id, remote_viewer_result, viewer_result)
            stack.callback(_kill_remote_worker,
                           viewer_host, remote_viewer_pid, viewer_proc, "viewer")

            time.sleep(setup_delay)

            # Camera: same shape as viewer.
            print(f"[scenario] starting camera on{camera_host}", flush=True)
            camera_proc = _ssh_popen(
                camera_host,
                _ssh_worker_cmd(remote_camera_spec, remote_camera_result, remote_camera_pid,
                                camera_env_str, extra_args),
            )
            stack.callback(_fetch_and_cleanup_remote,
                           camera_host, run_id, remote_camera_result, camera_result)
            stack.callback(_kill_remote_worker,
                           camera_host, remote_camera_pid, camera_proc, "camera")

            # During hooks: registered LAST so they run FIRST in unwind —
            # we want during's tc-transition ladder to stop affecting the
            # workers before we kill them.
            during_procs = _spawn_hook_async(
                "during_run", during_hooks,
                camera_host=camera_host, viewer_host=viewer_host,
                project_root=project_root,
            )
            stack.callback(_stop_during_procs, during_procs)

            # Bound the wait to catch wedged workers (we've seen
            # vp8/scream pipelines reach EOS but fail to exit during
            # `set_state(NULL)` in cleanup, leaving the worker
            # forever-sleeping). The ExitStack registered above will
            # SIGTERM/SIGKILL the worker via its PID file regardless,
            # so timing out here is safe — it just abandons this rep
            # and lets `experiment.py` advance to the next one.
            timeout_s = _WORKER_WAIT_TIMEOUT_S
            try:
                camera_proc.wait(timeout=timeout_s)
                print(f"[scenario] camera exited rc={camera_proc.returncode}",
                      flush=True)
                time.sleep(drain_delay)
            except subprocess.TimeoutExpired:
                print(f"[scenario] camera worker did not exit within "
                      f"{timeout_s}s — aborting this run "
                      f"(stack cleanup will kill the worker)",
                      flush=True)
        except KeyboardInterrupt:
            # Cleanup will run on the way out of the with-block. Just
            # log the interrupt; don't re-raise (preserves CLI semantic).
            print("\n[scenario] interrupted", flush=True)


def run_local(
    *, project_root: Path,
    camera_cfg: Path, viewer_cfg: Path,
    camera_result: Path, viewer_result: Path,
    base_cmd: list, child_env: dict,
    wrap_camera: list, wrap_viewer: list,
    setup_delay: float, drain_delay: float,
    pre_hooks: list, during_hooks: list, post_hooks: list,
    view_display=None, view_xauthority=None,
) -> None:
    """Local runner: spawn workers as child processes on this host.

    `wrap_camera` / `wrap_viewer` let a configuration prepend a wrapper
    (e.g. `sudo ip netns exec ns1`); when that wrapper is `sudo`, the
    parent's GST_PLUGIN_PATH / DISPLAY are stripped by sudoers default
    policy, so we re-inject the relevant vars inside the wrapped command
    via `env`.

    When view_display is set (expo mode), DISPLAY (and optionally
    XAUTHORITY) are added to the viewer's child environment and
    `--view-display <value>` is passed to the viewer worker so it
    knows to add the autovideosink branch. The camera is never affected.
    """
    _PASSED_ENV_KEYS = ("GST_PLUGIN_PATH", "LD_LIBRARY_PATH", "DISPLAY", "XAUTHORITY")

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
    # allocation pushing its teardown. See run_distributed for the full
    # rationale. Local mode differs only in the kill helpers (Popen on
    # this host, no remote PID file or result fetch).
    with contextlib.ExitStack() as stack:
        try:
            stack.callback(_run_post_run, post_hooks,
                           camera_host=None, viewer_host=None,
                           project_root=project_root)
            _run_hook_sync("pre_run", pre_hooks,
                           camera_host=None, viewer_host=None,
                           project_root=project_root, check=True)

            print("[scenario] starting viewer", flush=True)
            viewer_extras = [str(viewer_cfg), "--result-out", str(viewer_result)]
            if view_display:
                viewer_extras.extend(["--view-display", view_display])
            viewer_proc = subprocess.Popen(
                _build_cmd(wrap_viewer, viewer_env, *viewer_extras),
                env=viewer_env,
            )
            stack.callback(_kill_local_worker, viewer_proc,
                           wrap=wrap_viewer, spec_path=viewer_cfg, label="viewer")

            time.sleep(setup_delay)
            print("[scenario] starting camera", flush=True)
            camera_proc = subprocess.Popen(
                _build_cmd(wrap_camera, child_env, str(camera_cfg), "--result-out", str(camera_result)),
                env=child_env,
            )
            stack.callback(_kill_local_worker, camera_proc,
                           wrap=wrap_camera, spec_path=camera_cfg, label="camera")

            during_procs = _spawn_hook_async(
                "during_run", during_hooks,
                camera_host=None, viewer_host=None,
                project_root=project_root,
            )
            stack.callback(_stop_during_procs, during_procs)

            # Bounded wait — same rationale as run_distributed.
            try:
                camera_proc.wait(timeout=_WORKER_WAIT_TIMEOUT_S)
                # Let in-flight frames drain before teardown.
                time.sleep(drain_delay)
            except subprocess.TimeoutExpired:
                print(f"[scenario] camera worker did not exit within "
                      f"{_WORKER_WAIT_TIMEOUT_S}s — aborting this run",
                      flush=True)
        except KeyboardInterrupt:
            print("\n[scenario] interrupted", flush=True)
