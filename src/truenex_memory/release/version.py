"""Version constants for Truenex Memory."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from truenex_memory import __version__


APP_VERSION = __version__
# 8 adds memory_nodes.superseded_by. The bump is what makes the column
# reach an EXISTING store: migrate_apply() returns early when the recorded
# version already equals this one, so adding an entry to
# apply_column_upgrades() without bumping here leaves every store that is
# already at the previous version without the column — new databases get
# it from CREATE TABLE and the tests pass, while the live store fails at
# runtime with "no such column".
DB_SCHEMA_VERSION = "8"
MCP_TOOLS_VERSION = "1"
LICENSE_FORMAT_VERSION = "1"
MEMORY_EXPORT_VERSION = "1"
CLOUD_API_VERSION = "0"
DEFAULT_UPDATE_CHANNEL = "stable"


@dataclass(frozen=True)
class VersionInfo:
    """Distinct version values used by release and migration code."""

    app_version: str = APP_VERSION
    db_schema_version: str = DB_SCHEMA_VERSION
    mcp_tools_version: str = MCP_TOOLS_VERSION
    license_format_version: str = LICENSE_FORMAT_VERSION
    memory_export_version: str = MEMORY_EXPORT_VERSION
    cloud_api_version: str = CLOUD_API_VERSION
    update_channel: str = DEFAULT_UPDATE_CHANNEL


def get_version_info() -> dict[str, str]:
    """Return version info as a JSON-friendly dict."""

    return asdict(VersionInfo())
