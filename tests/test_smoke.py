"""Smoke tests: the coordinator package must be importable."""

import coordinator


def test_package_imports() -> None:
    """The coordinator package imports and exposes a version string."""
    version = coordinator.__version__

    assert isinstance(version, str)
    assert version
