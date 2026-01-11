"""Pytest configuration and shared fixtures."""

import os
import pytest

# Set dummy env vars before importing modules that require them
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("LINEAR_API_KEY", "test-key")


@pytest.fixture
def temp_data_dir(tmp_path):
    """Create a temporary data directory for tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir
