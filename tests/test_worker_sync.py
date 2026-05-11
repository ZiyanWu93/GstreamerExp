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


if __name__ == "__main__":
    unittest.main()
