"""Smoke test: the package is importable and exposes a version."""
import cclog


def test_package_importable():
    assert cclog is not None


def test_version_string():
    assert isinstance(cclog.__version__, str)
    assert len(cclog.__version__) > 0
