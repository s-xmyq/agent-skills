"""Opt-in network tests.

Run with:
    AGENT_SKILLS_NETWORK=1 python -m unittest discover -s tests -v

Kept out of the default run because external services are flaky and may be
rate-limited. Only read-only metadata calls are made here; nothing is written
outside a temporary directory.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "skills" / "video-download" / "scripts" / "fetch_video.py"

NETWORK_ENABLED = os.environ.get("AGENT_SKILLS_NETWORK") == "1"


@unittest.skipUnless(
    NETWORK_ENABLED, "set AGENT_SKILLS_NETWORK=1 to run network tests"
)
class BilibiliMetadataTests(unittest.TestCase):
    def test_known_video_metadata(self):
        proc = subprocess.run(
            [sys.executable, str(VIDEO), "BV1xx411c7mD", "--check", "--pretty"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["platform"], "bilibili")
        self.assertEqual(payload["video_id"], "BV1xx411c7mD")
        self.assertEqual(payload["source_api"], "bilibili_web")


if __name__ == "__main__":
    unittest.main()
