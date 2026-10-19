#!/usr/bin/env bash
# Smoke test for the agent-skills repository.
#
#   ./tests/smoke.sh            offline checks only (default)
#   AGENT_SKILLS_NETWORK=1 ./tests/smoke.sh   also probe the live network
#
# Exits non-zero on the first failure.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHON_BIN=python3
fi

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

echo "== Python =="
"$PYTHON_BIN" -c 'import sys; print(sys.version.split()[0], sys.executable)'

echo "== Compile =="
"$PYTHON_BIN" -m compileall -q skills tests scripts || fail "compileall"

echo "== Unit tests (offline) =="
"$PYTHON_BIN" -m unittest discover -s tests -v || fail "unit tests"

echo "== Version consistency =="
"$PYTHON_BIN" scripts/check_version.py || fail "version check"

if [[ "${AGENT_SKILLS_NETWORK:-0}" == "1" ]]; then
    echo "== Live network probe =="
    WEB="$ROOT/skills/web-research/scripts/web.py"
    "$PYTHON_BIN" "$WEB" doctor | "$PYTHON_BIN" -c '
import json, sys
report = json.load(sys.stdin)
print("proxy mode:", report["checks"]["proxy"]["mode"])
print("backends:", report["backend_chain"])
if not report["ok"]:
    sys.exit("no search backend reachable")
' || fail "doctor"
fi

echo "OK"
