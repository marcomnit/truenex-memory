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


from truenex_memory import __version__


def test_version_command() -> None:
    """version command should print version and exit 0."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "truenex-mem" in result.stdout
    assert __version__ in result.stdout
