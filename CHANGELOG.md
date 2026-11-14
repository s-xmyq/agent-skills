# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-11-15

### Added

- `video-download` now supports Bilibili: `b23.tv` / `bili2233.cn` short links,
  `bilibili.com/video` links, BV and av IDs, and multi-part selection via `--page`.
  Uses the official web API; unauthenticated downloads are capped at 720P
  (combined stream, no ffmpeg needed).
- JSON output now includes a generic `video_id`, plus Bilibili-specific `bvid`,
  `aid`, `cid`, `page`, `page_title`, and `part_count`; `aweme_id` is unchanged
  for Douyin.
- Multi-segment videos are saved as `.partN` files and reported via `paths`;
  multi-part Bilibili filenames include the `P` number to avoid overwrites.

### Changed

- Split the skill into platform modules (`douyin.py`, `bilibili.py`) and shared
  code (`_common.py`); `fetch_video.py` remains the single CLI entry.
- `--frame` is skipped with a warning for multi-segment videos and in `--check`
  mode; `--page` is ignored with a warning on non-Bilibili platforms.
- Local tests moved to `.test/` and excluded from git; CI now only validates
  manifest/version consistency.
- Download URLs returned by parsers are restricted to http(s) and reject
  loopback/private-IP literals before fetching.
- Fixed the stdout contract wording: default output is one JSON line; `--pretty`
  indents it.

## [0.2.0] - 2026-11-04

### Added

- `video-download` skill: resolve Douyin share links, download the highest-quality
  watermark-free video, optionally extract frames for manual watermark checks, and
  emit JSON. Standard library only; `--frame` optionally uses OpenCV.

### Fixed

- The Douyin parser no longer calls the same endpoint twice when the original and
  canonical video URLs are identical.

## [0.1.1] - 2026-10-19

### Changed

- `web-research` docs now reference `scripts/web.py` relative to the skill root,
  with no host-specific path or environment variable, so the same copy works
  unchanged in Claude Code, Codex, Cursor, and other skill hosts.

## [0.1.0] - 2026-10-19

### Added

- Repository skeleton: plugin manifest, self-hosted marketplace, CI, smoke tests.
- `web-research` skill: live web search (SearXNG, DuckDuckGo, Bing) and readable
  content extraction, standard library only.
- Requests bypass the system proxy by default (`CLAUDE_WEB_SYSTEM_PROXY=1` opts
  back in), avoiding multi-second delays from registry-proxy auto-detection.
- `doctor` subcommand reports backend reachability, extraction quality, cache
  location, and the active proxy mode.

[Unreleased]: https://github.com/s-xmyq/agent-skills/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/s-xmyq/agent-skills/releases/tag/v0.3.0
[0.2.0]: https://github.com/s-xmyq/agent-skills/releases/tag/v0.2.0
[0.1.1]: https://github.com/s-xmyq/agent-skills/releases/tag/v0.1.1
[0.1.0]: https://github.com/s-xmyq/agent-skills/releases/tag/v0.1.0
