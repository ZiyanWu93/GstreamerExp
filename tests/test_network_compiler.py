"""Network compiler tests — discriminated loss models + GE state reset.

The network spec compiler in validation._compile_network_steps takes a
list of (rate, delay, loss, duration, label) steps and produces the
pre_run / during_run / post_run shell hooks. Loss is the one impairment
dimension with multiple models; this file exercises both the validator
(reject malformed loss blocks) and the compiler (correct netem clauses
and correct del+add vs change at step boundaries).
"""

from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gstexp.validation import _compile_network_steps, _gemodel_p_r


@contextmanager
def assert_compile_fails_with(test_case, expected_substring: str):
    try:
        yield
    except SystemExit as e:
        if expected_substring not in str(e):
            test_case.fail(
                f"expected SystemExit containing {expected_substring!r}, "
                f"got: {e}")
    else:
        test_case.fail(
            f"expected SystemExit containing {expected_substring!r}, "
            f"but no exception was raised")


def _step(loss: dict, *, duration: int = 5, rate_kbps: int = 1000,
          delay_ms: int = 20, label: str = "test") -> dict:
    return {
        "duration": duration,
        "rate_kbps": rate_kbps,
        "delay_ms": delay_ms,
        "loss": loss,
        "label": label,
    }


def _spec(camera_steps: list, viewer_steps: list | None = None) -> dict:
    return {"name": "fixture", "description": "test fixture",
            "camera_steps": camera_steps,
            "viewer_steps": viewer_steps if viewer_steps is not None else []}


def _compile(camera_steps: list, viewer_steps: list | None = None) -> dict:
    return _compile_network_steps(Path("fixture.yaml"),
                                  _spec(camera_steps, viewer_steps))


def _camera_hook(hooks: dict, phase: str) -> dict:
    """Single camera-side hook for a phase. Asserts there's exactly one."""
    matches = [h for h in hooks[phase] if h["host"] == "camera"]
    assert len(matches) == 1, f"expected 1 camera hook in {phase}, got {len(matches)}"
    return matches[0]


def _viewer_hooks(hooks: dict, phase: str) -> list:
    return [h for h in hooks[phase] if h["host"] == "viewer"]


class TestLossValidator(unittest.TestCase):
    """Each loss model declares its own required keys; the validator
    rejects mistakes precisely."""

    def test_loss_block_required(self):
        # Drop the loss block entirely — caught by the step-level
        # `missing key(s)` check, not the loss validator.
        steps = [_step({"model": "none"})]
        del steps[0]["loss"]
        with assert_compile_fails_with(self, "missing key(s) ['loss']"):
            _compile(steps)

    def test_unknown_loss_model_rejected(self):
        with assert_compile_fails_with(self, "loss.model must be one of"):
            _compile([_step({"model": "gilbert"})])

    def test_none_with_extra_key_rejected(self):
        with assert_compile_fails_with(self, "loss[none]: unknown key"):
            _compile([_step({"model": "none", "pct": 2})])

    def test_uniform_missing_pct_rejected(self):
        with assert_compile_fails_with(self, "loss[uniform]: missing key"):
            _compile([_step({"model": "uniform"})])

    def test_uniform_pct_zero_rejected(self):
        # Encourage the user to write `model: none` instead of
        # `model: uniform, pct: 0`.
        with assert_compile_fails_with(self, "loss.pct must be a number in"):
            _compile([_step({"model": "uniform", "pct": 0})])

    def test_bursty_missing_burst_rejected(self):
        with assert_compile_fails_with(self, "loss[bursty]: missing key"):
            _compile([_step({"model": "bursty", "pct": 2})])

    def test_bursty_burst_one_rejected(self):
        # B=1 collapses GE to Bernoulli — route the user back to
        # `model: uniform` rather than silently accept.
        with assert_compile_fails_with(self, "loss.burst must be an integer >= 2"):
            _compile([_step({"model": "bursty", "pct": 2, "burst": 1})])


class TestNetemClauses(unittest.TestCase):
    """The compiler emits the right netem clause per loss model."""

    def test_none_omits_loss_clause(self):
        hooks = _compile([_step({"model": "none"})])
        self.assertIn("netem delay 20ms\n", _camera_hook(hooks, "pre_run")["script"])
        self.assertNotIn("loss", _camera_hook(hooks, "pre_run")["script"].split("netem")[1].split("\n")[0])

    def test_uniform_emits_percent_clause(self):
        hooks = _compile([_step({"model": "uniform", "pct": 5})])
        self.assertIn("netem delay 20ms loss 5%", _camera_hook(hooks, "pre_run")["script"])

    def test_bursty_emits_gemodel_clause(self):
        hooks = _compile([_step({"model": "bursty", "pct": 2, "burst": 3})])
        # r = 100/3 = 33.33%; p = 2*r/98 = 0.6803%
        self.assertIn("loss gemodel 0.6803 33.3333", _camera_hook(hooks, "pre_run")["script"])

    def test_step_echo_includes_derived_p_r_for_bursty(self):
        hooks = _compile([
            _step({"model": "none"}, duration=2),
            _step({"model": "bursty", "pct": 2, "burst": 3}, duration=2),
        ])
        self.assertIn("bursty (B=3, p=0.68%, r=33.3%)",
                      _camera_hook(hooks, "during_run")["script"])


class TestGemodelFormula(unittest.TestCase):
    """The friendly→raw conversion preserves the requested averages."""

    def test_p_r_from_pct_burst(self):
        # avg loss rate = p / (p + r); avg burst length = 1/r packets.
        for pct, burst in [(2, 3), (1, 5), (5, 10), (0.5, 20)]:
            with self.subTest(pct=pct, burst=burst):
                p, r = _gemodel_p_r(pct, burst)
                # Both p and r are in percent (0..100).
                self.assertAlmostEqual(r, 100.0 / burst, places=6)
                # Avg loss = p / (p + r); both p and r are percentages,
                # so the ratio is dimensionless.
                self.assertAlmostEqual(p / (p + r), pct / 100.0, places=6)


class TestStateResetTransitions(unittest.TestCase):
    """The compiler must del+add at any transition involving a bursty
    step so Gilbert-Elliott state starts fresh on entry. Transitions
    that don't touch bursty stay on the atomic `tc qdisc change` form."""

    def test_uniform_to_uniform_uses_change(self):
        hooks = _compile([
            _step({"model": "none"}, duration=2),
            _step({"model": "uniform", "pct": 2}, duration=2),
        ])
        script = _camera_hook(hooks, "during_run")["script"]
        self.assertIn("tc qdisc change dev", script)
        self.assertNotIn("tc qdisc del dev", script.split("sleep 2")[1])

    def test_uniform_to_bursty_uses_del_add(self):
        hooks = _compile([
            _step({"model": "uniform", "pct": 2}, duration=2),
            _step({"model": "bursty", "pct": 2, "burst": 5}, duration=2),
        ])
        script = _camera_hook(hooks, "during_run")["script"]
        # The during_run portion (after the first sleep) must do a hard
        # reset rather than a state-preserving change.
        transition = script.split("sleep 2")[1]
        self.assertIn("tc qdisc del dev", transition)
        self.assertIn("tc qdisc add dev", transition)
        self.assertNotIn("tc qdisc change dev", transition)

    def test_bursty_to_uniform_uses_del_add(self):
        # The exit transition matters too — leftover GE state could
        # leak into a subsequent re-entry to bursty in a longer spec.
        hooks = _compile([
            _step({"model": "bursty", "pct": 2, "burst": 5}, duration=2),
            _step({"model": "none"}, duration=2),
        ])
        transition = _camera_hook(hooks, "during_run")["script"].split("sleep 2")[1]
        self.assertIn("tc qdisc del dev", transition)
        self.assertNotIn("tc qdisc change dev", transition)

    def test_bursty_to_bursty_uses_del_add(self):
        # Two adjacent bursty steps with different parameters: still a
        # hard reset between them so the second step's chain begins from
        # a known state.
        hooks = _compile([
            _step({"model": "bursty", "pct": 2, "burst": 3}, duration=2),
            _step({"model": "bursty", "pct": 5, "burst": 10}, duration=2),
        ])
        transition = _camera_hook(hooks, "during_run")["script"].split("sleep 2")[1]
        self.assertIn("tc qdisc del dev", transition)
        self.assertNotIn("tc qdisc change dev", transition)


class TestRealNetworkSpecs(unittest.TestCase):
    """Every shipped spec must compile cleanly. Catches schema drift if
    a future change to validation.py breaks an existing spec."""

    def test_every_spec_compiles(self):
        import yaml
        for p in sorted((PROJECT_ROOT / "specs/networks").glob("*.yaml")):
            with self.subTest(network=p.name):
                spec = yaml.safe_load(p.read_text())
                hooks = _compile_network_steps(p, spec)
                # Schema invariant: every phase is a list, possibly empty.
                self.assertIsInstance(hooks["pre_run"], list)
                self.assertIsInstance(hooks["during_run"], list)
                self.assertIsInstance(hooks["post_run"], list)


class TestAsymmetricImpairment(unittest.TestCase):
    """Per-actor step lists compile independently into the right hook
    lists. The shaped-direction count drives the hook count per phase."""

    def test_empty_viewer_steps_no_viewer_hooks(self):
        # The default-equivalent case: only the camera direction is shaped.
        # Reproduces today's behaviour with no viewer-side tc invocations.
        hooks = _compile(
            [_step({"model": "uniform", "pct": 2}, duration=5)],
            viewer_steps=[],
        )
        self.assertEqual(len(_viewer_hooks(hooks, "pre_run")), 0)
        self.assertEqual(len(_viewer_hooks(hooks, "post_run")), 0)
        # Camera-side is still present.
        self.assertEqual(len([h for h in hooks["pre_run"] if h["host"] == "camera"]), 1)

    def test_viewer_steps_emit_viewer_hooks(self):
        # Both directions shaped → both directions get pre/post hooks.
        hooks = _compile(
            camera_steps=[_step({"model": "uniform", "pct": 2}, duration=5)],
            viewer_steps=[_step({"model": "uniform", "pct": 5}, duration=5)],
        )
        self.assertEqual(len(hooks["pre_run"]), 2)
        self.assertEqual(len(hooks["post_run"]), 2)
        roles = {h["host"] for h in hooks["pre_run"]}
        self.assertEqual(roles, {"camera", "viewer"})

    def test_independent_step_timelines(self):
        # camera_steps and viewer_steps may have different lengths and
        # different durations — each compiles its own during_run cadence.
        hooks = _compile(
            camera_steps=[
                _step({"model": "none"}, duration=3),
                _step({"model": "uniform", "pct": 2}, duration=5),
                _step({"model": "none"}, duration=3),
            ],
            viewer_steps=[
                _step({"model": "none"}, duration=2),
                _step({"model": "uniform", "pct": 5}, duration=8),
            ],
        )
        # Camera has 2 transitions (3-step list), viewer has 1 (2-step list).
        cam_during = _camera_hook(hooks, "during_run")["script"]
        view_during = _viewer_hooks(hooks, "during_run")[0]["script"]
        # First-sleep durations encode the previous step's duration —
        # different per direction.
        self.assertIn("sleep 3", cam_during)
        self.assertIn("sleep 5", cam_during)        # camera step 2 → 3
        self.assertIn("sleep 2", view_during)
        self.assertNotIn("sleep 3", view_during)    # viewer doesn't sleep 3
        self.assertNotIn("sleep 5", view_during)    # viewer doesn't sleep 5

    def test_bursty_state_reset_per_direction(self):
        # A bursty step in the camera direction triggers del+add on
        # camera-side transitions only — the viewer's hooks are unaffected.
        hooks = _compile(
            camera_steps=[
                _step({"model": "uniform", "pct": 2}, duration=5),
                _step({"model": "bursty", "pct": 2, "burst": 5}, duration=5),
            ],
            viewer_steps=[
                _step({"model": "uniform", "pct": 5}, duration=5),
                _step({"model": "uniform", "pct": 5}, duration=5),
            ],
        )
        cam_during = _camera_hook(hooks, "during_run")["script"]
        view_during = _viewer_hooks(hooks, "during_run")[0]["script"]
        # Camera transitions into bursty → del+add.
        self.assertIn("tc qdisc del dev", cam_during.split("sleep 5")[1])
        # Viewer stays uniform → atomic change.
        self.assertIn("tc qdisc change dev", view_during)
        self.assertNotIn("tc qdisc del dev",
                         view_during.split("sleep 5")[1])


if __name__ == "__main__":
    unittest.main()
