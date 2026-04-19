# SQLAlchemy Reflection & Primary Key Inspection: SQLite vs DuckDB

> Which SQLAlchemy Inspector / reflection APIs work identically on SQLite and DuckDB, and what raw SQL must you use when they don't?

## Status

**Status**: Complete  
**Last updated**: 2026-04-19  
**Versions**: SQLAlchemy 2.0.49 · duckdb 1.5.2 · duckdb-engine 0.17.0

## Key Findings

- **`get_columns()`, `MetaData.reflect()`, `Table(autoload_with=)` all crash on DuckDB** *(established)* — duckdb-engine's PostgreSQL compatibility layer queries `pg_catalog.pg_collation`, which DuckDB does not implement. Use `information_schema.columns` directly instead.
- **`get_pk_constraint()` silently returns empty on DuckDB** *(established)* — no exception is raised but `constrained_columns` is always `[]`. Must use `information_schema.table_constraints` + `key_column_usage` join to get real PK columns.
- **`get_indexes()` silently returns empty on DuckDB** *(established)* — duckdb-engine explicitly warns this is unsupported. Use the `duckdb_indexes()` table function instead.
- **`get_check_constraints()` works on both but DuckDB includes extra NOT NULL rows** *(established)* — DuckDB models NOT NULL column constraints as CHECK constraints internally; filter them out.
- **`get_foreign_keys()` works on both** *(established)* — constraint names differ (SQLite preserves user names; DuckDB generates verbose ones), but referred/constrained columns are correct.
- **`get_unique_constraints()` returns empty on both engines** *(established)* — SQLite stores UNIQUE constraints as indexes; DuckDB stores them in information_schema but the Inspector doesn't surface them.
- **11 of 14 Inspector features are functionally compatible** *(established)* — 3 features crash or silently return wrong data on DuckDB.

## Open Questions

- Will a newer duckdb-engine release fix the `pg_collation` crash and empty PK result?
- Do the `information_schema` workarounds hold for attached/multi-file DuckDB databases?
- Does the silent empty result from `get_pk_constraint()` break Alembic migration autogenerate?

## Files

| File | Description |
|------|-------------|
| `findings.md` | Detailed findings with evidence, root causes, and raw SQL workarounds |
| `compare_reflection.py` | Python script that exercises all Inspector APIs on both engines and prints a compatibility report |
