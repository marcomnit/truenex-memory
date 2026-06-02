"""License enforcement helpers for CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from truenex_memory.licensing import LicenseManager


def _default_license_dir() -> Path:
    return Path.home() / ".truenex-memory"


def check_license(tier: str = "pro", *, json_out: bool = False) -> None:
    """Check if the current license meets *tier*. Exit with code 1 if not.

    Parameters
    ----------
    tier:
        Minimum tier required (e.g. ``"pro"`` or ``"team"``).
    json_out:
        When ``True``, emit a JSON error object on stdout before exiting.
    """
    mgr = LicenseManager(_default_license_dir())
    satisfied = mgr.require_tier(tier)
    status = mgr.status()

    if satisfied:
        return

    msg = (
        f"🔒  Pro license required. "
        f"Current tier: {status['tier']}. "
        f"Activate with: truenex-mem license activate <YOUR_KEY>"
    )

    if json_out:
        typer.echo(
            json.dumps(
                {
                    "status": "error",
                    "error": "license_required",
                    "required_tier": tier,
                    "current_tier": status["tier"],
                    "message": msg,
                }
            )
        )
    else:
        typer.echo(msg, err=True)
        typer.echo(
            "   Purchase: https://buy.stripe.com/4gM14g1jta8MfesdxsgA800", err=True
        )

    raise typer.Exit(code=1)
