#!/usr/bin/env bash
# Offline smoke test: compile every script, then run the unit tests.
# Network tests are skipped unless AGENT_SKILLS_NETWORK=1.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"

"$PYTHON" -m compileall -q skills scripts tests
"$PYTHON" -m unittest discover -s tests -v
