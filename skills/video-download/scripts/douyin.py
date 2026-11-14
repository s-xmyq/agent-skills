#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音适配：短链 → aweme_id → 第三方解析接口 → 统一 meta。

接口解析服务是第三方，失效时按 SKILL.md「维护」一节追加 PARSERS。
"""

from __future__ import annotations

import re
import sys
import urllib.parse

from _common import (
    SkillError,
    UA_DESKTOP,
    UA_MOBILE,
    as_int,
    get_json,
    resolve_redirect,
)

KEY = "douyin"
NAME = "抖音"
DOMAINS = ("v.douyin.com", "douyin.com", "iesdouyin.com", "amemv.com")
SUPPORTED = True

REFERER = "https://www.douyin.com/"
DOWNLOAD_HEADERS = {
    "User-Agent": UA_DESKTOP,
    "Referer": REFERER,
    "Accept": "*/*",
}
API_HEADERS = {
    "User-Agent": UA_DESKTOP,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def looks_like_id(text: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}", (text or "").strip()))


# --------------------------------------------------------------------------
# 输入解析
# --------------------------------------------------------------------------

def resolve_id(text: str, timeout: int = 20) -> str:
    """把短链/分享链接解析成抖音 aweme_id（纯数字视频 ID）。"""
    raw = (text or "").strip()
    if re.fullmatch(r"\d{6,}", raw):
        return raw

    m = re.search(r"/(?:video|note)/(\d{6,})", raw)
    if m:
        return m.group(1)
    m = re.search(r"[?&](?:item_ids|aweme_id)=(\d{6,})", raw)
    if m:
        return m.group(1)

    final_url, body = resolve_redirect(
        raw, {"User-Agent": UA_MOBILE, "Accept": "text/html"}, timeout=timeout
    )
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


def fetch(
    ref_id: str,
    *,
    source_url: str | None = None,
    page: int | None = None,
    timeout: int = 25,
) -> dict:
    candidates: list[str] = []
    if source_url and "://" in source_url:
        candidates.append(source_url)
    canonical = f"https://www.douyin.com/video/{ref_id}"
    if canonical not in candidates:
        candidates.append(canonical)

    errors: list[str] = []
    for parser in PARSERS:
        for cand in candidates:
            endpoint = parser["endpoint"].format(urllib.parse.quote(cand, safe=""))
            try:
                payload = get_json(endpoint, headers=API_HEADERS, timeout=timeout)
                meta = parser["parse"](payload)
                print(
                    f"[info] parser={parser['name']} url={cand}", file=sys.stderr
                )
                return {
                    "video_id": ref_id,
                    "out_id": ref_id,
                    "title": meta.get("title") or "",
                    "author": meta.get("author") or "",
                    "urls": [{"url": meta["video_url"], "ext": "mp4"}],
                    "backup_url": meta.get("backup_url") or None,
                    "headers": dict(DOWNLOAD_HEADERS),
                    "quality": meta.get("quality"),
                    "width": as_int(meta.get("width")),
                    "height": as_int(meta.get("height")),
                    "fps": as_int(meta.get("fps")),
                    "duration": as_int(meta.get("duration")),
                    "size_reported": as_int(meta.get("size_reported")),
                    "source_api": parser["name"],
                    "extra": {
                        "aweme_id": ref_id,
                        "like": as_int(meta.get("like")),
                        "comment": as_int(meta.get("comment")),
                        "collect": as_int(meta.get("collect")),
                        "share": as_int(meta.get("share")),
                    },
                }
            except Exception as exc:  # noqa: BLE001 - 逐个后端容错
                errors.append(f"{parser['name']}: {type(exc).__name__}: {exc}")
                print(
                    f"[warn] 解析失败 {parser['name']} ({cand}): {exc}",
                    file=sys.stderr,
                )

    raise SkillError(
        "parse_failed",
        "所有解析接口都失败了",
        "公开解析服务可能临时不可用。可稍后重试、换一条分享链接，或到 "
        "scripts/douyin.py 的 PARSERS 里追加新接口。最近错误："
        + " | ".join(errors[-2:]),
    )
