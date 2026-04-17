"""
Container-based integration tests using testcontainers-python + PostgreSQL.

These tests spin up a real PostgreSQL 16 container (session-scoped) and verify
that the ETL pipeline works correctly against a production-grade database.

Requirements:
  - Docker daemon running
  - pip install testcontainers[postgres] psycopg sqlalchemy

The postgres_engine and postgres_container fixtures are defined in conftest.py.
Tests in this file are automatically skipped if Docker/testcontainers is unavailable.
"""

import pytest
from sqlalchemy import text

from etl import csv_to_db_pipeline, load_to_sqlalchemy, transform_rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def row_count(engine, table: str = "records") -> int:
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
        return result.scalar()


def fetch_all_records(engine) -> list[dict]:
    with engine.connect() as conn:
        result = conn.execute(text("SELECT name, value FROM records ORDER BY name"))
        return [{"name": r[0], "value": r[1]} for r in result]


@pytest.fixture(autouse=True)
def clean_table(postgres_engine):
    """Drop and recreate the records table before each test for isolation."""
    with postgres_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS records"))
    yield


# ---------------------------------------------------------------------------
# Individual task tests against PostgreSQL
# ---------------------------------------------------------------------------


class TestLoadToSqlAlchemyPostgres:
    def test_inserts_records(self, postgres_engine):
        records = [{"name": "pg_alpha", "value": 10.0}, {"name": "pg_beta", "value": 20.5}]
        count = load_to_sqlalchemy.fn(postgres_engine, records)
        assert count == 2

    def test_data_persisted_correctly(self, postgres_engine):
        records = [{"name": "x", "value": 3.14}, {"name": "y", "value": 2.71}]
        load_to_sqlalchemy.fn(postgres_engine, records)

        rows = fetch_all_records(postgres_engine)
        assert len(rows) == 2
        assert rows[0]["name"] == "x"
        assert rows[0]["value"] == pytest.approx(3.14)

    def test_null_value_allowed(self, postgres_engine):
        records = [{"name": "no_val", "value": None}]
        load_to_sqlalchemy.fn(postgres_engine, records)

        rows = fetch_all_records(postgres_engine)
        assert rows[0]["value"] is None

    def test_large_batch(self, postgres_engine):
        records = [{"name": f"item_{i}", "value": float(i)} for i in range(500)]
        count = load_to_sqlalchemy.fn(postgres_engine, records)
        assert count == 500
        assert row_count(postgres_engine) == 500


# ---------------------------------------------------------------------------
# Full pipeline against PostgreSQL
# ---------------------------------------------------------------------------


class TestCsvToDbPipelinePostgres:
    def test_full_pipeline(self, tmp_csv, postgres_engine):
        result = csv_to_db_pipeline(tmp_csv, postgres_engine)
        assert result["processed"] == 3
        assert result["status"] == "success"
        assert row_count(postgres_engine) == 3

    def test_pipeline_state_completed(self, tmp_csv, postgres_engine):
        state = csv_to_db_pipeline(tmp_csv, postgres_engine, return_state=True)
        assert state.is_completed()
        assert state.result()["processed"] == 3

    def test_pipeline_with_skipped_rows(self, tmp_csv_with_bad_rows, postgres_engine):
        result = csv_to_db_pipeline(tmp_csv_with_bad_rows, postgres_engine)
        # "bad_row_no_comma" is skipped by transform_rows
        assert result["processed"] == 2
        assert row_count(postgres_engine) == 2


# ---------------------------------------------------------------------------
# Schema / constraint testing (PostgreSQL-specific)
# ---------------------------------------------------------------------------


class TestPostgresConstraints:
    def test_name_column_is_not_null(self, postgres_engine):
        """Verify the NOT NULL constraint on name is enforced by PostgreSQL."""
        load_to_sqlalchemy.fn(postgres_engine, [{"name": "seed", "value": 1.0}])

        with postgres_engine.begin() as conn:
            with pytest.raises(Exception):  # sqlalchemy.exc.IntegrityError
                conn.execute(
                    text("INSERT INTO records (name, value) VALUES (NULL, 5.0)")
                )

    def test_serial_primary_key_auto_increments(self, postgres_engine):
        records = [{"name": "a", "value": 1.0}, {"name": "b", "value": 2.0}]
        load_to_sqlalchemy.fn(postgres_engine, records)

        with postgres_engine.connect() as conn:
            ids = conn.execute(text("SELECT id FROM records ORDER BY id")).scalars().all()

        assert ids[1] > ids[0]  # auto-incremented
