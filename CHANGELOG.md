# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/s-xmyq/agent-skills/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/s-xmyq/agent-skills/releases/tag/v0.1.1
[0.1.0]: https://github.com/s-xmyq/agent-skills/releases/tag/v0.1.0
