# Findings: SQLAlchemy Reflection & Primary Key Inspection — SQLite vs DuckDB

**Versions tested**: SQLAlchemy 2.0.49 · duckdb 1.5.2 · duckdb-engine 0.17.0  
**Date**: 2026-04-19

---

## Summary table

| Feature | SQLite | DuckDB | Compatible? |
|---|---|---|---|
| `Inspector.get_table_names()` | OK | OK | Yes |
| `Inspector.get_columns()` | OK | **CRASH** | **No** |
| `Inspector.get_pk_constraint()` | OK (correct data) | **Silent empty result** | **No** |
| `Inspector.get_foreign_keys()` | OK | OK (different name format) | Functional yes |
| `Inspector.get_unique_constraints()` | OK (returns []) | OK (returns []) | Yes (both miss it) |
| `Inspector.get_check_constraints()` | OK | OK (extra NOT NULL rows) | Mostly yes |
| `Inspector.get_indexes()` | OK | **Silent empty result** | **No** |
| `MetaData.reflect()` | OK | **CRASH** | **No** |
| `Table(..., autoload_with=engine)` | OK | **CRASH** | **No** |
| `Inspector.has_table()` | OK | OK | Yes |
| `Inspector.get_schema_names()` | OK | OK (extra schemas) | Yes |

---

## Critical findings

### Finding 1 — `get_columns()`, `MetaData.reflect()`, `Table(autoload_with=)` crash on DuckDB *(established)*

**Root cause**: duckdb-engine 0.17.0 inherits SQLAlchemy's PostgreSQL dialect for column
introspection. That dialect queries `pg_catalog.pg_collation`, which DuckDB does not
implement (it offers `pg_catalog.pg_constraint` but not `pg_catalog.pg_collation`).

**Error**:
```
CatalogError: Table with name pg_collation does not exist!
Did you mean "pg_constraint"?
```

**Impact**: Any code path that calls `get_columns()` internally — including
`MetaData.reflect()` and `Table(..., autoload_with=engine)` — crashes completely.
This is the single largest incompatibility.

**Workaround**: query `information_schema.columns` directly:
```sql
SELECT column_name, data_type, is_nullable, column_default, ordinal_position
FROM information_schema.columns
WHERE table_schema = 'main'
  AND table_name   = :table
ORDER BY ordinal_position;
```

---

### Finding 2 — `get_pk_constraint()` silently returns empty on DuckDB *(established)*

`Inspector.get_pk_constraint()` does not raise; it returns `{'name': None, 'constrained_columns': []}` for every table, regardless of whether a PRIMARY KEY constraint exists.

**SQLite**:
```python
{'constrained_columns': ['emp_id', 'dept_id'], 'name': 'pk_employee'}
```
**DuckDB (Inspector)**:
```python
{'name': None, 'constrained_columns': []}   # WRONG — always empty
```

This is a silent data-correctness bug: code that checks `if pk['constrained_columns']` will
incorrectly conclude the table has no primary key.

**Workaround** — query `information_schema` directly:
```sql
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
```
This correctly returns `[{'column_name': 'emp_id', ...}, {'column_name': 'dept_id', ...}]`.

---

### Finding 3 — `get_indexes()` silently returns empty on DuckDB *(established)*

duckdb-engine itself emits a `DuckDBEngineWarning`: *"duckdb-engine doesn't yet support
reflection on indices"*. The call succeeds (returns `[]`) but no index data is returned.

**SQLite** returns:
```python
[{'name': 'idx_emp_last', 'column_names': ['last_name'], 'unique': 0, ...}]
```
**DuckDB Inspector** returns: `[]`

**Workaround** — use the `duckdb_indexes()` table function:
```sql
SELECT index_name, sql
FROM duckdb_indexes()
WHERE table_name  = :table
  AND schema_name = 'main';
```
Returns: `{'index_name': 'idx_emp_last', 'sql': 'CREATE INDEX idx_emp_last ON employee(last_name);'}`

---

### Finding 4 — PK constraint name is not preserved on DuckDB *(established)*

Even when using the raw-SQL workaround for PKs, `information_schema.table_constraints`
stores the system-generated constraint name (e.g., `pk_employee`) in
`tc.constraint_name`, but the `get_pk_constraint()` Inspector path returns `name: None`.
There is no supported way to retrieve the user-defined constraint name via the SQLAlchemy
Inspector on DuckDB.

---

### Finding 5 — `get_unique_constraints()` returns empty on both engines *(established)*

Both SQLite and DuckDB return `[]` for `get_unique_constraints('department')` even
though a `UNIQUE` constraint on `dept_name` was declared.

- **SQLite** stores UNIQUE constraints as indexes; they show up in `get_indexes()` instead.
- **DuckDB** stores them in `information_schema.table_constraints` (constraint_type = 'UNIQUE').

**DuckDB workaround**:
```sql
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
```
Returns: `{'constraint_name': 'department_dept_name_key', 'column_name': 'dept_name'}`

---

### Finding 6 — `get_check_constraints()` returns extra NOT NULL rows on DuckDB *(established)*

DuckDB models NOT NULL column constraints as `CHECK` constraints internally. The Inspector
returns them alongside user-defined CHECK constraints:

```python
# DuckDB
[
  {'name': 'employee_dept_id_not_null', 'sqltext': 'dept_id IS NOT NULL'},
  {'name': 'employee_emp_id_not_null',  'sqltext': 'emp_id IS NOT NULL'},
  {'name': 'employee_salary_check',     'sqltext': 'salary > 0'},        # user-defined
]
```

SQLite returns only the user-defined constraint:
```python
[{'sqltext': 'salary > 0', 'name': 'chk_salary'}]
```

Filter out NOT NULL checks by excluding rows where `sqltext` ends with `IS NOT NULL`.

---

### Finding 7 — `get_foreign_keys()` works on both but names differ *(likely)*

SQLite preserves the user-defined constraint name (`fk_emp_dept`). DuckDB auto-generates
a verbose name from the column/table pattern:
`'FOREIGN KEY (dept_id) REFERENCES department(dept_id)'`.

The referred table, referred columns, and constrained columns are correct on both.

---

### Finding 8 — `get_schema_names()` returns extra internal schemas on DuckDB *(established)*

SQLite: `['main']`  
DuckDB: `['memory.main', 'system.information_schema', 'system.main', 'temp.main']`

Code that iterates over schema names to enumerate user tables must filter to `memory.main`
(or use `table_schema = 'main'` in SQL predicates).

---

## Raw SQL reference card for DuckDB

These queries substitute for broken/unreliable Inspector calls on DuckDB (SQLAlchemy + duckdb-engine).

### Primary key columns
```sql
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
```

### Foreign keys
```sql
SELECT
    kcu.column_name          AS constrained_column,
    ccu.table_name           AS referred_table,
    ccu.column_name          AS referred_column,
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
```

### Unique constraints
```sql
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
```

### Check constraints (user-defined only)
```sql
SELECT tc.constraint_name, cc.check_clause
FROM information_schema.table_constraints tc
JOIN information_schema.check_constraints cc
  ON tc.constraint_name = cc.constraint_name
 AND tc.table_schema    = cc.constraint_schema
WHERE tc.constraint_type = 'CHECK'
  AND tc.table_schema    = 'main'
  AND tc.table_name      = :table
  AND cc.check_clause NOT LIKE '%IS NOT NULL';
```

### Indexes
```sql
SELECT index_name, sql
FROM duckdb_indexes()
WHERE table_name  = :table
  AND schema_name = 'main';
```

### Columns
```sql
SELECT column_name, data_type, is_nullable, column_default, ordinal_position
FROM information_schema.columns
WHERE table_schema = 'main'
  AND table_name   = :table
ORDER BY ordinal_position;
```

---

## Open questions

1. Does upgrading duckdb-engine beyond 0.17.0 (if released) fix `pg_collation` / `get_columns()`?
2. Is there a planned fix for `get_pk_constraint()` returning empty in duckdb-engine?
3. Can `MetaData.reflect()` work if a custom `dialect` or `connection_args` forces DuckDB's
   own catalog path instead of the PostgreSQL compatibility layer?
4. How do the workarounds behave with attached databases (multi-file DuckDB)?
5. Does `get_unique_constraints()` returning empty affect Alembic migration autogenerate?
