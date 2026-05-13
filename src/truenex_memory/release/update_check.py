"""Manual update check support."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable
from urllib.request import Request, urlopen

from truenex_memory.release.manifest import DEFAULT_MANIFEST_URL, ReleaseManifest
from truenex_memory.release.version import APP_VERSION


Fetcher = Callable[[str], dict[str, object]]


@dataclass(frozen=True)
class UpdateCheckResult:
    """Result of a local-first manual update check."""

    current_version: str
    latest_version: str
    update_available: bool
    force_update: bool
    update_full: bool
    requires_migration: bool
    min_supported_version: str
    channel: str
    download_url: str | None
    release_notes_url: str | None
    manifest_url: str

    def to_dict(self) -> dict[str, object]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "force_update": self.force_update,
            "update_full": self.update_full,
            "requires_migration": self.requires_migration,
            "min_supported_version": self.min_supported_version,
            "channel": self.channel,
            "download_url": self.download_url,
            "release_notes_url": self.release_notes_url,
            "manifest_url": self.manifest_url,
        }


def check_for_updates(
    *,
    manifest_url: str = DEFAULT_MANIFEST_URL,
    current_version: str = APP_VERSION,
    fetcher: Fetcher | None = None,
) -> UpdateCheckResult:
    """Fetch a public manifest and compare it to the installed version.

    This sends only an HTTP GET to the manifest URL. It does not send project
    paths, indexed content, memory data, or machine identifiers.
    """

    payload = (fetcher or fetch_manifest)(manifest_url)
    manifest = ReleaseManifest.from_dict(payload)
    update_available = manifest.force_update or compare_versions(manifest.version, current_version) > 0
    return UpdateCheckResult(
        current_version=current_version,
        latest_version=manifest.version,
        update_available=update_available,
        force_update=manifest.force_update,
        update_full=manifest.update_full,
        requires_migration=manifest.requires_migration,
        min_supported_version=manifest.min_supported_version,
        channel=manifest.channel,
        download_url=manifest.download_url,
        release_notes_url=manifest.release_notes_url,
        manifest_url=manifest_url,
    )


def fetch_manifest(manifest_url: str) -> dict[str, object]:
    """Read a JSON manifest from a public URL."""

    request = Request(manifest_url, headers={"User-Agent": "truenex-memory-update-check"})
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("release manifest must be a JSON object")
    return payload


def compare_versions(left: str, right: str) -> int:
    """Compare SemVer-like versions, returning 1, 0 or -1."""

    left_parts = _version_tuple(left)
    right_parts = _version_tuple(right)
    return (left_parts > right_parts) - (left_parts < right_parts)


def _version_tuple(version: str) -> tuple[int, int, int]:
    raw = version.strip().lstrip("v")
    parts = raw.split(".")
    if len(parts) != 3:
        raise ValueError(f"version must use MAJOR.MINOR.PATCH: {version!r}")
    try:
        return tuple(int(part) for part in parts)  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"version must use numeric MAJOR.MINOR.PATCH: {version!r}") from exc
