# SQLAlchemy Engine Inspection — Findings

**Date:** 2026-04-20  
**SQLAlchemy version:** 2.0.49  
**Databases tested:** SQLite (built-in), DuckDB 1.5.2, PostgreSQL 16.13, MySQL 8.0.45  
**Drivers:** sqlite3 (stdlib), duckdb-engine 0.17.0, psycopg2-binary 2.9.11, mysql-connector-python 9.6.0

---

## Test Results Summary

| Database   | Tests Passed | Total | Pass Rate |
|------------|-------------|-------|-----------|
| SQLite     | 19          | 19    | 100%      |
| DuckDB     | 22          | 24    | 92%       |
| PostgreSQL | 19          | 19    | 100%      |
| MySQL      | 19          | 19    | 100%      |
| **Total**  | **79**      | **81**| **98%**   |

---

## Per-Database Findings

### SQLite — PASS (19/19) *established*

SQLite's `sqlalchemy.inspect()` is fully functional for all tested inspection operations.

- `get_table_names()`, `get_columns()`, `get_pk_constraint()`, `get_foreign_keys()`, `get_indexes()` all work correctly.
- Column types are returned as exact SQLAlchemy type instances: `INTEGER`, `VARCHAR`, `TEXT`, `NUMERIC`, `BOOLEAN`, `TIMESTAMP`, `DATE`, `TIME`.
- Nullability (`nullable=False` for `NOT NULL` columns) is correctly reported.
- Default values are returned as raw SQL expression strings (e.g. `'0'`, `'TRUE'`).
- Foreign key names are `None` (SQLite does not assign constraint names), but the `constrained_columns` and `referred_table`/`referred_columns` are correct.
- `MetaData.reflect()` succeeds and produces a fully usable `Table` object.

**Notable behaviour:** SQLite's uniqueness flag in `get_indexes()` returns `0` (int) rather than `False` (bool) — a minor dialect quirk that does not affect correctness.

---

### DuckDB 1.5.2 (duckdb-engine 0.17.0) — PARTIAL (22/24) *established*

DuckDB via `duckdb-engine` has a significant inspection gap in SQLAlchemy 2.0.x.

#### What works
- `connect()` and query execution work correctly.
- `inspect().get_table_names()` works — tables are enumerated correctly.
- `inspect().get_pk_constraint()` works via the native dialect.
- `inspect().get_foreign_keys()` works via the native dialect.
- `inspect().get_indexes()` does not raise an error but emits a `DuckDBEngineWarning: duckdb-engine doesn't yet support reflection on indices` warning and returns an empty list. Index data must be queried via `duckdb_indexes()` system function.
- `information_schema.columns` is fully populated and returns correct column names, data types (with precision/scale, e.g. `DECIMAL(10,2)`), nullability, and default expressions.
- Roundtrip INSERT/SELECT/DELETE works correctly.

#### What fails
- **`inspect().get_columns()` raises `CatalogException: Table with name pg_collation does not exist`.**  
  Root cause: `duckdb-engine` registers itself under SQLAlchemy's PostgreSQL dialect. SQLAlchemy 2.0's PostgreSQL introspection query joins `pg_catalog.pg_collation` to resolve column collations. DuckDB's PostgreSQL compatibility layer does not implement `pg_collation`.  
  Affected SQLAlchemy operations: `get_columns()`, `MetaData.reflect()`, any ORM automap that calls column reflection.

- **`MetaData.reflect()` fails** as a consequence of the above.

#### Workaround *established*
Query `information_schema.columns` directly via `engine.connect()`:

```python
conn.execute(text(
    "SELECT column_name, data_type, is_nullable, column_default "
    "FROM information_schema.columns WHERE table_name = :t"
), {"t": "mytable"})
```

Note: `data_type` includes precision/scale (e.g. `DECIMAL(10,2)`); strip the parameter section before mapping to SQLAlchemy types.

#### Default expression quirk
DuckDB renders boolean defaults as SQL casts: `CAST('t' AS BOOLEAN)` rather than `TRUE`. Code that compares default strings literally will need normalisation.

---

### PostgreSQL 16 (psycopg2-binary 2.9.11) — PASS (19/19) *established*

PostgreSQL is the reference implementation against which SQLAlchemy's inspection is optimised.

- All inspection APIs work correctly.
- Column types are native SQLAlchemy types: `INTEGER`, `VARCHAR`, `TEXT`, `NUMERIC`, `BOOLEAN`, `TIMESTAMP`, `DATE`, `TIME`.
- Foreign key constraints include the constraint name (`orders_product_id_fkey`), `referred_schema=None` (same schema), and full column mappings.
- Index introspection includes `postgresql_include` dialect options (empty list for basic indexes).
- `MetaData.reflect()` succeeds.
- Default values are returned as raw SQL strings (`'0'`, `'true'`).

---

### MySQL 8.0.45 (mysql-connector-python 9.6.0) — PASS (19/19) *established*

MySQL passes all tests with two notable type differences worth documenting.

#### BOOLEAN represented as TINYINT(1)
MySQL has no native BOOLEAN storage type. `is_active BOOLEAN` in DDL is stored and reflected as `TINYINT`. SQLAlchemy's MySQL dialect maps this to `sa.TINYINT`, which is an `Integer` subclass — the `isinstance(type, sa.Integer)` check passes. However, callers that check `isinstance(type, sa.Boolean)` will get `False`. This is a well-known MySQL quirk.

#### TIMESTAMP vs DATETIME
`TIMESTAMP` in DDL maps to `sa.DATETIME` in MySQL reflection, not `sa.TIMESTAMP`. The test accepts `DATETIME` as a valid reflected type for a `TIMESTAMP`/`DATETIME` input column.

#### Default value quoting
MySQL wraps default values in single quotes in the reflection output (e.g. `"'0'"` and `"'1'"`), even for numeric defaults. Applications parsing defaults must strip the quotes.

#### Driver conflict: pymysql + cryptography + duckdb-engine
**`pymysql` cannot be used in the same process as `duckdb-engine` on this environment.**  
Root cause: `pymysql` imports `cryptography`, which loads `_cffi_backend`. The system `cryptography` package (Debian 41.0.7) uses cffi's Rust backend (pyo3), which conflicts with duckdb-engine's own pyo3 runtime already loaded in the process, causing a panic.  
**Fix:** Use `mysql-connector-python` (`mysql+mysqlconnector://`) instead of `pymysql`. mysql-connector-python has no cffi dependency and coexists correctly with duckdb-engine in the same Python process.

---

## Cross-Database Type Mapping Reference

| SQLAlchemy concept | SQLite     | DuckDB (info_schema) | PostgreSQL | MySQL          |
|--------------------|------------|----------------------|------------|----------------|
| Integer PK         | `INTEGER`  | `Integer`            | `INTEGER`  | `INTEGER`      |
| String (VARCHAR)   | `VARCHAR`  | `String`             | `VARCHAR`  | `VARCHAR`      |
| Long text          | `TEXT`     | `String`             | `TEXT`     | `TEXT`         |
| Numeric/Decimal    | `NUMERIC`  | `Numeric`            | `NUMERIC`  | `DECIMAL`      |
| Boolean            | `BOOLEAN`  | `Boolean`            | `BOOLEAN`  | `TINYINT`      |
| Timestamp          | `TIMESTAMP`| `DateTime`           | `TIMESTAMP`| `DATETIME`     |
| Date               | `DATE`     | `Date`               | `DATE`     | `DATE`         |
| Time               | `TIME`     | `Time`               | `TIME`     | `TIME`         |
| Float              | `FLOAT`    | `Float`              | `FLOAT`    | `FLOAT`        |

---

## Key Conclusions *established*

1. **SQLite and PostgreSQL implement the full SQLAlchemy inspection contract** with no gaps or workarounds needed.

2. **MySQL implements the full inspection contract** but has two type-level semantic gaps (BOOLEAN→TINYINT, TIMESTAMP→DATETIME) that can trip up code relying on exact type identity rather than `isinstance()` checks.

3. **DuckDB's `inspect().get_columns()` is broken with SQLAlchemy 2.0.x** because the dialect issues a `pg_collation` query that DuckDB's compatibility layer doesn't implement. This is a driver/version-compatibility bug in `duckdb-engine 0.17.0`. The `information_schema` path is a reliable workaround.

4. **pymysql must not be mixed with duckdb-engine in the same process** on systems where the `cryptography` package was installed via the OS package manager (Debian/Ubuntu). Use `mysql-connector-python` instead.

5. **Default value representations are inconsistent across databases** — all return strings but with different quoting and expression styles. Applications should treat defaults as opaque strings unless normalising them for comparison.

---

## Open Questions

- Does duckdb-engine fix the `pg_collation` gap in a version newer than 0.17.0?
- Does downgrading SQLAlchemy to 1.4.x restore full inspection compatibility with duckdb-engine?
- How does MySQL handle `BOOLEAN` in `inspect().get_columns()` when the column was defined with `BOOLEAN` vs `TINYINT(1)` explicitly — is the dialect label different?
- Does PostgreSQL's `inspect()` correctly reflect partitioned tables and views (not tested here)?
- Are there DuckDB inspection gaps beyond `get_columns()` when using SQLAlchemy 2.0 features like `get_check_constraints()` or `get_unique_constraints()`?
