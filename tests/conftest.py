"""Pytest configuration and fixtures."""

import pytest
import tempfile
import sqlite3
from pathlib import Path
from curator import db


@pytest.fixture
def temp_archive():
    """Create a temporary archive directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def archive_with_db(temp_archive):
    """Create an archive with initialized database."""
    conn = db.init_db(temp_archive)
    yield temp_archive, conn
    conn.close()
