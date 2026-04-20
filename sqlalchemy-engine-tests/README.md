# SQLAlchemy Engine Inspection Tests

> How correctly do SQLite, DuckDB, PostgreSQL, MySQL, and IBM Db2 implement the SQLAlchemy inspection contract for column types, nullability, defaults, PKs, FKs, and indexes?

## Status

**Status**: Complete  
**Last updated**: 2026-04-20

## Key Findings

- **SQLite and PostgreSQL pass 100% (19/19) of inspection tests** *(established)* — Both fully implement `inspect().get_columns()`, `get_pk_constraint()`, `get_foreign_keys()`, `get_indexes()`, and `MetaData.reflect()` with accurate type, nullability, and default information.

- **MySQL passes 100% (19/19) but maps BOOLEAN to TINYINT** *(established)* — MySQL has no native BOOLEAN type; `inspect()` returns `TINYINT` for boolean columns. Code using `isinstance(col_type, sa.Boolean)` will silently fail; use `isinstance(col_type, sa.Integer)` or check for `TINYINT` explicitly.

- **DuckDB's `inspect().get_columns()` is broken with SQLAlchemy 2.0.x** *(established)* — `duckdb-engine 0.17.0` registers under SQLAlchemy's PostgreSQL dialect, whose column introspection query references `pg_catalog.pg_collation` — a system table DuckDB does not implement. This causes `CatalogException` on every `get_columns()` / `MetaData.reflect()` call. Workaround: query `information_schema.columns` directly.

- **pymysql conflicts with duckdb-engine in the same Python process** *(established)* — `pymysql` imports `cryptography` which uses cffi; on Debian/Ubuntu the system cryptography package conflicts with duckdb-engine's pyo3 runtime. Fix: use `mysql-connector-python` (`mysql+mysqlconnector://`) instead.

- **Default value representations are inconsistent across engines** *(established)* — All databases return defaults as strings but with different formats: SQLite returns `'TRUE'`/`'0'`; PostgreSQL returns `'true'`/`'0'`; MySQL returns quoted numerics (`"'0'"`, `"'1'"`); DuckDB returns SQL cast expressions (`"CAST('t' AS BOOLEAN)"`). Do not compare defaults as literals across databases.

- **IBM Db2 Community Edition exists and its Python driver is pip-installable, but could not be run here** *(established)* — `ibmcom/db2` Docker image requires `security.capability` xattrs during layer extraction, which the nested-container + kernel 4.4.0 environment does not support. The `ibm_db 3.2.8` / `ibm_db_sa 0.4.4` Python drivers install cleanly on Python 3.11. Expected Db2-specific quirks: no native BOOLEAN (uses SMALLINT), TEXT stored as CLOB. See `findings.md` for full setup instructions.

## Open Questions

- Does a newer `duckdb-engine` release (>0.17.0) fix the `pg_collation` inspection gap?
- Does SQLAlchemy 1.4.x restore full `inspect()` compatibility with DuckDB?
- Does MySQL differentiate between `BOOLEAN` (DDL keyword) and `TINYINT(1)` (storage) in `get_columns()` output?
- Are there additional DuckDB inspection gaps for `get_check_constraints()`, `get_unique_constraints()`, or view reflection?

## Files

| File | Description |
|------|-------------|
| `findings.md` | Detailed findings per database with confidence labels and evidence |
| `test_engines.py` | Runnable test suite — 81 tests across 4 databases |

## Running the Tests

```bash
# Ensure PostgreSQL and MySQL are running, then:
cd sqlalchemy-engine-tests
python3 test_engines.py
```

Dependencies (installed via pip): `sqlalchemy`, `psycopg2-binary`, `duckdb`, `duckdb-engine`, `mysql-connector-python`
