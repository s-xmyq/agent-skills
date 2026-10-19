"""Offline unit tests for web.py.

No network access is required. Run from the repository root:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = REPO_ROOT / "skills" / "web-research" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import web  # noqa: E402


class UrlSafetyTests(unittest.TestCase):
    def test_public_urls_allowed(self) -> None:
        for url in (
            "https://example.com",
            "http://example.com/a/b?c=1",
            "https://docs.python.org/3/",
        ):
            with self.subTest(url=url):
                self.assertTrue(web.is_public_url(url))

    def test_private_and_loopback_rejected(self) -> None:
        for url in (
            "http://127.0.0.1/",
            "http://localhost/",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
        ):
            with self.subTest(url=url):
                self.assertFalse(web.is_public_url(url))

    def test_non_http_schemes_rejected(self) -> None:
        for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://example.com"):
            with self.subTest(url=url):
                self.assertFalse(web.is_public_url(url))


class BingUrlDecodeTests(unittest.TestCase):
    def test_decodes_ck_a_payload(self) -> None:
        target = "https://example.com/page?x=1"
        encoded = base64.b64encode(target.encode()).decode()
        # Bing uses url-safe base64 with padding stripped.
        payload = encoded.replace("+", "-").replace("/", "_").rstrip("=")
        href = f"https://www.bing.com/ck/a?!&&u=a1{payload}"
        self.assertEqual(web._decode_bing_url(href), target)

    def test_passthrough_for_plain_url(self) -> None:
        self.assertEqual(
            web._decode_bing_url("https://example.com"), "https://example.com"
        )

    def test_protocol_relative_becomes_https(self) -> None:
        self.assertEqual(
            web._decode_bing_url("//example.com/x"), "https://example.com/x"
        )


class StripTagsTests(unittest.TestCase):
    def test_removes_tags_and_unescapes(self) -> None:
        self.assertEqual(
            web._strip_tags("<b>a</b> &amp; <i>b</i>"), "a & b"
        )

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(web._strip_tags("a\n\n   b\t c"), "a b c")


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CLAUDE_WEB_CACHE")
        os.environ["CLAUDE_WEB_CACHE"] = self._tmp.name

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("CLAUDE_WEB_CACHE", None)
        else:
            os.environ["CLAUDE_WEB_CACHE"] = self._old
        self._tmp.cleanup()

    def test_roundtrip(self) -> None:
        key = web.cache_key("search", "bing", "query", 8)
        self.assertIsNone(web.cache_get(key, ttl=60))
        web.cache_put(key, {"ok": True, "value": "中文"})
        self.assertEqual(web.cache_get(key, ttl=60), {"ok": True, "value": "中文"})

    def test_expired_entry_ignored(self) -> None:
        key = web.cache_key("search", "expired")
        web.cache_put(key, {"ok": True})
        self.assertIsNone(web.cache_get(key, ttl=-1))

    def test_corrupt_entry_ignored(self) -> None:
        key = web.cache_key("search", "corrupt")
        path = Path(web.cache_dir()) / f"{key}.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(web.cache_get(key, ttl=60))


class BackendChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("CLAUDE_WEB_BACKEND")

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("CLAUDE_WEB_BACKEND", None)
        else:
            os.environ["CLAUDE_WEB_BACKEND"] = self._old

    def test_explicit_backend_wins(self) -> None:
        os.environ["CLAUDE_WEB_BACKEND"] = "auto"
        chain = web.backend_chain("bing", None)
        self.assertEqual(chain, [("bing", None)])

    def test_env_backend_wins_over_auto(self) -> None:
        os.environ["CLAUDE_WEB_BACKEND"] = "bing"
        self.assertEqual(web.backend_chain("auto", None), [("bing", None)])

    def test_auto_falls_back_to_ddg_then_bing(self) -> None:
        os.environ["CLAUDE_WEB_BACKEND"] = "auto"
        self.assertEqual(
            web.backend_chain("auto", None), [("ddg", None), ("bing", None)]
        )


class ProxyModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("CLAUDE_WEB_SYSTEM_PROXY")

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("CLAUDE_WEB_SYSTEM_PROXY", None)
        else:
            os.environ["CLAUDE_WEB_SYSTEM_PROXY"] = self._old

    def test_direct_by_default(self) -> None:
        os.environ.pop("CLAUDE_WEB_SYSTEM_PROXY", None)
        self.assertFalse(web.use_system_proxy())

    def test_opt_in_values(self) -> None:
        for value in ("1", "true", "YES", "on"):
            with self.subTest(value=value):
                os.environ["CLAUDE_WEB_SYSTEM_PROXY"] = value
                self.assertTrue(web.use_system_proxy())

    def test_other_values_mean_direct(self) -> None:
        for value in ("0", "false", "", "nope"):
            with self.subTest(value=value):
                os.environ["CLAUDE_WEB_SYSTEM_PROXY"] = value
                self.assertFalse(web.use_system_proxy())


class ManifestTests(unittest.TestCase):
    def test_plugin_and_marketplace_are_consistent(self) -> None:
        plugin = json.loads(
            (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        market = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plugin["name"], market["name"])
        entry = next(
            p for p in market["plugins"] if p["name"] == plugin["name"]
        )
        self.assertEqual(entry["version"], plugin["version"])

    def test_every_skill_has_frontmatter(self) -> None:
        skills_dir = REPO_ROOT / "skills"
        for skill_md in skills_dir.glob("*/SKILL.md"):
            with self.subTest(skill=skill_md.parent.name):
                text = skill_md.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"), "missing frontmatter")
                head = text.split("---", 2)[1]
                self.assertIn("name:", head)
                self.assertIn("description:", head)

    def test_skill_docs_use_no_host_specific_paths(self) -> None:
        """Skill files must be portable: they are copied into different hosts'
        skill directories (Claude Code, Codex, Cursor, ...), so they may only
        reference files relative to the skill root."""
        forbidden = (
            "CLAUDE_PLUGIN_ROOT",
            "$HOME/.claude",
            ".claude/skills",
            "CODEX_HOME",
            ".codex/skills",
            ".cursor/skills",
        )
        for path in (REPO_ROOT / "skills").rglob("*.md"):
            with self.subTest(file=str(path.relative_to(REPO_ROOT))):
                text = path.read_text(encoding="utf-8")
                for marker in forbidden:
                    self.assertNotIn(
                        marker,
                        text,
                        f"use paths relative to the skill root, not {marker}",
                    )


if __name__ == "__main__":
    unittest.main()
