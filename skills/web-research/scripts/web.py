#!/usr/bin/env python3
"""
web.py -- live web search + page content extraction.

Zero required dependencies (Python stdlib only).

Subcommands:
  search QUERY     Search the live web -> JSON list of results
  fetch URL...     Download pages and extract main content -> JSON
  doctor           Diagnose backends / optional dependencies

Optional upgrade:
  pip install trafilatura     much better article extraction
  self-hosted SearXNG          better search coverage than the DDG fallback

Environment variables:
  SEARXNG_URL        e.g. http://127.0.0.1:8080  (enables the SearXNG backend)
  CLAUDE_WEB_BACKEND auto | searxng | ddg | bing (default: auto: ddg, then bing)
  CLAUDE_WEB_CACHE   cache directory override
  CLAUDE_WEB_TTL     cache TTL in seconds (default: search 600, fetch 3600)
  CLAUDE_WEB_SYSTEM_PROXY
                     set to 1 to route requests through the OS/env proxy.
                     Default is direct, which avoids slow registry proxies.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import html as html_module
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

VERSION = "1.0.0"

# Windows consoles/files default to the ANSI codepage (cp936 etc.).
# Force UTF-8 so JSON output is valid across platforms.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

MAX_BYTES = 6 * 1024 * 1024


class BackendError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# optional dependency
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - depends on the environment
    import trafilatura  # type: ignore

    TRAFILATURA_VERSION = getattr(trafilatura, "__version__", "installed")
except Exception:  # noqa: BLE001
    trafilatura = None  # type: ignore
    TRAFILATURA_VERSION = None


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #


def cache_dir() -> Path:
    override = os.environ.get("CLAUDE_WEB_CACHE")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "claude-web-skill"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "claude-web-skill"


def cache_key(*parts: Any) -> str:
    raw = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def cache_get(key: str, ttl: float) -> Any | None:
    try:
        path = cache_dir() / f"{key}.json"
        if time.time() - path.stat().st_mtime > ttl:
            return None
        return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def cache_put(key: str, value: Any) -> None:
    try:
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.json").write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# SSRF guard + HTTP
# --------------------------------------------------------------------------- #


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


_TEREDO = ipaddress.ip_network("2001::/32")
_6TO4 = ipaddress.ip_network("2002::/16")


def _is_transitional_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Teredo/6to4 are IPv4 tunnels. Python's ipaddress flags 2001::/23 as
    private, which makes hosts that also publish a normal A record (very common
    on polluted resolvers) look non-public. They are never intranet targets in
    a meaningful sense, so ignore them; an address list with only tunnel
    addresses still gets blocked below."""
    if ip.version != 6:
        return False
    return ip in _TEREDO or ip in _6TO4


def is_public_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.strip("[]").lower()
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        return _is_public_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    except Exception:  # noqa: BLE001
        return False
    if not infos:
        return False
    addrs = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not _is_transitional_ip(ip):
            addrs.append(ip)
    if not addrs:
        return False
    return all(_is_public_ip(ip) for ip in addrs)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private: bool = False) -> None:
        super().__init__()
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not self.allow_private and not is_public_url(newurl):
            raise urllib.error.URLError(
                f"blocked redirect to private/non-public address: {newurl}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(
    url: str,
    timeout: float = 20,
    headers: dict[str, str] | None = None,
    allow_private: bool = False,
) -> tuple[int, str, dict[str, str], bytes]:
    """GET a URL. Returns (status, final_url, headers, body_bytes)."""
    if not allow_private and not is_public_url(url):
        raise ValueError(f"refusing to fetch non-public URL: {url}")

    opener = urllib.request.build_opener(
        _proxy_handler(),
        _SafeRedirectHandler(allow_private=allow_private),
    )
    req = urllib.request.Request(url, headers={**DEFAULT_HEADERS, **(headers or {})})
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES + 1)
            truncated = len(raw) > MAX_BYTES
            if truncated:
                raw = raw[:MAX_BYTES]
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:  # noqa: BLE001
                    pass
            elif encoding == "deflate":
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    try:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                    except zlib.error:
                        pass
            return resp.status, resp.geturl(), dict(resp.headers), raw
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(4096)
        except Exception:  # noqa: BLE001
            pass
        return exc.code, exc.geturl() if hasattr(exc, "geturl") else url, dict(exc.headers or {}), body


def decode_body(data: bytes, content_type: str) -> tuple[str, str]:
    charset = None
    m = re.search(r"charset=([\w.:-]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    if not charset:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w.:-]+)', data[:8192], re.I)
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    for enc in (charset, "utf-8", "gb18030", "big5", "latin-1"):
        if not enc:
            continue
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace"), "utf-8"


# --------------------------------------------------------------------------- #
# content extraction: trafilatura first, stdlib fallback
# --------------------------------------------------------------------------- #

SKIP_TAGS = {
    "script", "style", "noscript", "svg", "iframe", "canvas", "template",
    "nav", "header", "footer", "aside", "form", "button", "select",
    "textarea", "menu", "dialog", "figure", "video", "audio", "map",
}
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
# Only container-ish tags get class/id based boilerplate detection; applying it
# to <html>/<body> can match tokens inside long class lists and blank the page.
BOILER_CHECK_TAGS = {
    "div", "section", "aside", "ul", "ol", "li", "table", "tbody", "tr",
    "td", "th", "span", "form", "p",
}
BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre",
    "td", "th", "dd", "dt", "figcaption", "caption",
}
BOILER_TOKENS = {
    "nav", "navbar", "menu", "footer", "sidebar", "aside", "comment",
    "comments", "share", "sharing", "social", "related", "recommend",
    "recommended", "advert", "ads", "ad", "banner", "cookie", "breadcrumb",
    "pagination", "pager", "popup", "modal", "subscribe", "newsletter",
    "toolbar", "login", "signup", "searchform", "widget",
}


def _tokens(value: str) -> set[str]:
    return {t for t in re.split(r"[\s_\-]+", (value or "").lower()) if t}


class SimpleExtractor(HTMLParser):
    """Small readability-style extractor. Used when trafilatura is absent."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.blocks: list[dict[str, Any]] = []
        self._skip_depth = 0
        self._skip_stack: list[str] = []
        self._main_depth = 0
        self._buf: list[str] = []
        self._buf_tag = ""
        self._buf_main = False
        self._buf_link_chars = 0
        self._buf_total_chars = 0
        self._in_link = 0
        self._in_title = False
        self._in_pre = 0

    # -- helpers ---------------------------------------------------------- #
    def _flush(self) -> None:
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self._buf)).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        self._buf, self._buf_tag = [], ""
        if len(text) < 2:
            return
        link_ratio = (
            self._buf_link_chars / self._buf_total_chars
            if self._buf_total_chars
            else 0.0
        )
        if link_ratio > 0.7 and len(text) < 300:
            return
        self.blocks.append(
            {
                "text": text,
                "tag": self._buf_tag,
                "main": self._buf_main,
                "link_ratio": link_ratio,
            }
        )
        self._buf_link_chars = 0
        self._buf_total_chars = 0

    def _start_block(self, tag: str) -> None:
        self._flush()
        self._buf_tag = tag
        self._buf_main = self._main_depth > 0
        self._buf_link_chars = 0
        self._buf_total_chars = 0

    # -- parser hooks ----------------------------------------------------- #
    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description", "twitter:description"):
                if not self.description:
                    self.description = a.get("content", "")
            return

        if tag in VOID_TAGS:
            return

        classes = _tokens(a.get("class", "")) | _tokens(a.get("id", ""))
        boiler = tag in BOILER_CHECK_TAGS and bool(classes & BOILER_TOKENS)

        if tag in SKIP_TAGS or boiler:
            self._skip_depth += 1
            self._skip_stack.append(tag)
            return
        if self._skip_depth:
            return

        if tag in ("article", "main"):
            self._main_depth += 1
        if tag == "a":
            self._in_link += 1
        if tag == "pre":
            self._in_pre += 1
        if tag in BLOCK_TAGS:
            self._start_block(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        # void elements: no content, nothing to flush
        return

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if self._skip_stack and tag in self._skip_stack:
            # Pop through the matching tag: malformed HTML often leaves
            # intermediate tags unclosed.
            while self._skip_stack:
                popped = self._skip_stack.pop()
                self._skip_depth = max(0, self._skip_depth - 1)
                if popped == tag:
                    break
            return
        if self._skip_depth:
            return
        if tag in ("article", "main"):
            self._main_depth = max(0, self._main_depth - 1)
        if tag == "a":
            self._in_link = max(0, self._in_link - 1)
        if tag == "pre":
            self._in_pre = max(0, self._in_pre - 1)
        if tag in BLOCK_TAGS:
            if self._buf_tag == tag:
                self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth or not data:
            return
        if self._in_link:
            self._buf_link_chars += len(data)
        self._buf_total_chars += len(data)
        self._buf.append(data)

    # -- output ----------------------------------------------------------- #
    def result(self) -> tuple[str, str]:
        self._flush()
        blocks = self.blocks
        main_blocks = [b for b in blocks if b["main"]]
        main_len = sum(len(b["text"]) for b in main_blocks)
        if main_blocks and main_len >= max(300, sum(len(b["text"]) for b in blocks) * 0.3):
            blocks = main_blocks

        out: list[str] = []
        for b in blocks:
            text = b["text"]
            tag = b["tag"]
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                out.append(f"{'#' * int(tag[1])} {text}")
            elif tag == "li":
                out.append(f"- {text}")
            elif tag == "pre":
                out.append(f"```\n{text}\n```")
            else:
                out.append(text)
        return "\n\n".join(out).strip(), re.sub(r"\s+", " ", self.title).strip()


def extract_content(html: str, url: str) -> tuple[str, str, str | None]:
    """Returns (markdown_text, backend, title)."""
    if trafilatura is not None:
        try:
            md = trafilatura.extract(
                html,
                url=url,
                output_format="markdown",
                include_links=True,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
            )
            if md and len(md.strip()) >= 80:
                title = None
                try:
                    meta = trafilatura.extract_metadata(html)
                    title = getattr(meta, "title", None) if meta else None
                except Exception:  # noqa: BLE001
                    pass
                return md.strip(), "trafilatura", title
        except Exception:  # noqa: BLE001
            pass

    parser = SimpleExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    text, title = parser.result()
    if not text and parser.description:
        text = parser.description
    return text, "fallback", title


# --------------------------------------------------------------------------- #
# search: SearXNG
# --------------------------------------------------------------------------- #


def searxng_search(
    base: str,
    query: str,
    num: int,
    lang: str,
    categories: str,
    time_range: str | None,
    timeout: float,
) -> list[dict[str, Any]]:
    params = {
        "q": query,
        "format": "json",
        "categories": categories,
        "language": lang,
        "safesearch": "0",
    }
    if time_range:
        params["time_range"] = time_range
    url = base.rstrip("/") + "/search?" + urllib.parse.urlencode(params)
    # `base` comes from SEARXNG_URL / --searxng (the user's own config), not from
    # fetched page content, so the SSRF guard does not apply: a self-hosted
    # instance normally lives on 127.0.0.1 or a private LAN address.
    status, _, headers, body = http_get(url, timeout=timeout, allow_private=True)
    if status != 200:
        raise BackendError(f"SearXNG returned HTTP {status}")
    text = body.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise BackendError(
            "SearXNG did not return JSON. Enable it in settings.yml:\n"
            "  search:\n    formats:\n      - html\n      - json\n"
            "Then restart the container."
        ) from None
    results = []
    for item in data.get("results", [])[:num]:
        results.append(
            {
                "title": (item.get("title") or "").strip(),
                "url": item.get("url") or "",
                "snippet": (item.get("content") or "").strip(),
                "engine": item.get("engine") or "",
                "score": item.get("score"),
                "published": item.get("publishedDate"),
            }
        )
    dead = data.get("unresponsive_engines") or []
    if not results and dead:
        raise BackendError(
            "SearXNG returned no results; engines failed: "
            + ", ".join(f"{e}: {r}" for e, r in dead[:5])
        )
    return results


# --------------------------------------------------------------------------- #
# search: DuckDuckGo HTML fallback (no key, no instance)
# --------------------------------------------------------------------------- #


class DDGParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._cur: dict[str, str] | None = None
        self._depth = 0
        self._in_result = False
        self._capture: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k: (v or "") for k, v in attrs}
        cls = a.get("class", "")
        tokens = set(cls.split())

        if tag == "div" and "result" in tokens and "--ad" not in cls and "result-sponsored" not in tokens:
            if self._in_result:
                self._depth += 1
            else:
                self._cur = {"title": "", "url": "", "snippet": ""}
                self._in_result = True
                self._depth = 1
            return

        if not self._in_result:
            return
        if tag == "div":
            self._depth += 1
        if "result__a" in cls:
            self._capture, self._buf = "title", []
            self._cur["url"] = a.get("href", "")  # type: ignore[index]
        elif "result__snippet" in cls:
            self._capture, self._buf = "snippet", []

    def handle_startendtag(self, tag: str, attrs) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        if not self._in_result:
            return
        if self._capture and tag in ("a", "div", "span"):
            self._cur[self._capture] = " ".join(self._buf).strip()  # type: ignore[index]
            self._capture = None
        if tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                self._commit()

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buf.append(data)

    def _commit(self) -> None:
        if self._cur and self._cur.get("title") and self._cur.get("url"):
            self.results.append(self._cur)
        self._cur = None
        self._in_result = False
        self._depth = 0
        self._capture = None


def _decode_ddg_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("uddg"):
            return qs["uddg"][0]
    return href


def ddg_search(query: str, num: int, lang: str, time_range: str | None, timeout: float) -> list[dict[str, Any]]:
    params = {"q": query, "kl": "cn-zh" if lang.lower().startswith("zh") else "wt-wt"}
    if time_range:
        params["df"] = {"day": "d", "week": "w", "month": "m", "year": "y"}[time_range]
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode(params)
    status, _, headers, body = http_get(url, timeout=timeout)
    if status != 200:
        raise BackendError(f"DuckDuckGo returned HTTP {status}")
    html = body.decode("utf-8", "replace")
    if "anomaly" in html.lower() and "result__a" not in html:
        raise BackendError(
            "DuckDuckGo served an anti-bot page. "
            "Run a local SearXNG (see references/setup.md) for reliable search."
        )
    parser = DDGParser()
    parser.feed(html)
    parser.close()
    out = []
    seen = set()
    for item in parser.results:
        real = _decode_ddg_url(item["url"])
        if not real.startswith("http") or real in seen:
            continue
        if "duckduckgo.com/y.js" in real or "ad_domain" in real:
            continue
        seen.add(real)
        out.append(
            {
                "title": item["title"],
                "url": real,
                "snippet": item["snippet"],
                "engine": "duckduckgo",
                "score": None,
                "published": None,
            }
        )
        if len(out) >= num:
            break
    return out


# --------------------------------------------------------------------------- #
# search: Bing HTML fallback (no key; used when DDG serves anti-bot pages)
# --------------------------------------------------------------------------- #

_BING_RESULT_SPLIT = re.compile(r'(?=<li[^>]*class="[^"]*\bb_algo\b)', re.I)
_BING_TITLE_RE = re.compile(
    r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I
)
_BING_SNIPPET_RE = re.compile(
    r'<p[^>]*class="[^"]*\bb_lineclamp\b[^"]*"[^>]*>(.*?)</p>', re.S | re.I
)
_BING_CAPTION_RE = re.compile(r'<div[^>]*class="[^"]*\bb_caption\b[^"]*"[^>]*>(.*?)</div>',
                              re.S | re.I)
_BING_ANY_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(html_module.unescape(text).split())


def _decode_bing_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/a"):
        qs = urllib.parse.parse_qs(parsed.query)
        u = (qs.get("u") or [""])[0]
        if u.startswith("a1"):
            s = u[2:].replace("-", "+").replace("_", "/")
            s += "=" * (-len(s) % 4)
            try:
                return base64.b64decode(s).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return href
    return href


def bing_search(query: str, num: int, lang: str, time_range: str | None, timeout: float) -> list[dict[str, Any]]:
    # Bing's freshness filter (filters/qft) is silently ignored for
    # unauthenticated HTML clients, so time_range is accepted but unused here.
    params = {
        "q": query,
        "count": str(min(max(num, 1), 50)),
        "setlang": lang.lower(),
    }
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(params)
    status, _, _, body = http_get(url, timeout=timeout)
    if status != 200:
        raise BackendError(f"Bing returned HTTP {status}")
    html = body.decode("utf-8", "replace")
    if "b_algo" not in html:
        raise BackendError(
            "Bing returned no parseable results (captcha, consent wall, or layout change)."
        )
    out = []
    seen = set()
    for chunk in _BING_RESULT_SPLIT.split(html):
        if not re.match(r'<li[^>]*class="[^"]*\bb_algo\b', chunk, re.I):
            continue
        head = chunk[:chunk.find(">") + 1]
        if re.search(r"\bb_ad\b", head, re.I):
            continue
        title_m = _BING_TITLE_RE.search(chunk)
        if not title_m:
            continue
        real = _decode_bing_url(title_m.group(1))
        title = _strip_tags(title_m.group(2))
        if not real.startswith("http") or not title or real in seen:
            continue
        snippet = ""
        snip_m = _BING_SNIPPET_RE.search(chunk) or _BING_CAPTION_RE.search(chunk)
        if snip_m:
            inner = snip_m.group(1)
            p_m = _BING_ANY_P_RE.search(inner)
            snippet = _strip_tags(p_m.group(1) if p_m else inner)
        seen.add(real)
        out.append(
            {
                "title": title,
                "url": real,
                "snippet": snippet,
                "engine": "bing",
                "score": None,
                "published": None,
            }
        )
        if len(out) >= num:
            break
    return out


def use_system_proxy() -> bool:
    """Whether to honour the OS/env proxy settings.

    Off by default: on managed Windows machines urllib picks up the registry
    proxy, which can add seconds per request. Set CLAUDE_WEB_SYSTEM_PROXY=1
    (or true/yes) to route through the system proxy again.
    """
    return os.environ.get("CLAUDE_WEB_SYSTEM_PROXY", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _proxy_handler() -> urllib.request.BaseHandler:
    if use_system_proxy():
        return urllib.request.ProxyHandler()
    return urllib.request.ProxyHandler({})


def _probe(url: str, timeout: float = 1.5) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.build_opener(_proxy_handler()).open(
            req, timeout=timeout
        ) as resp:
            return 200 <= resp.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except Exception:  # noqa: BLE001
        return False


def pick_backend(explicit: str, searxng_url: str | None) -> tuple[str, str | None]:
    env_backend = os.environ.get("CLAUDE_WEB_BACKEND", "auto").lower()
    if explicit != "auto":
        return explicit, searxng_url
    if searxng_url:
        return "searxng", searxng_url
    if env_backend in ("searxng", "ddg", "bing"):
        return env_backend, searxng_url
    if _probe("http://127.0.0.1:8080/"):
        return "searxng", "http://127.0.0.1:8080"
    return "ddg", None


def backend_chain(explicit: str, searxng_url: str | None) -> list[tuple[str, str | None]]:
    """Backends to try in order. auto = DDG, then Bing (DDG rate-limits and
    serves anti-bot pages on some networks). An explicit choice never falls
    back, so a broken backend fails loudly instead of silently switching."""
    env_backend = os.environ.get("CLAUDE_WEB_BACKEND", "auto").lower()
    if explicit != "auto" or env_backend in ("searxng", "ddg", "bing"):
        return [pick_backend(explicit, searxng_url)]
    primary, url = pick_backend("auto", searxng_url)
    if primary == "searxng":
        return [("searxng", url)]
    return [("ddg", None), ("bing", None)]


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_search(args: argparse.Namespace) -> int:
    chain = backend_chain(args.backend, args.searxng)
    ttl = float(os.environ.get("CLAUDE_WEB_TTL", 600))
    if not args.no_cache:
        for candidate, cand_url in chain:
            key = cache_key("search", candidate, cand_url, args.query, args.num,
                            args.lang, args.categories, args.time_range)
            cached = cache_get(key, ttl)
            if cached is not None:
                cached["cached"] = True
                print(json.dumps(cached, ensure_ascii=False, indent=2))
                return 0

    results: list[dict[str, Any]] | None = None
    used = chain[0][0]
    errors: list[str] = []
    for backend, searxng_url in chain:
        try:
            if backend == "searxng" and searxng_url:
                results = searxng_search(searxng_url, args.query, args.num, args.lang,
                                         args.categories, args.time_range, args.timeout)
            elif backend == "bing":
                results = bing_search(args.query, args.num, args.lang,
                                      args.time_range, args.timeout)
            else:
                results = ddg_search(args.query, args.num, args.lang,
                                     args.time_range, args.timeout)
            used = backend
            break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{backend}: {exc}")
    if results is None:
        print(json.dumps({
            "ok": False,
            "error": "; ".join(errors) if errors else "no search backend available",
            "backend": chain[0][0],
            "backends_tried": [b for b, _ in chain],
            "hint": "Run `web.py doctor` to check backends.",
        }, ensure_ascii=False, indent=2))
        return 1
    payload = {
        "ok": True,
        "backend": used,
        "query": args.query,
        "count": len(results),
        "results": results,
        "cached": False,
    }
    if errors:
        payload["fallback_from"] = errors
    if not args.no_cache:
        key = cache_key("search", used, dict(chain).get(used), args.query, args.num,
                        args.lang, args.categories, args.time_range)
        cache_put(key, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def fetch_one(url: str, args: argparse.Namespace) -> dict[str, Any]:
    key = cache_key("fetch", url, args.max_chars, args.min_chars)
    if not args.no_cache:
        cached = cache_get(key, float(os.environ.get("CLAUDE_WEB_TTL", 3600)))
        if cached is not None:
            cached["cached"] = True
            return cached
    out: dict[str, Any] = {
        "ok": False, "url": url, "final_url": url, "status": None,
        "title": None, "chars": 0, "content": "", "backend": None,
        "truncated": False, "error": None, "fetched_at": None,
    }
    try:
        if not args.allow_private and not is_public_url(url):
            raise ValueError("refusing non-public URL (use --allow-private to override)")
        status, final_url, headers, body = http_get(
            url, timeout=args.timeout, allow_private=args.allow_private
        )
        out["status"] = status
        out["final_url"] = final_url
        ctype = (headers.get("Content-Type") or "").lower()
        if status >= 400:
            raise BackendError(f"HTTP {status}")
        if any(t in ctype for t in ("image/", "video/", "audio/", "application/pdf",
                                    "application/zip", "application/octet-stream")):
            raise BackendError(f"unsupported content-type: {ctype or 'unknown'}")
        text, encoding = decode_body(body, ctype)
        md, backend, title = extract_content(text, final_url)
        md = md.strip()
        out["backend"] = f"{backend}"
        out["title"] = title
        if args.max_chars and len(md) > args.max_chars:
            md = md[: args.max_chars].rstrip() + "\n\n[... truncated]"
            out["truncated"] = True
        out["chars"] = len(md)
        out["content"] = md
        out["encoding"] = encoding
        out["quality"] = "good" if len(md) >= args.min_chars else "short"
        # "ok" means something readable was extracted; "short" is a hint that
        # the page may be JS-rendered or partially blocked.
        out["ok"] = len(md) >= 40
        if not out["ok"]:
            out["error"] = (
                "no readable content extracted; the page is probably "
                "JS-rendered, paywalled, or blocked"
            )
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    out["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not args.no_cache and out["ok"]:
        cache_put(key, out)
    return out


def cmd_fetch(args: argparse.Namespace) -> int:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(4, len(args.urls))) as pool:
        results = list(pool.map(lambda u: fetch_one(u, args), args.urls))
    ok_count = sum(1 for r in results if r["ok"])
    print(json.dumps({
        "ok": ok_count > 0,
        "urls": len(results),
        "ok_count": ok_count,
        "pages": results,
    }, ensure_ascii=False, indent=2))
    return 0 if ok_count else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "version": VERSION,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "checks": {},
        "hints": [],
    }
    checks = report["checks"]

    # proxy mode -- a slow registry proxy is the usual cause of 5s+ requests
    system_proxies = urllib.request.getproxies()
    checks["proxy"] = {
        "mode": "system" if use_system_proxy() else "direct",
        "system_proxies": system_proxies,
        "env_override": "CLAUDE_WEB_SYSTEM_PROXY",
    }
    if system_proxies and not use_system_proxy():
        report["hints"].append(
            "Requests bypass the system proxy (CLAUDE_WEB_SYSTEM_PROXY unset). "
            "Set it to 1 if a site is only reachable through the proxy."
        )

    # trafilatura
    checks["trafilatura"] = {
        "ok": trafilatura is not None,
        "version": TRAFILATURA_VERSION,
        "impact": "article extraction quality",
    }
    if trafilatura is None:
        report["hints"].append(
            f'"{sys.executable}" -m pip install trafilatura   (optional, recommended)'
        )

    # SearXNG env
    env_url = os.environ.get("SEARXNG_URL")
    local_up = _probe("http://127.0.0.1:8080/")
    checks["SEARXNG_URL"] = {"set": bool(env_url), "value": env_url}
    checks["searxng_localhost_8080"] = {"reachable": local_up}
    base = env_url or ("http://127.0.0.1:8080" if local_up else None)
    if base:
        try:
            probe = searxng_search(base, "ping", 1, "en-US", "general", None, 8)
            checks["searxng_api"] = {"ok": True, "results": len(probe)}
        except Exception as exc:  # noqa: BLE001
            checks["searxng_api"] = {"ok": False, "error": str(exc)}
            report["hints"].append(
                "SearXNG reachable but JSON API failed. In settings.yml set "
                "`search.formats: [html, json]`, then restart."
            )
    else:
        checks["searxng_api"] = {"ok": False, "error": "not configured"}

    # DDG fallback
    try:
        r = ddg_search("test", 1, "en-US", None, 12)
        checks["duckduckgo"] = {"ok": bool(r), "results": len(r)}
    except Exception as exc:  # noqa: BLE001
        checks["duckduckgo"] = {"ok": False, "error": str(exc)}

    # Bing fallback
    try:
        r = bing_search("test", 1, "en-US", None, 12)
        checks["bing"] = {"ok": bool(r), "results": len(r)}
    except Exception as exc:  # noqa: BLE001
        checks["bing"] = {"ok": False, "error": str(exc)}

    # cache
    try:
        d = cache_dir()
        files = list(d.glob("*.json")) if d.exists() else []
        checks["cache"] = {"dir": str(d), "entries": len(files)}
    except Exception as exc:  # noqa: BLE001
        checks["cache"] = {"error": str(exc)}

    chain = backend_chain("auto", env_url)
    backend = chain[0][0]
    report["backend_chain"] = [b for b, _ in chain]
    report["selected_backend"] = backend
    report["ok"] = any(
        checks.get(k, {}).get("ok") for k in ("searxng_api", "duckduckgo", "bing")
    )
    if backend == "ddg" and "bing" in report["backend_chain"]:
        report["hints"].append(
            "Using DuckDuckGo HTML with Bing as fallback. DDG is rate-limited and "
            "may serve anti-bot pages; run a local SearXNG (references/setup.md) "
            "for the most reliable results."
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="web.py",
        description="Live web search + page content extraction (stdlib only).",
    )
    p.add_argument("--version", action="version", version=f"web.py {VERSION}")
    p.add_argument("--timeout", type=float, default=20)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search the live web")
    s.add_argument("query")
    s.add_argument("-k", "--num", type=int, default=8)
    s.add_argument("--backend", choices=["auto", "searxng", "ddg", "bing"], default="auto")
    s.add_argument("--searxng", default=os.environ.get("SEARXNG_URL"))
    s.add_argument("--lang", default="zh-CN")
    s.add_argument("--categories", default="general")
    s.add_argument("--time-range", choices=["day", "week", "month", "year"])
    s.add_argument("--no-cache", action="store_true")
    s.set_defaults(func=cmd_search)

    f = sub.add_parser("fetch", help="download pages and extract main content")
    f.add_argument("urls", nargs="+")
    f.add_argument("--max-chars", type=int, default=6000)
    f.add_argument("--min-chars", type=int, default=200)
    f.add_argument("--no-cache", action="store_true")
    f.add_argument("--allow-private", action="store_true",
                   help="allow intranet/private addresses (SSRF guard off)")
    f.set_defaults(func=cmd_fetch)

    d = sub.add_parser("doctor", help="diagnose backends and dependencies")
    d.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
