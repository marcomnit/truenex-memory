"""Test package import and version."""

import truenex_memory


def test_package_imports() -> None:
    """Package should import cleanly."""
    assert truenex_memory is not None


def test_version_is_string() -> None:
    """__version__ should be a non-empty string."""
    v = truenex_memory.__version__
    assert isinstance(v, str)
    assert len(v) > 0
    assert v.count(".") == 2  # SemVer: MAJOR.MINOR.PATCH
