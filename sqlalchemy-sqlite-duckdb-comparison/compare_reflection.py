"""
SQLAlchemy reflection & primary-key inspection: SQLite vs DuckDB comparison.

Exercises every major Inspector/reflection surface and documents what works,
what raises, and what raw SQL workaround is required for DuckDB.
"""
import json
import textwrap
import traceback
from dataclasses import dataclass, field
from typing import Any

import duckdb
import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class Result:
    feature: str
    sqlite_ok: bool
    sqlite_value: Any
    duckdb_ok: bool
    duckdb_value: Any
    duckdb_workaround: str = ""

results: list[Result] = []

def record(feature, sqlite_ok, sqlite_val, duckdb_ok, duckdb_val, workaround=""):
    results.append(Result(feature, sqlite_ok, sqlite_val, duckdb_ok, duckdb_val, workaround))

# ---------------------------------------------------------------------------
# Schema used for both databases
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS department (
    dept_id   INTEGER PRIMARY KEY,
    dept_name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS employee (
    emp_id     INTEGER,
    dept_id    INTEGER NOT NULL,
    first_name VARCHAR(50),
    last_name  VARCHAR(50),
    salary     NUMERIC(10,2),
    CONSTRAINT pk_employee PRIMARY KEY (emp_id, dept_id),
    CONSTRAINT fk_emp_dept FOREIGN KEY (dept_id) REFERENCES department(dept_id),
    CONSTRAINT chk_salary  CHECK (salary > 0)
);

CREATE INDEX IF NOT EXISTS idx_emp_last ON employee(last_name);
"""

DUCKDB_DDL = """
CREATE TABLE IF NOT EXISTS department (
    dept_id   INTEGER PRIMARY KEY,
    dept_name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS employee (
    emp_id     INTEGER,
    dept_id    INTEGER NOT NULL,
    first_name VARCHAR(50),
    last_name  VARCHAR(50),
    salary     DECIMAL(10,2),
    CONSTRAINT pk_employee PRIMARY KEY (emp_id, dept_id),
    CONSTRAINT fk_emp_dept FOREIGN KEY (dept_id) REFERENCES department(dept_id),
    CONSTRAINT chk_salary  CHECK (salary > 0)
);

CREATE INDEX IF NOT EXISTS idx_emp_last ON employee(last_name);
"""


def setup_sqlite(path=":memory:"):
    eng = create_engine(f"sqlite:///{path}", echo=False)
    with eng.begin() as conn:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
    return eng


def setup_duckdb(path=":memory:"):
    eng = create_engine(f"duckdb:///{path}", echo=False)
    with eng.begin() as conn:
        for stmt in DUCKDB_DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
    return eng


# ---------------------------------------------------------------------------
# Raw-SQL workarounds for DuckDB
# ---------------------------------------------------------------------------

DUCKDB_PK_WORKAROUND = textwrap.dedent("""\
    -- DuckDB workaround: query information_schema for primary key columns
    SELECT kcu.column_name, kcu.ordinal_position
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema    = kcu.table_schema
     AND tc.table_name      = kcu.table_name
    WHERE tc.constraint_type = 'PRIMARY KEY'
      AND tc.table_schema    = 'main'
      AND tc.table_name      = :table
    ORDER BY kcu.ordinal_position;
""")

DUCKDB_FK_WORKAROUND = textwrap.dedent("""\
    -- DuckDB workaround: foreign keys via information_schema
    SELECT
        kcu.column_name                    AS constrained_column,
        ccu.table_name                     AS referred_table,
        ccu.column_name                    AS referred_column,
        rc.constraint_name
    FROM information_schema.referential_constraints rc
    JOIN information_schema.key_column_usage kcu
      ON kcu.constraint_name = rc.constraint_name
     AND kcu.table_schema    = rc.constraint_schema
    JOIN information_schema.key_column_usage ccu
      ON ccu.constraint_name = rc.unique_constraint_name
     AND ccu.table_schema    = rc.constraint_schema
    WHERE rc.constraint_schema = 'main'
      AND kcu.table_name       = :table;
""")

DUCKDB_UNIQUE_WORKAROUND = textwrap.dedent("""\
    -- DuckDB workaround: unique constraints via information_schema
    SELECT tc.constraint_name, kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema    = kcu.table_schema
     AND tc.table_name      = kcu.table_name
    WHERE tc.constraint_type = 'UNIQUE'
      AND tc.table_schema    = 'main'
      AND tc.table_name      = :table
    ORDER BY kcu.ordinal_position;
""")

DUCKDB_CHECK_WORKAROUND = textwrap.dedent("""\
    -- DuckDB workaround: check constraints via information_schema
    SELECT tc.constraint_name, cc.check_clause
    FROM information_schema.table_constraints tc
    JOIN information_schema.check_constraints cc
      ON tc.constraint_name = cc.constraint_name
     AND tc.table_schema    = cc.constraint_schema
    WHERE tc.constraint_type = 'CHECK'
      AND tc.table_schema    = 'main'
      AND tc.table_name      = :table;
""")

DUCKDB_INDEX_WORKAROUND = textwrap.dedent("""\
    -- DuckDB workaround: indexes via duckdb_indexes() table function
    SELECT index_name, index_oid, sql
    FROM duckdb_indexes()
    WHERE table_name = :table
      AND schema_name = 'main';
""")

DUCKDB_COLUMNS_WORKAROUND = textwrap.dedent("""\
    -- DuckDB workaround: column details via information_schema.columns
    SELECT column_name, data_type, is_nullable, column_default, ordinal_position
    FROM information_schema.columns
    WHERE table_schema = 'main'
      AND table_name   = :table
    ORDER BY ordinal_position;
""")


# ---------------------------------------------------------------------------
# Probe each Inspector feature
# ---------------------------------------------------------------------------

def probe(eng_sqlite, eng_duckdb):

    # --- 1. get_table_names ---
    feat = "Inspector.get_table_names()"
    try:
        insp = inspect(eng_sqlite)
        sv = sorted(insp.get_table_names())
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        dv = sorted(insp.get_table_names())
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv)

    # --- 2. get_columns() ---
    feat = "Inspector.get_columns('employee')"
    try:
        insp = inspect(eng_sqlite)
        sv = [(c["name"], str(c["type"]), c["nullable"]) for c in insp.get_columns("employee")]
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        dv = [(c["name"], str(c["type"]), c["nullable"]) for c in insp.get_columns("employee")]
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv,
           workaround=DUCKDB_COLUMNS_WORKAROUND if not dok else "")

    # --- 3. get_pk_constraint() ---
    feat = "Inspector.get_pk_constraint('employee')"
    try:
        insp = inspect(eng_sqlite)
        sv = insp.get_pk_constraint("employee")
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        dv = insp.get_pk_constraint("employee")
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv,
           workaround=DUCKDB_PK_WORKAROUND if not dok else "")

    # --- 4. get_pk_constraint() single-col table ---
    feat = "Inspector.get_pk_constraint('department')"
    try:
        insp = inspect(eng_sqlite)
        sv = insp.get_pk_constraint("department")
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        dv = insp.get_pk_constraint("department")
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv,
           workaround=DUCKDB_PK_WORKAROUND if not dok else "")

    # --- 5. get_foreign_keys() ---
    feat = "Inspector.get_foreign_keys('employee')"
    try:
        insp = inspect(eng_sqlite)
        sv = insp.get_foreign_keys("employee")
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        dv = insp.get_foreign_keys("employee")
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv,
           workaround=DUCKDB_FK_WORKAROUND if not dok else "")

    # --- 6. get_unique_constraints() ---
    feat = "Inspector.get_unique_constraints('department')"
    try:
        insp = inspect(eng_sqlite)
        sv = insp.get_unique_constraints("department")
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        dv = insp.get_unique_constraints("department")
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv,
           workaround=DUCKDB_UNIQUE_WORKAROUND if not dok else "")

    # --- 7. get_check_constraints() ---
    feat = "Inspector.get_check_constraints('employee')"
    try:
        insp = inspect(eng_sqlite)
        sv = insp.get_check_constraints("employee")
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        dv = insp.get_check_constraints("employee")
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv,
           workaround=DUCKDB_CHECK_WORKAROUND if not dok else "")

    # --- 8. get_indexes() ---
    feat = "Inspector.get_indexes('employee')"
    try:
        insp = inspect(eng_sqlite)
        sv = insp.get_indexes("employee")
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        dv = insp.get_indexes("employee")
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv,
           workaround=DUCKDB_INDEX_WORKAROUND if not dok else "")

    # --- 9. MetaData.reflect() autoload ---
    feat = "MetaData.reflect() full autoload"
    try:
        m = MetaData()
        m.reflect(bind=eng_sqlite)
        sv = sorted(m.tables.keys())
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        m = MetaData()
        m.reflect(bind=eng_duckdb)
        dv = sorted(m.tables.keys())
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv)

    # --- 10. Table autoload_with (single table reflection) ---
    feat = "Table(..., autoload_with=engine)"
    try:
        m = MetaData()
        t = Table("employee", m, autoload_with=eng_sqlite)
        sv = {
            "columns": [c.name for c in t.columns],
            "pk": [c.name for c in t.primary_key],
        }
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        m = MetaData()
        t = Table("employee", m, autoload_with=eng_duckdb)
        dv = {
            "columns": [c.name for c in t.columns],
            "pk": [c.name for c in t.primary_key],
        }
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv)

    # --- 11. Reflected PK constraint name ---
    feat = "PK constraint name preserved after reflection"
    try:
        insp = inspect(eng_sqlite)
        sv = insp.get_pk_constraint("employee").get("name")
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        pk = insp.get_pk_constraint("employee")
        dv = pk.get("name") if isinstance(pk, dict) else pk
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv)

    # --- 12. Raw SQL PK workaround on DuckDB ---
    feat = "Raw SQL PK workaround on DuckDB (information_schema)"
    try:
        with eng_duckdb.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT kcu.column_name, kcu.ordinal_position
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema    = kcu.table_schema
                     AND tc.table_name      = kcu.table_name
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema    = 'main'
                      AND tc.table_name      = 'employee'
                    ORDER BY kcu.ordinal_position
                """)
            ).fetchall()
        dv = [dict(r._mapping) for r in rows]
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    try:
        with eng_sqlite.connect() as conn:
            rows = conn.execute(
                text("PRAGMA table_info('employee')")
            ).fetchall()
        sv = [{"column_name": r[1], "ordinal_position": r[0]} for r in rows if r[5]]
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    record(feat, sok, sv, dok, dv)

    # --- 13. has_table() ---
    feat = "Inspector.has_table('employee')"
    try:
        insp = inspect(eng_sqlite)
        sv = insp.has_table("employee")
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        dv = insp.has_table("employee")
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv)

    # --- 14. get_schema_names() ---
    feat = "Inspector.get_schema_names()"
    try:
        insp = inspect(eng_sqlite)
        sv = insp.get_schema_names()
        sok = True
    except Exception as e:
        sv, sok = str(e), False

    try:
        insp = inspect(eng_duckdb)
        dv = insp.get_schema_names()
        dok = True
    except Exception as e:
        dv, dok = str(e), False

    record(feat, sok, sv, dok, dv)


# ---------------------------------------------------------------------------
# Run raw SQL workarounds on DuckDB and verify they return correct data
# ---------------------------------------------------------------------------

def verify_workarounds(eng_duckdb):
    print("\n" + "="*70)
    print("VERIFYING RAW SQL WORKAROUNDS ON DUCKDB")
    print("="*70)

    queries = {
        "PK columns (employee)": (
            """
            SELECT kcu.column_name, kcu.ordinal_position
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
             AND tc.table_name      = kcu.table_name
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema    = 'main'
              AND tc.table_name      = 'employee'
            ORDER BY kcu.ordinal_position
            """,
        ),
        "FK constraints (employee)": (
            """
            SELECT
                kcu.column_name                    AS constrained_column,
                ccu.table_name                     AS referred_table,
                ccu.column_name                    AS referred_column,
                rc.constraint_name
            FROM information_schema.referential_constraints rc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = rc.constraint_name
             AND kcu.table_schema    = rc.constraint_schema
            JOIN information_schema.key_column_usage ccu
              ON ccu.constraint_name = rc.unique_constraint_name
             AND ccu.table_schema    = rc.constraint_schema
            WHERE rc.constraint_schema = 'main'
              AND kcu.table_name       = 'employee'
            """,
        ),
        "UNIQUE constraints (department)": (
            """
            SELECT tc.constraint_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
             AND tc.table_name      = kcu.table_name
            WHERE tc.constraint_type = 'UNIQUE'
              AND tc.table_schema    = 'main'
              AND tc.table_name      = 'department'
            ORDER BY kcu.ordinal_position
            """,
        ),
        "CHECK constraints (employee)": (
            """
            SELECT tc.constraint_name, cc.check_clause
            FROM information_schema.table_constraints tc
            JOIN information_schema.check_constraints cc
              ON tc.constraint_name = cc.constraint_name
             AND tc.table_schema    = cc.constraint_schema
            WHERE tc.constraint_type = 'CHECK'
              AND tc.table_schema    = 'main'
              AND tc.table_name      = 'employee'
            """,
        ),
        "Indexes (employee) via duckdb_indexes()": (
            """
            SELECT index_name, sql
            FROM duckdb_indexes()
            WHERE table_name  = 'employee'
              AND schema_name = 'main'
            """,
        ),
        "Columns (employee) via information_schema": (
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'main'
              AND table_name   = 'employee'
            ORDER BY ordinal_position
            """,
        ),
    }

    with eng_duckdb.connect() as conn:
        for label, (sql,) in queries.items():
            print(f"\n--- {label} ---")
            try:
                rows = conn.execute(text(sql)).fetchall()
                for r in rows:
                    print(" ", dict(r._mapping))
                if not rows:
                    print("  (no rows returned)")
            except Exception as e:
                print(f"  ERROR: {e}")


# ---------------------------------------------------------------------------
# Pretty-print results
# ---------------------------------------------------------------------------

def print_results():
    compat_count = sum(1 for r in results if r.sqlite_ok and r.duckdb_ok)
    total = len(results)
    print("\n" + "="*70)
    print(f"COMPATIBILITY SUMMARY  ({compat_count}/{total} features fully compatible)")
    print("="*70)

    for r in results:
        sqlite_sym = "OK" if r.sqlite_ok else "FAIL"
        duckdb_sym = "OK" if r.duckdb_ok else "FAIL"
        compat = "COMPAT" if (r.sqlite_ok and r.duckdb_ok) else "DIFFER"
        print(f"\n[{compat}] {r.feature}")
        print(f"  SQLite  [{sqlite_sym}]: {r.sqlite_value}")
        print(f"  DuckDB  [{duckdb_sym}]: {r.duckdb_value}")
        if r.duckdb_workaround:
            print(f"  WORKAROUND (DuckDB):\n{textwrap.indent(r.duckdb_workaround, '    ')}")

    print("\n" + "="*70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Setting up SQLite (in-memory)...")
    eng_sqlite = setup_sqlite()

    print("Setting up DuckDB (in-memory)...")
    eng_duckdb = setup_duckdb()

    print("Probing Inspector features...\n")
    probe(eng_sqlite, eng_duckdb)

    print_results()
    verify_workarounds(eng_duckdb)

    print("\nDone.")
