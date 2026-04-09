"""Shared pytest fixtures for cclog tests."""
import sqlite3
import pytest


@pytest.fixture
def mem_conn():
    """Return an in-memory SQLite connection with the cclog schema applied."""
    from cclog.db import setup_schema  # imported here so Task 1 tests don't fail

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        setup_schema(conn)
        yield conn
    finally:
        conn.close()
