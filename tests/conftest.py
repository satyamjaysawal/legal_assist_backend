"""Shared pytest fixtures — path bootstrap and the FastAPI TestClient."""

import sys
from pathlib import Path

import pytest

# Make the backend root importable (services.*, agents.*, main, …)
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient for the whole app (no external services required)."""
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)
