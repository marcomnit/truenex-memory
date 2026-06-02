"""Self-upgrade command for Truenex Memory."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys


def detect_install_method() -> str | None:
    """Detect whether truenex-memory was installed via pipx or pip.

    Returns "pipx" or "pip", or None if detection fails.
    """
    # pipx metadata lives at the venv root
    try:
        import truenex_memory  # noqa: F811
    except ImportError:
        return None

    # Check for pipx CLI + pipx list
    if shutil.which("pipx"):
        try:
            result = subprocess.run(
                ["pipx", "list", "--json"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if "truenex-memory" in data.get("venvs", {}):
                    return "pipx"
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass

    return "pip"


def run_self_update() -> int:
    """Run the appropriate upgrade command. Returns exit code."""
    method = detect_install_method()

    if method == "pipx":
        cmd = ["pipx", "upgrade", "truenex-memory"]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "truenex-memory"]

    print(f"Upgrading truenex-memory via {method}...")
    print(f"$ {' '.join(cmd)}")
    sys.stdout.flush()
    return subprocess.call(cmd)
