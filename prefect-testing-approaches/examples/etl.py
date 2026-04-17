"""
Sample ETL pipeline: CSV → transform → SQLite / PostgreSQL.

This module is the system-under-test for all example test files.
"""

import logging
import sqlite3
from pathlib import Path

from prefect import flow, task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@task(name="extract-csv", retries=2, retry_delay_seconds=1)
def extract_csv(source_path: str) -> list[list[str]]:
    """Read a CSV file and return rows as lists of strings."""
    path = Path(source_path)
    rows = []
    with path.open() as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                rows.append(stripped.split(","))
    logger.info("Extracted %d rows from %s", len(rows), source_path)
    return rows


@task(name="transform-rows")
def transform_rows(rows: list[list[str]]) -> list[dict]:
    """Convert raw rows to dicts, coercing numeric columns."""
    transformed = []
    for row in rows:
        if len(row) < 2:
            continue
        transformed.append({"name": row[0].strip(), "value": _to_float(row[1])})
    return transformed


@task(name="load-sqlite")
def load_to_sqlite(db_path: str, records: list[dict]) -> int:
    """Upsert records into a SQLite table; return the count loaded."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT    NOT NULL,
                value REAL
            )
            """
        )
        cursor.executemany(
            "INSERT INTO records (name, value) VALUES (:name, :value)",
            records,
        )
        conn.commit()
        return len(records)
    finally:
        conn.close()


@task(name="load-sqlalchemy")
def load_to_sqlalchemy(engine, records: list[dict]) -> int:
    """Load records using a SQLAlchemy engine (works with any dialect)."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id    SERIAL PRIMARY KEY,
                    name  TEXT NOT NULL,
                    value FLOAT
                )
                """
            )
        )
        for rec in records:
            conn.execute(
                text("INSERT INTO records (name, value) VALUES (:name, :value)"),
                rec,
            )
    return len(records)


# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------


@flow(name="csv-to-sqlite-etl")
def csv_to_sqlite_pipeline(source_path: str, db_path: str) -> dict:
    """Full ETL: CSV file → SQLite database."""
    rows = extract_csv(source_path)
    records = transform_rows(rows)
    count = load_to_sqlite(db_path, records)
    return {"processed": count, "status": "success"}


@flow(name="csv-to-db-etl")
def csv_to_db_pipeline(source_path: str, engine) -> dict:
    """Full ETL using a SQLAlchemy engine (dialect-agnostic)."""
    rows = extract_csv(source_path)
    records = transform_rows(rows)
    count = load_to_sqlalchemy(engine, records)
    return {"processed": count, "status": "success"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None
