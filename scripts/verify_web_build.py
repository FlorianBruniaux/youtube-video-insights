#!/usr/bin/env python3
"""Fail when the committed Astro build is not reproducible or mutates sources."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = REPOSITORY_ROOT / "src" / "yt_insights" / "web" / "static"
BuildRunner = Callable[[Path], subprocess.CompletedProcess[object]]


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


def inventory_diff(
    committed: dict[str, str], built: dict[str, str]
) -> dict[str, tuple[str, ...]]:
    """Classify every relative output difference without exposing host paths."""
    return {
        "added": tuple(sorted(built.keys() - committed.keys())),
        "deleted": tuple(sorted(committed.keys() - built.keys())),
        "modified": tuple(
            sorted(
                path
                for path in committed.keys() & built.keys()
                if committed[path] != built[path]
            )
        ),
    }


def _run_astro_build(
    repository_root: Path, output_root: Path
) -> subprocess.CompletedProcess[object]:
    environment = os.environ.copy()
    environment["ASTRO_TELEMETRY_DISABLED"] = "1"
    return subprocess.run(
        [
            "pnpm",
            "--dir",
            "web",
            "exec",
            "astro",
            "build",
            "--outDir",
            str(output_root),
        ],
        cwd=repository_root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def verify_build(
    repository_root: Path = REPOSITORY_ROOT,
    *,
    run_build: BuildRunner | None = None,
) -> int:
    """Build outside the checkout and compare the complete static inventory."""
    static_root = repository_root / "src" / "yt_insights" / "web" / "static"
    committed = snapshot_tree(static_root, relative_to=static_root)
    runner = run_build or (lambda output: _run_astro_build(repository_root, output))
    with tempfile.TemporaryDirectory(prefix="yt-insights-web-build-") as directory:
        output_root = Path(directory) / "static"
        output_root.mkdir()
        result = runner(output_root)
        if result.returncode != 0:
            print(
                "web build failed; run `pnpm --dir web exec astro build` for diagnostics",
                file=sys.stderr,
            )
            return 2
        built = snapshot_tree(output_root, relative_to=output_root)
        differences = inventory_diff(committed, built)
        if any(differences.values()):
            print("committed web build is not reproducible:", file=sys.stderr)
            for category in ("added", "deleted", "modified"):
                for path in differences[category]:
                    print(f"{category}: {path}", file=sys.stderr)
            return 1
        print(f"web build verified: {len(built)} files")
        return 0


def main() -> int:
    return verify_build()


if __name__ == "__main__":
    raise SystemExit(main())
