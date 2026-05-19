"""Test package import and version."""

import re

import truenex_memory


def test_package_imports() -> None:
    """Package should import cleanly."""
    assert truenex_memory is not None


def test_version_is_string() -> None:
    """__version__ should be a non-empty semver string."""
    v = truenex_memory.__version__
    assert isinstance(v, str)
    assert len(v) > 0
    # Support pre-release versions like 0.2.0a1 or 0.1.0-alpha.1
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v.split("-")[0])
    assert m is not None, f"Expected MAJOR.MINOR.PATCH numeric prefix in {v}"
    assert all(g.isdigit() for g in m.groups()), f"First three version segments must be numeric in {v}"
