#!/usr/bin/env python3
"""Bump version across all project files and prepare a release.

Usage:
    python scripts/bump_version.py 0.1.0-alpha.2
    python scripts/bump_version.py 0.1.0 --channel stable

Next steps after running:
    git add -A
    git commit -m "release: v<version>"
    git tag v<version>
    git push origin main --tags
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path


def bump_init_py(root: Path, new_version: str) -> None:
    path = root / "src" / "truenex_memory" / "__init__.py"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{new_version}"', text)
    path.write_text(text, encoding="utf-8")
    print(f"  [OK] {path}")


def bump_pyproject_toml(root: Path, new_version: str) -> None:
    path = root / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'^version = "[^"]+"', f'version = "{new_version}"', text, flags=re.MULTILINE)
    path.write_text(text, encoding="utf-8")
    print(f"  [OK] {path}")


def bump_version_json(root: Path, new_version: str, channel: str) -> None:
    path = root / "releases" / "version.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = new_version
    data["channel"] = channel
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  [OK] {path}")


def bump_changelog(root: Path, new_version: str) -> None:
    path = root / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    today = date.today().isoformat()

    # Insert new version section below Unreleased
    # Find the first section header after [Unreleased]
    pattern = r"(## \[Unreleased\]\n\n### Added\n\n)(## \[)"
    replacement = (
        f"## [Unreleased]\n\n### Added\n\n"
        f"## [{new_version}] — {today}\n\n### Added\n\n## ["
    )
    if not re.search(pattern, text):
        print(f"  [ERR] {path} — unexpected format, update manually")
        sys.exit(1)
    text = re.sub(pattern, replacement, text, count=1)
    path.write_text(text, encoding="utf-8")
    print(f"  [OK] {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bump Truenex Memory version")
    parser.add_argument("version", help="New version, e.g. 0.1.0-alpha.2")
    parser.add_argument("--channel", default="alpha", help="Release channel (alpha, beta, stable)")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    print(f"Bumping to {args.version} (channel: {args.channel})\n")

    bump_init_py(root, args.version)
    bump_pyproject_toml(root, args.version)
    bump_version_json(root, args.version, args.channel)
    bump_changelog(root, args.version)

    print(f"\nDone. Next steps:")
    print(f"  git add -A")
    print(f'  git commit -m "release: v{args.version}"')
    print(f"  git tag v{args.version}")
    print(f"  git push origin main --tags")
    print(f"\nThen open https://github.com/marcomnit/truenex-memory/releases to publish.")


if __name__ == "__main__":
    main()
