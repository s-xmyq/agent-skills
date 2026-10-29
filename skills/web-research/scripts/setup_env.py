#!/usr/bin/env python3
"""Create the optional skill-local environment for web-research.

The skill itself is stdlib-only. This script installs trafilatura into
``<skill>/.venv`` so the host Python is never modified. It prefers ``uv``
(fast; can fetch a standalone Python 3.12) and falls back to
``python -m venv`` when a Python >= 3.10 is available.

Usage:
    python scripts/setup_env.py            # create/verify .venv
    python scripts/setup_env.py --check    # report only, do not install
    python scripts/setup_env.py --recreate # delete and rebuild .venv
    python scripts/setup_env.py --no-uv    # force the venv+pip path
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = SKILL_ROOT / ".venv"
REQUIREMENTS = Path(__file__).resolve().with_name("requirements-optional.txt")
MIN_PYTHON = (3, 10)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    print("[setup] " + " ".join(cmd), file=sys.stderr)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _version_tuple(text: str) -> tuple[int, int, int] | None:
    for token in text.replace("\n", " ").split():
        token = token.strip("v")
        parts = token.split(".")
        if len(parts) >= 2 and all(part.isdigit() for part in parts[:3]):
            numbers = [int(part) for part in parts[:3]]
            while len(numbers) < 3:
                numbers.append(0)
            return numbers[0], numbers[1], numbers[2]
    return None


def _python_version(cmd: list[str]) -> tuple[int, int, int] | None:
    try:
        proc = subprocess.run(
            cmd + ["--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return _version_tuple((proc.stdout or "") + " " + (proc.stderr or ""))


def find_python() -> list[str] | None:
    candidates: list[list[str]] = []
    for name in ("python", "python3", "python3.13", "python3.12", "python3.11", "python3.10"):
        found = shutil.which(name)
        if found:
            candidates.append([found])
    py = shutil.which("py")
    if py:
        candidates.append([py, "-3"])
    for candidate in candidates:
        version = _python_version(candidate)
        if version and version[:2] >= MIN_PYTHON:
            print(
                "[setup] base interpreter: {} ({}.{}.{})".format(" ".join(candidate), *version),
                file=sys.stderr,
            )
            return candidate
    return None


def trafilatura_version(executable: Path | None = None) -> str | None:
    exe = executable or venv_python()
    if not exe.exists():
        return None
    try:
        proc = subprocess.run(
            [str(exe), "-c", "import trafilatura; print(trafilatura.__version__)"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except Exception:
        return None
    if proc.returncode == 0:
        return (proc.stdout or "").strip() or "installed"
    return None


def _python_version_text(executable: Path) -> str | None:
    version = _python_version([str(executable)])
    if not version:
        return None
    return "{}.{}.{}".format(*version)


def report(ok: bool, **fields: object) -> None:
    payload = {"ok": ok}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _remove_venv() -> None:
    if VENV_DIR.exists():
        print("[setup] removing {}".format(VENV_DIR), file=sys.stderr)
        shutil.rmtree(VENV_DIR)


def _install_with_uv() -> str | None:
    uv = shutil.which("uv")
    if not uv:
        return None
    if VENV_DIR.exists():
        proc = _run([uv, "venv", "--clear", str(VENV_DIR), "--python", "3.12"])
        if proc.returncode != 0:
            # Older uv releases may not support --clear.
            _remove_venv()
            proc = _run([uv, "venv", str(VENV_DIR), "--python", "3.12"])
    else:
        proc = _run([uv, "venv", str(VENV_DIR), "--python", "3.12"])
    if proc.returncode != 0:
        print((proc.stderr or "").strip()[-500:], file=sys.stderr)
        return None

    proc = _run(
        [uv, "pip", "install", "--python", str(venv_python()), "-r", str(REQUIREMENTS)]
    )
    if proc.returncode != 0:
        print((proc.stderr or "").strip()[-1000:], file=sys.stderr)
        return None
    return trafilatura_version()


def _install_with_pip() -> str | None:
    base = find_python()
    if base is None:
        return None
    if VENV_DIR.exists():
        _remove_venv()
    proc = _run(base + ["-m", "venv", str(VENV_DIR)])
    if proc.returncode != 0:
        print((proc.stderr or "").strip()[-500:], file=sys.stderr)
        return None
    proc = _run(
        [
            str(venv_python()),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(REQUIREMENTS),
        ]
    )
    if proc.returncode != 0:
        print((proc.stderr or "").strip()[-1000:], file=sys.stderr)
        return None
    return trafilatura_version()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the optional web-research venv.")
    parser.add_argument("--check", action="store_true", help="report status only")
    parser.add_argument("--recreate", action="store_true", help="delete and rebuild .venv")
    parser.add_argument("--no-uv", action="store_true", help="skip uv; use python -m venv")
    args = parser.parse_args(argv)

    if not REQUIREMENTS.exists():
        report(False, reason="missing requirements file", path=str(REQUIREMENTS))
        return 2

    installed = trafilatura_version()
    if args.check:
        if installed:
            report(
                True,
                venv=str(VENV_DIR),
                python=str(venv_python()),
                python_version=_python_version_text(venv_python()),
                trafilatura=installed,
                message="optional enhancement already installed",
            )
            return 0
        report(
            False,
            venv=str(VENV_DIR),
            python=str(venv_python()),
            reason="trafilatura is not installed in the skill-local environment",
            hint="run: python scripts/setup_env.py",
        )
        return 1

    if args.recreate:
        _remove_venv()

    installed = trafilatura_version()
    if installed:
        report(
            True,
            venv=str(VENV_DIR),
            python=str(venv_python()),
            python_version=_python_version_text(venv_python()),
            trafilatura=installed,
            message="optional enhancement already installed",
        )
        return 0

    installer = None
    if not args.no_uv:
        installed = _install_with_uv()
        if installed:
            installer = "uv"
    if not installed:
        installed = _install_with_pip()
        if installed:
            installer = "pip"

    if not installed:
        report(
            False,
            venv=str(VENV_DIR),
            reason="could not create the optional environment",
            hint="install uv, or use Python 3.10+ so `python -m venv` can run",
            fallback="web.py will use its built-in extractor",
        )
        return 2

    report(
        True,
        venv=str(VENV_DIR),
        python=str(venv_python()),
        python_version=_python_version_text(venv_python()),
        trafilatura=installed,
        installer=installer,
        message="optional enhancement ready; web.py will use it automatically",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
