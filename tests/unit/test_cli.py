"""Test CLI commands."""

from typer.testing import CliRunner

from truenex_memory.cli.main import app

runner = CliRunner()


def test_help() -> None:
    """--help should print usage and exit 0."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "truenex-mem" in result.stdout
    assert "Local-first" in result.stdout


def test_version_command() -> None:
    """version command should print version and exit 0."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "truenex-mem" in result.stdout
    assert "0.1.0" in result.stdout
