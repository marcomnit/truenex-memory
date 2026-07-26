"""Tests for release manifest and update checks."""

import json

from typer.testing import CliRunner

from truenex_memory.cli.main import app
from truenex_memory.release.manifest import DEFAULT_MANIFEST_URL, ReleaseManifest
from truenex_memory.release.update_check import check_for_updates, compare_versions
from truenex_memory.release.version import get_version_info
import truenex_memory


runner = CliRunner()


def test_version_info_contains_distinct_versions() -> None:
    info = get_version_info()

    assert info["app_version"] == truenex_memory.__version__
    assert info["db_schema_version"] == "6"
    assert info["mcp_tools_version"] == "1"
    assert info["memory_export_version"] == "1"
    assert info["update_channel"] == "stable"


def test_release_manifest_parses_simple_releases_repo_shape() -> None:
    manifest = ReleaseManifest.from_dict(
        {
            "version": "0.2.0",
            "channel": "dev",
            "force_update": False,
            "update_full": False,
            "download_url": None,
            "release_notes_url": None,
            "requires_migration": True,
            "min_supported_version": "0.1.0",
        }
    )

    assert manifest.version == "0.2.0"
    assert manifest.requires_migration is True
    assert manifest.to_dict()["manifest_version"] == "1"


def test_update_check_uses_injected_fetcher_and_reports_available_update() -> None:
    def fetcher(url: str) -> dict[str, object]:
        assert url == DEFAULT_MANIFEST_URL
        return {
            "version": "0.5.0",
            "channel": "dev",
            "force_update": False,
            "update_full": True,
            "download_url": "https://example.invalid/truenex-memory.zip",
            "release_notes_url": None,
            "requires_migration": False,
            "min_supported_version": "0.1.0",
        }

    result = check_for_updates(fetcher=fetcher)

    assert result.update_available is True
    assert result.latest_version == "0.5.0"
    assert result.update_full is True
    assert result.manifest_url == DEFAULT_MANIFEST_URL


def test_update_check_supports_force_update_for_same_version() -> None:
    current = truenex_memory.__version__.split("-")[0].split("a")[0].split("b")[0].split("rc")[0]
    result = check_for_updates(
        current_version=current,
        fetcher=lambda url: {"version": current, "force_update": True},
    )

    assert result.update_available is True
    assert result.force_update is True


def test_compare_versions() -> None:
    assert compare_versions("0.2.0", "0.1.9") == 1
    assert compare_versions("0.1.0", "0.1.0") == 0
    assert compare_versions("0.1.0", "0.2.0") == -1
    assert compare_versions("v0.2.0", "0.1.0") == 1


def test_cli_version_info() -> None:
    result = runner.invoke(app, ["version-info"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["app_version"] == truenex_memory.__version__
