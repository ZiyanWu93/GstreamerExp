"""ResolutionController — the rate→resolution-tier decision for CC-driven
adaptive resolution.

Pure Python, no GStreamer: the camera worker feeds it the smoothed CC rate
on each bitrate-notify and, when it returns a new (width, height), the worker
performs the actual capsfilter switch at a frame boundary (camera.py). Keeping
the decision logic here makes the rate→tier + hysteresis state machine
unit-testable off a GStreamer host with a synthetic rate trace.

The ladder object is duck-typed (a pipeline_config.ResolutionLadder, but only
its fields are read) so importing this module pulls in no gi/Gst.
"""
from __future__ import annotations

from typing import Optional, Tuple


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


class ResolutionController:
    """Per-stream adaptive-resolution decision.

    Starts at the top tier (== source resolution). On each `update(rate, now)`
    it smooths the rate (EWMA), finds the highest tier whose rate floor the
    smoothed rate clears, and switches to it only after the asymmetric hold
    time (slow up / fast down) AND the minimum switch interval have elapsed —
    so a brief excursion or a rate parked between two floors can't flap.
    Returns the new (width, height) on a committed switch, else None.
    """

    def __init__(self, ladder, src_w: int, src_h: int):
        # (height, min_rate_kbps) per tier, height-descending (validated upstream).
        self.tiers = [(t.height, t.min_rate_kbps) for t in ladder.tiers]
        self.src_w = src_w
        self.src_h = src_h
        self.up_hold = ladder.hysteresis_up_hold_s
        self.down_hold = ladder.hysteresis_down_hold_s
        self.min_interval = ladder.min_switch_interval_s
        self.alpha = ladder.ewma_alpha

        self.idx = 0                 # current tier (0 = top = source resolution)
        self.ewma: Optional[float] = None
        self.last_switch_t: Optional[float] = None
        self._cand_idx: Optional[int] = None   # tier we're currently holding toward
        self._cand_since: Optional[float] = None

    def dims(self, idx: int) -> Tuple[int, int]:
        """(width, height) for a tier — width derived from the source aspect
        ratio, rounded to even (I420 needs even dimensions)."""
        h = self.tiers[idx][0]
        return (_even(round(self.src_w * h / self.src_h)), h)

    def current_dims(self) -> Tuple[int, int]:
        return self.dims(self.idx)

    def _desired_idx(self) -> int:
        # Tiers are min_rate-descending; the first one the smoothed rate clears
        # is the highest tier we can support. The lowest tier's floor is 0, so
        # this always resolves.
        for i, (_h, floor) in enumerate(self.tiers):
            if self.ewma >= floor:
                return i
        return len(self.tiers) - 1

    def update(self, rate_kbps: float, now: float) -> Optional[Tuple[int, int]]:
        self.ewma = (float(rate_kbps) if self.ewma is None
                     else self.alpha * rate_kbps + (1.0 - self.alpha) * self.ewma)
        if self.last_switch_t is None:
            self.last_switch_t = now

        desired = self._desired_idx()
        if desired == self.idx:
            self._cand_idx = self._cand_since = None
            return None

        # Hold toward `desired`; restart the timer if the target changed.
        if self._cand_idx != desired:
            self._cand_idx, self._cand_since = desired, now
        hold = self.up_hold if desired < self.idx else self.down_hold
        if (now - self._cand_since) >= hold and (now - self.last_switch_t) >= self.min_interval:
            self.idx = desired
            self.last_switch_t = now
            self._cand_idx = self._cand_since = None
            return self.dims(self.idx)
        return None
