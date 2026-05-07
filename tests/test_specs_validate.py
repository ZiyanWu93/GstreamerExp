"""Positive validator tests — every spec file in the project loads and
validates clean.

Asserts that for every directory under specs/, the canonical loader for
that spec type accepts every file. Catches schema regressions where a
change rejects a real spec we depend on.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gstexp.validation import resolve_includes, validate_doc, project_to_roles
from experiment import _load_and_validate_spec as load_experiment
from gstexp.expo import _load_and_validate_spec as load_expo


class TestConfigurations(unittest.TestCase):
    """Each configuration resolves (video/network refs inlined), validates
    against the strict schema, and projects to per-role dicts whose
    camera encoder.codec equals viewer decoder.codec and whose ports
    match — the structural invariants the unified spec was built to
    enforce."""

    def test_every_configuration_validates_and_projects(self):
        seen_ports = {}
        for p in sorted((PROJECT_ROOT / "specs/configurations").glob("*.yaml")):
            with self.subTest(config=p.name):
                doc = yaml.safe_load(p.read_text())
                doc = resolve_includes(doc, PROJECT_ROOT, p)
                validate_doc(doc, p)
                camera, viewer = project_to_roles(doc, p.stem)
                self.assertEqual(camera["encoder"]["codec"],
                                 viewer["decoder"]["codec"],
                                 f"{p.name}: codec mismatch")
                self.assertEqual(camera["egress"]["port"],
                                 viewer["ingress"]["port"],
                                 f"{p.name}: port mismatch")
                self.assertEqual(camera["egress"]["rtcp_port"],
                                 camera["egress"]["port"] + 1,
                                 f"{p.name}: rtcp_port not derived as port+1")
                # Topology fields must be present and pair correctly:
                # camera.egress.host (where the camera sends) is the
                # viewer's host, viewer.ingress.peer_host (where the
                # viewer sends RTCP back) is the camera's host. Asserting
                # this here catches projection regressions that would
                # otherwise only fail in the worker.
                self.assertEqual(camera["egress"]["host"],
                                 viewer["ingress"]["host"],
                                 f"{p.name}: camera.egress.host vs "
                                 f"viewer.ingress.host disagreement")
                self.assertEqual(camera["egress"]["bind_host"],
                                 viewer["ingress"]["peer_host"],
                                 f"{p.name}: camera bind vs viewer "
                                 f"peer disagreement")
                # Port derivation must be unique across configurations
                port = camera["egress"]["port"]
                if port in seen_ports:
                    self.fail(f"port collision: {p.stem!r} and "
                              f"{seen_ports[port]!r} both derive to {port}")
                seen_ports[port] = p.stem


class TestExperiments(unittest.TestCase):
    """Each experiment loads, validates structurally, and passes the
    `varies` deep-compare against the real configurations it lists."""

    def test_every_experiment_validates(self):
        for p in sorted((PROJECT_ROOT / "specs/experiments").glob("*.yaml")):
            with self.subTest(experiment=p.name):
                load_experiment(p)


class TestExpos(unittest.TestCase):
    """Each expo loads, validates, and references a configuration that exists."""

    def test_every_expo_validates(self):
        for p in sorted((PROJECT_ROOT / "specs/expos").glob("*.yaml")):
            with self.subTest(expo=p.name):
                load_expo(p)


class TestVideoAndNetworkSpecs(unittest.TestCase):
    """Video and network specs aren't validated in isolation today (they
    flow through resolve_includes when a configuration references them).
    The smoke test here is just YAML parseability — real validation
    happens via TestConfigurations indirectly."""

    def test_every_video_spec_parses(self):
        for p in sorted((PROJECT_ROOT / "specs/videos").glob("*.yaml")):
            with self.subTest(video=p.name):
                doc = yaml.safe_load(p.read_text())
                self.assertIsInstance(doc, dict, f"{p.name}: not a mapping")
                self.assertIn("source", doc, f"{p.name}: missing `source` block")

    def test_every_network_spec_parses(self):
        for p in sorted((PROJECT_ROOT / "specs/networks").glob("*.yaml")):
            with self.subTest(network=p.name):
                doc = yaml.safe_load(p.read_text())
                self.assertIsInstance(doc, dict, f"{p.name}: not a mapping")
                self.assertIn("camera_steps", doc,
                              f"{p.name}: missing `camera_steps` block")
                self.assertIn("viewer_steps", doc,
                              f"{p.name}: missing `viewer_steps` block")


if __name__ == "__main__":
    unittest.main()
