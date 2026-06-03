"""Shared fixtures for unit tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_check_license():
    """Bypass Pro license checks for CLI commands during tests."""
    with patch("truenex_memory.cli.main.check_license") as m:
        m.return_value = None
        yield
