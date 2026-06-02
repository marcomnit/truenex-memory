#!/usr/bin/env python3
"""Build release artifacts (sdist + wheel), compute SHA-256 hashes, and publish a GitHub Release.

Usage:
    python scripts/build_release.py

Requires:
    - git
    - gh (GitHub CLI) authenticated
    - python -m build
    - pytest

The script will:
  1. Run tests
  2. Build sdist + wheel
  3. Compute SHA-256 hashes
  4. Create a git tag v{version} (if missing)
  5. Create a GitHub Release with artifacts attached
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check)


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


def extract_changelog_notes(root: Path, version: str) -> str:
    """Extract the notes for *version* from CHANGELOG.md."""
    path = root / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    # Match ## [VERSION] — optional date, then capture everything until next ## [ or EOF
    pattern = rf"## \[{re.escape(version)}\].*?\n(.*?)(?=\n## \[|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def tag_exists_locally(tag: str, cwd: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", tag],
        cwd=cwd,
        capture_output=True,
    )
    return result.returncode == 0


def tag_exists_on_remote(tag: str, cwd: Path) -> bool:
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "origin", tag],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return tag in result.stdout


def gh_available() -> bool:
    return shutil.which("gh") is not None


def is_prerelease(version: str) -> bool:
    return any(k in version.lower() for k in ("alpha", "beta", "a", "b", "rc"))


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    dist_dir = root / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    version = read_version(root / "pyproject.toml")
    tag = f"v{version}"

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

    # ── Git tag ───────────────────────────────────────────────────────────────
    if not tag_exists_locally(tag, root):
        print(f"\n[INFO] Creating local git tag {tag}")
        run(["git", "tag", tag], cwd=root)
    else:
        print(f"\n[INFO] Local git tag {tag} already exists")

    if not tag_exists_on_remote(tag, root):
        print(f"[INFO] Pushing tag {tag} to origin")
        run(["git", "push", "origin", tag], cwd=root)
    else:
        print(f"[INFO] Remote tag {tag} already exists")

    # ── GitHub Release ────────────────────────────────────────────────────────
    if not gh_available():
        print("[WARN] gh (GitHub CLI) not found. Skipping GitHub Release creation.")
        print("       Install from: https://cli.github.com/")
        return 0

    notes = extract_changelog_notes(root, version)
    if not notes:
        notes = f"Release {version}"

    prerelease_flag = ["--prerelease"] if is_prerelease(version) else []

    # Collect artifact paths (skip release-info.json — it's for our records)
    asset_paths = [str(a) for a in artifacts if a.name != "release-info.json"]

    print(f"\n[INFO] Creating GitHub Release {tag}")
    cmd = [
        "gh", "release", "create", tag,
        "--title", f"{tag}",
        "--notes", notes,
        *prerelease_flag,
        *asset_paths,
    ]
    run(cmd, cwd=root)

    print(f"\n[OK] Release {tag} published on GitHub.")
    print(f"     URL: https://github.com/marcomnit/truenex-memory/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
