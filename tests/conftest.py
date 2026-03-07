import os
import sys

import pytest

# Allow imports from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(scope="session", autouse=True)
def check_stack():
    """Verify MindsDB + PostgreSQL are reachable before running any test."""
    from agent_client import _get_server

    try:
        _get_server().query("SELECT 1")
    except Exception as e:
        pytest.skip(f"Docker stack not running: {e}")
