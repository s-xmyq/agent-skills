#!/usr/bin/env bash
# Offline smoke test: compile every script, run the unit tests, check versions.
# Network tests are skipped unless AGENT_SKILLS_NETWORK=1.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"

"$PYTHON" -m compileall -q skills scripts tests
"$PYTHON" -m unittest discover -s tests -v
"$PYTHON" scripts/check_version.py
