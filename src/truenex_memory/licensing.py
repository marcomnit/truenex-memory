"""License layer for Truenex Memory Pro.

Manages license activation, validation, and tier enforcement.
Activation is server-side with RS256 JWT tokens and device binding.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import jwt

_TIER_ORDER = {"free": 0, "pro": 1, "team": 2}

# ---------------------------------------------------------------------------
# Embedded public key for RS256 offline verification (safe to distribute)
# ---------------------------------------------------------------------------
_LICENSE_PUBLIC_KEY = """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA3s6mFbHXUm93ALE+BTJk
SkdNICdnnpW8+Ovzim3NlLNbgT7n6QLYUsqUHPZOV2S5MwZV8Pu7WmbUOh1nxcaj
TwVRotu5457eN5xFWDxUcCSxxUwZlMgtNm/NXT8BAY8OdX5bvzzk28Dwt475uVF8
RXMVcVx0mbTervmIZo7Ae87U4GH1xRbdkJ13TvUlnyaaop20OVBJGch6TeREl7/V
O1irduh7U7RqHMljeSHxpDpByFQa3tZEyacIMtTBL2N/ErHSfriwqVuGginIMV6W
JNnDIlr/YUZuz6vL6F5PFUomTZUWrZ44K2b7VMl1BtwrVZ8It1PuXY4NG8EfQcnN
DwIDAQAB
-----END PUBLIC KEY-----
"""

_LICENSE_SERVER_URL = os.getenv(
    "TRUENEX_LICENSE_SERVER", "https://memory.truenex.ai/api/v1/license"
)
_JWT_ALGORITHM = "RS256"
_JWT_ISSUER = "truenex-memory-license-server"
_TOKEN_EXPIRY_DAYS = 30


def _get_device_id() -> str:
    """Stable device fingerprint (hostname + user + MAC node)."""
    raw = f"{platform.node()}|{getpass.getuser()}|{uuid.getnode()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LicenseInfo:
    """Immutable license record persisted locally after server activation."""

    key: str
    token: str
    device_id: str
    tier: str = "pro"
    activated_at: datetime = field(default_factory=_utc_now)
    expires_at: datetime | None = None
    offline_grace_days: int = 7

    def __post_init__(self) -> None:
        if self.tier not in _TIER_ORDER:
            raise ValueError(
                f"Invalid tier {self.tier!r}; expected one of {sorted(_TIER_ORDER)}"
            )

    def is_valid(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return True
        now = _ensure_aware(now)
        return now <= self.expires_at

    def is_in_grace_period(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = _ensure_aware(now)
        if now <= self.expires_at:
            return False
        grace_end = self.expires_at + timedelta(days=self.offline_grace_days)
        return now <= grace_end

    def days_remaining(self, now: datetime | None = None) -> int | None:
        if self.expires_at is None:
            return None
        now = _ensure_aware(now)
        delta = self.expires_at - now
        return max(0, delta.days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "token": self.token,
            "device_id": self.device_id,
            "tier": self.tier,
            "activated_at": self.activated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "offline_grace_days": self.offline_grace_days,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LicenseInfo:
        return cls(
            key=data["key"],
            token=data["token"],
            device_id=data.get("device_id", _get_device_id()),
            tier=data.get("tier", "pro"),
            activated_at=_parse_dt(data.get("activated_at")) or _utc_now(),
            expires_at=_parse_dt(data.get("expires_at")),
            offline_grace_days=data.get("offline_grace_days", 7),
        )


# ---------------------------------------------------------------------------
# License Manager
# ---------------------------------------------------------------------------
class LicenseManager:
    """Manages license.json persistence, server activation, and offline verification."""

    _FILENAME = "license.json"

    def __init__(self, license_dir: str | Path | None = None) -> None:
        self.license_dir = Path(license_dir) if license_dir else Path.home() / ".truenex-memory"
        self.license_path = self.license_dir / self._FILENAME

    def load(self) -> LicenseInfo | None:
        if not self.license_path.exists():
            return None
        try:
            data = json.loads(self.license_path.read_text(encoding="utf-8"))
            return LicenseInfo.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    def save(self, info: LicenseInfo) -> None:
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
        """Activate against the license server and persist the JWT token locally."""
        device_id = _get_device_id()
        try:
            resp = httpx.post(
                f"{_LICENSE_SERVER_URL}/activate",
                json={"key": key, "device_id": device_id},
                timeout=15.0,
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text if exc.response else str(exc)
            raise RuntimeError(f"License activation failed: {detail}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Cannot reach license server ({_LICENSE_SERVER_URL}). Are you online?"
            ) from exc

        token = payload["token"]
        # Use server-provided expiry (more robust than local JWT decode)
        expires_at = _parse_dt(payload.get("expires_at"))

        info = LicenseInfo(
            key=key,
            token=token,
            device_id=device_id,
            tier=payload.get("tier", tier),
            activated_at=_utc_now(),
            expires_at=expires_at,
        )
        self.save(info)
        return info

    def deactivate(self) -> bool:
        """Deactivate this device on the server and remove local license file."""
        info = self.load()
        if info is None:
            return False
        try:
            httpx.post(
                f"{_LICENSE_SERVER_URL}/deactivate",
                json={"key": info.key, "device_id": info.device_id},
                timeout=15.0,
            )
        except Exception:
            # Best-effort: still delete local file so user can free the slot manually
            pass
        self.license_path.unlink(missing_ok=True)
        return True

    def status(self) -> dict[str, Any]:
        """Return local status after verifying the JWT token and device binding."""
        info = self.load()
        if info is None:
            return {
                "tier": "free",
                "status": "inactive",
                "key": None,
                "device_id": None,
                "activated_at": None,
                "expires_at": None,
                "days_remaining": None,
                "grace_period": False,
                "offline_grace_days": 7,
            }

        now = _utc_now()
        token_valid = False
        token_expired = False
        token_tier = info.tier

        try:
            decoded = jwt.decode(
                info.token,
                _LICENSE_PUBLIC_KEY,
                algorithms=[_JWT_ALGORITHM],
                issuer=_JWT_ISSUER,
            )
            # Device binding check
            if decoded.get("device_id") != info.device_id:
                token_valid = False
            else:
                token_valid = True
                token_tier = decoded.get("tier", info.tier)
        except jwt.ExpiredSignatureError:
            token_expired = True
            # Decode without exp check to extract metadata for grace period
            try:
                decoded = jwt.decode(
                    info.token,
                    _LICENSE_PUBLIC_KEY,
                    algorithms=[_JWT_ALGORITHM],
                    issuer=_JWT_ISSUER,
                    options={"verify_exp": False},
                )
                token_tier = decoded.get("tier", info.tier)
            except Exception:
                pass
        except jwt.InvalidTokenError:
            token_valid = False

        if token_valid:
            lic_status = "active"
        elif token_expired and info.is_in_grace_period(now):
            lic_status = "grace"
        elif token_expired:
            lic_status = "expired"
        else:
            lic_status = "invalid"

        return {
            "tier": token_tier if (token_valid or token_expired) else "free",
            "status": lic_status,
            "key": info.key,
            "device_id": info.device_id,
            "activated_at": info.activated_at.isoformat() if info.activated_at else None,
            "expires_at": info.expires_at.isoformat() if info.expires_at else None,
            "days_remaining": info.days_remaining(now),
            "grace_period": lic_status == "grace",
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ensure_aware(dt: datetime | None) -> datetime:
    """Return a timezone-aware UTC datetime (default now)."""
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_dt(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        dt = datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
