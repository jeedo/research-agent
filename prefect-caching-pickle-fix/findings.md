# Findings: Prefect Caching Pickle Errors

## Context

Prefect's task caching system must serialise task inputs to build a cache key.
It uses **cloudpickle** (a superset of Python's stdlib pickle) for that
serialisation.  When an input cannot be pickled, Prefect raises a
`ValueError` or `TypeError` and either skips caching silently or crashes the
task run, depending on the Prefect version.

---

## How Prefect Caching Works (the short version)

```
flow run
  └─ task called with parameters
        ├─ Prefect serialises parameters → builds cache key (string)
        ├─ looks up cache key in result store
        │     HIT  → return stored result, skip task body
        │     MISS → run task body → store result → return
        └─ result stored as pickle (default) or JSON blob
```

Two serialisation steps can fail:
1. **Cache-key generation** — pickling inputs to hash them.
2. **Result persistence** — pickling the return value to store it.

Most "pickle errors with caching" are step 1 failures.

---

## When It Fails — Root Causes

### Established

- **Threading primitives passed as parameters** (`threading.Lock`, `Event`,
  `Semaphore`, `Condition`) — the underlying C-level lock cannot be serialised.
  Error: `TypeError: cannot pickle '_thread.lock' object`

- **Database connections as parameters** (`psycopg2.connection`,
  `sqlalchemy.Engine`, `sqlite3.Connection`) — hold open file descriptors /
  sockets which the OS does not allow to be copied via pickle.
  Error: `TypeError: cannot pickle 'psycopg2.connection' object`

- **Open file handles** (`open(...)`, `io.BufferedWriter`, etc.) — same
  reason as database connections (underlying OS fd).

- **SSL / socket objects** — `ssl.SSLContext`, `socket.socket`, etc.

### Likely

- **Lambdas and closures defined inside another function** — cloudpickle
  handles *most* lambdas, but closures that capture un-picklable objects
  (connections, locks) still fail.

- **Some third-party C-extension objects** — e.g. certain Arrow / DuckDB
  cursor objects, CUDA tensors without custom pickle support.

### Speculative

- **Pydantic v1 models with `arbitrary_types_allowed`** containing
  un-picklable fields — may silently drop fields or raise depending on
  cloudpickle version.

---

## Workarounds

### A — Disable caching for the affected task (`NO_CACHE`) *(established)*

```python
from prefect.cache_policies import NO_CACHE

@task(cache_policy=NO_CACHE)
def my_task(conn, value):
    ...
```

Pros: zero boilerplate.  Cons: loses all caching benefits.

---

### B — Custom `cache_key_fn` that skips un-picklable params *(established)*

```python
import hashlib

def key_from_value_only(context, parameters):
    return hashlib.md5(str(parameters["value"]).encode()).hexdigest()

@task(cache_key_fn=key_from_value_only)
def my_task(conn, value):
    ...
```

Pros: preserves caching on the safe params.
Cons: you must manually list which params contribute to the key.

---

### C — Create resources *inside* the task, never pass them in *(established)*

```python
@task(cache_policy=INPUTS)      # safe — only strings/ints as params
def fetch_rows(dsn: str, query: str):
    import psycopg2
    conn = psycopg2.connect(dsn)   # created here, not passed in
    rows = conn.execute(query).fetchall()
    conn.close()
    return rows
```

Pros: cleanest design; fully safe.
Cons: connection overhead per task call (mitigated with connection pooling).

---

### D — Pydantic model with `@field_serializer` that drops bad fields *(likely)*

```python
from pydantic import BaseModel, field_serializer, ConfigDict
from typing import Any

class Config(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str
    conn: Any

    @field_serializer("conn")
    def _drop_conn(self, v):
        return None    # excluded from cache-key serialisation

@task(cache_policy=INPUTS)
def my_task(config: Config, value: int):
    ...
```

Pros: works well when you have a rich config object.
Cons: Pydantic v2 only; requires discipline to keep serialiser in sync.

---

### E — Add `cache_expiration` as a safety net *(established)*

```python
from datetime import timedelta

@task(cache_key_fn=my_safe_key_fn, cache_expiration=timedelta(hours=1))
def my_task(...):
    ...
```

Does not prevent the pickle error, but limits how long a bad cache entry
persists.

---

## Diff vs Prior Work

- Prior research in `prefect-testing-approaches/` covers unit/integration
  testing patterns using `.fn()` and `prefect_test_harness`.
- This folder is the first to document the *caching pickle failure* failure
  mode specifically.

---

## Open Questions

1. Does Prefect 3.x change the default cache policy (away from `INPUTS`) in a
   way that reduces accidental pickle failures?
2. Is there a way to configure a global fallback serialiser (e.g. `dill`) so
   that more types are picklable without per-task workarounds?
3. Do Prefect result backends (S3, GCS) behave identically to the local file
   backend when a pickle error occurs?
