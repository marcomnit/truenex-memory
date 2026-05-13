"""Version constants for Truenex Memory."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from truenex_memory import __version__


APP_VERSION = __version__
DB_SCHEMA_VERSION = "4"
MCP_TOOLS_VERSION = "1"
LICENSE_FORMAT_VERSION = "1"
MEMORY_EXPORT_VERSION = "1"
CLOUD_API_VERSION = "0"
DEFAULT_UPDATE_CHANNEL = "dev"


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
