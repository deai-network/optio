"""Shared test fixtures for optio-agents."""

import shutil
import tempfile

import pytest


@pytest.fixture
def tmp_workdir():
    """A temporary directory that is removed after the test."""
    path = tempfile.mkdtemp(prefix="optio-agents-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
