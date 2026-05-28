#!/usr/bin/env python3
"""Diagnose UMN's SCReAM controller from a preserved run dir. Answers
two questions the config can't toggle:

  1. Command-following: does the controller's *commanded* target_kbps
     actually translate to the *sent* rate? (We saw cases where it
     commanded 268 but sent 1368.) Parsed straight from the controller's
     own scream_allocation log, which prints both target_kbps and the
     measured send_kbps on each decision.
  2. Decision breakdown: what fraction of decisions fire on each signal
     (probe / queue / delay / loss)? Shows which input actually drives
     the controller — e.g. if delay never fires even with feedback on,
     the delay path is wired but ineffective.

Usage:  diag_umn.py <run_dir>   (e.g. ~/comparison-runs/umn-scream-v2/cqi/rep1)
"""
from __future__ import annotations
import glob, re, sys
from pathlib import Path

PAT = re.compile(
    r"scream_allocation reason=(?P<reason>\w+) target_kbps=(?P<target>[\d.]+) "
    r"capacity_kbps=(?P<cap>[\d.]+) send_kbps=(?P<send>[\d.]+) "
    r"backlog=(?P<backlog>\d+) drop_rate=(?P<drop>[\d.]+) "
    r"receiver_loss=(?P<rloss>[\d.]+) receiver_delay_ms=(?P<rdelay>[-\d.]+)"
)


def main(run_dir: str):
    d = Path(run_dir)
    txlogs = glob.glob(str(d / "transmitter" / "transmitter-*" / "system.log"))
    if not txlogs:
        print(f"no transmitter log in {d}")
        return
    decisions = []
    for line in open(txlogs[0]):
        m = PAT.search(line)
        if m:
            decisions.append({k: float(v) if k != "reason" else v
                              for k, v in m.groupdict().items()})
    if not decisions:
        print("no scream_allocation decisions logged")
        return

    reasons: dict[str, int] = {}
    for x in decisions:
        reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1
    n = len(decisions)
    targets = [x["target"] for x in decisions]
    sends = [x["send"] for x in decisions]
    caps = [x["cap"] for x in decisions]
    rdelays = [x["rdelay"] for x in decisions]
    rlosses = [x["rloss"] for x in decisions]

    print(f"=== {d} ===")
    print(f"decisions logged: {n}")
    print(f"decision reasons: " +
          ", ".join(f"{r}={c} ({100*c/n:.0f}%)" for r, c in sorted(reasons.items())))
    print(f"commanded target_kbps : min {min(targets):.0f}  max {max(targets):.0f}  "
          f"last {targets[-1]:.0f}")
    print(f"measured  send_kbps   : min {min(sends):.0f}  max {max(sends):.0f}  "
          f"last {sends[-1]:.0f}")
    print(f"capacity_kbps seen    : min {min(caps):.0f}  max {max(caps):.0f}")
    print(f"receiver_delay_ms     : valid={sum(1 for x in rdelays if x>=0)}/{n}  "
          f"range {min(rdelays):.0f}..{max(rdelays):.0f}")
    print(f"receiver_loss>0 fires : {sum(1 for x in rlosses if x>0)}/{n}")
    # command-following: where commanded target < send (controller asked
    # for less than what went out) — the encoder/sender not following down.
    not_following = sum(1 for x in decisions if x["send"] > x["target"] * 1.3)
    print(f"send >> target (>1.3x): {not_following}/{n} decisions "
          f"-> {'ENCODER NOT FOLLOWING controller down' if not_following > n*0.3 else 'follows ok'}")


if __name__ == "__main__":
    main(sys.argv[1])
