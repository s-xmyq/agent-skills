#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站适配：b23.tv / BV / av → 官方 web 接口 → 统一 meta。

未登录态下 playurl 最高返回 720P 的合并流（durl），不需要 ffmpeg。
清晰度上限由 B站接口决定；1080P 及以上需要登录态，本技能当前不读取 cookie。
"""

from __future__ import annotations

import re
import urllib.parse

from _common import (
    SkillError,
    UA_DESKTOP,
    UA_MOBILE,
    as_int,
    get_json,
    resolve_redirect,
)

KEY = "bilibili"
NAME = "B站"
DOMAINS = ("bilibili.com", "b23.tv", "bili2233.cn")
SUPPORTED = True

REFERER = "https://www.bilibili.com/"
DOWNLOAD_HEADERS = {
    "User-Agent": UA_DESKTOP,
    "Referer": REFERER,
    "Accept": "*/*",
}
API_HEADERS = {
    "User-Agent": UA_DESKTOP,
    "Referer": REFERER,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"

BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")

# B站清晰度代码（playurl.quality）
QUALITY_LABELS = {
    127: "8K",
    126: "杜比视界",
    125: "HDR",
    120: "4K",
    116: "1080P60",
    112: "1080P+",
    80: "1080P",
    74: "720P60",
    64: "720P",
    32: "480P",
    16: "360P",
}

# 实际下载流的分辨率（不是 view.dimension 的源视频分辨率）
QUALITY_SIZES = {
    127: (7680, 4320),
    126: (3840, 2160),
    125: (3840, 2160),
    120: (3840, 2160),
    116: (1920, 1080),
    112: (1920, 1080),
    80: (1920, 1080),
    74: (1280, 720),
    64: (1280, 720),
    32: (852, 480),
    16: (640, 360),
}


def looks_like_id(text: str) -> bool:
    raw = (text or "").strip()
    return bool(
        re.fullmatch(r"BV[0-9A-Za-z]{10}", raw, re.I)
        or re.fullmatch(r"av\d+", raw, re.I)
    )


# --------------------------------------------------------------------------
# 输入解析
# --------------------------------------------------------------------------

def _unsupported_reason(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("live."):
        return "B站直播"
    if host == "bilibili.com" or host.endswith(".bilibili.com"):
        has_video_path = bool(re.search(r"/video/", parsed.path))
        has_id_query = bool(re.search(r"(?:^|&)(?:bvid|aid)=", parsed.query))
        if not has_video_path and not has_id_query:
            return "B站非视频页面"
    return None


def resolve_id(text: str, timeout: int = 20) -> str:
    """把 b23.tv/bili2233.cn 短链或视频链接解析成 BV 号 / av 号。"""
    raw = (text or "").strip()

    m = re.fullmatch(r"BV[0-9A-Za-z]{10}", raw, re.I)
    if m:
        return "BV" + raw[2:]
    m = re.fullmatch(r"av(\d+)", raw, re.I)
    if m:
        return "av" + m.group(1)

    if "://" not in raw:
        raise SkillError(
            "resolve_failed",
            "无法识别的 B站输入",
            "需要 b23.tv / bili2233.cn 短链、bilibili.com/video 链接、BV 号或 av 号。",
        )

    reason = _unsupported_reason(raw)
    if reason:
        raise SkillError(
            "unsupported_platform",
            f"{reason}暂不支持",
            "当前仅支持 B站普通视频（/video/BV...）；番剧、直播、课程、专栏等未支持。",
        )

    m = re.search(r"/video/(BV[0-9A-Za-z]{10})", raw, re.I)
    if m:
        return "BV" + m.group(1)[2:]
    m = re.search(r"/video/av(\d+)", raw, re.I)
    if m:
        return "av" + m.group(1)
    m = re.search(r"[?&]bvid=(BV[0-9A-Za-z]{10})", raw, re.I)
    if m:
        return "BV" + m.group(1)[2:]
    m = re.search(r"[?&]aid=(\d+)", raw)
    if m:
        return "av" + m.group(1)

    host = (urllib.parse.urlparse(raw).hostname or "").lower()
    if host == "bilibili.com" or host.endswith(".bilibili.com"):
        # 普通网页链接不应走短链兜底，否则可能从推荐位里抓到无关的 BV 号。
        raise SkillError(
            "resolve_failed",
            "链接里没有找到 BV / av 号",
            "请确认是 /video/BV... 形式的视频链接；番剧、直播、课程等暂不支持。",
        )

    current = raw
    for _ in range(4):
        final_url, body = resolve_redirect(
            current, {"User-Agent": UA_MOBILE, "Accept": "text/html"}, timeout=timeout
        )
        for candidate in (final_url, urllib.parse.unquote(final_url), body):
            m = re.search(r"BV[0-9A-Za-z]{10}", candidate or "")
            if m:
                return m.group(0)
            m = re.search(r"/video/av(\d+)", candidate or "", re.I)
            if m:
                return "av" + m.group(1)
        next_host = (urllib.parse.urlparse(final_url).hostname or "").lower()
        if final_url == current or next_host not in ("b23.tv", "bili2233.cn"):
            break
        current = final_url

    raise SkillError(
        "resolve_failed",
        "短链重定向后没有找到 BV 号",
        "链接可能已失效，请重新复制 B站分享链接。",
    )


def _page_from_url(url: str | None) -> int | None:
    if not url:
        return None
    m = re.search(r"[?&]p=(\d+)", url)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------
# 官方接口
# --------------------------------------------------------------------------

def _view(ref_id: str, timeout: int) -> dict:
    if ref_id.lower().startswith("av"):
        params = {"aid": ref_id[2:]}
    else:
        params = {"bvid": ref_id}
    try:
        payload = get_json(
            f"{VIEW_API}?{urllib.parse.urlencode(params)}",
            headers=API_HEADERS,
            timeout=timeout,
        )
    except Exception as exc:  # 网络/JSON 错误统一成 parse_failed
        raise SkillError(
            "parse_failed",
            f"B站视频信息接口请求失败：{exc}",
            "检查网络或稍后重试；接口可能触发风控。",
        ) from exc
    if as_int(payload.get("code")) != 0:
        code = payload.get("code")
        msg = payload.get("message") or "接口返回错误"
        raise SkillError(
            "parse_failed",
            f"B站视频信息接口失败（{code}）：{msg}",
            "视频可能已删除、仅会员可见或触发风控；确认链接有效后重试。",
        )
    return payload.get("data") or {}


def fetch(
    ref_id: str,
    *,
    source_url: str | None = None,
    page: int | None = None,
    timeout: int = 25,
) -> dict:
    data = _view(ref_id, timeout)

    pages = data.get("pages") or []
    if not pages and data.get("cid"):
        pages = [
            {
                "cid": data.get("cid"),
                "part": "",
                "duration": data.get("duration"),
            }
        ]
    if not pages:
        raise SkillError(
            "parse_failed",
            "B站接口没有返回分P信息",
            "该视频可能是互动视频或特殊稿件，暂不支持。",
        )

    wanted = page if page is not None else (_page_from_url(source_url) or 1)
    if not 1 <= wanted <= len(pages):
        raise SkillError(
            "parse_failed",
            f"分P {wanted} 不存在",
            f"该视频共 {len(pages)} 个分P，请用 --page 选择 1-{len(pages)}。",
        )
    page_info = pages[wanted - 1]

    cid = as_int(page_info.get("cid")) or as_int(data.get("cid"))
    if not cid:
        raise SkillError(
            "parse_failed",
            "B站接口没有返回 cid",
            "无法确定视频流，请稍后重试或换一个链接。",
        )

    bvid = data.get("bvid") or (ref_id if ref_id.upper().startswith("BV") else "")
    if not bvid:
        raise SkillError(
            "parse_failed",
            "B站接口没有返回 BV 号",
            "无法确定视频 ID，请稍后重试或换一个链接。",
        )

    params = urllib.parse.urlencode(
        {"bvid": bvid, "cid": cid, "qn": 80, "fnval": 1, "fourk": 0}
    )
    try:
        play = get_json(
            f"{PLAYURL_API}?{params}", headers=API_HEADERS, timeout=timeout
        )
    except Exception as exc:  # 网络/JSON 错误统一成 parse_failed
        raise SkillError(
            "parse_failed",
            f"B站播放地址接口请求失败：{exc}",
            "检查网络或稍后重试；接口可能触发风控。",
        ) from exc
    if as_int(play.get("code")) != 0:
        code = play.get("code")
        msg = play.get("message") or "接口返回错误"
        raise SkillError(
            "parse_failed",
            f"B站播放地址接口失败（{code}）：{msg}",
            "视频可能需要登录/会员，或接口触发风控；当前版本未登录，最高只能取 720P。",
        )

    pdata = play.get("data") or {}
    fmt = str(pdata.get("format") or "")
    ext = "flv" if fmt.lower().startswith("flv") else "mp4"
    durl = pdata.get("durl") or []
    urls = [
        {"url": str(item.get("url")), "ext": ext}
        for item in durl
        if item.get("url")
    ]
    if not urls:
        raise SkillError(
            "parse_failed",
            "B站接口没有返回视频流地址",
            "该视频可能是付费/会员专享或接口风控，当前无法下载。",
        )

    qn = as_int(pdata.get("quality")) or 0
    width, height = QUALITY_SIZES.get(qn, (None, None))
    size = sum(as_int(item.get("size")) or 0 for item in durl) or None

    title = data.get("title") or ""
    part = page_info.get("part") or ""
    if len(pages) > 1:
        # P 号放在标题段最前面，避免长标题把 P 号截掉后互相覆盖。
        suffix = f"P{wanted}" + (f"_{part}" if part else "")
        filename_title = f"{suffix}_{title}" if title else suffix
    else:
        filename_title = title

    return {
        "video_id": bvid,
        "out_id": bvid,
        "title": title,
        "filename_title": filename_title,
        "author": (data.get("owner") or {}).get("name") or "",
        "urls": urls,
        "backup_url": None,
        "headers": dict(DOWNLOAD_HEADERS),
        "quality": QUALITY_LABELS.get(qn) or (fmt or None),
        "width": width,
        "height": height,
        "fps": None,
        "duration": as_int(page_info.get("duration")) or as_int(data.get("duration")),
        "size_reported": size,
        "source_api": "bilibili_web",
        "extra": {
            "bvid": bvid,
            "aid": as_int(data.get("aid")),
            "cid": cid,
            "page": wanted,
            "page_title": page_info.get("part") or "",
            "part_count": len(pages),
        },
    }
