"""
pytest configuration — add src/ and project root to sys.path
so tests can import both rragent (src layout) and top-level modules
(evolution, gateway, workers, etc.)

Marks:
  - `live`: tests that require a running server (skip by default)
  - `integration`: tests that require live API credentials
"""
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "live: tests requiring a live running server")
    config.addinivalue_line("markers", "integration: tests requiring live API credentials")


def pytest_collection_modifyitems(config, items):
    """Auto-skip live/integration tests unless --live flag given."""
    if config.getoption("--live", default=False):
        return
    skip_live = pytest.mark.skip(reason="requires live server (use --live to enable)")
    for item in items:
        if "live" in item.keywords or "integration" in item.keywords:
            item.add_marker(skip_live)


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="run live server tests")
