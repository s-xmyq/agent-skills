"""Offline CLI-contract and orchestration tests for video-download."""

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skills" / "video-download" / "scripts"
SCRIPT = SKILL_SCRIPTS / "fetch_video.py"
sys.path.insert(0, str(SKILL_SCRIPTS))

import fetch_video  # noqa: E402


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def make_args(**overrides):
    values = {
        "input": "7707101340123663616",
        "out": None,
        "flat": False,
        "check": False,
        "page": None,
        "frame": [],
        "timeout": 10,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


FAKE_META = {
    "video_id": "7707101340123663616",
    "out_id": "7707101340123663616",
    "title": "测试视频",
    "author": "测试作者",
    "urls": [{"url": "https://1.1.1.1/video.mp4", "ext": "mp4"}],
    "headers": {},
    "quality": "1080p",
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "duration": 10,
    "size_reported": 100,
    "source_api": "test",
    "extra": {"aweme_id": "7707101340123663616"},
}


class CliContractTests(unittest.TestCase):
    def test_check_deps(self):
        proc = run_cli("--check-deps", "--pretty")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertIn("dependencies", payload)
        self.assertIn("services", payload)

    def test_missing_input(self):
        proc = run_cli("--pretty")
        self.assertEqual(proc.returncode, 5)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["error"]["code"], "no_url")

    def test_text_without_url(self):
        proc = run_cli("这段文字里没有链接", "--pretty")
        self.assertEqual(proc.returncode, 5)
        self.assertEqual(json.loads(proc.stdout)["error"]["code"], "no_url")

    def test_unsupported_platform(self):
        proc = run_cli("https://www.tiktok.com/@a/video/1", "--pretty")
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["error"]["code"], "unsupported_platform")


class RunFlowTests(unittest.TestCase):
    def test_download_path_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                fetch_video.douyin, "fetch", return_value=dict(FAKE_META)
            ), mock.patch.object(
                fetch_video, "download", return_value=100
            ) as download:
                result = fetch_video.run(make_args(out=tmp))

        self.assertTrue(result["ok"])
        self.assertEqual(result["size_bytes"], 100)
        self.assertEqual(Path(result["path"]).suffix, ".mp4")
        self.assertEqual(download.call_count, 1)

    def test_check_skips_download(self):
        with mock.patch.object(
            fetch_video.douyin, "fetch", return_value=dict(FAKE_META)
        ), mock.patch.object(fetch_video, "download") as download:
            result = fetch_video.run(make_args(check=True))

        self.assertIsNone(result["path"])
        download.assert_not_called()

    def test_frame_degrades_when_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                fetch_video.douyin, "fetch", return_value=dict(FAKE_META)
            ), mock.patch.object(
                fetch_video, "download", return_value=100
            ), mock.patch.object(
                fetch_video, "grab_frame", return_value=None
            ):
                result = fetch_video.run(make_args(out=tmp, frame=[5]))

        self.assertTrue(any("抽帧失败" in item for item in result["warnings"]))

    def test_page_warns_on_non_bilibili(self):
        with mock.patch.object(
            fetch_video.douyin, "fetch", return_value=dict(FAKE_META)
        ), mock.patch.object(fetch_video, "download", return_value=100):
            result = fetch_video.run(make_args(check=True, page=2))

        self.assertTrue(
            any("--page 仅对 B站分P有效" in item for item in result["warnings"])
        )


if __name__ == "__main__":
    unittest.main()
