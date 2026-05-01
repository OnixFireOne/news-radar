import os
import sqlite3
import tempfile
import pytest
import respx

# Ensure we can import from the main app
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.schema import init_db, get_db

@pytest.fixture
def test_db():
    """
    Creates a temporary SQLite database, runs migrations, 
    yields the path, and deletes it after the test.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    # Initialize schema
    init_db(path)
    
    yield path
    
    # Cleanup
    try:
        os.unlink(path)
    except OSError:
        pass

@pytest.fixture
def db_conn(test_db):
    """Yields a connection to the test database that auto-closes."""
    conn = get_db(test_db)
    yield conn
    conn.close()

@pytest.fixture
def mock_openclaw():
    """Provides a mocked HTTP environment for testing Agent routing without real requests."""
    with respx.mock(assert_all_mocked=False) as respx_mock:
        yield respx_mock
