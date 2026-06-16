"""Network model probe — empirical verification harness.

Independent of the GStreamer pipeline. Loads a network spec, applies its
hooks on the camera host, blasts a UDP probe stream from camera to
viewer, captures arrival times on the viewer, then computes per-step
loss / burst length / delay and compares each against the spec's claims.

The unit tests verify the compiler emits the right `tc qdisc ...` strings.
This script verifies that those strings, applied to the real NIC, produce
the impairment behaviour the spec promises.

Usage:
    python3 tools/network_probe.py <spec_name> \\
        --camera-host <camera-ip> \\
        --viewer-host <viewer-ip> \\
        --nic <interface-name>

Each non-loopback step gets a row in the output:
    step 2 (uniform pct=2):  loss=2.1% (claim 2%) ✓   burst=1.0 (n/a) ✓   delay=101ms (claim 100±5) ✓
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gstexp.runner import _ssh, _measure_clock_skew                   # noqa: E402
from gstexp.validation import (_compile_streams_network,               # noqa: E402
                               _port_for_stream)


# --- knobs --------------------------------------------------------------

# The HTB-based compiler classifies a stream's lane by its destination
# port: `match ip dport <rtp_port> 0xfffe`. The probe must therefore send
# on the SAME port the compiler filtered, or the tc lane never catches it
# and we'd measure the unshaped passthrough class. We probe a single
# representative stream (idx 0) of a notional config and reuse its
# derived rtp_port both as the compiler's filter port and the probe's
# UDP destination port, keeping the two in lockstep.
_PROBE_CONFIG_ID = "0"
_PROBE_STREAM_IDX = 0
_PROBE_PORT = _port_for_stream(_PROBE_CONFIG_ID, _PROBE_STREAM_IDX)
# Probe rate must fit the smallest TBF cap in any tested spec, otherwise
# TBF (downstream of netem) drops the rate-cap excess and we'd attribute
# rate-cap drops to the loss model. 100 pps × 32-byte payload + ~62 byte
# UDP/IP/Eth overhead = ~75 kbps wire, comfortably under 300 kbps (the
# smallest cap in any current spec).
_PROBE_RATE_PPS = 100
# Bursty steps shorter than this give too few burst events for stable
# burst-length and loss-rate estimation. At 2% loss × 100 pps the burst
# event rate is N × p_loss / B per second; with B=15 and 60s windows
# that's ~8 events, leaving loss% with ±100% sample variance. 180s
# gives ~24 events at B=15 and ~120 at B=3 — enough for ±50% bands on
# both loss and burst length to land cleanly. Same tc rules, longer
# measurement window; the spec on disk is unchanged.
_BURSTY_PROBE_MIN_DURATION_S = 180
# Delay tolerance is loose because cross-host skew measurement via SSH
# inherits SSH's round-trip variance — typically tens of ms on networks
# with non-NTP-synced hosts. The harness logs absolute delay too, but
# only flags catastrophic deltas. The skew-invariant delay-delta check
# (step_i delay vs step_0 delay) is the tighter test when it applies.
_DELAY_TOLERANCE_MS = 30
# Delay-delta tolerance must accommodate two real effects: (1) TBF queue
# transients during step transitions, which can shift average delay over
# a step by a few ms; (2) skew measurement run-to-run variance from
# SSH RTT noise. ±20ms is generous enough to PASS substantively correct
# runs while still catching order-of-magnitude netem misconfigurations.
_DELAY_DELTA_TOLERANCE_MS = 20
# GE finite-sample variance at low loss rates is substantial: at 2% loss
# over 60s × 100 pps = 12k packets, the natural standard deviation of
# loss% is sqrt(p*(1-p)/N) ≈ 0.13%, but the GE Markov chain's
# autocorrelation makes the effective sample size much smaller for
# bursty regimes — observed run-to-run loss% can vary by ±50% relative.
_LOSS_TOLERANCE_REL = 0.50
# Min absolute loss tolerance for non-zero claims — covers cases where
# the relative tolerance shrinks below the natural statistical floor.
_LOSS_MIN_TOL = 0.01              # ±1.0% absolute floor
# "Clean" steps occasionally show small non-zero loss from kernel-edge
# drops (RX queue overruns, cgroup throttling, etc.) unrelated to netem.
_NONE_LOSS_THRESHOLD = 0.015
_BURST_TOLERANCE_REL = 0.50       # GE burst-length sample variance is large
_MIN_PACKETS_FOR_ASSERTION = 200  # below this, skip rather than mis-conclude


# --- remote helpers -----------------------------------------------------

def _push_remote_script(host: str, script_text: str, remote_path: str) -> None:
    """Stage a script on the remote host (one-shot SSH that uses stdin
    for the source). Decoupling delivery from execution lets the runner
    detach stdin (e.g. `< /dev/null`) when it later launches the script
    in the background — pipeing source AND backgrounding in one command
    creates a stdin contention that drops the source on the floor."""
    _ssh(host, f"cat > {shlex.quote(remote_path)}",
         input=script_text, text=True, check=True)
    _ssh(host, f"chmod +x {shlex.quote(remote_path)}", check=True)


def _run_remote_python_bg(host: str, remote_path: str, args: list[str],
                          remote_log: str) -> str:
    """Launch `python3 <remote_path> <args>` in the background; return PID."""
    quoted_args = " ".join(shlex.quote(a) for a in args)
    cmd = (f"nohup python3 {shlex.quote(remote_path)} {quoted_args} "
           f">{shlex.quote(remote_log)} 2>&1 < /dev/null & echo $!")
    p = _ssh(host, cmd, capture_output=True, text=True, check=True)
    pid = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
    if not pid.isdigit():
        sys.exit(f"[probe] failed to launch background python on {host}: "
                 f"stdout={p.stdout!r} stderr={p.stderr!r}")
    return pid


def _run_remote_python_fg(host: str, remote_path: str, args: list[str]):
    quoted_args = " ".join(shlex.quote(a) for a in args)
    return _ssh(host, f"python3 {shlex.quote(remote_path)} {quoted_args}",
                check=True, capture_output=True, text=True)


def _scp_back(host: str, remote_path: str, local_path: Path) -> None:
    subprocess.run(["scp",
                    "-o", "ControlMaster=auto",
                    "-o", "ControlPath=/tmp/gstexp-ssh-%C",
                    f"{host}:{remote_path}", str(local_path)],
                   check=True, capture_output=True)


# --- analysis ----------------------------------------------------------

def _parse_recv_log(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _compute_step_stats(pkts: list[dict], step_start_ns: int, step_end_ns: int,
                        skew_offset_ns: int):
    """Return (loss_frac, burst_avg, delay_ms, n_in_step) for the slice
    of probe packets whose SEND timestamps fall inside [start, end).

    Loss is measured against the contiguous seq# range observed in the
    slice — we assume the probe sends at a steady cadence so any seq
    gaps are network drops, not local camera drops. Burst length is the
    mean run of consecutive missing seq numbers within the slice. Delay
    is the mean (recv - send), corrected by the cross-host skew.
    """
    in_step = [p for p in pkts
               if step_start_ns <= p["send_ts_ns"] < step_end_ns]
    if len(in_step) < _MIN_PACKETS_FOR_ASSERTION:
        return None
    in_step.sort(key=lambda p: p["seq"])
    seqs = [p["seq"] for p in in_step]
    s0, s1 = seqs[0], seqs[-1]
    expected = s1 - s0 + 1
    received = len(in_step)
    loss_frac = 1.0 - received / expected

    seq_set = set(seqs)
    burst_lengths: list[int] = []
    cur = 0
    for s in range(s0, s1 + 1):
        if s in seq_set:
            if cur:
                burst_lengths.append(cur)
                cur = 0
        else:
            cur += 1
    if cur:
        burst_lengths.append(cur)
    burst_avg = (sum(burst_lengths) / len(burst_lengths)
                 if burst_lengths else 0.0)

    delays_ns = [(p["recv_ts_ns"] - p["send_ts_ns"]) - skew_offset_ns
                 for p in in_step]
    delay_ms = (sum(delays_ns) / len(delays_ns)) / 1e6

    return {"loss_frac": loss_frac, "burst_avg": burst_avg,
            "delay_ms": delay_ms, "n": received, "expected": expected,
            "n_bursts": len(burst_lengths)}


# --- main ---------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", help="network spec name (file stem under specs/networks/)")
    ap.add_argument("--camera-host", required=True)
    ap.add_argument("--viewer-host", required=True)
    ap.add_argument("--nic", required=True,
                    help="NIC name on the SENDING side for this direction "
                         "(the spec's $NIC). For --direction camera-to-viewer "
                         "this is the camera's NIC; for viewer-to-camera "
                         "this is the viewer's NIC.")
    ap.add_argument("--direction", default="camera-to-viewer",
                    choices=("camera-to-viewer", "viewer-to-camera"),
                    help="which direction to probe (default: camera-to-viewer, "
                         "the forward/video path). Use viewer-to-camera to "
                         "verify return-path impairment in symmetric specs.")
    args = ap.parse_args()

    spec_path = PROJECT_ROOT / "specs" / "networks" / f"{args.spec}.yaml"
    if not spec_path.is_file():
        sys.exit(f"network spec not found: {spec_path}")
    spec = yaml.safe_load(spec_path.read_text())

    # Direction selects which side's steps to probe. The probe runs on
    # the SENDING side (whose egress is shaped) and receives on the
    # other host. We apply only the direction-of-interest's hooks —
    # the other direction stays unimpaired during the probe so it
    # can't confound the measurement.
    if args.direction == "camera-to-viewer":
        steps_key = "camera_steps"
        send_host = args.camera_host
        recv_host = args.viewer_host
        hook_role = "camera"
    else:  # viewer-to-camera
        steps_key = "viewer_steps"
        send_host = args.viewer_host
        recv_host = args.camera_host
        hook_role = "viewer"

    steps = spec.get(steps_key) or []
    if not steps:
        sys.exit(f"{args.spec}: `{steps_key}` is empty — nothing to probe in "
                 f"the {args.direction} direction")

    # If any step has duration=0 (e.g. fixed-5mbps-20ms), it holds for
    # the whole run — substitute a probe duration so we still measure it.
    for s in steps:
        if s["duration"] == 0:
            s["duration"] = 8
    # Extend bursty steps so the burst-length estimator has enough events
    # to be stable. The tc rules are unchanged; we just hold each bursty
    # regime longer than the spec's authored duration.
    for s in steps:
        if (s["loss"]["model"] == "bursty"
                and s["duration"] < _BURSTY_PROBE_MIN_DURATION_S):
            s["duration"] = _BURSTY_PROBE_MIN_DURATION_S

    total_duration_s = sum(s["duration"] for s in steps)
    print(f"[probe] direction={args.direction} send={send_host} recv={recv_host}")
    print(f"[probe] total probe duration: {total_duration_s}s "
          f"({len(steps)} steps; durations="
          f"{[s['duration'] for s in steps]})")

    # Compile the spec into tc hooks. The multi-stream compiler takes a
    # list of per-stream network profiles; we wrap this single spec as a
    # one-element stream_nets list (idx 0, a probe name, the rtp_port the
    # probe sends on, and the parsed network yaml). It returns
    # {phase: [hook, ...]} with one camera + one viewer hook per phase
    # when both directions are shaped; we filter to the role of interest
    # below so the other direction stays unimpaired during the probe.
    stream_nets = [{
        "idx": _PROBE_STREAM_IDX,
        "name": f"probe-{args.spec}",
        "rtp_port": _PROBE_PORT,
        "spec": spec,
    }]
    hooks = _compile_streams_network(stream_nets, spec_path.name)

    # Inject NIC + PEER_IP into hook scripts (mirrors what resolve_includes
    # does with network_env + actor topology). The compiler doesn't know
    # the NIC, and the HTB-based tc rules filter by `match ip dst
    # $PEER_IP_RESOLVED ... match ip dport <rtp_port>`, so the lane only
    # catches probe traffic when PEER_IP names the receiving host and the
    # probe sends on _PROBE_PORT. PEER_IP is the recv host (the peer of
    # the shaping sender). Filter by role — only this direction's hooks
    # get tc applied.
    env_export = (f'export NIC={shlex.quote(args.nic)}\n'
                  f'export PEER_IP={shlex.quote(recv_host)}\n')
    role_hooks: dict = {phase: [h for h in hook_list if h["host"] == hook_role]
                        for phase, hook_list in hooks.items()}
    for hook_list in role_hooks.values():
        for h in hook_list:
            h["script"] = env_export + h["script"]

    # --- clock skew ----------------------------------------------------
    # rx-tx offset is in the recv-host's frame minus the send-host's frame,
    # so direction matters: forward = viewer - camera; return = camera - viewer.
    print("[probe] measuring clock skew on both hosts...")
    skew_send = _measure_clock_skew(send_host)
    skew_recv = _measure_clock_skew(recv_host)
    skew_offset_ns = int((skew_recv - skew_send) * 1e9)
    print(f"[probe] skew(send)={skew_send*1000:+.1f}ms "
          f"skew(recv)={skew_recv*1000:+.1f}ms "
          f"=> rx-tx offset={skew_offset_ns/1e6:+.1f}ms")

    # --- stage scripts on the remote hosts ----------------------------
    # The probe-sender script runs on whichever host is sending (camera
    # for forward, viewer for return); the probe-receiver script runs on
    # the other one. Pick the staging path by role for clarity.
    probe_send_src = (PROJECT_ROOT / "tools/probe_camera.py").read_text()
    probe_recv_src = (PROJECT_ROOT / "tools/probe_viewer.py").read_text()
    send_remote = "/tmp/probe_send.py"
    recv_remote = "/tmp/probe_recv.py"
    _push_remote_script(recv_host, probe_recv_src, recv_remote)
    _push_remote_script(send_host, probe_send_src, send_remote)

    # --- start probe receiver in background ---------------------------
    recv_remote_out = "/tmp/probe-recv.jsonl"
    print(f"[probe] starting probe receiver on {recv_host}")
    recv_pid = _run_remote_python_bg(
        recv_host, recv_remote,
        args=["--bind-host", recv_host,
              "--port", str(_PROBE_PORT),
              "--max-duration-s", str(total_duration_s + 5),
              "--stop-after-s", "3",
              "--output", recv_remote_out],
        remote_log="/tmp/probe-recv.log",
    )

    time.sleep(1.5)   # let the receiver bind before the sender races

    # --- run pre_run + during_run on the sending host (sudo) ----------
    print(f"[probe] applying network spec on {send_host} (NIC={args.nic})")
    for h in role_hooks.get("pre_run", []):
        _ssh(send_host, "sudo bash -s", input=h["script"], text=True, check=True)

    during_procs: list = []
    for h in role_hooks.get("during_run", []):
        p = subprocess.Popen(
            ["ssh",
             "-o", "ControlMaster=auto",
             "-o", "ControlPath=/tmp/gstexp-ssh-%C",
             "-o", "ControlPersist=120",
             send_host, "sudo bash -s"],
            stdin=subprocess.PIPE, text=True)
        p.stdin.write(h["script"])
        p.stdin.close()
        during_procs.append(p)

    # --- run probe sender (foreground) --------------------------------
    print(f"[probe] running probe sender on {send_host} "
          f"({_PROBE_RATE_PPS} pps × {total_duration_s}s)")
    send_result = _run_remote_python_fg(
        send_host, send_remote,
        args=["--target-host", recv_host,
              "--port", str(_PROBE_PORT),
              "--rate-pps", str(_PROBE_RATE_PPS),
              "--duration-s", str(total_duration_s)],
    )
    print(f"[probe] {send_result.stdout.strip()}")

    # let during_run hooks finish if still running, then tear down.
    for p in during_procs:
        p.wait(timeout=30)

    print("[probe] tearing down via post_run")
    for h in role_hooks.get("post_run", []):
        _ssh(send_host, "sudo bash -s", input=h["script"], text=True, check=True)

    # --- stop receiver, fetch log -------------------------------------
    # Receiver self-stops on its idle timeout, so just wait it out.
    time.sleep(4)
    _ssh(recv_host, f"kill {recv_pid} 2>/dev/null; true", check=False)
    local_log = Path(f"/tmp/probe-recv-{args.spec}-{args.direction}.jsonl")
    _scp_back(recv_host, recv_remote_out, local_log)
    pkts = _parse_recv_log(local_log)
    print(f"[probe] captured {len(pkts)} packets at the receiver")

    # --- per-step analysis --------------------------------------------
    # We bin packets by SEND timestamp relative to the run start (the
    # send_ts_ns of the very first received packet — close enough to
    # spec's t=0). Each step claims its own duration, so cumulative
    # boundaries fall out of the spec.
    if not pkts:
        sys.exit("[probe] no packets captured — bind/firewall/timing issue?")
    t0_ns = pkts[0]["send_ts_ns"]
    boundaries_ns = [t0_ns]
    cum_s = 0
    for s in steps:
        cum_s += s["duration"]
        boundaries_ns.append(t0_ns + cum_s * 10**9)

    print()
    print(f"=== {args.spec} ({args.direction}) ===")
    all_pass = True
    # Cache step 0's absolute delay so we can do a skew-invariant
    # delta check on subsequent steps (claim_delay_i - claim_delay_0
    # vs measured_delay_i - measured_delay_0).
    step0_stats = _compute_step_stats(pkts, boundaries_ns[0], boundaries_ns[1],
                                      skew_offset_ns)
    base_measured_delay_ms = step0_stats["delay_ms"] if step0_stats else None
    base_claim_delay_ms = steps[0]["delay_ms"]

    for i, s in enumerate(steps):
        loss_block = s["loss"]
        model = loss_block["model"]
        claim_loss = (loss_block["pct"] / 100.0
                      if model in ("uniform", "bursty") else 0.0)
        claim_burst = (loss_block["burst"] if model == "bursty"
                       else (1 if model == "uniform" else None))

        stats = _compute_step_stats(pkts, boundaries_ns[i], boundaries_ns[i+1],
                                    skew_offset_ns)
        if stats is None:
            print(f"  step {i+1}: too few packets to assess "
                  f"(< {_MIN_PACKETS_FOR_ASSERTION}); skipping")
            continue

        # loss check
        loss_ok = True
        if model == "none":
            loss_ok = stats["loss_frac"] < _NONE_LOSS_THRESHOLD
        else:
            tol = max(_LOSS_MIN_TOL, claim_loss * _LOSS_TOLERANCE_REL)
            loss_ok = abs(stats["loss_frac"] - claim_loss) <= tol

        # burst check (only for bursty; uniform should have B≈1)
        burst_ok = True
        burst_str = "n/a"
        if model == "bursty":
            tol = max(0.5, claim_burst * _BURST_TOLERANCE_REL)
            burst_ok = (stats["n_bursts"] >= 5 and
                        abs(stats["burst_avg"] - claim_burst) <= tol)
            burst_str = (f"burst={stats['burst_avg']:.1f}/{claim_burst} "
                         f"(n={stats['n_bursts']}) {'OK' if burst_ok else 'FAIL'}")
        elif model == "uniform":
            burst_str = f"burst={stats['burst_avg']:.2f} (uniform; ~1 expected)"

        # delay checks: absolute (loose, dominated by skew error) +
        # skew-invariant delta-vs-step-0 (tight).
        abs_delay_ok = abs(stats["delay_ms"] - s["delay_ms"]) <= _DELAY_TOLERANCE_MS
        if i == 0:
            delay_delta_ok = True
            delay_str = (f"delay={stats['delay_ms']:6.1f}ms "
                         f"(claim {s['delay_ms']}; baseline)")
        else:
            measured_delta = stats["delay_ms"] - base_measured_delay_ms
            claim_delta = s["delay_ms"] - base_claim_delay_ms
            delay_delta_ok = (abs(measured_delta - claim_delta)
                              <= _DELAY_DELTA_TOLERANCE_MS)
            delay_str = (f"delay={stats['delay_ms']:6.1f}ms "
                         f"(claim {s['delay_ms']}; "
                         f"Δ={measured_delta:+.1f} vs claim Δ={claim_delta:+.1f}) "
                         f"{'OK' if delay_delta_ok else 'FAIL'}")

        # The delay-delta check is what we trust; the absolute check is
        # advisory. Don't fail the run on absolute alone if delta passes.
        delay_ok = delay_delta_ok or (i == 0 and abs_delay_ok)

        verdict = "PASS" if (loss_ok and burst_ok and delay_ok) else "FAIL"
        all_pass = all_pass and (loss_ok and burst_ok and delay_ok)

        print(f"  step {i+1} ({model:7s}): "
              f"loss={stats['loss_frac']*100:5.2f}% "
              f"(claim {claim_loss*100:.1f}%) "
              f"{'OK' if loss_ok else 'FAIL':>4}   "
              f"{burst_str:<40}   "
              f"{delay_str}   [{verdict}]")

    print()
    print(f"=== overall: {'PASS' if all_pass else 'FAIL'} ===")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
