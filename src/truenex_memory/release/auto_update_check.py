"""Auto-check for new versions at CLI startup.

Queries PyPI once every 24 hours, caches the last-check timestamp,
and prints a non-blocking warning to stderr when a newer version exists.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from truenex_memory.release.update_check import compare_versions

_CHECK_INTERVAL_S = 86400  # 24 hours
_PYPI_URL = "https://pypi.org/pypi/truenex-memory/json"
_REQUEST_TIMEOUT_S = 2


def _cache_path() -> Path:
    return Path.home() / ".truenex-memory" / ".update_cache.json"


def _read_cache() -> dict[str, object]:
    path = _cache_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_cache(data: dict[str, object]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def check_and_notify(current_version: str) -> None:
    """Check PyPI for a newer version; print warning to stderr if found.

    Only contacts PyPI if 24h have passed since the last check.
    Always catches exceptions silently so the CLI is never blocked.
    """
    cache = _read_cache()
    last_check = float(cache.get("last_check", 0))

    if time.time() - last_check < _CHECK_INTERVAL_S:
        return

    cache["last_check"] = time.time()
    _write_cache(cache)

    try:
        req = Request(
            _PYPI_URL,
            headers={"User-Agent": "truenex-memory-auto-check"},
        )
        with urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = str(data.get("info", {}).get("version", ""))
        if latest and compare_versions(latest, current_version) > 0:
            _print_update_notice(latest)
    except Exception:
        pass


def _print_update_notice(latest: str) -> None:
    notice = f"New version {latest} available. Run: truenex-mem update self"
    try:
        if hasattr(sys.stderr, "isatty") and sys.stderr.isatty():
            # bold yellow
            sys.stderr.write(f"\033[1;33m{notice}\033[0m\n")
            sys.stderr.flush()
            return
    except Exception:
        pass
    print(notice, file=sys.stderr)
