"""Offline unit tests for web-research's URL guard and fallback extractor."""

import argparse
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "web-research"
    / "scripts"
)
sys.path.insert(0, str(SKILL_SCRIPTS))

import web  # noqa: E402


def search_args(**overrides):
    values = {
        "backend": "auto",
        "searxng": None,
        "query": "query",
        "num": 1,
        "lang": "zh-CN",
        "categories": "general",
        "time_range": None,
        "no_cache": True,
        "timeout": 5,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def run_search(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = web.cmd_search(args)
    return code, json.loads(buf.getvalue())


class UrlSafetyTests(unittest.TestCase):
    def test_accepts_public_ip(self):
        self.assertTrue(web.is_public_url("https://1.1.1.1/page"))

    def test_rejects_loopback(self):
        for url in (
            "http://127.0.0.1/x",
            "http://localhost/x",
            "http://[::1]/x",
        ):
            with self.subTest(url=url):
                self.assertFalse(web.is_public_url(url))

    def test_rejects_private_and_link_local(self):
        for url in (
            "http://10.0.0.1/x",
            "http://192.168.1.1/x",
            "http://169.254.169.254/latest/meta-data",
        ):
            with self.subTest(url=url):
                self.assertFalse(web.is_public_url(url))

    def test_rejects_non_http_schemes(self):
        for url in ("file:///etc/passwd", "ftp://1.1.1.1/x"):
            with self.subTest(url=url):
                self.assertFalse(web.is_public_url(url))


class CacheKeyTests(unittest.TestCase):
    def test_stable_and_distinct(self):
        first = web.cache_key("search", "python")
        self.assertEqual(first, web.cache_key("search", "python"))
        self.assertNotEqual(first, web.cache_key("search", "rust"))
        self.assertEqual(len(first), 32)


class StripTagsTests(unittest.TestCase):
    def test_unescapes_and_collapses_whitespace(self):
        self.assertEqual(
            web._strip_tags("<p>Hello &amp; <b>world</b></p>"),
            "Hello & world",
        )


class SimpleExtractorTests(unittest.TestCase):
    def test_extracts_title_and_main_content(self):
        body = "这是一段正文内容。" * 40
        html = (
            "<html><head><title> 测试标题 </title></head><body>"
            "<article><h1>大标题</h1><p>%s</p></article>"
            "<footer>页脚不应出现</footer></body></html>" % body
        )
        extractor = web.SimpleExtractor()
        extractor.feed(html)
        extractor.close()
        content, title = extractor.result()

        self.assertEqual(title, "测试标题")
        self.assertIn("# 大标题", content)
        self.assertIn("这是一段正文内容", content)
        self.assertNotIn("页脚不应出现", content)


class SearchBackendTests(unittest.TestCase):
    def test_explicit_searxng_without_url_fails_loudly(self):
        with mock.patch.object(web, "ddg_search") as ddg, mock.patch.object(
            web, "bing_search"
        ) as bing:
            code, payload = run_search(search_args(backend="searxng"))

        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["backend"], "searxng")
        self.assertIn("SEARXNG_URL", payload["error"])
        ddg.assert_not_called()
        bing.assert_not_called()

    def test_env_selected_searxng_without_url_also_fails(self):
        with mock.patch.dict(
            "os.environ", {"CLAUDE_WEB_BACKEND": "searxng"}
        ), mock.patch.object(web, "ddg_search") as ddg:
            code, payload = run_search(search_args(backend="auto"))

        self.assertEqual(code, 1)
        self.assertIn("SEARXNG_URL", payload["error"])
        ddg.assert_not_called()

    def test_explicit_searxng_uses_configured_url(self):
        fake = [
            {
                "title": "t",
                "url": "https://example.com",
                "snippet": "",
                "engine": "searxng",
                "score": None,
                "published": None,
            }
        ]
        with mock.patch.object(
            web, "searxng_search", return_value=fake
        ) as searxng, mock.patch.object(web, "ddg_search") as ddg:
            code, payload = run_search(
                search_args(backend="searxng", searxng="http://127.0.0.1:8080")
            )

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["backend"], "searxng")
        searxng.assert_called_once()
        self.assertEqual(searxng.call_args.args[0], "http://127.0.0.1:8080")
        ddg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
