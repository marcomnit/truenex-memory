"""Release manifest parsing and validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MANIFEST_VERSION = "1"
DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/marcomnit/"
    "truenex-memory-releases/main/version.json"
)
VALID_CHANNELS = {"dev", "beta", "stable"}


@dataclass(frozen=True)
class ReleaseManifest:
    """Public update manifest read from the releases repository."""

    version: str
    channel: str = "dev"
    force_update: bool = False
    update_full: bool = False
    download_url: str | None = None
    release_notes_url: str | None = None
    requires_migration: bool = False
    min_supported_version: str = "0.1.0"
    manifest_version: str = MANIFEST_VERSION

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReleaseManifest":
        """Validate and parse a release manifest payload."""

        version = _required_str(payload, "version")
        channel = str(payload.get("channel", "dev"))
        if channel not in VALID_CHANNELS:
            raise ValueError(f"unsupported release channel: {channel}")
        return cls(
            version=version,
            channel=channel,
            force_update=_bool(payload, "force_update", False),
            update_full=_bool(payload, "update_full", False),
            download_url=_optional_str(payload, "download_url"),
            release_notes_url=_optional_str(payload, "release_notes_url"),
            requires_migration=_bool(payload, "requires_migration", False),
            min_supported_version=str(payload.get("min_supported_version", "0.1.0")),
            manifest_version=str(payload.get("manifest_version", MANIFEST_VERSION)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly manifest dict."""

        return {
            "manifest_version": self.manifest_version,
            "version": self.version,
            "channel": self.channel,
            "force_update": self.force_update,
            "update_full": self.update_full,
            "download_url": self.download_url,
            "release_notes_url": self.release_notes_url,
            "requires_migration": self.requires_migration,
            "min_supported_version": self.min_supported_version,
        }


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest field {key!r} must be a non-empty string")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest field {key!r} must be null or a non-empty string")
    return value


def _bool(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"manifest field {key!r} must be boolean")
    return value
