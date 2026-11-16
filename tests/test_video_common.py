"""Offline unit tests for video-download's shared helpers."""

import socket
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "video-download"
    / "scripts"
)
sys.path.insert(0, str(SKILL_SCRIPTS))

import _common  # noqa: E402


def addrinfo(*addresses):
    """Build getaddrinfo-shaped tuples for DNS mocks."""
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (address, 0, 0, 0) if ":" in address else (address, 0),
        )
        for address in addresses
    ]


class ExtractUrlTests(unittest.TestCase):
    def test_share_text(self):
        text = "7.43 复制打开抖音，看看【作者的作品】 https://v.douyin.com/abc123/ 12/27"
        self.assertEqual(_common.extract_url(text), "https://v.douyin.com/abc123/")

    def test_strips_trailing_punctuation(self):
        self.assertEqual(
            _common.extract_url("看这里 https://b23.tv/xyz，谢谢"),
            "https://b23.tv/xyz",
        )

    def test_missing_url(self):
        with self.assertRaises(_common.SkillError) as ctx:
            _common.extract_url("没有链接的文字")
        self.assertEqual(ctx.exception.code, "no_url")


class NamingTests(unittest.TestCase):
    def test_build_stem_removes_hashtags(self):
        stem = _common.build_stem("作者", "标题 #话题 更多 #tag", "123")
        self.assertEqual(stem, "作者_标题 更多_123")

    def test_build_stem_falls_back(self):
        self.assertEqual(_common.build_stem("", "", ""), "video")

    def test_safe_name_strips_illegal_chars(self):
        self.assertEqual(_common.safe_name('a/b:c*?"<>|d'), "a b c d")


class AsIntTests(unittest.TestCase):
    def test_accepts_numeric_strings_and_floats(self):
        self.assertEqual(_common.as_int("42"), 42)
        self.assertEqual(_common.as_int(3.9), 3)

    def test_rejects_invalid_values(self):
        self.assertIsNone(_common.as_int(None))
        self.assertIsNone(_common.as_int("abc"))


class DownloadUrlTests(unittest.TestCase):
    def test_accepts_public_http(self):
        _common.validate_download_url("https://1.1.1.1/video.mp4")

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(_common.SkillError) as ctx:
            _common.validate_download_url("file:///etc/passwd")
        self.assertEqual(ctx.exception.code, "download_failed")

    def test_rejects_loopback(self):
        for url in (
            "http://127.0.0.1/video.mp4",
            "http://localhost/video.mp4",
            "http://[::1]/video.mp4",
        ):
            with self.subTest(url=url):
                with self.assertRaises(_common.SkillError):
                    _common.validate_download_url(url)

    def test_rejects_private_and_link_local(self):
        for url in (
            "http://10.0.0.1/video.mp4",
            "http://192.168.1.1/video.mp4",
            "http://169.254.169.254/latest/meta-data",
        ):
            with self.subTest(url=url):
                with self.assertRaises(_common.SkillError):
                    _common.validate_download_url(url)


class DownloadDnsGuardTests(unittest.TestCase):
    def test_rejects_hostname_resolving_to_loopback(self):
        with mock.patch.object(
            _common.socket, "getaddrinfo", return_value=addrinfo("127.0.0.1")
        ):
            with self.assertRaises(_common.SkillError) as ctx:
                _common.validate_download_url(
                    "http://loopback.example.com/video.mp4"
                )
        self.assertEqual(ctx.exception.code, "download_failed")

    def test_rejects_hostname_with_mixed_public_and_private(self):
        with mock.patch.object(
            _common.socket,
            "getaddrinfo",
            return_value=addrinfo("1.1.1.1", "10.0.0.5"),
        ):
            with self.assertRaises(_common.SkillError):
                _common.validate_download_url(
                    "https://mixed.example.com/video.mp4"
                )

    def test_accepts_hostname_resolving_to_public(self):
        with mock.patch.object(
            _common.socket,
            "getaddrinfo",
            return_value=addrinfo("1.1.1.1", "2606:4700:4700::1111"),
        ):
            _common.validate_download_url("https://cdn.example.com/video.mp4")

    def test_reports_dns_failure(self):
        with mock.patch.object(
            _common.socket,
            "getaddrinfo",
            side_effect=socket.gaierror("dns failed"),
        ):
            with self.assertRaises(_common.SkillError) as ctx:
                _common.validate_download_url("https://gone.example.com/video.mp4")
        self.assertEqual(ctx.exception.code, "download_failed")
        self.assertIn("无法解析", ctx.exception.message)


class RedirectGuardTests(unittest.TestCase):
    def test_private_redirect_is_rejected(self):
        handler = _common._SafeRedirectHandler()
        req = urllib.request.Request("https://1.1.1.1/start.mp4")
        with self.assertRaises(_common.SkillError):
            handler.redirect_request(
                req, None, 302, "Found", {}, "http://127.0.0.1/evil.mp4"
            )

    def test_public_redirect_is_allowed(self):
        handler = _common._SafeRedirectHandler()
        req = urllib.request.Request("https://1.1.1.1/start.mp4")
        redirected = handler.redirect_request(
            req, None, 302, "Found", {}, "https://1.1.1.2/next.mp4"
        )
        self.assertEqual(redirected.full_url, "https://1.1.1.2/next.mp4")


if __name__ == "__main__":
    unittest.main()
