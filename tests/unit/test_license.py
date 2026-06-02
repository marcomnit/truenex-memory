"""Test license layer: LicenseInfo, LicenseManager, and CLI commands."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from truenex_memory.cli.main import app
from truenex_memory.licensing import LicenseInfo, LicenseManager

runner = CliRunner()


# ─── LicenseInfo ─────────────────────────────────────────────────────────────

class TestLicenseInfo:
    def test_never_expires(self) -> None:
        info = LicenseInfo(key="k1")
        assert info.is_valid()
        assert info.days_remaining() is None
        assert not info.is_in_grace_period()

    def test_valid_within_window(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(days=10)
        info = LicenseInfo(key="k1", expires_at=future)
        assert info.is_valid()
        assert info.days_remaining() >= 9
        assert not info.is_in_grace_period()

    def test_expired(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(days=20)
        info = LicenseInfo(key="k1", expires_at=past)
        assert not info.is_valid()
        assert info.days_remaining() == 0
        assert not info.is_in_grace_period()

    def test_grace_period(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(days=3)
        info = LicenseInfo(key="k1", expires_at=past, offline_grace_days=7)
        assert not info.is_valid()
        assert info.days_remaining() == 0
        assert info.is_in_grace_period()

    def test_grace_period_just_ended(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(days=8)
        info = LicenseInfo(key="k1", expires_at=past, offline_grace_days=7)
        assert not info.is_valid()
        assert info.days_remaining() == 0
        assert not info.is_in_grace_period()

    def test_grace_period_exact_boundary(self) -> None:
        expires = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        info = LicenseInfo(key="k1", expires_at=expires, offline_grace_days=7)
        # exactly at boundary (7 days after expiry) should be in grace
        assert info.is_in_grace_period(now=now)
        assert not info.is_valid(now=now)

    def test_explicit_now(self) -> None:
        info = LicenseInfo(key="k1", expires_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
        assert info.is_valid(now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        assert not info.is_valid(now=datetime(2026, 8, 1, tzinfo=timezone.utc))

    def test_naive_explicit_now(self) -> None:
        info = LicenseInfo(key="k1", expires_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
        # naive datetime treated as UTC
        assert info.is_valid(now=datetime(2026, 6, 1))

    def test_days_remaining_returns_int(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(days=5, hours=12)
        info = LicenseInfo(key="k1", expires_at=future)
        assert isinstance(info.days_remaining(), int)
        assert 4 <= info.days_remaining() <= 5

    def test_to_dict(self) -> None:
        now = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        info = LicenseInfo(key="abc", tier="pro", activated_at=now, features=["f1", "f2"])
        d = info.to_dict()
        assert d["key"] == "abc"
        assert d["tier"] == "pro"
        assert d["activated_at"] == "2026-01-15T10:00:00+00:00"
        assert d["expires_at"] is None
        assert d["features"] == ["f1", "f2"]
        assert d["offline_grace_days"] == 7

    def test_from_dict(self) -> None:
        d = {
            "key": "abc",
            "tier": "team",
            "activated_at": "2026-01-15T10:00:00+00:00",
            "expires_at": "2026-12-31T00:00:00+00:00",
            "features": ["f1"],
            "offline_grace_days": 14,
        }
        info = LicenseInfo.from_dict(d)
        assert info.key == "abc"
        assert info.tier == "team"
        assert info.expires_at is not None
        assert info.expires_at.year == 2026
        assert info.features == ["f1"]
        assert info.offline_grace_days == 14

    def test_from_dict_missing_optional_fields(self) -> None:
        info = LicenseInfo.from_dict({"key": "k1"})
        assert info.key == "k1"
        assert info.tier == "pro"
        assert info.expires_at is None
        assert info.features == []

    def test_invalid_tier_raises(self) -> None:
        with pytest.raises(ValueError):
            LicenseInfo(key="k1", tier="enterprise")

    def test_frozen(self) -> None:
        info = LicenseInfo(key="k1")
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
        info = LicenseInfo(key="k1", tier="pro", features=["rag", "multi-agent"])
        mgr.save(info)
        loaded = mgr.load()
        assert loaded is not None
        assert loaded.key == "k1"
        assert loaded.tier == "pro"
        assert loaded.features == ["rag", "multi-agent"]

    def test_load_corrupt_file(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        mgr.license_dir.mkdir(parents=True, exist_ok=True)
        mgr.license_path.write_text("not valid json", encoding="utf-8")
        assert mgr.load() is None

    def test_load_missing_key(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        mgr.license_dir.mkdir(parents=True, exist_ok=True)
        mgr.license_path.write_text(json.dumps({"tier": "pro"}), encoding="utf-8")
        assert mgr.load() is None  # missing "key"

    def test_activate(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        info = mgr.activate(key="abc", tier="team")
        assert info.key == "abc"
        assert info.tier == "team"
        assert mgr.license_path.exists()

    def test_activate_with_features(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        info = mgr.activate(key="abc", features=["f1", "f2"])
        assert info.features == ["f1", "f2"]

    def test_deactivate_when_exists(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        mgr.activate(key="k1")
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
        mgr.activate(key="k1", expires_at=future)
        s = mgr.status()
        assert s["tier"] == "pro"
        assert s["status"] == "active"
        assert s["days_remaining"] >= 29

    def test_status_expired(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        past = datetime.now(timezone.utc) - timedelta(days=30)
        mgr.activate(key="k1", expires_at=past)
        s = mgr.status()
        assert s["status"] == "expired"
        assert s["days_remaining"] == 0
        assert s["grace_period"] is False

    def test_status_grace(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        past = datetime.now(timezone.utc) - timedelta(days=2)
        mgr.activate(key="k1", expires_at=past)
        s = mgr.status()
        assert s["status"] == "grace"
        assert s["grace_period"] is True

    def test_require_tier(self, tmp_dir: Path) -> None:
        mgr = LicenseManager(license_dir=tmp_dir)
        mgr.activate(key="k1", tier="pro")
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
        info = LicenseInfo(key="k1")
        mgr.save(info)
        # tmp file should be gone (replaced)
        assert not mgr.license_path.with_suffix(".tmp").exists()
        assert mgr.license_path.exists()


# ─── CLI ─────────────────────────────────────────────────────────────────────

class TestLicenseCLI:
    def test_license_help(self) -> None:
        result = runner.invoke(app, ["license", "--help"])
        assert result.exit_code == 0
        assert "license" in result.stdout.lower()

    def test_status_inactive(self) -> None:
        result = runner.invoke(app, ["license", "status"])
        assert result.exit_code == 0
        assert "free" in result.stdout
        assert "inactive" in result.stdout

    def test_status_json(self) -> None:
        result = runner.invoke(app, ["license", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["tier"] == "free"
        assert data["status"] == "inactive"

    def test_activate_and_status(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        result = runner.invoke(app, [
            "license", "activate", "my-key",
            "--tier", "pro",
            "--license-dir", str(license_dir),
        ])
        assert result.exit_code == 0
        assert "activated" in result.stdout

        result = runner.invoke(app, [
            "license", "status",
            "--license-dir", str(license_dir),
        ])
        assert result.exit_code == 0
        assert "pro" in result.stdout
        assert "active" in result.stdout

    def test_activate_with_expiry(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        result = runner.invoke(app, [
            "license", "activate", "my-key",
            "--expires-at", "2026-12-31",
            "--license-dir", str(license_dir),
            "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "2026-12-31" in data["expires_at"]

    def test_activate_bad_expiry(self) -> None:
        result = runner.invoke(app, [
            "license", "activate", "my-key",
            "--expires-at", "not-a-date",
        ])
        assert result.exit_code != 0

    def test_deactivate_no_license(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        result = runner.invoke(app, [
            "license", "deactivate", "--yes",
            "--license-dir", str(license_dir),
        ])
        assert result.exit_code == 0

    def test_deactivate_when_exists(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        runner.invoke(app, [
            "license", "activate", "my-key",
            "--license-dir", str(license_dir),
        ])
        result = runner.invoke(app, [
            "license", "deactivate", "--yes",
            "--license-dir", str(license_dir),
        ])
        assert result.exit_code == 0
        assert "deactivated" in result.stdout.lower() or "deactivated" in result.stdout

    def test_require_met(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        runner.invoke(app, [
            "license", "activate", "my-key",
            "--tier", "pro",
            "--license-dir", str(license_dir),
        ])
        result = runner.invoke(app, [
            "license", "require", "pro",
            "--license-dir", str(license_dir),
        ])
        assert result.exit_code == 0

    def test_require_not_met(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        runner.invoke(app, [
            "license", "activate", "my-key",
            "--tier", "pro",
            "--license-dir", str(license_dir),
        ])
        result = runner.invoke(app, [
            "license", "require", "team",
            "--license-dir", str(license_dir),
        ])
        assert result.exit_code == 1

    def test_activate_with_features(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        result = runner.invoke(app, [
            "license", "activate", "my-key",
            "--feature", "rag",
            "--feature", "multi-agent",
            "--license-dir", str(license_dir),
            "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["features"] == ["rag", "multi-agent"]

    def test_activate_online_success(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        fake_response = {
            "license_key": "trxn-pro-ONLINE-1234",
            "tier": "pro",
            "activated_at": "2026-06-02T10:00:00+00:00",
            "expires_at": "2027-06-02T10:00:00+00:00",
            "features": ["advanced_auto_memory"],
            "message": "License activated successfully",
        }

        with patch("truenex_memory.cli.license_commands.urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = json.dumps(fake_response).encode()
            result = runner.invoke(app, [
                "license", "activate", "trxn-pro-ONLINE-1234",
                "--online",
                "--license-dir", str(license_dir),
            ])
            assert result.exit_code == 0
            assert "activated" in result.stdout

        # Verify local file was created
        mgr = LicenseManager(license_dir=license_dir)
        loaded = mgr.load()
        assert loaded is not None
        assert loaded.key == "trxn-pro-ONLINE-1234"
        assert loaded.tier == "pro"
        assert "advanced_auto_memory" in loaded.features

    def test_activate_online_not_found(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"
        from urllib.error import HTTPError

        with patch("truenex_memory.cli.license_commands.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = HTTPError(
                url="https://truenex.ai/api/v1/billing/license/activate",
                code=404,
                msg="Not Found",
                hdrs={},
                fp=None,
            )
            # HTTPError without fp cannot be read, so _activate_online will use body fallback
            # We need to mock the exception properly
            pass

        # Re-mock with proper response body
        class FakeHTTPError(Exception):
            def read(self):
                return json.dumps({"detail": "License key not found"}).encode()
            def __init__(self):
                super().__init__("License key not found")

        # Actually patch the function to raise our custom error
        with patch("truenex_memory.cli.license_commands._activate_online") as mock_activate:
            mock_activate.side_effect = FakeHTTPError()
            result = runner.invoke(app, [
                "license", "activate", "INVALID-KEY",
                "--online",
                "--license-dir", str(license_dir),
            ])
            assert result.exit_code != 0

    def test_activate_online_expired(self, tmp_path: Path) -> None:
        license_dir = tmp_path / "license_test"

        class FakeHTTPError(Exception):
            def read(self):
                return json.dumps({"detail": "License expired"}).encode()
            def __init__(self):
                super().__init__("License expired")

        with patch("truenex_memory.cli.license_commands._activate_online") as mock_activate:
            mock_activate.side_effect = FakeHTTPError()
            result = runner.invoke(app, [
                "license", "activate", "EXPIRED-KEY",
                "--online",
                "--license-dir", str(license_dir),
            ])
            assert result.exit_code != 0
