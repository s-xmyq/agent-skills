"""Offline tests for platform detection and input parsing."""

import sys
import unittest
from pathlib import Path

SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "video-download"
    / "scripts"
)
sys.path.insert(0, str(SKILL_SCRIPTS))

import bilibili  # noqa: E402
import douyin  # noqa: E402
import fetch_video  # noqa: E402
from _common import SkillError  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_douyin_numeric_id(self):
        self.assertEqual(
            fetch_video.classify("7707101340123663616"),
            ("douyin", "7707101340123663616"),
        )

    def test_bv_id(self):
        self.assertEqual(
            fetch_video.classify("BV1xx411c7mD"),
            ("bilibili", "BV1xx411c7mD"),
        )

    def test_av_id(self):
        self.assertEqual(
            fetch_video.classify("av170001"),
            ("bilibili", "av170001"),
        )

    def test_share_text_extracts_url(self):
        platform, source = fetch_video.classify(
            "复制打开抖音 https://v.douyin.com/abc123/ 看看"
        )
        self.assertEqual(platform, "douyin")
        self.assertEqual(source, "https://v.douyin.com/abc123/")

    def test_tiktok_is_detected_but_unsupported(self):
        platform, _ = fetch_video.classify(
            "https://www.tiktok.com/@someone/video/123"
        )
        self.assertEqual(platform, "tiktok")
        self.assertIsNone(fetch_video.PLATFORMS[platform]["module"])

    def test_unknown_domain(self):
        with self.assertRaises(SkillError) as ctx:
            fetch_video.classify("https://example.com/video/1")
        self.assertEqual(ctx.exception.code, "unknown_platform")


class DouyinParsingTests(unittest.TestCase):
    def test_looks_like_id(self):
        self.assertTrue(douyin.looks_like_id("7707101340123663616"))
        self.assertFalse(douyin.looks_like_id("12345"))
        self.assertFalse(douyin.looks_like_id("BV1xx411c7mD"))

    def test_resolve_numeric_id(self):
        self.assertEqual(
            douyin.resolve_id("7707101340123663616"), "7707101340123663616"
        )

    def test_resolve_canonical_url_without_network(self):
        self.assertEqual(
            douyin.resolve_id(
                "https://www.douyin.com/video/7707101340123663616?foo=1"
            ),
            "7707101340123663616",
        )


class BilibiliParsingTests(unittest.TestCase):
    def test_looks_like_id(self):
        self.assertTrue(bilibili.looks_like_id("BV1xx411c7mD"))
        self.assertTrue(bilibili.looks_like_id("av170001"))
        self.assertFalse(bilibili.looks_like_id("7707101340123663616"))

    def test_resolve_bv(self):
        self.assertEqual(
            bilibili.resolve_id("BV1xx411c7mD"), "BV1xx411c7mD"
        )

    def test_resolve_av(self):
        self.assertEqual(bilibili.resolve_id("av170001"), "av170001")

    def test_resolve_video_url(self):
        self.assertEqual(
            bilibili.resolve_id(
                "https://www.bilibili.com/video/BV1xx411c7mD/?p=2"
            ),
            "BV1xx411c7mD",
        )

    def test_resolve_query_style_url(self):
        self.assertEqual(
            bilibili.resolve_id(
                "https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD"
            ),
            "BV1xx411c7mD",
        )

    def test_page_from_url(self):
        self.assertEqual(
            bilibili._page_from_url("https://www.bilibili.com/video/BV1?p=3"),
            3,
        )
        self.assertIsNone(bilibili._page_from_url("https://www.bilibili.com/video/BV1"))

    def test_live_is_unsupported(self):
        with self.assertRaises(SkillError) as ctx:
            bilibili.resolve_id("https://live.bilibili.com/123")
        self.assertEqual(ctx.exception.code, "unsupported_platform")

    def test_non_video_page_is_unsupported(self):
        with self.assertRaises(SkillError) as ctx:
            bilibili.resolve_id("https://www.bilibili.com/read/cv123")
        self.assertEqual(ctx.exception.code, "unsupported_platform")


class DependencyContractTests(unittest.TestCase):
    def test_check_deps_structure(self):
        report = fetch_video.check_deps()
        self.assertTrue(report["ok"])
        deps = {item["name"]: item for item in report["dependencies"]}
        self.assertTrue(deps["Python >= 3.8"]["required"])
        self.assertFalse(deps["opencv-python-headless"]["required"])
        services = {item["name"] for item in report["services"]}
        self.assertIn("api.xingzhige.com", services)
        self.assertIn("api.bilibili.com", services)

    def test_missing_dependency_exit_code(self):
        self.assertEqual(fetch_video.EXIT_CODES["missing_dependency"], 6)


if __name__ == "__main__":
    unittest.main()
