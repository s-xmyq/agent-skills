#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频无水印下载器（当前支持：抖音）。

用法示例:
    python scripts/fetch_video.py "https://v.douyin.com/Z7wbWe_Ompc/"
    python scripts/fetch_video.py "7.43 复制打开抖音... https://v.douyin.com/xxx/" --out D:/Videos
    python scripts/fetch_video.py 7696355215896825107 --check
    python scripts/fetch_video.py "https://v.douyin.com/xxx/" --frame 10 --frame 30

stdout 只输出一行 JSON；进度和日志走 stderr。
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

# 平台支持表：新增平台时在这里登记，并实现对应的解析器。
PLATFORMS = {
    "douyin": {
        "name": "抖音",
        "domains": ("v.douyin.com", "douyin.com", "iesdouyin.com", "amemv.com"),
        "supported": True,
    },
    "tiktok": {"name": "TikTok", "domains": ("tiktok.com",), "supported": False},
    "kuaishou": {
        "name": "快手",
        "domains": ("kuaishou.com", "gifshow.com", "chenzhongtech.com"),
        "supported": False,
    },
    "xiaohongshu": {
        "name": "小红书",
        "domains": ("xiaohongshu.com", "xhslink.com"),
        "supported": False,
    },
    "bilibili": {
        "name": "B站",
        "domains": ("bilibili.com", "b23.tv"),
        "supported": False,
    },
}

SUPPORTED_TABLE = "、".join(f'{v["name"]}（{k}）' for k, v in PLATFORMS.items() if v["supported"])


class SkillError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


# --------------------------------------------------------------------------
# 输入解析
# --------------------------------------------------------------------------

def extract_url(text: str) -> str:
    """从分享口令/整段文字里抠出第一个 URL。"""
    m = re.search(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", text)
    if not m:
        raise SkillError(
            "no_url",
            "没有在输入里找到链接",
            "需要抖音分享链接，例如 https://v.douyin.com/xxxx/ ；也可以直接传纯数字的 aweme_id。",
        )
    return m.group(0).rstrip(".,;:!?)]}'\"，。；：！？、）》」』")


def detect_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    for key, info in PLATFORMS.items():
        if any(host == d or host.endswith("." + d) for d in info["domains"]):
            return key
    raise SkillError(
        "unknown_platform",
        f"无法识别链接所属平台：{host or url}",
        f"目前仅支持：{SUPPORTED_TABLE}。",
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_aweme_id(url: str, timeout: int = 20) -> str:
    """把短链/分享链接解析成抖音 aweme_id（纯数字视频 ID）。"""
    if re.fullmatch(r"\d{6,}", url.strip()):
        return url.strip()

    m = re.search(r"/(?:video|note)/(\d{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&](?:item_ids|aweme_id)=(\d{6,})", url)
    if m:
        return m.group(1)

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": UA_MOBILE, "Accept": "text/html"})
    final_url, body = url, ""
    try:
        with opener.open(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            body = resp.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location") if exc.headers else None
        final_url = location or exc.geturl() or url
        try:
            body = exc.read(200_000).decode("utf-8", "replace")
        except Exception:
            body = ""
    except Exception as exc:  # 网络问题
        raise SkillError("resolve_failed", f"短链解析失败：{exc}", "检查网络或稍后重试。") from exc

    for candidate in (final_url, urllib.parse.unquote(final_url), body):
        m = re.search(r"/(?:video|note)/(\d{6,})", candidate or "")
        if m:
            return m.group(1)
        m = re.search(r"(?:item_ids|aweme_id)%3D(\d{6,})", candidate or "")
        if m:
            return m.group(1)

    raise SkillError(
        "resolve_failed",
        "链接重定向后没有找到视频 ID",
        "链接可能已失效（抖音分享链接有时效），请重新复制分享链接。",
    )


# --------------------------------------------------------------------------
# 解析接口（第三方公开服务）
# --------------------------------------------------------------------------

def _get_json(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA_DESKTOP,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
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


def parse_xingzhige(payload: dict) -> dict:
    """api.xingzhige.com/API/douyin/ 的返回结构。"""
    if payload.get("code") != 0:
        raise ValueError(payload.get("msg") or "接口返回 code != 0")
    data = payload.get("data") or {}
    item = data.get("item") or {}
    video_url = item.get("url") or item.get("ury")
    if not video_url:
        raise ValueError("返回中缺少视频直链")
    author = (data.get("author") or {}).get("name") or ""
    stat = data.get("stat") or {}
    return {
        "title": item.get("title") or "",
        "author": author,
        "video_url": video_url.replace("&amp;", "&"),
        "backup_url": (item.get("ury") or "").replace("&amp;", "&"),
        "cover": item.get("cover") or "",
        "music": item.get("muisic") or item.get("music") or "",
        "quality": item.get("quality") or "",
        "width": item.get("width"),
        "height": item.get("height"),
        "fps": item.get("fps"),
        "duration": item.get("duration"),
        "size_reported": item.get("size"),
        "like": stat.get("like"),
        "comment": stat.get("comment"),
        "collect": stat.get("collect"),
        "share": stat.get("share"),
    }


# 解析器顺序 = 尝试顺序。新增服务时：加一个 parse_xxx()，再往列表里加一项即可。
PARSERS = [
    {
        "name": "xingzhige",
        "endpoint": "https://api.xingzhige.com/API/douyin/?url={}",
        "parse": parse_xingzhige,
    },
]


def fetch_meta(aweme_id: str, original_url: str) -> dict:
    candidates = [original_url]
    canonical = f"https://www.douyin.com/video/{aweme_id}"
    if canonical not in candidates:
        candidates.append(canonical)
    errors: list[str] = []
    for parser in PARSERS:
        for cand in candidates:
            endpoint = parser["endpoint"].format(urllib.parse.quote(cand, safe=""))
            try:
                payload = _get_json(endpoint)
                meta = parser["parse"](payload)
                meta["source_api"] = parser["name"]
                return meta
            except Exception as exc:  # noqa: BLE001 - 逐个后端容错
                errors.append(f'{parser["name"]}: {type(exc).__name__}: {exc}')
                print(f"[warn] 解析失败 {parser['name']} ({cand}): {exc}", file=sys.stderr)
    raise SkillError(
        "parse_failed",
        "所有解析接口都失败了",
        "公开解析服务可能临时不可用。可稍后重试、换一条分享链接，或到 "
        "scripts/fetch_video.py 的 PARSERS 里追加新接口。最近错误："
        + " | ".join(errors[-2:]),
    )


# --------------------------------------------------------------------------
# 下载与校验
# --------------------------------------------------------------------------

def safe_name(value: str, maxlen: int = 40) -> str:
    value = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:maxlen].strip()


def default_out_dir() -> Path:
    home = Path.home()
    for name in ("Downloads", "下载"):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    return home


def download(url: str, dest: Path, timeout: int = 300) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA_DESKTOP,
            "Referer": "https://www.douyin.com/",
            "Accept": "*/*",
        },
    )
    written = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            expected = int(resp.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    if expected:
                        pct = written * 100 / expected
                        print(f"\r[download] {written / 1048576:.1f}MB / {expected / 1048576:.1f}MB ({pct:.0f}%)", end="", file=sys.stderr)
                    else:
                        print(f"\r[download] {written / 1048576:.1f}MB", end="", file=sys.stderr)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise SkillError("download_failed", f"下载失败：{exc}", "直链有时效，重新解析一次通常可以恢复。") from exc
    print(file=sys.stderr)
    tmp.replace(dest)
    return written


def grab_frame(video: Path, sec: float, out: Path) -> str | None:
    """抽一帧用于人工确认是否有水印；缺 cv2 时返回 None（不阻塞）。"""
    try:
        import cv2  # type: ignore
    except Exception:
        return None
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return None
    buf.tofile(str(out))
    return str(out)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def emit(payload: dict, pretty: bool) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


def run(args: argparse.Namespace) -> dict:
    raw = args.input.strip()
    url = raw if re.fullmatch(r"\d{6,}", raw) else extract_url(raw)
    platform = "douyin" if re.fullmatch(r"\d{6,}", raw) else detect_platform(url)
    info = PLATFORMS[platform]
    if not info["supported"]:
        raise SkillError(
            "unsupported_platform",
            f"{info['name']} 暂不支持",
            f"当前仅支持：{SUPPORTED_TABLE}。",
        )

    aweme_id = resolve_aweme_id(url)
    print(f"[info] aweme_id={aweme_id}", file=sys.stderr)
    meta = fetch_meta(aweme_id, f"https://www.douyin.com/video/{aweme_id}")

    title = meta.get("title") or ""
    author = meta.get("author") or ""
    # 文件名里去掉话题标签，避免动不动几十个字符把标题挤没
    title_for_name = re.sub(r"\s*#\S+", "", title).strip() or title
    stem = "_".join(x for x in (safe_name(author, 20), safe_name(title_for_name, 40), aweme_id) if x)
    if len(stem) > 110:
        stem = stem[:110].rstrip("_")

    result = {
        "ok": True,
        "platform": platform,
        "platform_name": info["name"],
        "aweme_id": aweme_id,
        "title": title,
        "author": author,
        "source_api": meta.get("source_api"),
        "video_url": meta.get("video_url"),
        "quality": meta.get("quality"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "fps": meta.get("fps"),
        "duration": meta.get("duration"),
        "size_reported": meta.get("size_reported"),
        "like": meta.get("like"),
        "comment": meta.get("comment"),
        "collect": meta.get("collect"),
        "share": meta.get("share"),
        "path": None,
        "size_bytes": None,
        "frame": None,
        "warnings": [],
    }

    if args.check:
        return result

    out_root = Path(args.out).expanduser() if args.out else default_out_dir()
    out_dir = out_root if args.flat else out_root / f"douyin_{aweme_id}"
    dest = out_dir / f"{stem}.mp4"

    try:
        written = download(meta["video_url"], dest, timeout=args.timeout)
    except SkillError:
        backup = meta.get("backup_url")
        if not backup or backup == meta.get("video_url"):
            raise
        print("[warn] 主线路失败，改用备用线路", file=sys.stderr)
        written = download(backup, dest, timeout=args.timeout)

    result["path"] = str(dest)
    result["size_bytes"] = written
    result["size_mb"] = round(written / 1048576, 2)
    reported = meta.get("size_reported")
    if reported and abs(written - reported) > max(1024, reported * 0.01):
        result["warnings"].append(f"文件大小与接口上报不一致：{written} vs {reported}")

    for idx, sec in enumerate(args.frame or []):
        frame_path = out_dir / f"frame_{aweme_id}_{str(sec).replace('.', 'p')}s.jpg"
        saved = grab_frame(dest, sec, frame_path)
        if saved:
            result.setdefault("frames", []).append({"sec": sec, "path": saved})
        else:
            result["warnings"].append("抽帧失败：未安装 cv2 或该时间点无画面")
            break

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="下载抖音无水印视频")
    ap.add_argument("input", help="分享链接、分享口令整段文字，或纯数字 aweme_id")
    ap.add_argument("--out", help="保存目录，默认 ~/Downloads")
    ap.add_argument("--flat", action="store_true", help="直接存到 --out，不再建 douyin_<id> 子目录")
    ap.add_argument("--check", action="store_true", help="只解析元数据，不下载")
    ap.add_argument("--frame", type=float, action="append", metavar="SEC", help="抽取指定秒数的画面用于人工查水印，可重复")
    ap.add_argument("--timeout", type=int, default=300, help="下载超时秒数，默认 300")
    ap.add_argument("--pretty", action="store_true", help="JSON 缩进输出")
    args = ap.parse_args()

    try:
        emit(run(args), args.pretty)
        return 0
    except SkillError as exc:
        emit(
            {"ok": False, "error": {"code": exc.code, "message": exc.message, "hint": exc.hint}},
            args.pretty,
        )
        return {"no_url": 5, "unknown_platform": 2, "unsupported_platform": 2,
                "resolve_failed": 3, "parse_failed": 3, "download_failed": 4}.get(exc.code, 1)
    except KeyboardInterrupt:
        emit({"ok": False, "error": {"code": "interrupted", "message": "用户中断", "hint": ""}}, args.pretty)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
