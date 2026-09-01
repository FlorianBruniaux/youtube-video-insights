#!/usr/bin/env python3
"""Fail when the committed Astro build is not reproducible or mutates sources."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = REPOSITORY_ROOT / "src" / "yt_insights" / "web" / "static"
WEB_ROOT = REPOSITORY_ROOT / "web"
SOURCE_INPUTS = (
    WEB_ROOT / "src",
    WEB_ROOT / "public",
    WEB_ROOT / "astro.config.mjs",
    WEB_ROOT / "package.json",
    WEB_ROOT / "pnpm-lock.yaml",
    WEB_ROOT / "pnpm-workspace.yaml",
    WEB_ROOT / "tsconfig.json",
)


def snapshot_tree(root: Path, *, relative_to: Path) -> dict[str, str]:
    """Return a stable SHA-256 inventory whose keys never expose absolute paths."""
    if not root.exists():
        return {}
    paths = (root,) if root.is_file() else tuple(root.rglob("*"))
    return {
        path.relative_to(relative_to).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(paths)
        if path.is_file() and not path.is_symlink()
    }


def snapshot_inputs() -> dict[str, str]:
    inventory: dict[str, str] = {}
    for root in SOURCE_INPUTS:
        inventory.update(snapshot_tree(root, relative_to=REPOSITORY_ROOT))
    return inventory


def changed_paths(before: dict[str, str], after: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            path
            for path in before.keys() | after.keys()
            if before.get(path) != after.get(path)
        )
    )


def main() -> int:
    before_static = snapshot_tree(STATIC_ROOT, relative_to=REPOSITORY_ROOT)
    before_inputs = snapshot_inputs()
    environment = os.environ.copy()
    environment["ASTRO_TELEMETRY_DISABLED"] = "1"
    result = subprocess.run(
        ["pnpm", "--dir", "web", "build"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print("web build failed; run `pnpm --dir web build` for diagnostics", file=sys.stderr)
        return 2
    after_static = snapshot_tree(STATIC_ROOT, relative_to=REPOSITORY_ROOT)
    after_inputs = snapshot_inputs()
    source_changes = changed_paths(before_inputs, after_inputs)
    build_changes = changed_paths(before_static, after_static)
    if source_changes:
        print("web build mutated source inputs:", file=sys.stderr)
        for path in source_changes:
            print(path, file=sys.stderr)
    if build_changes:
        print("committed web build is not reproducible:", file=sys.stderr)
        for path in build_changes:
            print(path, file=sys.stderr)
    if source_changes or build_changes:
        return 1
    print(f"web build verified: {len(after_static)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
