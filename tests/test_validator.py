"""Negative validator tests — the validator catches realistic mistakes
with precise error messages.

Each test mutates a real configuration (config 8, which exercises the
distributed + SCReAM + fluctuating-network paths) and asserts the
mutation is rejected. Tests cover the constraints the validator is
responsible for today: typos at any nesting level, missing required
blocks, conditional rules (sink.path with file backend), the metric
registry cross-check, and CC algorithm dispatch / value validation.

Config 8 is now a multi-stream document: the single-stream payload
(source/codec/encoder/sink/recovery/latency_budget_ms + optional
congestion_control) lives under `streams[0]`, joined by `name`/`priority`,
and a top-level `sync:` block declares cross-stream synchronization. So a
mutation that used to read `doc["codec"]` now reads
`doc["streams"][0]["codec"]`, and per-stream validator errors are
index-tagged with a `streams[0].` prefix.
"""

from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gstexp.validation import resolve_includes, validate_doc

BASE_CONFIG_PATH = PROJECT_ROOT / "specs/configurations/8.yaml"
BASE_DOC = yaml.safe_load(BASE_CONFIG_PATH.read_text())


@contextmanager
def assert_validation_fails_with(test_case, expected_substring: str):
    """Asserts the wrapped block raises SystemExit and the message
    contains `expected_substring`. Otherwise fails the test with the
    actual message (or "did not raise") so a regression debug starts
    with the right signal."""
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


def _resolve_and_validate(doc: dict) -> None:
    doc = resolve_includes(doc, PROJECT_ROOT, BASE_CONFIG_PATH)
    validate_doc(doc, BASE_CONFIG_PATH)


def _stream0(doc: dict) -> dict:
    """The lone stream of config 8 — where the former single-stream
    payload (codec/encoder/sink/recovery/...) now lives. Mutations that
    used to read doc["<key>"] now read _stream0(doc)["<key>"]."""
    return doc["streams"][0]


class TestTopLevelStructure(unittest.TestCase):

    def setUp(self):
        self.doc = deepcopy(BASE_DOC)

    def test_unknown_top_level_key_rejected(self):
        self.doc["unknown_field"] = "x"
        with assert_validation_fails_with(self, "unknown top-level key"):
            _resolve_and_validate(self.doc)

    def test_missing_required_block_rejected(self):
        # A required per-stream field (codec) is missing — surfaces with
        # the index-tagged `streams[0]. missing key(s)` message that points
        # at the offending stream.
        del _stream0(self.doc)["codec"]
        with assert_validation_fails_with(self, "streams[0]. missing key(s)"):
            _resolve_and_validate(self.doc)

    def test_missing_required_top_level_block_rejected(self):
        # A required top-level block (sync) is missing — distinct from a
        # missing per-stream field; surfaces with "missing required block".
        del self.doc["sync"]
        with assert_validation_fails_with(self, "missing required block"):
            _resolve_and_validate(self.doc)

    def test_unknown_codec_rejected(self):
        # The codec discriminator is checked against the implemented set;
        # asking for h264 today produces a precise error rather than a
        # confusing "no element h264enc" failure at pipeline-build time.
        _stream0(self.doc)["codec"] = "h264"
        with assert_validation_fails_with(self, "codec must be one of"):
            _resolve_and_validate(self.doc)

    def test_unknown_key_inside_pipeline_block_rejected(self):
        # Per-block allow-set check: any key not in the block's schema is
        # rejected with the expected-set listed.
        _stream0(self.doc)["encoder"]["extra_field"] = 42
        with assert_validation_fails_with(self, "encoder: unknown key"):
            _resolve_and_validate(self.doc)

    def test_latency_budget_required(self):
        del _stream0(self.doc)["latency_budget_ms"]
        with assert_validation_fails_with(self, "streams[0]. missing key(s)"):
            _resolve_and_validate(self.doc)

    def test_latency_budget_must_be_int(self):
        _stream0(self.doc)["latency_budget_ms"] = "100"
        with assert_validation_fails_with(self,
                "latency_budget_ms must be a non-negative integer"):
            _resolve_and_validate(self.doc)

    def test_latency_budget_negative_rejected(self):
        _stream0(self.doc)["latency_budget_ms"] = -1
        with assert_validation_fails_with(self,
                "latency_budget_ms must be a non-negative integer"):
            _resolve_and_validate(self.doc)

    def test_latency_budget_zero_disables(self):
        # 0 is the explicit "no enforcement" value; the validator
        # accepts it without complaint.
        _stream0(self.doc)["latency_budget_ms"] = 0
        _resolve_and_validate(self.doc)


class TestSyncBlock(unittest.TestCase):
    """The `sync:` block declares how the N streams are cross-synchronized
    — discriminated by `mode`, with a `termination` policy. It's a required
    top-level block; a typo or bad value must surface at load time, not
    inside the start-barrier."""

    def setUp(self):
        self.doc = deepcopy(BASE_DOC)

    def test_sync_missing_key_rejected(self):
        del self.doc["sync"]["termination"]
        with assert_validation_fails_with(self, "sync: missing key"):
            _resolve_and_validate(self.doc)

    def test_sync_unknown_key_rejected(self):
        self.doc["sync"]["barrier"] = "soft"
        with assert_validation_fails_with(self, "sync: unknown key"):
            _resolve_and_validate(self.doc)

    def test_sync_bad_mode_rejected(self):
        self.doc["sync"]["mode"] = "wallclock"
        with assert_validation_fails_with(self, "sync.mode must be one of"):
            _resolve_and_validate(self.doc)

    def test_sync_bad_termination_rejected(self):
        self.doc["sync"]["termination"] = "longest"
        with assert_validation_fails_with(self,
                "sync.termination must be one of"):
            _resolve_and_validate(self.doc)


class TestStreamsStructure(unittest.TestCase):
    """The top-level `streams:` list is required, non-empty, and each
    entry needs a unique name — the port stride and the per-stream
    degradation order both key off identity, so a collision must fail
    at load time."""

    def setUp(self):
        self.doc = deepcopy(BASE_DOC)

    def test_empty_streams_rejected(self):
        self.doc["streams"] = []
        with assert_validation_fails_with(self,
                "`streams` must be a non-empty list"):
            _resolve_and_validate(self.doc)

    def test_duplicate_stream_name_rejected(self):
        # A second stream that reuses the first's name collides. Give it a
        # distinct port-driving identity is impossible without a unique
        # name, so the validator rejects the duplicate.
        second = deepcopy(_stream0(self.doc))    # same name: "main"
        self.doc["streams"].append(second)
        with assert_validation_fails_with(self, "streams: duplicate name"):
            _resolve_and_validate(self.doc)

    def test_negative_priority_rejected(self):
        _stream0(self.doc)["priority"] = -1
        with assert_validation_fails_with(self,
                "priority must be a non-negative integer"):
            _resolve_and_validate(self.doc)


class TestSourceBackend(unittest.TestCase):
    """Source backend dispatch — same pattern as `congestion_control` and
    `codec`. Unknown backends are rejected explicitly rather than slipping
    through to a confusing GStreamer "no element <foo>" failure."""

    def setUp(self):
        self.doc = deepcopy(BASE_DOC)
        # The base config's stream uses `video: ...` which inlines source
        # at resolve time. To mutate source, drop the video ref and set
        # source inline on the stream (mirrors the schema's mutual
        # exclusion).
        st = _stream0(self.doc)
        st.pop("video", None)
        st["source"] = {
            "backend": "synthetic",
            "width": 640,
            "height": 480,
            "fps": 15,
            "num_frames": 150,
            "clock_overlay": False,
            "synthetic": {"pattern": "ball"},
        }

    def test_unknown_source_backend_rejected(self):
        # `camera` (real v4l2-style hardware capture) is the next un-
        # implemented backend — preserves the rejection-pathway test
        # now that `file` works.
        _stream0(self.doc)["source"]["backend"] = "camera"
        del _stream0(self.doc)["source"]["synthetic"]
        with assert_validation_fails_with(self, "source.backend `camera` is not implemented"):
            _resolve_and_validate(self.doc)

    def test_wrong_source_subblock_rejected(self):
        # backend=synthetic but a `file` sub-block also set.
        _stream0(self.doc)["source"]["file"] = {"path": "/tmp/x", "loop": True}
        with assert_validation_fails_with(self, "block set but backend is 'synthetic'"):
            _resolve_and_validate(self.doc)

    def test_synthetic_missing_pattern_rejected(self):
        _stream0(self.doc)["source"]["synthetic"] = {}
        with assert_validation_fails_with(self, "source.synthetic: missing key"):
            _resolve_and_validate(self.doc)

    def _switch_to_file(self, sub):
        source = _stream0(self.doc)["source"]
        del source["synthetic"]
        source["backend"] = "file"
        source["file"] = sub

    def test_file_missing_path_rejected(self):
        self._switch_to_file({"loop": True})
        with assert_validation_fails_with(self, "source.file: missing key"):
            _resolve_and_validate(self.doc)

    def test_file_loop_must_be_bool(self):
        self._switch_to_file({"path": "/tmp/x.mp4", "loop": "yes"})
        with assert_validation_fails_with(self, "source.file.loop must be a bool"):
            _resolve_and_validate(self.doc)

    def test_file_extra_knob_rejected(self):
        self._switch_to_file({"path": "/tmp/x.mp4", "loop": True,
                              "rate_limit": 1000})
        with assert_validation_fails_with(self, "source.file: unknown key"):
            _resolve_and_validate(self.doc)

    def test_file_well_formed_passes(self):
        # Positive case: valid file backend validates cleanly. The path
        # is not checked for existence (it's a path on a different host).
        self._switch_to_file({"path": "/tmp/x.mp4", "loop": False})
        _resolve_and_validate(self.doc)   # must not raise


class TestSinkConditionalRules(unittest.TestCase):

    def setUp(self):
        self.doc = deepcopy(BASE_DOC)

    def test_autovideo_sink_rejected(self):
        # Visual rendering moved to expo specs; configurations carry
        # only measurement sinks.
        _stream0(self.doc)["sink"] = {"backend": "autovideo", "sync": True}
        with assert_validation_fails_with(self, "sink.backend must be one of"):
            _resolve_and_validate(self.doc)

    def test_sink_path_with_non_file_backend_rejected(self):
        _stream0(self.doc)["sink"] = {"backend": "fake", "sync": False, "path": "/tmp/x"}
        with assert_validation_fails_with(self, "only applies when backend"):
            _resolve_and_validate(self.doc)

    def test_file_backend_without_path_rejected(self):
        _stream0(self.doc)["sink"] = {"backend": "file", "sync": False}
        with assert_validation_fails_with(self, "sink: `path` is required"):
            _resolve_and_validate(self.doc)


class TestScenarioValidation(unittest.TestCase):

    def setUp(self):
        self.doc = deepcopy(BASE_DOC)

    def test_scenario_typo_caught(self):
        self.doc["scenario"]["setup_delay_secnds"] = 3.0
        with assert_validation_fails_with(self, "scenario: unknown key"):
            _resolve_and_validate(self.doc)

    def test_metric_name_typo_caught(self):
        self.doc["scenario"]["metrics"] = ["frame_count_kbps"]
        with assert_validation_fails_with(self, "unknown metric"):
            _resolve_and_validate(self.doc)

    def test_unknown_key_in_actor_rejected(self):
        self.doc["scenario"]["actors"]["camera"]["extra_field"] = ":20"
        with assert_validation_fails_with(self, "scenario.actors.camera: unknown key"):
            _resolve_and_validate(self.doc)

    def test_ssh_host_and_media_host_split_passes(self):
        self.doc["scenario"]["actors"]["camera"] = {
            "host": "aum-ts",
            "ssh_host": "aum-ts",
            "media_host": "10.10.10.40",
            "project_root": "~/gstexp",
            "network_env": {"NIC": "eth0"},
        }
        self.doc["scenario"]["actors"]["viewer"] = {
            "host": "veda-ts",
            "ssh_host": "veda-ts",
            "media_host": "10.10.10.41",
            "project_root": "~/gstexp",
        }
        _resolve_and_validate(self.doc)

    def test_actor_missing_host_rejected(self):
        # The validator must reject a doc whose actor block is missing
        # required fields after hosts.yaml merge. We pass hosts_override={}
        # to bypass the project's actual hosts.yaml so the missing host
        # actually reaches the validator. The base config has its actor
        # block scrubbed (filled from hosts.yaml in normal use), so we
        # set host on the camera actor first to ensure the explicit
        # deletion is meaningful.
        self.doc["scenario"]["actors"]["camera"] = {
            "host": "1.2.3.4", "project_root": "/x",
            "network_env": {"NIC": "eth0"},
        }
        del self.doc["scenario"]["actors"]["camera"]["host"]
        with assert_validation_fails_with(self, "scenario.actors.camera: missing key"):
            doc = resolve_includes(self.doc, PROJECT_ROOT, BASE_CONFIG_PATH,
                                   hosts_override={})
            validate_doc(doc, BASE_CONFIG_PATH)

    def test_unknown_role_rejected(self):
        self.doc["scenario"]["actors"]["relay"] = {"host": "127.0.0.1"}
        with assert_validation_fails_with(self, "scenario.actors: unknown role"):
            _resolve_and_validate(self.doc)


class TestRecoveryBlock(unittest.TestCase):

    def setUp(self):
        self.doc = deepcopy(BASE_DOC)

    def test_recovery_block_required(self):
        del _stream0(self.doc)["recovery"]
        with assert_validation_fails_with(self, "streams[0]. missing key(s)"):
            _resolve_and_validate(self.doc)

    def test_buffer_ms_required_when_nack_true(self):
        _stream0(self.doc)["recovery"] = {"nack": True, "pli": False, "fec": False}
        with assert_validation_fails_with(self, "rtx_buffer_ms` is required"):
            _resolve_and_validate(self.doc)

    def test_buffer_ms_forbidden_when_nack_false(self):
        _stream0(self.doc)["recovery"] = {"nack": False, "pli": False, "fec": False,
                                          "rtx_buffer_ms": 500}
        with assert_validation_fails_with(self, "only applies when nack: true"):
            _resolve_and_validate(self.doc)

    def test_unknown_recovery_key_rejected(self):
        _stream0(self.doc)["recovery"]["red"] = True
        with assert_validation_fails_with(self, "recovery: unknown key"):
            _resolve_and_validate(self.doc)

    def test_nack_must_be_bool(self):
        _stream0(self.doc)["recovery"] = {"nack": "yes", "pli": False, "fec": False}
        with assert_validation_fails_with(self, "recovery.nack must be a bool"):
            _resolve_and_validate(self.doc)

    def test_pli_required(self):
        _stream0(self.doc)["recovery"] = {"nack": False, "fec": False}    # missing pli
        with assert_validation_fails_with(self, "recovery: missing key"):
            _resolve_and_validate(self.doc)

    def test_pli_must_be_bool(self):
        _stream0(self.doc)["recovery"] = {"nack": False, "pli": "yes", "fec": False}
        with assert_validation_fails_with(self, "recovery.pli must be a bool"):
            _resolve_and_validate(self.doc)

    def test_fec_required(self):
        _stream0(self.doc)["recovery"] = {"nack": False, "pli": False}     # missing fec
        with assert_validation_fails_with(self, "recovery: missing key"):
            _resolve_and_validate(self.doc)

    def test_fec_must_be_bool(self):
        _stream0(self.doc)["recovery"] = {"nack": False, "pli": False, "fec": "yes"}
        with assert_validation_fails_with(self, "recovery.fec must be a bool"):
            _resolve_and_validate(self.doc)

    def test_fec_percentage_required_when_fec_true(self):
        _stream0(self.doc)["recovery"] = {"nack": False, "pli": False, "fec": True}
        with assert_validation_fails_with(self, "fec_percentage` is required"):
            _resolve_and_validate(self.doc)

    def test_fec_percentage_forbidden_when_fec_false(self):
        _stream0(self.doc)["recovery"] = {"nack": False, "pli": False, "fec": False,
                                          "fec_percentage": 25}
        with assert_validation_fails_with(self, "only applies when fec: true"):
            _resolve_and_validate(self.doc)

    def test_fec_percentage_out_of_range(self):
        _stream0(self.doc)["recovery"] = {"nack": False, "pli": False, "fec": True,
                                          "fec_percentage": 150}
        with assert_validation_fails_with(self, "fec_percentage must be an integer in"):
            _resolve_and_validate(self.doc)


class TestCongestionControlDispatch(unittest.TestCase):

    def setUp(self):
        self.doc = deepcopy(BASE_DOC)

    def test_wrong_subblock_for_algorithm_rejected(self):
        # config 8 has algorithm: scream; gcc block must be forbidden
        _stream0(self.doc)["congestion_control"]["gcc"] = {"estimator": "kalman"}
        with assert_validation_fails_with(self, "`gcc` block set but algorithm is 'scream'"):
            _resolve_and_validate(self.doc)

    def test_scream_knob_typo_caught(self):
        _stream0(self.doc)["congestion_control"]["scream"] = {"delay_targt_seconds": 0.06}
        with assert_validation_fails_with(self, "congestion_control.scream: unknown key"):
            _resolve_and_validate(self.doc)

    def test_invalid_ect_value_rejected(self):
        _stream0(self.doc)["congestion_control"]["scream"] = {"ect": 2}     # valid set is {-1, 0, 1, 3}
        with assert_validation_fails_with(self, "ect must be one of"):
            _resolve_and_validate(self.doc)

    def test_unknown_algorithm_rejected(self):
        _stream0(self.doc)["congestion_control"]["algorithm"] = "bbr"
        with assert_validation_fails_with(self, "must be 'scream' or 'gcc'"):
            _resolve_and_validate(self.doc)

    def test_invalid_gcc_estimator_rejected(self):
        # Switch to gcc algorithm (and remove scream-only fields if any)
        _stream0(self.doc)["congestion_control"]["algorithm"] = "gcc"
        _stream0(self.doc)["congestion_control"]["gcc"] = {"estimator": "kullback-leibler"}
        with assert_validation_fails_with(self, "estimator must be one of"):
            _resolve_and_validate(self.doc)


class TestDecodedPsnrConstraints(unittest.TestCase):
    """The decoded_psnr metric reproduces the camera's source on the
    viewer for per-frame ground-truth comparison. Only synthetic sources
    are reproducible today, and clock_overlay would burn wall-clock text
    that the viewer can't match pixel-exactly. The validator enforces
    both constraints at config-load time."""

    def setUp(self):
        self.doc = deepcopy(BASE_DOC)
        # Resolve source inline so we can mutate clock_overlay / backend.
        st = _stream0(self.doc)
        st.pop("video", None)
        st["source"] = {
            "backend": "synthetic",
            "width": 640,
            "height": 480,
            "fps": 15,
            "num_frames": 150,
            "clock_overlay": False,
            "synthetic": {"pattern": "ball"},
        }

    def test_decoded_psnr_with_synthetic_no_overlay_passes(self):
        self.doc["scenario"]["metrics"].append("decoded_psnr")
        # Should not raise.
        _resolve_and_validate(self.doc)

    def test_decoded_psnr_with_file_no_loop_passes(self):
        # file backend is supported as long as loop=false (camera and
        # viewer seek-on-EOS aren't synchronized, so single-pass only).
        self.doc["scenario"]["metrics"].append("decoded_psnr")
        _stream0(self.doc)["source"] = {
            "backend": "file",
            "width": 1280, "height": 720, "fps": 30,
            "num_frames": 600, "clock_overlay": False,
            "file": {"path": "/tmp/whatever.webm", "loop": False},
        }
        # Should not raise.
        _resolve_and_validate(self.doc)

    def test_decoded_psnr_rejects_file_with_loop(self):
        self.doc["scenario"]["metrics"].append("decoded_psnr")
        _stream0(self.doc)["source"] = {
            "backend": "file",
            "width": 1280, "height": 720, "fps": 30,
            "num_frames": 600, "clock_overlay": False,
            "file": {"path": "/tmp/whatever.webm", "loop": True},
        }
        with assert_validation_fails_with(
                self, "requires file.loop == false"):
            _resolve_and_validate(self.doc)

    def test_decoded_psnr_rejects_clock_overlay(self):
        self.doc["scenario"]["metrics"].append("decoded_psnr")
        _stream0(self.doc)["source"]["clock_overlay"] = True
        with assert_validation_fails_with(
                self, "decoded_psnr metric requires clock_overlay == false"):
            _resolve_and_validate(self.doc)

    def test_projection_omits_ground_truth_when_metric_off(self):
        # Sanity: without decoded_psnr in scenario.metrics, the viewer
        # spec should not carry ground_truth — keep the spec minimal.
        from gstexp.validation import project_to_roles
        doc = resolve_includes(deepcopy(self.doc), PROJECT_ROOT, BASE_CONFIG_PATH)
        validate_doc(doc, BASE_CONFIG_PATH)
        _cameras, viewers = project_to_roles(doc, "8")
        self.assertNotIn("ground_truth", viewers[0])

    def test_projection_carries_ground_truth_when_metric_on(self):
        from gstexp.validation import project_to_roles
        self.doc["scenario"]["metrics"].append("decoded_psnr")
        doc = resolve_includes(deepcopy(self.doc), PROJECT_ROOT, BASE_CONFIG_PATH)
        validate_doc(doc, BASE_CONFIG_PATH)
        _cameras, viewers = project_to_roles(doc, "8")
        viewer = viewers[0]
        self.assertIn("ground_truth", viewer)
        self.assertEqual(viewer["ground_truth"]["backend"], "synthetic")
        self.assertEqual(viewer["ground_truth"]["width"], 640)
        self.assertEqual(viewer["ground_truth"]["height"], 480)
        self.assertEqual(viewer["ground_truth"]["fps"], 15)


if __name__ == "__main__":
    unittest.main()
