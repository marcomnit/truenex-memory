"""Local diagnostics for Truenex Memory."""

from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from truenex_memory import __version__


def _check_writable(path: Path) -> tuple[bool, str | None]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(prefix=".truenex-memory-", dir=path, delete=True):
            pass
    except OSError as exc:
        return False, str(exc)
    return True, None


def run_diagnostics(base_path: str | Path | None = None) -> dict[str, Any]:
    """Run local diagnostics without contacting external services."""

    target = Path.cwd() if base_path is None else Path(base_path)
    python_supported = sys.version_info >= (3, 12)
    target_exists = target.exists()
    target_is_dir = target.is_dir() if target_exists else False
    writable, writable_error = _check_writable(target) if target_is_dir else (False, "path is not a directory")

    checks: list[dict[str, Any]] = [
        {
            "name": "python_version",
            "ok": python_supported,
            "detail": sys.version.split()[0],
        },
        {
            "name": "package_import",
            "ok": True,
            "detail": f"truenex-memory {__version__}",
        },
        {
            "name": "base_path_exists",
            "ok": target_exists,
            "detail": str(target),
        },
        {
            "name": "base_path_writable",
            "ok": writable,
            "detail": "writable" if writable else writable_error,
        },
    ]

    return {
        "status": "ok" if all(check["ok"] for check in checks) else "warn",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "package_version": __version__,
        "platform": {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "release": platform.release(),
        },
        "base_path": str(target),
        "checks": checks,
    }


__all__ = ["run_diagnostics"]
