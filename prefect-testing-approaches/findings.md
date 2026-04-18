# Findings: Prefect Testing Approaches for ETL with Databases

**Date**: 2026-04-17  
**Prefect versions targeted**: 2.18+ / 3.x

---

## 1. Core Testing Philosophy

*(established)* Prefect tasks are plain Python functions decorated with `@task`. This means the testing strategy has two distinct layers:

1. **Logic layer** — test the function's business logic directly, no Prefect engine involved.
2. **Orchestration layer** — test that Prefect correctly wires tasks together, tracks state, retries, etc.

Mixing these concerns in every test is unnecessary and slow. Prefer unit tests for logic, reserve harness-backed tests for orchestration concerns.

---

## 2. Unit Testing with `.fn()` and Mocking

*(established)*

### `.fn()` accessor
Every `@task`-decorated function exposes `.fn` — the raw underlying callable. Calling it skips the Prefect engine completely:

```python
result = my_task.fn(arg1, arg2)
```

**Gotcha**: `get_run_logger()` inside the task will raise `MissingContextError` when called this way. Fix: replace with `logging.getLogger(__name__)` in the task, or wrap with `disable_run_logger()`.

### Mocking DB connections
Use `unittest.mock.patch` or `pytest-mock`'s `mocker` fixture to intercept `sqlite3.connect`, `sqlalchemy.create_engine`, etc. This keeps unit tests self-contained with no filesystem or network dependency.

---

## 3. Integration Testing Strategies

*(established)*

### SQLite `:memory:`
- Zero setup, zero teardown.
- Best for logic that doesn't rely on PostgreSQL-specific SQL.
- Pass the connection object directly into tasks as a parameter (dependency injection pattern).

### DuckDB `:memory:`
- Same zero-setup benefit as SQLite but with columnar analytics SQL support.
- Useful when the ETL targets a data warehouse with window functions, ARRAY types, etc.

### testcontainers-python
- Pulls a Docker image, starts a real PostgreSQL/MySQL/Redis container, yields connection details, then stops it.
- `scope="session"` is critical — container startup takes 5–15 seconds; reuse across all tests.
- Requires Docker daemon running in CI.

---

## 4. End-to-End Testing with `prefect_test_harness`

*(established)*

`prefect_test_harness()` starts a lightweight in-process Prefect server backed by SQLite. Inside it, `@flow` runs are tracked with full run history, state transitions, and retries.

```python
from prefect.testing.utilities import prefect_test_harness

with prefect_test_harness():
    result = my_flow()
```

**Critical performance note**: Creating a new harness per test function is expensive (~1–2s each). Always use a `scope="session"` autouse fixture in `conftest.py`.

---

## 5. State-Based Assertions

*(established)*

Pass `return_state=True` to any flow or task call to get a `State` object instead of the raw result:

```python
state = my_flow(return_state=True)
assert state.is_completed()
assert state.result() == expected_value

failed_state = failing_task(return_state=True)
assert failed_state.is_failed()
```

State types: `COMPLETED`, `FAILED`, `CRASHED`, `CANCELLED`, `PENDING`, `RUNNING`.

---

## 6. Async Flows

*(established)*

Prefect supports `async def` flows and tasks natively. Tests need `pytest-asyncio`:

```python
@pytest.mark.asyncio
async def test_async_flow():
    result = await my_async_flow()
    assert result == expected
```

Set `asyncio_mode = "auto"` in `pytest.ini` / `pyproject.toml` to avoid marking every test manually.

---

## 7. Key Gotchas

| Gotcha | Fix |
|--------|-----|
| `get_run_logger()` raises `MissingContextError` in unit tests | Use `disable_run_logger()` context or test via `.fn()` |
| `prefect_test_harness` slow per test | Session-scoped `autouse` fixture in `conftest.py` |
| testcontainers not working in CI | Ensure Docker socket is available; use `dind` in GitHub Actions |
| Task caching polluting test runs | Set `cache_key_fn=None` or `refresh_cache=True` on test tasks |
| `pytest-xdist` parallel workers conflict | Use one external Prefect server for all workers; harness is not multiprocess-safe |
| `return_state=True` with mapping | `.map()` returns a list of futures; use `wait(futures)` before checking states |

---

## Open Questions

- Prefect 3.x introduced significant internal changes — are there new testing utilities not yet documented?
- How should Prefect Blocks (credential stores) be mocked in tests without leaking secrets?
- Is there a recommended pattern for testing scheduled deployments (cron triggers) in isolation?
