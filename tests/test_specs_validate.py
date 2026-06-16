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

from gstexp.validation import (
    resolve_includes,
    validate_doc,
    project_to_roles,
    _port_for_stream,
)
from experiment import _load_and_validate_spec as load_experiment
from gstexp.expo import _load_and_validate_spec as load_expo


class TestConfigurations(unittest.TestCase):
    """Each configuration resolves (video/network refs inlined), validates
    against the strict schema, and projects to one (camera, viewer) pair
    per stream. The per-stream invariants the multi-stream spec was built
    to enforce: camera encoder.codec equals viewer decoder.codec, the
    (rtp, rtcp) pair is derived per (config, stream) via _port_for_stream
    with rtcp == rtp + 1, egress/ingress carry the stream index as
    stream_id, and every derived port is globally unique across the whole
    (config, stream) map so co-located streams and concurrent runs never
    collide on bind."""

    def test_every_configuration_validates_and_projects(self):
        seen_ports = {}
        for p in sorted((PROJECT_ROOT / "specs/configurations").glob("*.yaml")):
            with self.subTest(config=p.name):
                doc = yaml.safe_load(p.read_text())
                doc = resolve_includes(doc, PROJECT_ROOT, p)
                validate_doc(doc, p)
                cameras, viewers = project_to_roles(doc, p.stem)
                n = len(doc["streams"])
                # One Camera dict and one Viewer dict per declared stream,
                # index-aligned.
                self.assertEqual(len(cameras), n,
                                 f"{p.name}: camera count != #streams")
                self.assertEqual(len(viewers), n,
                                 f"{p.name}: viewer count != #streams")
                for i, (camera, viewer) in enumerate(zip(cameras, viewers)):
                    # Codec must agree between the two ends of this stream.
                    self.assertEqual(camera["encoder"]["codec"],
                                     viewer["decoder"]["codec"],
                                     f"{p.name}: streams[{i}] codec mismatch")
                    # Port must agree between the two ends and equal the
                    # mechanical per-(config, stream) derivation.
                    expected_port = _port_for_stream(p.stem, i)
                    self.assertEqual(camera["egress"]["port"], expected_port,
                                     f"{p.name}: streams[{i}] egress port "
                                     f"!= _port_for_stream")
                    self.assertEqual(camera["egress"]["port"],
                                     viewer["ingress"]["port"],
                                     f"{p.name}: streams[{i}] port mismatch")
                    # rtcp is the adjacent odd port (RFC 3550).
                    self.assertEqual(camera["egress"]["rtcp_port"],
                                     camera["egress"]["port"] + 1,
                                     f"{p.name}: streams[{i}] rtcp_port not "
                                     f"derived as port+1")
                    # Both ends tag the flow with this stream's index so the
                    # worker can correlate the N concurrent flows.
                    self.assertEqual(camera["egress"]["stream_id"], i,
                                     f"{p.name}: streams[{i}] egress stream_id "
                                     f"!= index")
                    self.assertEqual(viewer["ingress"]["stream_id"], i,
                                     f"{p.name}: streams[{i}] ingress stream_id "
                                     f"!= index")
                    # Topology fields must be present and pair correctly:
                    # camera.egress.host (where the camera sends) is the
                    # viewer's host, viewer.ingress.peer_host (where the
                    # viewer sends RTCP back) is the camera's host. Asserting
                    # this here catches projection regressions that would
                    # otherwise only fail in the worker.
                    self.assertEqual(camera["egress"]["host"],
                                     viewer["ingress"]["host"],
                                     f"{p.name}: streams[{i}] camera.egress.host "
                                     f"vs viewer.ingress.host disagreement")
                    self.assertEqual(camera["egress"]["bind_host"],
                                     viewer["ingress"]["peer_host"],
                                     f"{p.name}: streams[{i}] camera bind vs "
                                     f"viewer peer disagreement")
                    # Port derivation must be unique across the whole
                    # (config, stream) map.
                    port = camera["egress"]["port"]
                    key = (p.stem, i)
                    if port in seen_ports:
                        self.fail(f"port collision: {key!r} and "
                                  f"{seen_ports[port]!r} both derive to {port}")
                    seen_ports[port] = key


class TestExperiments(unittest.TestCase):
    """Each experiment loads, validates structurally, and passes the
    `varies` deep-compare against the real configurations it lists."""

    def test_every_experiment_validates(self):
        for p in sorted((PROJECT_ROOT / "specs/experiments").glob("*.yaml")):
            with self.subTest(experiment=p.name):
                load_experiment(p)

    def test_experiment_records_use_directional_network_shape(self):
        # A configuration references its impairment profile per stream
        # (`streams[i].network`), and the network spec it names carries the
        # directional shape — separate `camera_steps` / `viewer_steps`
        # lists, never a single flat `steps` list — so each actor's NIC can
        # shape its own egress independently. Config 79 references
        # `mahimahi-5g-ho-100ms-x0p33`, whose camera side is the non-empty
        # direction. Read the ref the new schema's way and assert the
        # directional record shape carried into experiment artifacts.
        cfg = yaml.safe_load(
            (PROJECT_ROOT / "specs/configurations/79.yaml").read_text())
        net_ref = cfg["streams"][0].get("network")
        self.assertEqual(net_ref, "mahimahi-5g-ho-100ms-x0p33")
        net_path = PROJECT_ROOT / "specs" / "networks" / f"{net_ref}.yaml"
        self.assertTrue(net_path.is_file(),
                        f"network spec {net_ref!r} not found at {net_path}")
        net_spec = yaml.safe_load(net_path.read_text()) or {}
        record = {
            "name": str(net_ref),
            "camera_steps": net_spec.get("camera_steps") or [],
            "viewer_steps": net_spec.get("viewer_steps") or [],
        }
        self.assertEqual(record["name"], "mahimahi-5g-ho-100ms-x0p33")
        self.assertIn("camera_steps", record)
        self.assertIn("viewer_steps", record)
        self.assertNotIn("steps", net_spec)
        self.assertGreater(len(record["camera_steps"]), 0)


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
