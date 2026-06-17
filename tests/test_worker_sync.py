from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gstexp.runner import _worker_payload_rsync_args


class TestWorkerPayloadSync(unittest.TestCase):

    def test_sync_is_allowlisted_runtime_payload(self):
        cmd = _worker_payload_rsync_args(
            PROJECT_ROOT, "aum-ts", "~/gstexp")

        joined = " ".join(cmd)
        self.assertIn("--include=/gstexp/***", cmd)
        self.assertIn("--include=/scripts/setup_remote.sh", cmd)
        self.assertIn("--include=/scripts/scream-eos-fix.patch", cmd)
        self.assertIn("--exclude=*", cmd)
        self.assertIn("--delete", cmd)
        self.assertIn("--prune-empty-dirs", cmd)
        self.assertIn(f"{PROJECT_ROOT}/", cmd)
        self.assertIn("aum-ts:~/gstexp/", cmd)

        # These stay controller-local or remote-cache-local; they should
        # not be sent as part of every worker sync.
        self.assertNotIn("/runs/", joined)
        self.assertNotIn("/analysis/", joined)
        self.assertNotIn("/docs/", joined)
        self.assertNotIn("/scream/", joined)


class TestStartBarrierHelper(unittest.TestCase):
    """_seconds_until backs the shared-epoch start barrier: the camera waits
    this long before PLAYING so a run's cameras (same host clock) release
    together."""

    def test_seconds_until(self):
        try:
            from gstexp.worker import _seconds_until
        except Exception as e:                       # worker imports gi/Gst
            self.skipTest(f"worker import needs GStreamer: {e}")
        self.assertEqual(_seconds_until(None, 100.0), 0.0)   # no epoch
        self.assertEqual(_seconds_until(0, 100.0), 0.0)      # falsy epoch
        self.assertEqual(_seconds_until(105.0, 100.0), 5.0)  # future -> wait
        self.assertEqual(_seconds_until(95.0, 100.0), 0.0)   # past -> immediate


if __name__ == "__main__":
    unittest.main()
