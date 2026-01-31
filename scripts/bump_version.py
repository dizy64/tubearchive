#!/usr/bin/env python3
"""Bump project version in pyproject.toml and tubearchive/__init__.py."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT_FILE = ROOT / "tubearchive" / "__init__.py"

VERSION_RE = re.compile(r'^(?P<prefix>\s*version\s*=\s*")(?P<ver>[^"]+)(".*)$')
INIT_RE = re.compile(r'^(?P<prefix>\s*__version__\s*=\s*")(?P<ver>[^"]+)(".*)$')
SEMVER_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bump project version.")
    parser.add_argument(
        "--part",
        choices=["major", "minor", "patch"],
        default="patch",
        help="Version part to increment (default: patch).",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Run `uv build` after updating the version.",
    )
    parser.add_argument(
        "--branch",
        action="append",
        default=["main", "master"],
        help="Allowed branches for bump (default: main, master).",
    )
    parser.add_argument(
        "--no-branch-check",
        action="store_true",
        help="Skip git branch check.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the next version without modifying files.",
    )
    return parser.parse_args()


def get_current_branch() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def read_version_from_file(path: Path, pattern: re.Pattern[str]) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        match = pattern.match(line)
        if match:
            return match.group("ver")
    raise RuntimeError(f"Version not found in {path}")


def bump_version(version: str, part: str) -> str:
    match = SEMVER_RE.match(version)
    if not match:
        raise ValueError(f"Unsupported version format: {version}")

    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))

    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def replace_version(path: Path, pattern: re.Pattern[str], new_version: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        match = pattern.match(line)
        if match:
            updated.append(f"{match.group('prefix')}{new_version}{match.group(3)}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        raise RuntimeError(f"Version not updated in {path}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()

    if not args.no_branch_check:
        branch = get_current_branch()
        if branch is None:
            print("Failed to detect git branch. Use --no-branch-check to override.", file=sys.stderr)
            return 2
        allowed = {b.strip() for b in args.branch if b.strip()}
        if branch not in allowed:
            print(
                f"Current branch '{branch}' is not allowed. Allowed: {', '.join(sorted(allowed))}",
                file=sys.stderr,
            )
            return 2

    pyproject_version = read_version_from_file(PYPROJECT, VERSION_RE)
    init_version = read_version_from_file(INIT_FILE, INIT_RE)
    if pyproject_version != init_version:
        raise RuntimeError(
            f"Version mismatch: pyproject.toml={pyproject_version}, __init__.py={init_version}"
        )

    next_version = bump_version(pyproject_version, args.part)

    if args.dry_run:
        print(next_version)
        return 0

    replace_version(PYPROJECT, VERSION_RE, next_version)
    replace_version(INIT_FILE, INIT_RE, next_version)
    print(f"Bumped version: {pyproject_version} -> {next_version}")

    if args.build:
        subprocess.run(["uv", "build"], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
