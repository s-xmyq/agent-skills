"""Offline unit tests for video-download/scripts/fetch_video.py.

No network access is required. Run from the repository root:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = REPO_ROOT / "skills" / "video-download" / "scripts"
SCRIPT = SKILL_SCRIPTS / "fetch_video.py"
sys.path.insert(0, str(SKILL_SCRIPTS))

import fetch_video as fv  # noqa: E402

AWEME_ID = "7696355215896825107"
VIDEO_URL = f"https://www.douyin.com/video/{AWEME_ID}"


def api_payload() -> dict:
    return {
        "code": 0,
        "data": {
            "item": {
                "title": "标题 #话题",
                "url": "https://cdn.example/v.mp4?a=1&amp;b=2",
                "ury": "https://cdn.example/backup.mp4",
                "cover": "https://cdn.example/cover.jpg",
                "quality": "1080p",
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "duration": 56,
                "size": 1000,
            },
            "author": {"name": "作者"},
            "stat": {"like": 1, "comment": 2, "collect": 3, "share": 4},
        },
    }


def meta_fields(**overrides) -> dict:
    meta = {
        "title": "标题 #话题",
        "author": "作者",
        "video_url": "https://cdn.example/v.mp4",
        "backup_url": "https://cdn.example/backup.mp4",
        "quality": "1080p",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "duration": 56,
        "size_reported": 1000,
        "source_api": "xingzhige",
    }
    meta.update(overrides)
    return meta


def make_args(**overrides) -> argparse.Namespace:
    values = {
        "input": VIDEO_URL,
        "out": None,
        "flat": False,
        "check": False,
        "frame": None,
        "timeout": 300,
        "pretty": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ExtractUrlTests(unittest.TestCase):
    def test_extracts_from_share_text(self) -> None:
        text = "7.43 复制打开抖音，看看【迟迟的作品】 https://v.douyin.com/abc/ 12/27"
        self.assertEqual(fv.extract_url(text), "https://v.douyin.com/abc/")

    def test_strips_trailing_punctuation(self) -> None:
        self.assertEqual(
            fv.extract_url("看这个 https://v.douyin.com/abc/。"),
            "https://v.douyin.com/abc/",
        )

    def test_missing_url_raises(self) -> None:
        with self.assertRaises(fv.SkillError) as ctx:
            fv.extract_url("没有链接的一段话")
        self.assertEqual(ctx.exception.code, "no_url")


class DetectPlatformTests(unittest.TestCase):
    def test_douyin_domains(self) -> None:
        for url in (
            "https://v.douyin.com/abc/",
            "https://www.douyin.com/video/1",
            "https://www.iesdouyin.com/share/video/1/",
            "https://v.amemv.com/abc/",
            "https://v.douyin.com:443/abc/",
        ):
            with self.subTest(url=url):
                self.assertEqual(fv.detect_platform(url), "douyin")

    def test_known_but_unsupported_platforms(self) -> None:
        cases = {
            "https://www.tiktok.com/@x/video/1": "tiktok",
            "https://v.kuaishou.com/abc": "kuaishou",
            "https://www.xiaohongshu.com/explore/abc": "xiaohongshu",
            "https://www.bilibili.com/video/BV1xx": "bilibili",
            "https://b23.tv/abc": "bilibili",
        }
        for url, platform in cases.items():
            with self.subTest(url=url):
                self.assertEqual(fv.detect_platform(url), platform)

    def test_unknown_domain_raises(self) -> None:
        for url in ("https://example.com/a", "https://notdouyin.com/a"):
            with self.subTest(url=url):
                with self.assertRaises(fv.SkillError) as ctx:
                    fv.detect_platform(url)
                self.assertEqual(ctx.exception.code, "unknown_platform")


class ResolveAwemeIdTests(unittest.TestCase):
    """Only the non-network branches; short links are resolved over HTTP."""

    def test_numeric_id_passthrough(self) -> None:
        self.assertEqual(fv.resolve_aweme_id(AWEME_ID), AWEME_ID)

    def test_video_and_note_urls(self) -> None:
        for url in (
            f"https://www.douyin.com/video/{AWEME_ID}",
            f"https://www.iesdouyin.com/share/video/{AWEME_ID}/?region=CN",
            f"https://www.douyin.com/note/{AWEME_ID}",
        ):
            with self.subTest(url=url):
                self.assertEqual(fv.resolve_aweme_id(url), AWEME_ID)

    def test_query_parameters(self) -> None:
        for url in (
            f"https://www.douyin.com/?aweme_id={AWEME_ID}",
            f"https://www.douyin.com/?item_ids={AWEME_ID}",
        ):
            with self.subTest(url=url):
                self.assertEqual(fv.resolve_aweme_id(url), AWEME_ID)


class ParseXingzhigeTests(unittest.TestCase):
    def test_maps_fields_and_unescapes_entities(self) -> None:
        meta = fv.parse_xingzhige(api_payload())
        self.assertEqual(meta["title"], "标题 #话题")
        self.assertEqual(meta["author"], "作者")
        self.assertEqual(meta["video_url"], "https://cdn.example/v.mp4?a=1&b=2")
        self.assertEqual(meta["backup_url"], "https://cdn.example/backup.mp4")
        self.assertEqual(meta["quality"], "1080p")
        self.assertEqual((meta["width"], meta["height"]), (1920, 1080))
        self.assertEqual(meta["size_reported"], 1000)
        self.assertEqual(meta["like"], 1)

    def test_falls_back_to_backup_url(self) -> None:
        payload = api_payload()
        del payload["data"]["item"]["url"]
        meta = fv.parse_xingzhige(payload)
        self.assertEqual(meta["video_url"], "https://cdn.example/backup.mp4")

    def test_error_code_raises(self) -> None:
        with self.assertRaises(ValueError):
            fv.parse_xingzhige({"code": 1, "msg": "bad"})

    def test_missing_video_url_raises(self) -> None:
        payload = api_payload()
        del payload["data"]["item"]["url"]
        del payload["data"]["item"]["ury"]
        with self.assertRaises(ValueError):
            fv.parse_xingzhige(payload)


class FetchMetaTests(unittest.TestCase):
    def test_identical_canonical_url_is_tried_once(self) -> None:
        calls: list[str] = []

        def fake_get_json(url: str, timeout: int = 25) -> dict:
            calls.append(url)
            return api_payload()

        with mock.patch.object(fv, "_get_json", fake_get_json), contextlib.redirect_stderr(io.StringIO()):
            meta = fv.fetch_meta(AWEME_ID, VIDEO_URL)

        self.assertEqual(len(calls), 1)
        self.assertEqual(meta["author"], "作者")
        self.assertEqual(meta["source_api"], "xingzhige")

    def test_falls_back_to_canonical_url(self) -> None:
        calls: list[str] = []

        def fake_get_json(url: str, timeout: int = 25) -> dict:
            calls.append(url)
            if len(calls) == 1:
                raise ValueError("first candidate failed")
            return api_payload()

        with mock.patch.object(fv, "_get_json", fake_get_json), contextlib.redirect_stderr(io.StringIO()):
            fv.fetch_meta("123456789", "https://v.douyin.com/abc/")

        self.assertEqual(len(calls), 2)
        self.assertIn(
            "https://www.douyin.com/video/123456789",
            urllib.parse.unquote(calls[1]),
        )

    def test_all_parsers_failing_raises_parse_failed(self) -> None:
        with mock.patch.object(fv, "_get_json", side_effect=ValueError("down")), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(fv.SkillError) as ctx:
                fv.fetch_meta(AWEME_ID, VIDEO_URL)
        self.assertEqual(ctx.exception.code, "parse_failed")


class SafeNameTests(unittest.TestCase):
    def test_replaces_invalid_characters(self) -> None:
        self.assertEqual(
            fv.safe_name('a/b:c*?"<>|d'),
            "a b c d",
        )

    def test_collapses_whitespace_and_limits_length(self) -> None:
        self.assertEqual(fv.safe_name("  a\n\t  b  "), "a b")
        self.assertLessEqual(len(fv.safe_name("x" * 100)), 40)


class RunTests(unittest.TestCase):
    def test_check_returns_metadata_without_download(self) -> None:
        with mock.patch.object(fv, "fetch_meta", return_value=meta_fields()), \
                mock.patch.object(fv, "download") as download:
            result = fv.run(make_args(check=True))

        download.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["path"])
        self.assertEqual(result["aweme_id"], AWEME_ID)
        self.assertEqual(result["quality"], "1080p")

    def test_download_builds_name_and_reports_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fv, "fetch_meta", return_value=meta_fields()), \
                    mock.patch.object(fv, "download", return_value=1234):
                result = fv.run(make_args(out=tmp))

            expected = (
                Path(tmp)
                / f"douyin_{AWEME_ID}"
                / f"作者_标题_{AWEME_ID}.mp4"
            )
            self.assertEqual(Path(result["path"]), expected)
            self.assertEqual(result["size_bytes"], 1234)
            self.assertEqual(result["size_mb"], 0.0)

    def test_flat_skips_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fv, "fetch_meta", return_value=meta_fields()), \
                    mock.patch.object(fv, "download", return_value=1000):
                result = fv.run(make_args(out=tmp, flat=True))

            self.assertEqual(Path(result["path"]).parent, Path(tmp))

    def test_size_mismatch_is_warned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fv, "fetch_meta", return_value=meta_fields(size_reported=5000)), \
                    mock.patch.object(fv, "download", return_value=1000):
                result = fv.run(make_args(out=tmp))

        self.assertTrue(any("不一致" in item for item in result["warnings"]))

    def test_frames_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame_path = str(Path(tmp) / "frame.jpg")
            with mock.patch.object(fv, "fetch_meta", return_value=meta_fields()), \
                    mock.patch.object(fv, "download", return_value=1000), \
                    mock.patch.object(fv, "grab_frame", return_value=frame_path):
                result = fv.run(make_args(out=tmp, frame=[10.0, 20.0]))

        self.assertEqual([f["sec"] for f in result["frames"]], [10.0, 20.0])
        self.assertTrue(all(f["path"] == frame_path for f in result["frames"]))

    def test_frame_failure_adds_warning_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fv, "fetch_meta", return_value=meta_fields()), \
                    mock.patch.object(fv, "download", return_value=1000), \
                    mock.patch.object(fv, "grab_frame", return_value=None) as grab:
                result = fv.run(make_args(out=tmp, frame=[10.0, 20.0]))

        self.assertEqual(grab.call_count, 1)
        self.assertNotIn("frames", result)
        self.assertTrue(any("抽帧失败" in item for item in result["warnings"]))

    def test_numeric_input_is_douyin(self) -> None:
        with mock.patch.object(fv, "fetch_meta", return_value=meta_fields()):
            result = fv.run(make_args(input=AWEME_ID, check=True))
        self.assertEqual(result["platform"], "douyin")


class CommandLineTests(unittest.TestCase):
    """Exercise the JSON contract and documented exit codes without network."""

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    def payload(self, proc: subprocess.CompletedProcess) -> dict:
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_no_url(self) -> None:
        proc = self.run_cli("没有链接的一段话")
        self.assertEqual(proc.returncode, 5)
        self.assertEqual(self.payload(proc)["error"]["code"], "no_url")

    def test_unsupported_platform(self) -> None:
        proc = self.run_cli("https://www.tiktok.com/@x/video/123456789")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.payload(proc)["error"]["code"], "unsupported_platform")

    def test_unknown_platform(self) -> None:
        proc = self.run_cli("https://example.com/a")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.payload(proc)["error"]["code"], "unknown_platform")


if __name__ == "__main__":
    unittest.main()
