"""
Shared pytest fixtures for all Prefect + database test files.

Structure:
  - prefect_test_fixture  : session-scoped Prefect harness (avoids per-test overhead)
  - tmp_csv               : a small CSV file written to a temp path
  - sqlite_db             : empty SQLite DB path (cleaned up after each test)
  - duckdb_conn           : in-memory DuckDB connection seeded with sample data
  - postgres_engine       : SQLAlchemy engine against a Dockerised PostgreSQL
                            (only used in test_containers.py; requires Docker)
"""

import os
import sqlite3
import tempfile

import pytest

from prefect.testing.utilities import prefect_test_harness


# ---------------------------------------------------------------------------
# Prefect harness
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def prefect_test_fixture():
    """
    Start a single lightweight Prefect server for the whole test session.
    Session scope avoids the ~1-2 s per-test database recreation overhead.
    """
    with prefect_test_harness():
        yield


# ---------------------------------------------------------------------------
# File-system helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_csv(tmp_path):
    """Write a small CSV file and return its path."""
    csv_file = tmp_path / "data.csv"
    csv_file.write_text(
        "alpha,1.5\n"
        "beta,2.0\n"
        "gamma,3.75\n"
    )
    return str(csv_file)


@pytest.fixture
def tmp_csv_with_bad_rows(tmp_path):
    """CSV that contains rows missing the value column."""
    csv_file = tmp_path / "bad_data.csv"
    csv_file.write_text(
        "alpha,1.5\n"
        "bad_row_no_comma\n"
        "gamma,3.75\n"
    )
    return str(csv_file)


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_db(tmp_path):
    """Return a path to a fresh (empty) SQLite database file."""
    db_path = str(tmp_path / "test.db")
    yield db_path
    # Cleanup is automatic because tmp_path is function-scoped


@pytest.fixture
def seeded_sqlite_db(tmp_path):
    """SQLite DB pre-populated with a 'records' table and two rows."""
    db_path = str(tmp_path / "seeded.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE records (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value REAL)"
    )
    conn.executemany(
        "INSERT INTO records (name, value) VALUES (?, ?)",
        [("existing_a", 10.0), ("existing_b", 20.0)],
    )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# DuckDB
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_conn():
    """In-memory DuckDB connection with a sample 'sales' table."""
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE sales (
            region  VARCHAR,
            product VARCHAR,
            amount  DOUBLE
        )
        """
    )
    conn.executemany(
        "INSERT INTO sales VALUES (?, ?, ?)",
        [
            ("US", "laptop", 999.99),
            ("US", "mouse", 29.99),
            ("EU", "laptop", 1099.00),
        ],
    )
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# PostgreSQL via testcontainers (requires Docker)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container():
    """
    Start a PostgreSQL 16 container for the session.
    Skipped automatically if testcontainers or Docker is unavailable.
    """
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    container = PostgresContainer("postgres:16-alpine")
    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker/PostgreSQL container unavailable: {exc}")

    yield container
    container.stop()


@pytest.fixture(scope="session")
def postgres_engine(postgres_container):
    """Session-scoped SQLAlchemy engine connected to the test PostgreSQL container."""
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from sqlalchemy import create_engine

    url = postgres_container.get_connection_url()
    engine = create_engine(url)
    yield engine
    engine.dispose()
