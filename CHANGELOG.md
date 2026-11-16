# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `docs/skill-authoring.md`: repository-wide skill authoring standard covering
  self-containment, dependency tiers (required / optional / external service),
  consent before installation, `doctor` output, exit codes, and test requirements.
- `video-download --check-deps`: read-only JSON report of Python/OpenCV
  availability and declared external services. Missing required dependencies now
  exit with code `6`.
- `skills/video-download/scripts/requirements-optional.txt` for the optional
  OpenCV frame extraction.

### Changed

- Tests are now version-controlled under `tests/` instead of the gitignored
  `.test/`; CI runs the offline suite on Python 3.8 and 3.12. Network tests are
  opt-in via `AGENT_SKILLS_NETWORK=1`.
- `video-download` SKILL.md documents its dependencies, degraded behavior, and
  the new exit code.
- `web-research` SKILL.md now has the standard dependency table and points at
  `doctor` for self-checks.
- `tests/smoke.sh` now also runs `scripts/check_version.py`, so manifest and
  version consistency are covered by the smoke test.

### Fixed

- `web-research`'s fallback HTML extractor cleared the block tag before storing
  it, so headings, list items, and code blocks lost their Markdown formatting.
- `web-research`: an explicitly selected SearXNG backend with no configured URL
  now fails with a clear error instead of silently querying DuckDuckGo while
  reporting `backend: searxng`.
- `video-download`: parser-supplied download URLs are now resolved and rejected
  when any address is private, loopback, or link-local, and every redirect hop is
  re-validated. Rejected redirects also clean up their temporary `.part` file.

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
