"""Negative tests for the experiment and expo validators — the
spec types that cross-reference configuration files.

Tests use temporary fixture directories and monkey-patch the runner
modules' CONFIGURATIONS_DIR / EXPOS_DIR / EXPERIMENTS_DIR so each test
runs against synthetic fixtures rather than the real specs/. This
isolates cross-validator behavior from the project's actual specs.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import experiment as experiment_mod
import gstexp.expo as expo_mod


@contextmanager
def assert_validation_fails_with(test_case, expected_substring: str):
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


# A minimal valid configuration the cross-spec tests can build on. It
# uses the real video and network specs (so resolve_includes works) but
# everything else is inline so the test isn't coupled to the project's
# actual configurations. No `transport` block — port is derived from
# the file id.
def _minimal_config(*, cc_algorithm: str = "scream") -> dict:
    return {
        "meta": {"name": "fixture", "description": "test fixture"},
        "video": "ball-480p15",
        "network": "loopback",
        "scenario": {
            "actors": {
                "camera": {"host": "127.0.0.1"},
                "viewer": {"host": "127.0.0.1"},
            },
            "setup_delay_seconds": 1.0,
            "drain_delay_seconds": 1.0,
            "metrics": ["frame_count", "encoder_target_kbps"],
        },
        "codec": "vp8",
        "encoder": {
            "bitrate_kbps": 500,
            "keyframe_interval_frames": 60,
        },
        "congestion_control": {
            "algorithm": cc_algorithm,
            "init_bitrate_kbps": 500,
            "min_bitrate_kbps": 200,
            "max_bitrate_kbps": 4000,
        },
        "sink": {"backend": "fake", "sync": False},
        "recovery": {"nack": False, "pli": False, "fec": False},
        "latency_budget_ms": 0,
    }


class TestExperimentVariesValidator(unittest.TestCase):
    """The varies-block deep-compare must catch undeclared divergences
    between configurations listed in the same experiment."""

    def _setup_fixtures(self, tmp: Path, *, configs: dict, experiment_doc: dict):
        configs_dir = tmp / "configurations"
        experiments_dir = tmp / "experiments"
        configs_dir.mkdir()
        experiments_dir.mkdir()
        for cid, doc in configs.items():
            (configs_dir / f"{cid}.yaml").write_text(yaml.safe_dump(doc))
        spec_path = experiments_dir / "fixture.yaml"
        spec_path.write_text(yaml.safe_dump(experiment_doc))
        return configs_dir, spec_path

    def test_diverging_path_outside_varies_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_a = _minimal_config(cc_algorithm="scream")
            cfg_b = _minimal_config(cc_algorithm="gcc")
            # Inject an undeclared divergence: different encoder bitrate
            cfg_b["encoder"]["bitrate_kbps"] = 1000
            experiment_doc = {
                "name": "fixture",
                "description": "diverging fixture",
                "reps": 1,
                "varies": ["congestion_control.algorithm", "transport.port",
                           "congestion_control.gcc", "congestion_control.scream"],
                "configurations": [
                    {"id": "a", "label": "A", "color": "#ff0000"},
                    {"id": "b", "label": "B", "color": "#0000ff"},
                ],
            }
            configs_dir, spec_path = self._setup_fixtures(
                tmp, configs={"a": cfg_a, "b": cfg_b},
                experiment_doc=experiment_doc)

            with patch.object(experiment_mod, "CONFIGURATIONS_DIR", configs_dir):
                with assert_validation_fails_with(self, "differ on paths not declared in `varies`"):
                    experiment_mod._load_and_validate_spec(spec_path)

    def test_matching_configs_pass(self):
        # Two configs that legitimately differ only on declared paths
        # validate cleanly.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_a = _minimal_config(cc_algorithm="scream")
            cfg_b = _minimal_config(cc_algorithm="gcc")
            experiment_doc = {
                "name": "fixture",
                "description": "matching fixture",
                "reps": 1,
                "varies": ["congestion_control.algorithm", "transport.port",
                           "congestion_control.gcc", "congestion_control.scream"],
                "configurations": [
                    {"id": "a", "label": "A", "color": "#ff0000"},
                    {"id": "b", "label": "B", "color": "#0000ff"},
                ],
            }
            configs_dir, spec_path = self._setup_fixtures(
                tmp, configs={"a": cfg_a, "b": cfg_b},
                experiment_doc=experiment_doc)
            with patch.object(experiment_mod, "CONFIGURATIONS_DIR", configs_dir):
                experiment_mod._load_and_validate_spec(spec_path)

    def test_duplicate_config_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            experiment_doc = {
                "name": "fixture",
                "reps": 1,
                "varies": [],
                "configurations": [
                    {"id": "a", "label": "A", "color": "#ff0000"},
                    {"id": "a", "label": "B", "color": "#0000ff"},
                ],
            }
            (tmp / "configurations").mkdir()
            (tmp / "experiments").mkdir()
            spec_path = tmp / "experiments/fixture.yaml"
            spec_path.write_text(yaml.safe_dump(experiment_doc))
            with patch.object(experiment_mod, "CONFIGURATIONS_DIR", tmp / "configurations"):
                with assert_validation_fails_with(self, "duplicate id"):
                    experiment_mod._load_and_validate_spec(spec_path)


class TestExpoValidator(unittest.TestCase):

    def _setup_fixtures(self, tmp: Path, *,
                        spec_doc: dict, configs: dict | None = None) -> Path:
        configs_dir = tmp / "configurations"
        expos_dir = tmp / "expos"
        configs_dir.mkdir()
        expos_dir.mkdir()
        for cid, doc in (configs or {}).items():
            (configs_dir / f"{cid}.yaml").write_text(yaml.safe_dump(doc))
        spec_path = expos_dir / "fixture.yaml"
        spec_path.write_text(yaml.safe_dump(spec_doc))
        return spec_path, configs_dir

    def test_unknown_configuration_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            spec_doc = {
                "name": "fixture",
                "configuration": "nonexistent",
                "display": ":0",
            }
            spec_path, configs_dir = self._setup_fixtures(tmp, spec_doc=spec_doc)
            with patch.object(expo_mod, "CONFIGURATIONS_DIR", configs_dir):
                with assert_validation_fails_with(self, "configuration 'nonexistent' not found"):
                    expo_mod._load_and_validate_spec(spec_path)

    def test_missing_display_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            spec_doc = {"name": "fixture", "configuration": "a"}
            spec_path, configs_dir = self._setup_fixtures(
                tmp, spec_doc=spec_doc,
                configs={"a": _minimal_config()})
            with patch.object(expo_mod, "CONFIGURATIONS_DIR", configs_dir):
                with assert_validation_fails_with(self, "missing required key"):
                    expo_mod._load_and_validate_spec(spec_path)

    def test_unknown_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            spec_doc = {
                "name": "fixture",
                "configuration": "a",
                "display": ":0",
                "extra_thing": True,
            }
            spec_path, configs_dir = self._setup_fixtures(
                tmp, spec_doc=spec_doc,
                configs={"a": _minimal_config()})
            with patch.object(expo_mod, "CONFIGURATIONS_DIR", configs_dir):
                with assert_validation_fails_with(self, "unknown key"):
                    expo_mod._load_and_validate_spec(spec_path)

    def test_well_formed_expo_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            spec_doc = {
                "name": "fixture",
                "configuration": "a",
                "display": ":0",
            }
            spec_path, configs_dir = self._setup_fixtures(
                tmp, spec_doc=spec_doc,
                configs={"a": _minimal_config()})
            with patch.object(expo_mod, "CONFIGURATIONS_DIR", configs_dir):
                spec = expo_mod._load_and_validate_spec(spec_path)
            self.assertEqual(spec["configuration"], "a")
            self.assertEqual(spec["display"], ":0")


if __name__ == "__main__":
    unittest.main()
