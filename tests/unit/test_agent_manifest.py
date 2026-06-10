"""Unit tests for the Agent Manifest system.

These tests cover the external manifest that replaces the hardcoded
``AGENT_ROOTS`` list with a persistent JSON file under
``~/.truenex-memory/agent_manifest.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from truenex_memory.discovery.agent_discovery import (
    add_agent_to_manifest,
    get_effective_agent_roots,
    heuristic_discovery,
    load_agent_manifest,
    remove_agent_from_manifest,
)


# ── helpers ───────────────────────────────────────────────────────────

def _manifest_path(home: Path) -> Path:
    return home / ".truenex-memory" / "agent_manifest.json"


def _patch_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: home)


# ── manifest lifecycle ────────────────────────────────────────────────

class TestManifestLifecycle:
    def test_manifest_created_with_defaults(self, tmp_path: Path, monkeypatch) -> None:
        """If the manifest file does not exist, load_agent_manifest creates it with embedded defaults."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        _patch_home(monkeypatch, fake_home)

        manifest = load_agent_manifest()

        manifest_file = _manifest_path(fake_home)
        assert manifest_file.exists(), "Manifest file should be created on disk"
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert "agents" in data
        assert len(data["agents"]) > 0
        # Verify the returned object matches what was written
        assert manifest == data

    def test_manifest_loads_from_disk(self, tmp_path: Path, monkeypatch) -> None:
        """If a manifest already exists on disk, it is read correctly."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        _patch_home(monkeypatch, fake_home)

        custom_data = {
            "version": 1,
            "agents": [
                {
                    "name": "custom",
                    "dir": ".custom",
                    "roots": [
                        {"label": "logs", "subdir": "logs"},
                    ],
                }
            ],
        }
        manifest_file = _manifest_path(fake_home)
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(custom_data), encoding="utf-8")

        manifest = load_agent_manifest()

        assert manifest["version"] == 1
        assert manifest["agents"][0]["name"] == "custom"
        assert manifest["agents"][0]["roots"][0]["subdir"] == "logs"

    def test_manifest_version_field(self, tmp_path: Path, monkeypatch) -> None:
        """The manifest must contain a version field."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        _patch_home(monkeypatch, fake_home)

        manifest = load_agent_manifest()

        assert "version" in manifest
        assert isinstance(manifest["version"], int)


# ── manifest mutations ────────────────────────────────────────────────

class TestManifestMutations:
    def test_manifest_add_agent(self, tmp_path: Path, monkeypatch) -> None:
        """Adding an agent persists it to the manifest file."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        _patch_home(monkeypatch, fake_home)

        # Ensure defaults are written first
        load_agent_manifest()

        add_agent_to_manifest("testagent", ".testagent", "sessions")

        manifest_file = _manifest_path(fake_home)
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        names = {a["name"] for a in data["agents"]}
        assert "testagent" in names

        agent = next(a for a in data["agents"] if a["name"] == "testagent")
        assert agent["roots"][0]["label"] == "sessions"
        assert agent["roots"][0]["subdir"] == "sessions"

    def test_manifest_remove_agent(self, tmp_path: Path, monkeypatch) -> None:
        """Removing an agent deletes it from the manifest file."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        _patch_home(monkeypatch, fake_home)

        load_agent_manifest()
        add_agent_to_manifest("deleteme", ".deleteme", "data")

        # Verify it exists
        manifest_file = _manifest_path(fake_home)
        data_before = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert any(a["name"] == "deleteme" for a in data_before["agents"])

        remove_agent_from_manifest("deleteme")

        data_after = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert not any(a["name"] == "deleteme" for a in data_after["agents"])


# ── effective roots ───────────────────────────────────────────────────

class TestEffectiveRoots:
    def test_get_effective_agent_roots_from_manifest(self, tmp_path: Path, monkeypatch) -> None:
        """get_effective_agent_roots returns (label, relative_dir, subdir) tuples from the manifest."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        _patch_home(monkeypatch, fake_home)

        custom_data = {
            "version": 1,
            "agents": [
                {
                    "name": "codex",
                    "dir": ".codex",
                    "roots": [
                        {"label": "sessions", "subdir": "sessions"},
                        {"label": "history", "subdir": "history.jsonl"},
                    ],
                },
                {
                    "name": "claude",
                    "dir": ".claude",
                    "roots": [
                        {"label": "projects", "subdir": "projects"},
                    ],
                },
            ],
        }
        manifest_file = _manifest_path(fake_home)
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(custom_data), encoding="utf-8")

        roots = get_effective_agent_roots()

        # Should be a list of tuples
        assert isinstance(roots, list)
        assert all(isinstance(r, tuple) and len(r) == 3 for r in roots)

        # Check specific entries
        assert ("codex-sessions", ".codex", "sessions") in roots
        assert ("codex-history", ".codex", "history.jsonl") in roots
        assert ("claude-projects", ".claude", "projects") in roots


# ── heuristic discovery ───────────────────────────────────────────────

class TestHeuristicDiscovery:
    def test_heuristic_discovery_ignores_manifest_entries(self, tmp_path: Path, monkeypatch) -> None:
        """heuristic_discovery does not return entries already present in the manifest."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        _patch_home(monkeypatch, fake_home)

        # Set up a manifest that already knows about .codex/sessions
        custom_data = {
            "version": 1,
            "agents": [
                {
                    "name": "codex",
                    "dir": ".codex",
                    "roots": [
                        {"label": "sessions", "subdir": "sessions"},
                    ],
                },
            ],
        }
        manifest_file = _manifest_path(fake_home)
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(custom_data), encoding="utf-8")

        # Create the corresponding directory on disk
        (fake_home / ".codex" / "sessions").mkdir(parents=True)
        (fake_home / ".codex" / "sessions" / "file.jsonl").write_text("{}", encoding="utf-8")

        found = heuristic_discovery(fake_home)

        keys = {(rel, sub) for _, rel, sub in found}
        assert (".codex", "sessions") not in keys, (
            "heuristic_discovery should skip entries already in the manifest"
        )
