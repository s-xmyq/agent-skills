#!/usr/bin/env python3
"""Check that plugin.json, marketplace.json and the git tag agree.

Used by CI and by tests/smoke.sh. Exits 1 with a readable message on mismatch.

    python scripts/check_version.py                 # manifest consistency only
    python scripts/check_version.py v0.1.0          # also require tag == version
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"missing file: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"invalid JSON in {path}: {exc}")


def main(argv: list[str]) -> int:
    plugin = load(ROOT / ".claude-plugin" / "plugin.json")
    market = load(ROOT / ".claude-plugin" / "marketplace.json")

    problems: list[str] = []

    version = plugin.get("version", "")
    if not SEMVER.match(version):
        problems.append(
            f"plugin.json version {version!r} is not MAJOR.MINOR.PATCH"
        )

    if plugin.get("name") != market.get("name"):
        problems.append(
            f"name mismatch: plugin.json={plugin.get('name')!r} "
            f"marketplace.json={market.get('name')!r}"
        )

    entries = [p for p in market.get("plugins", []) if p.get("name") == plugin.get("name")]
    if not entries:
        problems.append(
            f"marketplace.json has no entry for plugin {plugin.get('name')!r}"
        )
    else:
        entry_version = entries[0].get("version")
        if entry_version != version:
            problems.append(
                f"version mismatch: plugin.json={version!r} "
                f"marketplace entry={entry_version!r}"
            )

    # Every skills/<name>/SKILL.md must declare the same name as its directory.
    skills_dir = ROOT / "skills"
    if skills_dir.is_dir():
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            head = skill_md.read_text(encoding="utf-8").split("---", 2)
            if len(head) < 3:
                problems.append(f"{skill_md}: missing frontmatter")
                continue
            match = re.search(r"^name:\s*(\S+)\s*$", head[1], re.MULTILINE)
            if not match:
                problems.append(f"{skill_md}: frontmatter has no name")
            elif match.group(1) != skill_md.parent.name:
                problems.append(
                    f"{skill_md}: name {match.group(1)!r} != directory "
                    f"{skill_md.parent.name!r}"
                )

    if len(argv) > 1:
        tag = argv[1]
        expected = f"v{version}"
        if tag != expected:
            problems.append(f"git tag {tag!r} != expected {expected!r}")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    print(f"OK: {plugin['name']} v{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
