#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video-download 的共享能力：输入解析、HTTP、下载、命名与输出。

平台模块（douyin.py / bilibili.py）只负责自己的 ID 解析和接口调用，
通过统一的 meta 契约把结果交给 fetch_video.py：

    video_id       平台内视频 ID（抖音 aweme_id / B站 BV 号）
    out_id         落盘子目录用的 ID，默认同 video_id
    title/author   标题、作者
    filename_title 可选，用于文件名的标题（B站多P时含 P 号，避免互相覆盖）
    urls           [{"url": ..., "ext": "mp4"|"flv"}]，一个或多个分段
    backup_url     备用直链（可空）
    headers        下载请求头（Referer 等）
    quality        清晰度标签
    width/height/fps/duration/size_reported  整数或 None
    source_api     实际使用的解析后端
    extra          平台特有字段，原样并入最终 JSON

本模块不依赖任何平台模块。
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


class SkillError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


# --------------------------------------------------------------------------
# 输入与命名
# --------------------------------------------------------------------------

def extract_url(text: str) -> str:
    """从分享口令/整段文字里抠出第一个 URL。"""
    m = re.search(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", text)
    if not m:
        raise SkillError(
            "no_url",
            "没有在输入里找到链接",
            "需要视频分享链接，例如 https://v.douyin.com/xxxx/ 或 "
            "https://b23.tv/xxxx ；抖音也可以直接传纯数字的 aweme_id。",
        )
    return m.group(0).rstrip(".,;:!?)]}'\"，。；：！？、）》」』")


def as_int(value) -> int | None:
    """把第三方接口可能返回的字符串/浮点数字统一成 int。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_name(value: str, maxlen: int = 40) -> str:
    value = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:maxlen].strip()


def build_stem(author: str, title: str, video_id: str) -> str:
    # 文件名里去掉话题标签，避免动不动几十个字符把标题挤没
    title_for_name = re.sub(r"\s*#\S+", "", title or "").strip() or (title or "")
    stem = "_".join(
        x
        for x in (
            safe_name(author or "", 20),
            safe_name(title_for_name, 40),
            safe_name(video_id or "", 40),
        )
        if x
    )
    if len(stem) > 110:
        stem = stem[:110].rstrip("_")
    return stem or "video"


def default_out_dir() -> Path:
    home = Path.home()
    for name in ("Downloads", "下载"):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    return home


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def get_json(url: str, headers: dict | None = None, timeout: int = 25) -> dict:
    req = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": UA_DESKTOP},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    text = None
    for enc in ("utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("响应编码无法识别")
    return json.loads(text)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_redirect(
    url: str, headers: dict | None = None, timeout: int = 20
) -> tuple[str, str]:
    """请求一次短链，不跟随重定向，返回 (Location 或原 URL, 响应体)。"""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        url, headers=headers or {"User-Agent": UA_MOBILE, "Accept": "text/html"}
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.geturl(), resp.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location") if exc.headers else None
        final_url = location or exc.geturl() or url
        try:
            body = exc.read(200_000).decode("utf-8", "replace")
        except Exception:
            body = ""
        return final_url, body
    except Exception as exc:  # 网络问题
        raise SkillError(
            "resolve_failed", f"短链解析失败：{exc}", "检查网络或稍后重试。"
        ) from exc


def _reject_private(host: str, detail: str | None = None) -> None:
    target = f"{host} -> {detail}" if detail else host
    raise SkillError(
        "download_failed",
        f"拒绝内网下载地址：{target}",
        "解析服务返回了内网地址，已中止下载。",
    )


def _resolved_addresses(host: str) -> list:
    """解析域名到全部 IP；任何一步失败都按下载失败处理。"""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise SkillError(
            "download_failed",
            f"无法解析下载地址的域名：{host}",
            "检查网络或 DNS 设置；解析服务返回的直链可能已失效。",
        ) from exc

    addresses = []
    for info in infos:
        raw = str(info[4][0]).split("%", 1)[0]
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError as exc:
            raise SkillError(
                "download_failed",
                f"下载地址解析出无法识别的 IP：{info[4][0]}",
                "解析服务返回了异常地址，已中止下载。",
            ) from exc
    if not addresses:
        raise SkillError(
            "download_failed",
            f"下载地址无法解析到任何 IP：{host}",
            "检查网络或 DNS 设置；解析服务返回的直链可能已失效。",
        )
    return addresses


def validate_download_url(url: str) -> None:
    """解析服务返回的地址不可信：只允许解析到公网地址的 http(s)。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        raise SkillError(
            "download_failed",
            f"下载地址无效：{url[:120]}",
            "解析服务返回了无法识别的地址，已中止下载。",
        ) from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SkillError(
            "download_failed",
            f"拒绝非 http(s) 下载地址：{url[:120]}",
            "解析服务可能被篡改，已中止下载。",
        )
    host = parsed.hostname.strip("[]").lower()
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        _reject_private(host)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not ip.is_global:
            _reject_private(host, str(ip))
        return

    # 主机名同样不可信：必须解析并确认所有地址都是公网地址，
    # 否则 http://<公网域名解析到 127.0.0.1> 这类地址会绕过字面量检查。
    for address in _resolved_addresses(host):
        if not address.is_global:
            _reject_private(host, str(address))


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """下载时跟随重定向，但每一跳都重新执行同一套地址校验。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(
    url: str, dest: Path, headers: dict | None = None, timeout: int = 300
) -> int:
    validate_download_url(url)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SkillError(
            "download_failed",
            f"创建保存目录失败：{exc}",
            "检查目录权限或磁盘空间。",
        ) from exc
    tmp = dest.with_name(dest.name + ".part")
    req_headers = {"User-Agent": UA_DESKTOP}
    req_headers.update(headers or {})
    req = urllib.request.Request(url, headers=req_headers)
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    written = 0
    try:
        with opener.open(req, timeout=timeout) as resp:
            expected = as_int(resp.headers.get("Content-Length")) or 0
            with open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    if expected:
                        pct = written * 100 / expected
                        print(
                            f"\r[download] {written / 1048576:.1f}MB / "
                            f"{expected / 1048576:.1f}MB ({pct:.0f}%)",
                            end="",
                            file=sys.stderr,
                            flush=True,
                        )
                    else:
                        print(
                            f"\r[download] {written / 1048576:.1f}MB",
                            end="",
                            file=sys.stderr,
                            flush=True,
                        )
    except SkillError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise SkillError(
            "download_failed",
            f"下载失败：{exc}",
            "直链有时效，重新解析一次通常可以恢复。",
        ) from exc
    print(file=sys.stderr)
    try:
        tmp.replace(dest)
    except OSError as exc:
        raise SkillError(
            "download_failed",
            f"保存文件失败：{exc}",
            "检查目录权限或磁盘空间。",
        ) from exc
    return written


def grab_frame(video: Path, sec: float, out: Path) -> str | None:
    """抽一帧用于人工确认是否有水印；缺 cv2 或解码异常时返回 None（不阻塞）。"""
    cap = None
    try:
        import cv2  # type: ignore

        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
        ok, frame = cap.read()
        if not ok:
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            return None
        buf.tofile(str(out))
        return str(out)
    except Exception:  # 未安装 cv2、解码失败等都不应影响已下载的视频
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

def emit(payload: dict, pretty: bool) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
