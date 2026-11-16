#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频下载统一入口（抖音 / B站）。

用法示例:
    python scripts/fetch_video.py "https://v.douyin.com/Z7wbWe_Ompc/"
    python scripts/fetch_video.py "7.43 复制打开抖音... https://v.douyin.com/xxx/" --out D:/Videos
    python scripts/fetch_video.py "https://www.bilibili.com/video/BV1xx411c7mD" --check
    python scripts/fetch_video.py "https://b23.tv/xxxx" --page 2 --frame 10 --frame 30

stdout 默认只输出一行 JSON；--pretty 时为缩进 JSON。进度和日志走 stderr。
平台逻辑在 douyin.py / bilibili.py，共享能力在 _common.py。
"""

from __future__ import annotations

import argparse
import sys
import traceback
import urllib.parse
from pathlib import Path

import bilibili
import douyin
from _common import (
    SkillError,
    as_int,
    build_stem,
    default_out_dir,
    download,
    emit,
    extract_url,
    grab_frame,
    safe_name,
)

# module 为 None 表示能识别域名但未支持。
PLATFORMS = {
    "douyin": {"name": douyin.NAME, "domains": douyin.DOMAINS, "module": douyin},
    "bilibili": {
        "name": bilibili.NAME,
        "domains": bilibili.DOMAINS,
        "module": bilibili,
    },
    "tiktok": {"name": "TikTok", "domains": ("tiktok.com",), "module": None},
    "kuaishou": {
        "name": "快手",
        "domains": ("kuaishou.com", "gifshow.com", "chenzhongtech.com"),
        "module": None,
    },
    "xiaohongshu": {
        "name": "小红书",
        "domains": ("xiaohongshu.com", "xhslink.com"),
        "module": None,
    },
}

SUPPORTED_TABLE = "、".join(
    f"{info['name']}（{key}）"
    for key, info in PLATFORMS.items()
    if info["module"] is not None
)

EXIT_CODES = {
    "no_url": 5,
    "unknown_platform": 2,
    "unsupported_platform": 2,
    "resolve_failed": 3,
    "parse_failed": 3,
    "download_failed": 4,
    "missing_dependency": 6,
}

MIN_PYTHON = (3, 8)


def _module_version(name: str) -> tuple[bool, str | None]:
    """只读探测：模块能否导入、版本号是什么。"""
    try:
        module = __import__(name)
    except Exception:
        return False, None
    return True, getattr(module, "__version__", None)


def check_deps() -> dict:
    """输出运行依赖与外部服务声明，供 --check-deps 使用。"""
    version = ".".join(str(x) for x in sys.version_info[:3])
    py_ok = sys.version_info >= MIN_PYTHON
    cv2_ok, cv2_version = _module_version("cv2")
    return {
        "ok": py_ok,
        "python": version,
        "platform": sys.platform,
        "dependencies": [
            {
                "name": "Python >= 3.8",
                "type": "required",
                "available": py_ok,
                "version": version,
                "feature": "运行全部脚本",
                "install": "安装 Python 3.8 或更高版本",
                "required": True,
            },
            {
                "name": "opencv-python-headless",
                "type": "optional",
                "available": cv2_ok,
                "version": cv2_version,
                "feature": "--frame 抽帧验水印",
                "install": "pip install opencv-python-headless",
                "required": False,
            },
        ],
        "services": [
            {
                "name": "api.xingzhige.com",
                "type": "service",
                "available": None,
                "checked": False,
                "data_sent": "抖音分享链接或 aweme_id",
                "auth": False,
                "required_for": "抖音解析",
            },
            {
                "name": "api.bilibili.com",
                "type": "service",
                "available": None,
                "checked": False,
                "data_sent": "BV/av 号、cid",
                "auth": False,
                "required_for": "B站解析",
            },
        ],
    }


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


def classify(raw: str) -> tuple[str, str]:
    """返回 (平台 key, 用于解析的 URL 或 ID)。"""
    for key, info in PLATFORMS.items():
        module = info["module"]
        if module is not None and module.looks_like_id(raw):
            return key, raw
    url = extract_url(raw)
    return detect_platform(url), url


def run(args: argparse.Namespace) -> dict:
    if sys.version_info < MIN_PYTHON:
        raise SkillError(
            "missing_dependency",
            f"需要 Python 3.8 或更高版本，当前为 {sys.version.split()[0]}",
            "升级 Python 后重试；核心路径不使用第三方运行时依赖。",
        )
    if args.frame:
        cv2_ok, _ = _module_version("cv2")
        if not cv2_ok:
            print(
                "[warn] 未安装 cv2，--frame 抽帧将被跳过；"
                "需要时执行 pip install opencv-python-headless",
                file=sys.stderr,
            )

    platform, source = classify(args.input.strip())
    info = PLATFORMS[platform]
    module = info["module"]
    if module is None:
        raise SkillError(
            "unsupported_platform",
            f"{info['name']} 暂不支持",
            f"当前仅支持：{SUPPORTED_TABLE}。",
        )

    ref_id = module.resolve_id(source)
    print(f"[info] platform={platform} video_id={ref_id}", file=sys.stderr)
    meta = module.fetch(
        ref_id,
        source_url=source if "://" in source else None,
        page=args.page,
    )

    urls = [item for item in (meta.get("urls") or []) if item.get("url")]
    result = {
        "ok": True,
        "platform": platform,
        "platform_name": info["name"],
        "video_id": meta.get("video_id"),
        "title": meta.get("title") or "",
        "author": meta.get("author") or "",
        "source_api": meta.get("source_api"),
        "video_url": urls[0]["url"] if urls else None,
        "segment_count": len(urls) or None,
        "quality": meta.get("quality"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "fps": meta.get("fps"),
        "duration": meta.get("duration"),
        "size_reported": meta.get("size_reported"),
        "path": None,
        "size_bytes": None,
        "frame": None,  # 兼容旧输出字段，恒为 None；实际帧在 frames 列表里
        "warnings": [],
    }
    result.update(meta.get("extra") or {})

    if args.page is not None and platform != "bilibili":
        result["warnings"].append("--page 仅对 B站分P有效，已忽略")

    if args.check:
        if args.frame:
            result["warnings"].append("--check 模式下忽略 --frame")
        return result

    if not urls:
        raise SkillError(
            "parse_failed",
            "解析结果中没有视频地址",
            "接口可能返回了异常数据，请重试或换一条链接。",
        )

    out_root = Path(args.out).expanduser() if args.out else default_out_dir()
    out_dir = (
        out_root
        if args.flat
        else out_root / f"{platform}_{meta.get('out_id') or meta.get('video_id')}"
    )
    stem = build_stem(
        meta.get("author") or "",
        meta.get("filename_title") or meta.get("title") or "",
        meta.get("video_id") or "",
    )
    headers = meta.get("headers") or {}

    total = 0
    paths: list[str] = []
    for idx, item in enumerate(urls):
        suffix = "" if len(urls) == 1 else f".part{idx + 1}"
        dest = out_dir / f"{stem}{suffix}.{item.get('ext') or 'mp4'}"
        try:
            total += download(
                item["url"], dest, headers=headers, timeout=args.timeout
            )
        except SkillError:
            backup = meta.get("backup_url")
            if idx == 0 and backup and backup != item["url"]:
                print("[warn] 主线路失败，改用备用线路", file=sys.stderr)
                total += download(
                    backup, dest, headers=headers, timeout=args.timeout
                )
            else:
                raise
        paths.append(str(dest))

    result["path"] = paths[0]
    if len(paths) > 1:
        result["paths"] = paths
    result["size_bytes"] = total
    result["size_mb"] = round(total / 1048576, 2)

    reported = as_int(meta.get("size_reported"))
    if reported and abs(total - reported) > max(1024, reported * 0.01):
        result["warnings"].append(
            f"文件大小与接口上报不一致：{total} vs {reported}"
        )

    if args.frame:
        if len(paths) > 1:
            result["warnings"].append("分段视频暂不支持抽帧，已跳过")
        else:
            for sec in args.frame:
                frame_path = out_dir / (
                    f"frame_{safe_name(str(meta.get('video_id')), 40)}_"
                    f"{str(sec).replace('.', 'p')}s.jpg"
                )
                saved = grab_frame(Path(paths[0]), sec, frame_path)
                if saved:
                    result.setdefault("frames", []).append(
                        {"sec": sec, "path": saved}
                    )
                else:
                    result["warnings"].append(
                        "抽帧失败：未安装 cv2 或该时间点无画面"
                    )
                    break

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="下载抖音 / B站视频")
    ap.add_argument(
        "input",
        nargs="?",
        help="分享链接、分享口令整段文字、BV/av 号，或抖音纯数字 aweme_id",
    )
    ap.add_argument(
        "--check-deps",
        action="store_true",
        help="只检查运行依赖与外部服务声明，输出 JSON，不解析视频",
    )
    ap.add_argument("--out", help="保存目录，默认 ~/Downloads")
    ap.add_argument(
        "--flat", action="store_true", help="直接存到 --out，不再建 <平台>_<id> 子目录"
    )
    ap.add_argument("--check", action="store_true", help="只解析元数据，不下载")
    ap.add_argument(
        "--page",
        type=int,
        help="B站分P序号，默认取链接里的 p 参数或第 1 P",
    )
    ap.add_argument(
        "--frame",
        type=float,
        action="append",
        metavar="SEC",
        help="抽取指定秒数的画面用于人工查水印，可重复",
    )
    ap.add_argument("--timeout", type=int, default=300, help="下载超时秒数，默认 300")
    ap.add_argument("--pretty", action="store_true", help="JSON 缩进输出")
    args = ap.parse_args()

    try:
        if args.check_deps:
            report = check_deps()
            emit(report, args.pretty)
            return 0 if report["ok"] else EXIT_CODES["missing_dependency"]
        if not args.input:
            raise SkillError(
                "no_url",
                "缺少输入",
                "需要视频分享链接、BV/av 号或抖音 aweme_id；"
                "也可以用 --check-deps 检查运行依赖。",
            )
        emit(run(args), args.pretty)
        return 0
    except SkillError as exc:
        emit(
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "hint": exc.hint,
                },
            },
            args.pretty,
        )
        return EXIT_CODES.get(exc.code, 1)
    except KeyboardInterrupt:
        emit(
            {
                "ok": False,
                "error": {"code": "interrupted", "message": "用户中断", "hint": ""},
            },
            args.pretty,
        )
        return 130
    except Exception as exc:  # noqa: BLE001 - 保证 stdout 始终有 JSON
        traceback.print_exc()
        emit(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": f"{type(exc).__name__}: {exc}",
                    "hint": "脚本内部错误，请附上输入和错误信息反馈。",
                },
            },
            args.pretty,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
