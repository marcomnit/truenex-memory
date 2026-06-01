"""CLI commands for license management (truenex-mem license ...)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer

from truenex_memory.licensing import LicenseManager

license_app = typer.Typer(help="Manage Truenex Memory Pro license.")
_DEFAULT_DIR = Path.home() / ".truenex-memory"


def _manager(license_dir: Path | None = None) -> LicenseManager:
    return LicenseManager(license_dir or _DEFAULT_DIR)


@license_app.command("status")
def license_status(
    json_out: bool = typer.Option(False, "--json", help="Print status as JSON."),
    license_dir: Path | None = typer.Option(
        None,
        "--license-dir",
        help="Directory containing license.json (default: ~/.truenex-memory/).",
    ),
) -> None:
    """Show current license status."""
    status = _manager(license_dir).status()
    if json_out:
        typer.echo(json.dumps(status, indent=2, sort_keys=True))
        return

    typer.echo(f"Tier:    {status['tier']}")
    typer.echo(f"Status:  {status['status']}")
    if status["key"]:
        typer.echo(f"Key:     {status['key']}")
        typer.echo(f"Expires: {status['expires_at'] or 'never'}")
        remaining = status["days_remaining"]
        typer.echo(f"Days remaining: {remaining if remaining is not None else 'never'}")
        typer.echo(f"Features: {', '.join(status['features']) if status['features'] else 'none'}")
        typer.echo(f"Grace period: {'yes' if status['grace_period'] else 'no'} "
                   f"(offline grace: {status['offline_grace_days']} days)")


@license_app.command("activate")
def license_activate(
    key: str = typer.Argument(..., help="License key to activate."),
    tier: str = typer.Option("pro", "--tier", "-t", help="License tier: free, pro, or team."),
    expires_at: str | None = typer.Option(
        None,
        "--expires-at",
        help="Expiry date in ISO format (e.g. 2026-12-31). Leave blank for no expiry.",
    ),
    features: list[str] | None = typer.Option(
        None,
        "--feature",
        help="Feature flag to enable (repeatable).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Print result as JSON."),
    license_dir: Path | None = typer.Option(
        None,
        "--license-dir",
        help="Directory for license.json (default: ~/.truenex-memory/).",
    ),
) -> None:
    """Activate a Truenex Memory Pro license."""
    expiry_dt: datetime | None = None
    if expires_at:
        try:
            expiry_dt = datetime.fromisoformat(expires_at)
        except ValueError:
            raise typer.BadParameter(
                f"Invalid ISO date: {expires_at!r}. Use format YYYY-MM-DD or "
                f"YYYY-MM-DDTHH:MM:SS."
            ) from None
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)

    info = _manager(license_dir).activate(
        key=key,
        tier=tier,
        expires_at=expiry_dt,
        features=list(features or []),
    )

    if json_out:
        typer.echo(json.dumps(info.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(f"License activated: tier={info.tier}, "
                   f"expires={'never' if info.expires_at is None else info.expires_at.isoformat()}")


@license_app.command("deactivate")
def license_deactivate(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    json_out: bool = typer.Option(False, "--json", help="Print result as JSON."),
    license_dir: Path | None = typer.Option(
        None,
        "--license-dir",
        help="Directory containing license.json (default: ~/.truenex-memory/).",
    ),
) -> None:
    """Deactivate and remove the current license."""
    mgr = _manager(license_dir)

    if not yes:
        if not mgr.license_path.exists():
            typer.echo("No license file found; nothing to deactivate.")
            raise typer.Exit(code=0)
        status = mgr.status()
        try:
            answer = input(
                f"Deactivate {status['tier']} license (key: {status['key']})? [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            typer.echo("Aborted.")
            raise typer.Exit(code=1)
        if answer not in ("y", "yes"):
            typer.echo("Aborted.")
            raise typer.Exit(code=1)

    existed = mgr.deactivate()
    if json_out:
        typer.echo(json.dumps({"deactivated": existed}, indent=2))
    elif existed:
        typer.echo("License deactivated and removed.")
    else:
        typer.echo("No license file found; nothing to deactivate.")


@license_app.command("require")
def license_require(
    tier: str = typer.Argument(..., help="Minimum tier required: free, pro, or team."),
    json_out: bool = typer.Option(False, "--json", help="Print result as JSON."),
    license_dir: Path | None = typer.Option(
        None,
        "--license-dir",
        help="Directory containing license.json (default: ~/.truenex-memory/).",
    ),
) -> None:
    """Check if the current license meets a minimum tier requirement.

    Exits 0 if the requirement is met, 1 otherwise.
    """
    mgr = _manager(license_dir)
    satisfied = mgr.require_tier(tier)
    status = mgr.status()
    if json_out:
        typer.echo(json.dumps({
            "required_tier": tier,
            "current_tier": status["tier"],
            "satisfied": satisfied,
        }, indent=2))
    else:
        result = "OK" if satisfied else "NOT MET"
        typer.echo(f"Require {tier}: {result} (current: {status['tier']})")
    if not satisfied:
        raise typer.Exit(code=1)
