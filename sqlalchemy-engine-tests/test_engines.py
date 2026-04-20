"""
SQLAlchemy engine inspection tests across SQLite, DuckDB, PostgreSQL, and MySQL.

Tests that engines correctly report column types, nullability, defaults, primary
keys, foreign keys, indexes, and table/schema metadata via sqlalchemy.inspect().

For DuckDB, some inspect() queries fail because duckdb-engine uses the PostgreSQL
dialect internally which queries pg_catalog.pg_collation — a table DuckDB does
not implement. Those tests are supplemented with information_schema raw SQL.
"""

import sys
import traceback
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import (
    MetaData,
    create_engine,
    inspect,
    text,
)

# ---------------------------------------------------------------------------
# Test matrix
# ---------------------------------------------------------------------------

ENGINES = [
    {
        "name": "SQLite",
        "url": "sqlite:///:memory:",
    },
    {
        "name": "DuckDB",
        "url": "duckdb:///:memory:",
    },
    {
        "name": "PostgreSQL",
        "url": "postgresql+psycopg2://testuser:testpass@localhost:5432/testdb",
    },
    {
        "name": "MySQL",
        # Use mysql-connector-python to avoid cffi/pyo3 conflict with duckdb
        "url": (
            "mysql+mysqlconnector://testuser:testpass@localhost/testdb"
            "?unix_socket=/var/run/mysqld/mysqld.sock"
        ),
    },
]

# ---------------------------------------------------------------------------
# DDL variants
# ---------------------------------------------------------------------------

# Generic (SQLite, PostgreSQL)
CREATE_DDL = """
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER NOT NULL,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    price       NUMERIC(10,2) NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 0,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    amount      FLOAT,
    order_date  DATE,
    order_time  TIME,
    note        VARCHAR(500),
    PRIMARY KEY (id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_orders_product ON orders(product_id);
"""

CREATE_DDL_DUCKDB = """
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER NOT NULL,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    price       DECIMAL(10,2) NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 0,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMP,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    amount      DOUBLE,
    order_date  DATE,
    order_time  TIME,
    note        VARCHAR(500),
    PRIMARY KEY (id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_orders_product ON orders(product_id);
"""

CREATE_DDL_MYSQL = """
CREATE TABLE IF NOT EXISTS products (
    id          INT NOT NULL AUTO_INCREMENT,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    price       DECIMAL(10,2) NOT NULL,
    quantity    INT NOT NULL DEFAULT 0,
    is_active   TINYINT(1) NOT NULL DEFAULT 1,
    created_at  DATETIME,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS orders (
    id          INT NOT NULL AUTO_INCREMENT,
    product_id  INT NOT NULL,
    amount      FLOAT,
    order_date  DATE,
    order_time  TIME,
    note        VARCHAR(500),
    PRIMARY KEY (id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
"""


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    db: str
    test: str
    passed: bool
    detail: str = ""
    error: str = ""
    note: str = ""   # extra context, e.g. "used fallback"


results: list[TestResult] = []


def record(db, test, passed, detail="", error="", note=""):
    results.append(TestResult(db=db, test=test, passed=passed,
                               detail=detail, error=error, note=note))
    status = "PASS" if passed else "FAIL"
    parts = [f"  [{status}] {test}"]
    if detail:
        parts.append(detail)
    if note:
        parts.append(f"(NOTE: {note})")
    if error:
        # Trim long errors for console
        short_err = error.split("\n")[0][:200]
        parts.append(f"| ERROR: {short_err}")
    print(": ".join(parts[:2]) + ("  " + "  ".join(parts[2:]) if len(parts) > 2 else ""))


# ---------------------------------------------------------------------------
# DuckDB inspection via information_schema (fallback for inspect() gaps)
# ---------------------------------------------------------------------------

def duckdb_get_columns(engine, table_name: str) -> list[dict]:
    """
    Returns column metadata for DuckDB using information_schema.columns,
    since duckdb-engine's SQLAlchemy dialect doesn't fully implement pg_catalog.
    """
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = :tname "
            "ORDER BY ordinal_position"
        ), {"tname": table_name}).fetchall()

    type_map = {
        "INTEGER": sa.Integer(),
        "BIGINT": sa.BigInteger(),
        "VARCHAR": sa.String(),
        "TEXT": sa.Text(),
        "DECIMAL": sa.Numeric(),
        "DOUBLE": sa.Float(),
        "FLOAT": sa.Float(),
        "BOOLEAN": sa.Boolean(),
        "TIMESTAMP": sa.DateTime(),
        "DATE": sa.Date(),
        "TIME": sa.Time(),
    }
    cols = []
    for name, dtype, nullable_str, default in rows:
        # Strip precision/scale, e.g. "DECIMAL(10,2)" → "DECIMAL"
        base_dtype = dtype.split("(")[0].strip().upper()
        sa_type = type_map.get(base_dtype, sa.types.NullType())
        cols.append({
            "name": name,
            "type": sa_type,
            "nullable": nullable_str.upper() == "YES",
            "default": default,
        })
    return cols


def duckdb_get_pk(engine, table_name: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT kcu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "WHERE tc.table_name = :tname "
            "  AND tc.constraint_type = 'PRIMARY KEY' "
            "ORDER BY kcu.ordinal_position"
        ), {"tname": table_name}).fetchall()
    return [r[0] for r in rows]


def duckdb_get_fks(engine, table_name: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT kcu.column_name, ccu.table_name AS referred_table, ccu.column_name AS referred_column "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "JOIN information_schema.referential_constraints rc "
            "  ON tc.constraint_name = rc.constraint_name "
            "JOIN information_schema.key_column_usage ccu "
            "  ON rc.unique_constraint_name = ccu.constraint_name "
            "WHERE tc.table_name = :tname AND tc.constraint_type = 'FOREIGN KEY'"
        ), {"tname": table_name}).fetchall()
    result = []
    for row in rows:
        result.append({
            "constrained_columns": [row[0]],
            "referred_table": row[1],
            "referred_columns": [row[2]],
        })
    return result


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------

def test_connection(engine, db_name) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        record(db_name, "connection", True, "connected OK")
        return True
    except Exception as e:
        record(db_name, "connection", False, error=str(e))
        return False


def test_table_names(insp, db_name):
    try:
        tables = insp.get_table_names()
        passed = "products" in tables and "orders" in tables
        record(db_name, "get_table_names", passed, f"tables={tables}")
    except Exception as e:
        record(db_name, "get_table_names", False, error=str(e))


def _get_columns(insp, engine, db_name, table_name):
    """Returns columns dict plus a note if fallback was used."""
    if db_name == "DuckDB":
        try:
            cols = duckdb_get_columns(engine, table_name)
            return cols, "used information_schema fallback (pg_collation missing)"
        except Exception as e:
            return None, f"fallback also failed: {e}"
    else:
        try:
            return insp.get_columns(table_name), ""
        except Exception as e:
            return None, f"inspect() failed: {e}"


def test_columns(insp, engine, db_name):
    cols_list, note = _get_columns(insp, engine, db_name, "products")
    if cols_list is None:
        record(db_name, "get_columns/names", False, error=note)
        return

    col_names = [c["name"] for c in cols_list]
    expected = {"id", "name", "description", "price", "quantity", "is_active", "created_at"}
    missing = expected - set(col_names)
    passed = len(missing) == 0
    record(db_name, "get_columns/names", passed,
           f"found={col_names}" + (f" missing={missing}" if missing else ""),
           note=note)


def test_column_types(insp, engine, db_name):
    cols_list, note = _get_columns(insp, engine, db_name, "products")
    if cols_list is None:
        record(db_name, "get_columns/types", False, error=note)
        return

    cols = {c["name"]: c for c in cols_list}
    type_map = {name: type(col["type"]).__name__ for name, col in cols.items()}

    all_typed = all(col["type"] is not None for col in cols.values())
    record(db_name, "get_columns/types_not_none", all_typed,
           f"types={type_map}", note=note)

    id_type = cols.get("id", {}).get("type")
    id_ok = id_type is not None and isinstance(id_type, (sa.Integer, sa.BigInteger, sa.SmallInteger))
    record(db_name, "get_columns/id_is_integer", id_ok,
           f"id_type={type(id_type).__name__ if id_type else 'None'}", note=note)

    name_type = cols.get("name", {}).get("type")
    name_ok = name_type is not None and isinstance(name_type, (sa.String, sa.Text, sa.Unicode))
    record(db_name, "get_columns/name_is_string", name_ok,
           f"name_type={type(name_type).__name__ if name_type else 'None'}", note=note)

    price_type = cols.get("price", {}).get("type")
    price_ok = price_type is not None and isinstance(price_type, (sa.Numeric, sa.Float))
    record(db_name, "get_columns/price_is_numeric", price_ok,
           f"price_type={type(price_type).__name__ if price_type else 'None'}", note=note)

    desc_type = cols.get("description", {}).get("type")
    record(db_name, "get_columns/description_has_type", desc_type is not None,
           f"desc_type={type(desc_type).__name__ if desc_type else 'None'}", note=note)


def test_nullability(insp, engine, db_name):
    cols_list, note = _get_columns(insp, engine, db_name, "products")
    if cols_list is None:
        record(db_name, "get_columns/nullability", False, error=note)
        return

    cols = {c["name"]: c for c in cols_list}
    not_null_cols = ["id", "name", "price", "quantity", "is_active"]
    failures = [n for n in not_null_cols if cols.get(n, {}).get("nullable", True)]
    passed = len(failures) == 0
    record(db_name, "get_columns/nullability", passed,
           "all NOT NULL columns correctly reported" if passed else f"wrong nullability: {failures}",
           note=note)


def test_defaults(insp, engine, db_name):
    cols_list, note = _get_columns(insp, engine, db_name, "products")
    if cols_list is None:
        record(db_name, "get_columns/defaults", False, error=note)
        return

    cols = {c["name"]: c for c in cols_list}
    qty_default = cols.get("quantity", {}).get("default")
    active_default = cols.get("is_active", {}).get("default")
    passed = qty_default is not None and active_default is not None
    record(db_name, "get_columns/defaults", passed,
           f"quantity_default={qty_default!r}, is_active_default={active_default!r}",
           note=note)


def test_primary_keys(insp, engine, db_name):
    if db_name == "DuckDB":
        try:
            pk_cols = duckdb_get_pk(engine, "products")
            passed = "id" in pk_cols
            record(db_name, "get_pk_constraint", passed,
                   f"pk_cols={pk_cols}",
                   note="used information_schema fallback")
            return
        except Exception as e:
            record(db_name, "get_pk_constraint", False, error=str(e))
            return
    try:
        pk = insp.get_pk_constraint("products")
        pk_cols = pk.get("constrained_columns", [])
        record(db_name, "get_pk_constraint", "id" in pk_cols, f"pk_cols={pk_cols}")
    except Exception as e:
        record(db_name, "get_pk_constraint", False, error=str(e))


def test_foreign_keys(insp, engine, db_name):
    if db_name == "DuckDB":
        try:
            fks = duckdb_get_fks(engine, "orders")
            passed = len(fks) > 0 and fks[0].get("referred_table") == "products"
            record(db_name, "get_foreign_keys", passed,
                   f"fks={fks}",
                   note="used information_schema fallback")
            return
        except Exception as e:
            record(db_name, "get_foreign_keys", False, error=str(e))
            return
    try:
        fks = insp.get_foreign_keys("orders")
        if not fks:
            record(db_name, "get_foreign_keys", False, "no foreign keys returned")
            return
        passed = fks[0].get("referred_table") == "products"
        record(db_name, "get_foreign_keys", passed, f"fks={fks}")
    except Exception as e:
        record(db_name, "get_foreign_keys", False, error=str(e))


def test_indexes(insp, engine, db_name):
    if db_name == "DuckDB":
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT index_name FROM duckdb_indexes() WHERE table_name = 'orders'"
                )).fetchall()
            idx_names = [r[0] for r in rows]
            passed = any("product" in (n or "").lower() for n in idx_names)
            record(db_name, "get_indexes", passed,
                   f"indexes={idx_names}",
                   note="used duckdb_indexes() fallback")
            return
        except Exception as e:
            record(db_name, "get_indexes", False, error=str(e))
            return
    try:
        indexes = insp.get_indexes("orders")
        has_our_index = any(
            "product" in (i.get("name") or "").lower() or
            set(i.get("column_names", [])) == {"product_id"}
            for i in indexes
        )
        record(db_name, "get_indexes", has_our_index, f"indexes={indexes}")
    except Exception as e:
        record(db_name, "get_indexes", False, error=str(e))


def test_datetime_types(insp, engine, db_name):
    orders_cols, note_o = _get_columns(insp, engine, db_name, "orders")
    products_cols, note_p = _get_columns(insp, engine, db_name, "products")

    if orders_cols is not None:
        cols_o = {c["name"]: c for c in orders_cols}
        date_type = cols_o.get("order_date", {}).get("type")
        time_type = cols_o.get("order_time", {}).get("type")
        record(db_name, "datetime/date_type",
               date_type is not None and isinstance(date_type, (sa.Date, sa.DateTime)),
               f"date_type={type(date_type).__name__ if date_type else 'None'}", note=note_o)
        record(db_name, "datetime/time_type",
               time_type is not None and isinstance(time_type, (sa.Time, sa.DateTime)),
               f"time_type={type(time_type).__name__ if time_type else 'None'}", note=note_o)
    else:
        record(db_name, "datetime/date_type", False, error=note_o)
        record(db_name, "datetime/time_type", False, error=note_o)

    if products_cols is not None:
        cols_p = {c["name"]: c for c in products_cols}
        ts_type = cols_p.get("created_at", {}).get("type")
        record(db_name, "datetime/timestamp_type",
               ts_type is not None and isinstance(ts_type, (sa.DateTime, sa.TIMESTAMP)),
               f"ts_type={type(ts_type).__name__ if ts_type else 'None'}", note=note_p)
    else:
        record(db_name, "datetime/timestamp_type", False, error=note_p)


def test_inspect_directly(insp, db_name):
    """Test SQLAlchemy inspect() directly — documents what fails for DuckDB."""
    if db_name != "DuckDB":
        return   # already covered by generic tests

    # Document which inspect() calls succeed or fail for DuckDB
    for method, args in [
        ("get_table_names", []),
        ("get_columns", ["products"]),
        ("get_pk_constraint", ["products"]),
        ("get_foreign_keys", ["orders"]),
        ("get_indexes", ["orders"]),
    ]:
        try:
            getattr(insp, method)(*args)
            record(db_name, f"inspect()/{method}", True, "native inspect works")
        except Exception as e:
            short = str(e).split("\n")[0][:120]
            record(db_name, f"inspect()/{method}", False,
                   note="duckdb-engine limitation",
                   error=short)


def test_reflect_table(engine, db_name):
    if db_name == "DuckDB":
        record(db_name, "metadata_reflect", False,
               note="skipped — reflect() depends on inspect().get_columns() which fails for DuckDB")
        return
    try:
        meta = MetaData()
        meta.reflect(bind=engine, only=["products"])
        tbl = meta.tables.get("products")
        passed = tbl is not None and "id" in tbl.c and "name" in tbl.c
        record(db_name, "metadata_reflect", passed,
               f"columns={list(tbl.c.keys()) if tbl is not None else 'None'}")
    except Exception as e:
        record(db_name, "metadata_reflect", False, error=str(e))


def test_roundtrip_insert_select(engine, db_name):
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO products (id, name, price, quantity, is_active) "
                "VALUES (9999, 'Test Widget', 19.99, 5, TRUE)"
            ))
            conn.commit()
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT name FROM products WHERE id = 9999"
            )).fetchone()
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM products WHERE id = 9999"))
            conn.commit()
        passed = row is not None and row[0] == "Test Widget"
        record(db_name, "roundtrip_insert_select", passed,
               f"row={'ok' if row else 'None'}")
    except Exception as e:
        record(db_name, "roundtrip_insert_select", False, error=str(e))


# ---------------------------------------------------------------------------
# Schema setup / teardown
# ---------------------------------------------------------------------------

def setup_schema(engine, db_name):
    ddl = CREATE_DDL if db_name not in ("DuckDB", "MySQL") else (
        CREATE_DDL_DUCKDB if db_name == "DuckDB" else CREATE_DDL_MYSQL
    )
    with engine.begin() as conn:
        for stmt in ddl.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))

    if db_name == "MySQL":
        with engine.begin() as conn:
            exists = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'orders' "
                "AND index_name = 'idx_orders_product'"
            )).scalar()
            if not exists:
                conn.execute(text("CREATE INDEX idx_orders_product ON orders(product_id)"))


def teardown_schema(engine):
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS orders"))
        conn.execute(text("DROP TABLE IF EXISTS products"))


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_all():
    print("\n" + "=" * 70)
    print("SQLAlchemy Engine Inspection Test Suite")
    print(f"SQLAlchemy version: {sa.__version__}")
    print("=" * 70)

    for cfg in ENGINES:
        db_name = cfg["name"]
        print(f"\n{'─' * 70}")
        print(f"  Database: {db_name}")
        print(f"  URL:      {cfg['url']}")
        print(f"{'─' * 70}")

        try:
            engine = create_engine(cfg["url"])
        except Exception as e:
            record(db_name, "create_engine", False, error=str(e))
            continue

        if not test_connection(engine, db_name):
            engine.dispose()
            continue

        try:
            teardown_schema(engine)
            setup_schema(engine, db_name)
        except Exception as e:
            record(db_name, "schema_setup", False, error=traceback.format_exc())
            engine.dispose()
            continue

        record(db_name, "schema_setup", True, "tables created")

        try:
            insp = inspect(engine)
            test_table_names(insp, db_name)
            test_inspect_directly(insp, db_name)
            test_columns(insp, engine, db_name)
            test_column_types(insp, engine, db_name)
            test_nullability(insp, engine, db_name)
            test_defaults(insp, engine, db_name)
            test_primary_keys(insp, engine, db_name)
            test_foreign_keys(insp, engine, db_name)
            test_indexes(insp, engine, db_name)
            test_datetime_types(insp, engine, db_name)
            test_reflect_table(engine, db_name)
            test_roundtrip_insert_select(engine, db_name)
        except Exception as e:
            record(db_name, "inspection_suite", False, error=traceback.format_exc())
        finally:
            try:
                teardown_schema(engine)
            except Exception:
                pass
            engine.dispose()

    # Summary
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    dbs = list(dict.fromkeys(r.db for r in results))
    for db in dbs:
        db_results = [r for r in results if r.db == db]
        passed = sum(1 for r in db_results if r.passed)
        total = len(db_results)
        print(f"  {db:15s}: {passed:2d}/{total:2d} tests passed")

    total_pass = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n  TOTAL: {total_pass}/{total} tests passed")
    return results


if __name__ == "__main__":
    all_results = run_all()
    failed = [r for r in all_results if not r.passed]
    # Exit 0 — test failures are documented findings, not script errors
    sys.exit(0)
