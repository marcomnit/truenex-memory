"""Agent instruction file generators."""

from __future__ import annotations

from pathlib import Path

AGENTS_FILENAME = "AGENTS.md"
CLAUDE_FILENAME = "CLAUDE.md"


def render_agents_md(project_name: str = "Truenex Memory") -> str:
    """Render an AGENTS.md file for local coding agents."""

    return f"""# {project_name} Agent Notes

- Keep memory data local by default.
- Do not require cloud services for diagnostics, import, export, or local tools.
- Prefer small, testable changes and avoid unrelated rewrites.
- Run focused tests for touched surfaces before handing work back.
"""


def render_claude_md(project_name: str = "Truenex Memory") -> str:
    """Render a CLAUDE.md file for Claude-style coding agents."""

    return f"""# {project_name}

Use this repository as a local-first memory layer for coding agents.

## Working Rules

- Keep generated memory artifacts in local files unless the user asks otherwise.
- Treat MCP tools as local callable helpers during development.
- Do not assume production cloud, licensing, or UI services are available.
"""


def write_agent_docs(
    directory: str | Path,
    *,
    project_name: str = "Truenex Memory",
    overwrite: bool = False,
) -> dict[str, Path]:
    """Write AGENTS.md and CLAUDE.md files into a directory."""

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    files = {
        AGENTS_FILENAME: render_agents_md(project_name),
        CLAUDE_FILENAME: render_claude_md(project_name),
    }
    written: dict[str, Path] = {}
    for filename, content in files.items():
        path = target / filename
        if path.exists() and not overwrite:
            raise FileExistsError(f"{path} already exists")
        path.write_text(content, encoding="utf-8")
        written[filename] = path
    return written


__all__ = [
    "AGENTS_FILENAME",
    "CLAUDE_FILENAME",
    "render_agents_md",
    "render_claude_md",
    "write_agent_docs",
]
