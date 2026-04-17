"""
Integration tests using real (lightweight) databases.

Approaches demonstrated:
  1. SQLite file-based — real I/O, no containers, full dialect coverage for SQLite workloads.
  2. DuckDB in-memory — analytics SQL (window functions, ARRAY, etc.) without a server.

The session-scoped prefect_test_harness in conftest.py is active automatically (autouse=True),
so flows can be called normally here without any additional setup.
"""

import sqlite3

import pytest

from etl import (
    csv_to_sqlite_pipeline,
    extract_csv,
    load_to_sqlite,
    transform_rows,
)


# ---------------------------------------------------------------------------
# SQLite integration — individual tasks
# ---------------------------------------------------------------------------


class TestLoadToSqliteReal:
    """Call the task function directly against a real SQLite file."""

    def test_creates_table_and_inserts(self, sqlite_db):
        records = [{"name": "alpha", "value": 1.5}, {"name": "beta", "value": 2.0}]
        count = load_to_sqlite.fn(sqlite_db, records)
        assert count == 2

        conn = sqlite3.connect(sqlite_db)
        rows = conn.execute("SELECT name, value FROM records ORDER BY name").fetchall()
        conn.close()

        assert rows == [("alpha", 1.5), ("beta", 2.0)]

    def test_idempotent_on_multiple_calls(self, sqlite_db):
        records = [{"name": "x", "value": 9.0}]
        load_to_sqlite.fn(sqlite_db, records)
        load_to_sqlite.fn(sqlite_db, records)

        conn = sqlite3.connect(sqlite_db)
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        conn.close()
        assert count == 2  # two separate inserts, not upsert

    def test_empty_records_loads_zero(self, sqlite_db):
        count = load_to_sqlite.fn(sqlite_db, [])
        assert count == 0

        conn = sqlite3.connect(sqlite_db)
        count_db = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        conn.close()
        assert count_db == 0

    def test_null_value_stored_correctly(self, sqlite_db):
        records = [{"name": "no_value", "value": None}]
        load_to_sqlite.fn(sqlite_db, records)

        conn = sqlite3.connect(sqlite_db)
        row = conn.execute("SELECT value FROM records").fetchone()
        conn.close()
        assert row[0] is None

    def test_pre_existing_table_appends(self, seeded_sqlite_db):
        """
        seeded_sqlite_db already has 2 rows; adding 1 more should give 3 total.
        """
        records = [{"name": "new_row", "value": 5.5}]
        load_to_sqlite.fn(seeded_sqlite_db, records)

        conn = sqlite3.connect(seeded_sqlite_db)
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        conn.close()
        assert count == 3


# ---------------------------------------------------------------------------
# SQLite integration — full pipeline (tasks called within a flow)
# ---------------------------------------------------------------------------


class TestCsvToSqlitePipeline:
    def test_full_pipeline_returns_expected_count(self, tmp_csv, sqlite_db):
        result = csv_to_sqlite_pipeline(tmp_csv, sqlite_db)
        assert result["processed"] == 3
        assert result["status"] == "success"

    def test_data_is_persisted_correctly(self, tmp_csv, sqlite_db):
        csv_to_sqlite_pipeline(tmp_csv, sqlite_db)

        conn = sqlite3.connect(sqlite_db)
        rows = conn.execute("SELECT name, value FROM records ORDER BY name").fetchall()
        conn.close()

        assert ("alpha", 1.5) in rows
        assert ("beta", 2.0) in rows
        assert ("gamma", 3.75) in rows

    def test_pipeline_with_bad_rows_skips_them(self, tmp_csv_with_bad_rows, sqlite_db):
        result = csv_to_sqlite_pipeline(tmp_csv_with_bad_rows, sqlite_db)
        # "bad_row_no_comma" has no comma → transform skips it
        assert result["processed"] == 2


# ---------------------------------------------------------------------------
# DuckDB in-memory integration
# ---------------------------------------------------------------------------


class TestDuckDBAnalytics:
    """
    Demonstrate testing analytics tasks (aggregations, window functions)
    using a DuckDB in-memory fixture seeded in conftest.py.
    """

    def test_total_sales_per_region(self, duckdb_conn):
        result = duckdb_conn.execute(
            "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region"
        ).fetchall()

        totals = dict(result)
        assert totals["US"] == pytest.approx(999.99 + 29.99)
        assert totals["EU"] == pytest.approx(1099.00)

    def test_window_function_rank(self, duckdb_conn):
        result = duckdb_conn.execute(
            """
            SELECT product,
                   amount,
                   RANK() OVER (ORDER BY amount DESC) AS rnk
            FROM sales
            """
        ).fetchall()

        # Highest amount should rank 1
        ranked = {row[0]: row[2] for row in result}
        assert ranked["laptop"] == 1 or ranked["laptop"] == 2  # two laptops

    def test_duckdb_task_via_fn(self, duckdb_conn):
        """Show how to call a custom analytics task directly with DuckDB conn."""
        from prefect import task

        @task
        def summarise_sales(conn) -> dict:
            rows = conn.execute(
                "SELECT region, COUNT(*) AS cnt FROM sales GROUP BY region ORDER BY region"
            ).fetchall()
            return dict(rows)

        summary = summarise_sales.fn(duckdb_conn)
        assert summary["US"] == 2
        assert summary["EU"] == 1
