"""License layer for Truenex Memory Pro Local.

Manages license activation, validation, and tier enforcement.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_TIER_ORDER = {"free": 0, "pro": 1, "team": 2}


@dataclass(frozen=True)
class LicenseInfo:
    """Immutable license record."""

    key: str
    tier: str = "pro"
    activated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    features: list[str] = field(default_factory=list)
    offline_grace_days: int = 7

    def __post_init__(self) -> None:
        if self.tier not in _TIER_ORDER:
            raise ValueError(
                f"Invalid tier {self.tier!r}; expected one of {sorted(_TIER_ORDER)}"
            )

    def is_valid(self, now: datetime | None = None) -> bool:
        """True if never expires, or now is before expiry."""
        if self.expires_at is None:
            return True
        now = _ensure_aware(now)
        return now <= self.expires_at

    def is_in_grace_period(self, now: datetime | None = None) -> bool:
        """True if expired but within offline_grace_days from expiry."""
        if self.expires_at is None:
            return False
        now = _ensure_aware(now)
        if now <= self.expires_at:
            return False
        grace_end = self.expires_at + _timedelta_days(self.offline_grace_days)
        return now <= grace_end

    def days_remaining(self, now: datetime | None = None) -> int | None:
        """Days until expiry, or None if never expires. Minimum 0."""
        if self.expires_at is None:
            return None
        now = _ensure_aware(now)
        delta = self.expires_at - now
        return max(0, delta.days)

    def to_dict(self) -> dict:
        """JSON-friendly serialization with ISO format datetimes."""
        return {
            "key": self.key,
            "tier": self.tier,
            "activated_at": self.activated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "features": list(self.features),
            "offline_grace_days": self.offline_grace_days,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LicenseInfo:
        """Deserialize from dict (JSON-safe)."""
        return cls(
            key=data["key"],
            tier=data.get("tier", "pro"),
            activated_at=_parse_dt(data.get("activated_at")),
            expires_at=_parse_dt(data.get("expires_at")),
            features=list(data.get("features", [])),
            offline_grace_days=data.get("offline_grace_days", 7),
        )


class LicenseManager:
    """Manages license.json persistence and status queries."""

    _FILENAME = "license.json"

    def __init__(self, license_dir: str | Path | None = None) -> None:
        self.license_dir = Path(license_dir) if license_dir else Path.home() / ".truenex-memory"
        self.license_path = self.license_dir / self._FILENAME

    def load(self) -> LicenseInfo | None:
        """Load and parse the license file. Returns None if missing or malformed."""
        if not self.license_path.exists():
            return None
        try:
            data = json.loads(self.license_path.read_text(encoding="utf-8"))
            return LicenseInfo.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    def save(self, info: LicenseInfo) -> None:
        """Atomically write license to disk (tmp then rename)."""
        self.license_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(info.to_dict(), indent=2, sort_keys=True)
        tmp_path = self.license_path.with_suffix(".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, self.license_path)

    def activate(
        self,
        key: str,
        tier: str = "pro",
        expires_at: datetime | None = None,
        features: list[str] | None = None,
    ) -> LicenseInfo:
        """Create and persist a new license, returning the LicenseInfo."""
        info = LicenseInfo(
            key=key,
            tier=tier,
            activated_at=datetime.now(timezone.utc),
            expires_at=expires_at,
            features=list(features or []),
        )
        self.save(info)
        return info

    def deactivate(self) -> bool:
        """Remove the license file. Returns True if it existed."""
        if self.license_path.exists():
            self.license_path.unlink()
            return True
        return False

    def status(self) -> dict:
        """Return a dict with the current license status.

        Tier defaults to 'free' when no license is present.
        Status is one of: inactive, active, grace, expired.
        """
        info = self.load()
        if info is None:
            return {
                "tier": "free",
                "status": "inactive",
                "key": None,
                "activated_at": None,
                "expires_at": None,
                "days_remaining": None,
                "features": [],
                "grace_period": False,
                "offline_grace_days": 7,
            }

        now = datetime.now(timezone.utc)
        if info.is_valid(now):
            status = "active"
        elif info.is_in_grace_period(now):
            status = "grace"
        else:
            status = "expired"

        return {
            "tier": info.tier,
            "status": status,
            "key": info.key,
            "activated_at": info.activated_at.isoformat(),
            "expires_at": info.expires_at.isoformat() if info.expires_at else None,
            "days_remaining": info.days_remaining(now),
            "features": list(info.features),
            "grace_period": status == "grace",
            "offline_grace_days": info.offline_grace_days,
        }

    def require_tier(self, minimum_tier: str) -> bool:
        """True if the current tier is >= minimum_tier (free < pro < team)."""
        required = _TIER_ORDER.get(minimum_tier)
        if required is None:
            raise ValueError(
                f"Unknown tier {minimum_tier!r}; expected one of {sorted(_TIER_ORDER)}"
            )
        current_tier = self.status()["tier"]
        current = _TIER_ORDER.get(current_tier, 0)
        return current >= required


def _ensure_aware(dt: datetime | None) -> datetime:
    """Return a timezone-aware UTC datetime (default now)."""
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_dt(raw: object) -> datetime | None:
    """Parse an ISO datetime string to a naive UTC datetime."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        dt = datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
    # Normalize to UTC with tzinfo so comparisons work with aware datetimes
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _timedelta_days(days: int):
    """Import timedelta lazily to avoid top-level coupling."""
    from datetime import timedelta

    return timedelta(days=days)
