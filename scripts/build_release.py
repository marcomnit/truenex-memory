#!/usr/bin/env python3
"""Build release artifacts (sdist + wheel) and compute SHA-256 hashes.

Usage:
    python scripts/build_release.py
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def read_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError("version not found in pyproject.toml")
    return match.group(1)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    dist_dir = root / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    version = read_version(root / "pyproject.toml")

    # Run tests
    run([sys.executable, "-m", "pytest", "tests/", "-x", "--ignore=tests/e2e"], cwd=root)

    # Build
    run([sys.executable, "-m", "build"], cwd=root)

    artifacts = sorted(dist_dir.iterdir())
    if not artifacts:
        print("[ERR] No artifacts found in dist/")
        return 1

    hashes: dict[str, str] = {}
    for artifact in artifacts:
        hashes[artifact.name] = sha256_file(artifact)
        print(f"  SHA-256 {artifact.name}: {hashes[artifact.name]}")

    release_info = {
        "version": version,
        "artifacts": hashes,
    }
    info_path = dist_dir / "release-info.json"
    info_path.write_text(json.dumps(release_info, indent=2) + "\n", encoding="utf-8")
    print(f"  [OK] {info_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
