"""Self-upgrade command for Truenex Memory."""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


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


def _running_script_path() -> Path | None:
    """Best-effort path to the truenex-mem launcher stub currently executing.

    On Windows, pip cannot overwrite this file in place while the running
    process still has it mapped as its own executable image (WinError 32),
    even though no *other* process is holding it open.
    """
    candidate = shutil.which("truenex-mem")
    if candidate:
        return Path(candidate)
    argv0 = Path(sys.argv[0]) if sys.argv else None
    if argv0 and argv0.suffix.lower() == ".exe" and argv0.exists():
        return argv0
    return None


def _cleanup_stale_renames(script_path: Path) -> None:
    """Delete leftover *.old-<pid> files from previous self-update runs.

    These become deletable once the process that had them locked exits,
    so each new run sweeps up after the last one.
    """
    for stale in glob.glob(f"{script_path}{'.old-*'}"):
        try:
            os.remove(stale)
        except OSError:
            pass


def _unlock_self_for_overwrite(method: str) -> tuple[Path | None, Path | None]:
    """Rename the running exe out of the way so pip can write the new one.

    Windows allows renaming a running executable's file even though it
    disallows overwriting it in place. Returns (original_path, renamed_path)
    so the caller can restore on failure or discard on success.
    """
    if method != "pip" or sys.platform != "win32":
        return None, None
    script_path = _running_script_path()
    if script_path is None or not script_path.exists():
        return None, None
    _cleanup_stale_renames(script_path)
    renamed = script_path.with_name(f"{script_path.name}.old-{os.getpid()}")
    try:
        os.replace(script_path, renamed)
    except OSError:
        return None, None
    return script_path, renamed


def run_self_update() -> int:
    """Run the appropriate upgrade command. Returns exit code."""
    method = detect_install_method()

    if method == "pipx":
        cmd = ["pipx", "upgrade", "truenex-memory"]
    else:
        # --no-cache-dir: pip's local HTTP cache can keep serving a stale
        # index response right after a fresh PyPI release, making this
        # command report "already satisfied" on the old version.
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--no-cache-dir",
            "truenex-memory",
        ]

    original_path, renamed_path = _unlock_self_for_overwrite(method)

    print(f"Upgrading truenex-memory via {method}...")
    print(f"$ {' '.join(cmd)}")
    sys.stdout.flush()
    code = subprocess.call(cmd)
    if code != 0 and sys.platform == "win32":
        print("⚠️  First attempt failed. Trying force-reinstall...")
        sys.stdout.flush()
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "--no-cache-dir",
            "truenex-memory",
        ]
        print(f"$ {' '.join(cmd)}")
        sys.stdout.flush()
        code = subprocess.call(cmd)

    if code == 0:
        # New exe is in place; drop our renamed backup of the old one.
        if renamed_path is not None:
            try:
                os.remove(renamed_path)
            except OSError:
                pass
    elif original_path is not None and renamed_path is not None and renamed_path.exists():
        # pip failed outright (e.g. no network) - restore so the command
        # keeps working rather than leaving the user without truenex-mem.
        try:
            os.replace(renamed_path, original_path)
        except OSError:
            pass

    if code != 0 and sys.platform == "win32" and renamed_path is None:
        # Could not free the path ourselves (e.g. detection failed) - fall
        # back to a detached retry after this process exits.
        bat_path = Path(tempfile.gettempdir()) / "truenex_memory_update.bat"
        bat_content = (
            "@echo off\n"
            "timeout /t 2 /nobreak >nul\n"
            f'"{sys.executable}" -m pip install --upgrade --force-reinstall truenex-memory\n'
        )
        bat_path.write_text(bat_content, encoding="utf-8")
        subprocess.Popen(
            [str(bat_path)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Please close this terminal and reopen it in a few seconds.")
        return 0
    return code
