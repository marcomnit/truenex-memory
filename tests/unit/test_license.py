"""Test license layer: LicenseInfo, LicenseManager, and CLI commands."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import jwt
import pytest
from typer.testing import CliRunner

from truenex_memory.cli.main import app
from truenex_memory.licensing import LicenseInfo, LicenseManager

runner = CliRunner()


def _fake_token_response(token: str = "fake-jwt", tier: str = "pro", expires_at: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"token": token, "tier": tier, "expires_at": expires_at}
    resp.raise_for_status = MagicMock()
    return resp


def _fake_error_response(status_code: int, detail: str) -> MagicMock:
    from httpx import HTTPStatusError, Request, Response

    req = Request("POST", "http://test")
    resp = Response(status_code, request=req, text=json.dumps({"detail": detail}))
    err = HTTPStatusError("error", request=req, response=resp)
    resp.raise_for_status = MagicMock(side_effect=err)
    return resp


# ─── LicenseInfo ─────────────────────────────────────────────────────────────

class TestLicenseInfo:
    def test_never_expires(self) -> None:
        info = LicenseInfo(key="k1", token="tok", device_id="dev")
        assert info.is_valid()
        assert info.days_remaining() is None
        assert not info.is_in_grace_period()

    def test_valid_within_window(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(days=10)
        info = LicenseInfo(key="k1", token="tok", device_id="dev", expires_at=future)
        assert info.is_valid()
        assert info.days_remaining() >= 9
        assert not info.is_in_grace_period()

    def test_expired(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(days=20)
        info = LicenseInfo(key="k1", token="tok", device_id="dev", expires_at=past)
        assert not info.is_valid()
        assert info.days_remaining() == 0
        assert not info.is_in_grace_period()

    def test_grace_period(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(days=3)
        info = LicenseInfo(key="k1", token="tok", device_id="dev", expires_at=past, offline_grace_days=7)
        assert not info.is_valid()
        assert info.days_remaining() == 0
        assert info.is_in_grace_period()

    def test_grace_period_just_ended(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(days=8)
        info = LicenseInfo(key="k1", token="tok", device_id="dev", expires_at=past, offline_grace_days=7)
        assert not info.is_valid()
        assert info.days_remaining() == 0
        assert not info.is_in_grace_period()

    def test_grace_period_exact_boundary(self) -> None:
        expires = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        info = LicenseInfo(key="k1", token="tok", device_id="dev", expires_at=expires, offline_grace_days=7)
        assert info.is_in_grace_period(now=now)
        assert not info.is_valid(now=now)

    def test_explicit_now(self) -> None:
        info = LicenseInfo(key="k1", token="tok", device_id="dev", expires_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
        assert info.is_valid(now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        assert not info.is_valid(now=datetime(2026, 8, 1, tzinfo=timezone.utc))

    def test_naive_explicit_now(self) -> None:
        info = LicenseInfo(key="k1", token="tok", device_id="dev", expires_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
        assert info.is_valid(now=datetime(2026, 6, 1))

    def test_days_remaining_returns_int(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(days=5, hours=12)
        info = LicenseInfo(key="k1", token="tok", device_id="dev", expires_at=future)
        assert isinstance(info.days_remaining(), int)
        assert 4 <= info.days_remaining() <= 5

    def test_to_dict(self) -> None:
        now = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        info = LicenseInfo(key="abc", token="tok", device_id="dev", tier="pro", activated_at=now)
        d = info.to_dict()
        assert d["key"] == "abc"
        assert d["tier"] == "pro"
        assert d["token"] == "tok"
        assert d["device_id"] == "dev"
        assert d["activated_at"] == "2026-01-15T10:00:00+00:00"
        assert d["expires_at"] is None
        assert d["offline_grace_days"] == 7

    def test_from_dict(self) -> None:
        d = {
            "key": "abc",
            "tier": "team",
            "token": "tok",
            "device_id": "dev",
            "activated_at": "2026-01-15T10:00:00+00:00",
            "expires_at": "2026-12-31T00:00:00+00:00",
            "offline_grace_days": 14,
        }
        info = LicenseInfo.from_dict(d)
        assert info.key == "abc"
        assert info.tier == "team"
        assert info.expires_at is not None
        assert info.expires_at.year == 2026
        assert info.offline_grace_days == 14

    def test_from_dict_missing_optional_fields(self) -> None:
        info = LicenseInfo.from_dict({"key": "k1", "token": "tok", "device_id": "dev"})
        assert info.key == "k1"
        assert info.tier == "pro"
        assert info.expires_at is None
        assert info.token == "tok"
        assert info.device_id == "dev"

    def test_invalid_tier_raises(self) -> None:
        with pytest.raises(ValueError):
            LicenseInfo(key="k1", token="tok", device_id="dev", tier="enterprise")

    def test_frozen(self) -> None:
        info = LicenseInfo(key="k1", token="tok", device_id="dev")
        with pytest.raises(Exception):
            info.key = "k2"  # type: ignore[misc]


# ─── LicenseManager ──────────────────────────────────────────────────────────

class TestLicenseManager:
    @pytest.fixture
    def tmp_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    def test_load_missing_file(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        assert mgr.load() is None

    def test_save_and_load(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        info = LicenseInfo(key="k1", token="tok", device_id="dev", tier="pro")
        mgr.save(info)
        loaded = mgr.load()
        assert loaded is not None
        assert loaded.key == "k1"
        assert loaded.tier == "pro"
        assert loaded.token == "tok"
        assert loaded.device_id == "dev"

    def test_load_corrupt_file(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        mgr.license_dir.mkdir(parents=True, exist_ok=True)
        mgr.license_path.write_text("not valid json", encoding="utf-8")
        assert mgr.load() is None

    def test_load_missing_key(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        mgr.license_dir.mkdir(parents=True, exist_ok=True)
        mgr.license_path.write_text(json.dumps({"tier": "pro"}), encoding="utf-8")
        assert mgr.load() is None

    def test_activate(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_token_response("tok123", "team")):
            info = mgr.activate(key="abc")
        assert info.key == "abc"
        assert info.tier == "team"
        assert info.token == "tok123"
        assert mgr.license_path.exists()

    def test_activate_server_error(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_error_response(403, "limit reached")):
            with pytest.raises(RuntimeError, match="limit reached"):
                mgr.activate(key="abc")

    def test_activate_offline(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        with patch("truenex_memory.licensing.httpx.post", side_effect=httpx.RequestError("no network")):
            with pytest.raises(RuntimeError, match="Cannot reach license server"):
                mgr.activate(key="abc")

    def test_deactivate_when_exists(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_token_response()):
            mgr.activate(key="k1")
        with patch("truenex_memory.licensing.httpx.post", return_value=MagicMock()) as mock_post:
            assert mgr.deactivate() is True
        assert not mgr.license_path.exists()

    def test_deactivate_when_missing(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        assert mgr.deactivate() is False

    def test_status_inactive(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        s = mgr.status()
        assert s["tier"] == "free"
        assert s["status"] == "inactive"
        assert s["key"] is None

    def test_status_active(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        future = datetime.now(timezone.utc) + timedelta(days=30)
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_token_response(expires_at=future.isoformat())):
            mgr.activate(key="k1")
        with patch("truenex_memory.licensing.jwt.decode", return_value={"sub": "k1", "device_id": mgr.load().device_id, "tier": "pro", "exp": future.timestamp()}):
            s = mgr.status()
        assert s["tier"] == "pro"
        assert s["status"] == "active"
        assert s["days_remaining"] >= 29

    def test_status_expired(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        past = datetime.now(timezone.utc) - timedelta(days=30)
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_token_response(expires_at=past.isoformat())):
            mgr.activate(key="k1")
        with patch("truenex_memory.licensing.jwt.decode") as mock_jwt:
            mock_jwt.side_effect = [
                jwt.ExpiredSignatureError(),
                {"sub": "k1", "device_id": mgr.load().device_id, "tier": "pro", "exp": past.timestamp()},
            ]
            s = mgr.status()
        assert s["status"] == "expired"
        assert s["days_remaining"] == 0
        assert s["grace_period"] is False

    def test_status_grace(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        past = datetime.now(timezone.utc) - timedelta(days=2)
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_token_response(expires_at=past.isoformat())):
            mgr.activate(key="k1")
        with patch("truenex_memory.licensing.jwt.decode") as mock_jwt:
            mock_jwt.side_effect = [
                jwt.ExpiredSignatureError(),
                {"sub": "k1", "device_id": mgr.load().device_id, "tier": "pro", "exp": past.timestamp()},
            ]
            s = mgr.status()
        assert s["status"] == "grace"
        assert s["grace_period"] is True

    def test_status_invalid_token(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        mgr.license_dir.mkdir(parents=True, exist_ok=True)
        mgr.license_path.write_text(json.dumps({"key": "k1", "token": "bad", "device_id": "dev", "tier": "pro"}), encoding="utf-8")
        with patch("truenex_memory.licensing.jwt.decode", side_effect=jwt.InvalidTokenError()):
            s = mgr.status()
        assert s["status"] == "invalid"
        assert s["tier"] == "free"

    def test_status_device_mismatch(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        mgr.license_dir.mkdir(parents=True, exist_ok=True)
        mgr.license_path.write_text(json.dumps({"key": "k1", "token": "tok", "device_id": "dev1", "tier": "pro"}), encoding="utf-8")
        with patch("truenex_memory.licensing.jwt.decode", return_value={"sub": "k1", "device_id": "dev2", "tier": "pro"}):
            s = mgr.status()
        assert s["status"] == "invalid"
        assert s["tier"] == "free"

    def test_require_tier(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_token_response()):
            mgr.activate(key="k1")
        with patch("truenex_memory.licensing.jwt.decode", return_value={"sub": "k1", "device_id": mgr.load().device_id, "tier": "pro"}):
            assert mgr.require_tier("free")
            assert mgr.require_tier("pro")
            assert not mgr.require_tier("team")

    def test_require_tier_no_license_is_free(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        assert mgr.require_tier("free")
        assert not mgr.require_tier("pro")

    def test_require_tier_unknown_raises(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        with pytest.raises(ValueError):
            mgr.require_tier("enterprise")

    def test_atomic_save(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        info = LicenseInfo(key="k1", token="tok", device_id="dev")
        mgr.save(info)
        assert not mgr.license_path.with_suffix(".tmp").exists()
        assert mgr.license_path.exists()


# ─── CLI ─────────────────────────────────────────────────────────────────────

class TestLicenseCLI:
    def test_license_help(self) -> None:
        result = runner.invoke(app, ["license", "--help"])
        assert result.exit_code == 0
        assert "license" in result.stdout.lower()

    def test_status_inactive(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        result = runner.invoke(app, ["license", "status", "--license-dir", str(license_dir)])
        assert result.exit_code == 0
        assert "free" in result.stdout
        assert "inactive" in result.stdout

    def test_status_json(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        result = runner.invoke(app, ["license", "status", "--json", "--license-dir", str(license_dir)])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["tier"] == "free"
        assert data["status"] == "inactive"

    def test_activate_and_status(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_token_response("tok", "pro")):
            result = runner.invoke(app, [
                "license", "activate", "my-key",
                "--license-dir", str(license_dir),
            ])
        assert result.exit_code == 0
        assert "activated" in result.stdout

        # Read actual device_id written by activate()
        mgr = LicenseManager(license_dir=license_dir)
        loaded = mgr.load()
        device_id = loaded.device_id if loaded else "dev"

        with patch("truenex_memory.licensing.jwt.decode", return_value={"sub": "my-key", "device_id": device_id, "tier": "pro"}):
            result = runner.invoke(app, [
                "license", "status",
                "--license-dir", str(license_dir),
            ])
        assert result.exit_code == 0
        assert "pro" in result.stdout
        assert "active" in result.stdout

    def test_activate_server_error(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_error_response(403, "limit reached")):
            result = runner.invoke(app, [
                "license", "activate", "my-key",
                "--license-dir", str(license_dir),
            ])
        assert result.exit_code != 0
        assert "limit reached" in result.stdout or "limit reached" in result.stderr

    def test_deactivate_no_license(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        result = runner.invoke(app, [
            "license", "deactivate", "--yes",
            "--license-dir", str(license_dir),
        ])
        assert result.exit_code == 0

    def test_deactivate_when_exists(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_token_response()):
            runner.invoke(app, [
                "license", "activate", "my-key",
                "--license-dir", str(license_dir),
            ])
        with patch("truenex_memory.licensing.httpx.post", return_value=MagicMock()):
            result = runner.invoke(app, [
                "license", "deactivate", "--yes",
                "--license-dir", str(license_dir),
            ])
        assert result.exit_code == 0
        assert "deactivated" in result.stdout.lower() or "deactivated" in result.stdout

    def test_require_met(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_token_response()):
            runner.invoke(app, [
                "license", "activate", "my-key",
                "--license-dir", str(license_dir),
            ])
        mgr = LicenseManager(license_dir=license_dir)
        loaded = mgr.load()
        device_id = loaded.device_id if loaded else "dev"
        with patch("truenex_memory.licensing.jwt.decode", return_value={"sub": "my-key", "device_id": device_id, "tier": "pro"}):
            result = runner.invoke(app, [
                "license", "require", "pro",
                "--license-dir", str(license_dir),
            ])
        assert result.exit_code == 0

    def test_require_not_met(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        with patch("truenex_memory.licensing.httpx.post", return_value=_fake_token_response()):
            runner.invoke(app, [
                "license", "activate", "my-key",
                "--license-dir", str(license_dir),
            ])
        with patch("truenex_memory.licensing.jwt.decode", return_value={"sub": "my-key", "device_id": "dev", "tier": "pro"}):
            result = runner.invoke(app, [
                "license", "require", "team",
                "--license-dir", str(license_dir),
            ])
        assert result.exit_code == 1
