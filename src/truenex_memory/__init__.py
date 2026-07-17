"""Truenex Memory - Local-first memory layer for coding agents."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("truenex-memory")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0-dev"
