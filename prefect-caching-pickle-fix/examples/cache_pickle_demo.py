"""
Prefect Task Caching & Pickle Errors — Illustrated Examples
============================================================

CONCEPT PRIMER
--------------
Prefect can cache a task's result so that if you re-run the flow with the
same inputs, Prefect skips the task and returns the stored result directly.

To do that it needs two things:
  1. A **cache key** — a string that uniquely identifies "this set of inputs".
     By default Prefect builds it by serialising (pickling) every parameter.
  2. A **persisted result** — the actual return value, also pickled to disk.

The pickle error happens at step 1: when Prefect tries to hash/serialise the
*inputs* to build the cache key, and one of those inputs cannot be pickled.
"""

# ---------------------------------------------------------------------------
# PART 1 — SETUP
# Run:  pip install prefect
# ---------------------------------------------------------------------------

import threading
import hashlib
from datetime import timedelta

from prefect import flow, task
from prefect.cache_policies import NO_CACHE, INPUTS

# ---------------------------------------------------------------------------
# PART 2 — WHAT CAUSES THE ERROR
#
# Objects that cannot be pickled include:
#   • database connections  (psycopg2.connection, sqlalchemy.Engine, …)
#   • file handles          (open("file.txt"))
#   • threading primitives  (Lock, Event, Semaphore, …)
#   • lambda functions      (lambda x: x)
#   • SSL / socket objects
#
# When you decorate a task with cache_policy=INPUTS (or an older
# cache_key_fn that tries to hash every argument), Prefect calls
# cloudpickle.dumps() on each parameter.  If that raises, you get:
#
#   ValueError: Could not serialize object of type <Lock>
#   or
#   TypeError: cannot pickle '_thread.lock' object
# ---------------------------------------------------------------------------

# -- 2a. A threading.Lock cannot be pickled ---------------------------------

@task(cache_policy=INPUTS)   # INPUTS = hash every parameter → will fail
def task_with_lock(lock: threading.Lock, value: int) -> int:
    """This task will raise a serialisation error at cache-key time."""
    with lock:
        return value * 2


# -- 2b. A file handle cannot be pickled ------------------------------------

@task(cache_policy=INPUTS)
def task_with_file_handle(fh, multiplier: int) -> int:
    """Passing an open file object also triggers the pickle error."""
    data = fh.read()
    return len(data) * multiplier


# -- 2c. A lambda cannot be pickled by standard pickle ----------------------
# (cloudpickle actually *can* pickle most lambdas, but dynamically-created
#  functions defined inside another function often still fail)

def make_unpicklable_func():
    """Returns a closure that cloudpickle may fail to serialise."""
    secret = object()           # 'object()' instances are picklable,
                                # but real-world closures close over
                                # sockets, connections, etc.
    return lambda x: (x, secret)


@task(cache_policy=INPUTS)
def task_with_closure(fn, value: int):
    return fn(value)


# ---------------------------------------------------------------------------
# PART 3 — RUNNING THE BROKEN EXAMPLES
# (Wrapped so you can see the error message without crashing everything)
# ---------------------------------------------------------------------------

@flow
def broken_flow_demo():
    lock = threading.Lock()

    print("\n--- Example 1: passing a Lock to a cached task ---")
    try:
        result = task_with_lock(lock, 5)
        print(f"  result: {result}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")

    print("\n--- Example 2: passing an open file handle ---")
    import io
    fh = io.StringIO("hello world")
    try:
        result = task_with_file_handle(fh, 3)
        print(f"  result: {result}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# PART 4 — WORKAROUNDS
# ---------------------------------------------------------------------------

# ── Workaround A: NO_CACHE ─────────────────────────────────────────────────
# If caching is simply not needed for this task, opt out entirely.

@task(cache_policy=NO_CACHE)
def task_no_cache(lock: threading.Lock, value: int) -> int:
    """Caching disabled — no pickle attempt on inputs."""
    with lock:
        return value * 2


# ── Workaround B: Custom cache-key function ────────────────────────────────
# Provide your own function that builds the key from only the *serialisable*
# parts of the inputs, ignoring the problematic ones.

def cache_key_ignore_lock(context, parameters: dict) -> str:
    """Build a cache key from 'value' only; ignore the un-picklable 'lock'."""
    key_data = str(parameters["value"])
    return hashlib.md5(key_data.encode()).hexdigest()


@task(cache_key_fn=cache_key_ignore_lock,
      result_storage_key="task-with-lock-{parameters[value]}")
def task_cached_ignore_lock(lock: threading.Lock, value: int) -> int:
    """Cached correctly — the lock is excluded from the cache key."""
    with lock:
        return value * 2


# ── Workaround C: Move resource creation inside the task ──────────────────
# The cleanest solution: don't pass un-picklable objects as parameters at all.
# Create them inside the task body so they never need to be serialised.

@task(cache_policy=INPUTS)      # Safe: only 'query' is a parameter
def fetch_data(query: str) -> list:
    """
    All inputs are plain strings — perfectly picklable.
    The database connection lives entirely inside the task.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")          # created inside, not passed in
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    rows = conn.execute(query).fetchall()
    conn.close()
    return rows


# ── Workaround D: Serialize only what matters with a Pydantic model ────────
# When you need a rich input object, wrap it in a Pydantic model and
# provide a custom serializer that drops the un-picklable fields.

try:
    from pydantic import BaseModel, field_serializer, ConfigDict
    from typing import Any, Optional

    class JobConfig(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        name: str
        lock: Optional[Any] = None   # un-picklable at runtime

        @field_serializer("lock")
        def _drop_lock(self, v: Any) -> None:
            return None              # excluded from serialisation / cache key

    @task(cache_policy=INPUTS)
    def task_with_pydantic(config: JobConfig, value: int) -> int:
        """Cache key is built from the serialisable representation of JobConfig."""
        if config.lock:
            with config.lock:
                return value * 2
        return value * 2

except ImportError:
    pass    # pydantic not installed — skip this example


# ── Workaround E: cache_expiration to limit blast-radius ──────────────────
# If you keep caching enabled but accept that some runs may miss the cache,
# add an expiration so stale keys are not stored indefinitely.

@task(
    cache_key_fn=cache_key_ignore_lock,
    cache_expiration=timedelta(hours=1),
)
def task_cached_with_expiry(lock: threading.Lock, value: int) -> int:
    with lock:
        return value * 2


# ---------------------------------------------------------------------------
# PART 5 — RUNNING THE FIXED EXAMPLES
# ---------------------------------------------------------------------------

@flow
def fixed_flow_demo():
    lock = threading.Lock()
    value = 7

    print("\n--- Workaround A: NO_CACHE ---")
    r = task_no_cache(lock, value)
    print(f"  result: {r}")         # always re-runs, no cache error

    print("\n--- Workaround B: custom cache-key (lock ignored) ---")
    r = task_cached_ignore_lock(lock, value)
    print(f"  result: {r}")         # cached by 'value' alone

    print("\n--- Workaround C: resource created inside task ---")
    r = fetch_data("SELECT v FROM t")
    print(f"  result: {r}")         # fully safe caching

    try:
        print("\n--- Workaround D: Pydantic model with serializer ---")
        config = JobConfig(name="job-1", lock=lock)
        r = task_with_pydantic(config, value)
        print(f"  result: {r}")
    except NameError:
        print("  (skipped — pydantic not installed)")


# ---------------------------------------------------------------------------
# PART 6 — QUICK SUMMARY TABLE
# ---------------------------------------------------------------------------
#
#  Cause                        │ Why it breaks            │ Best fix
#  ─────────────────────────────┼──────────────────────────┼──────────────────
#  threading.Lock passed in     │ can't pickle lock object  │ C or B
#  DB connection passed in      │ can't pickle socket/fd    │ C
#  Open file handle passed in   │ can't pickle file object  │ C
#  Lambda / local closure       │ pickle may fail on scope  │ use named fn or C
#  3rd-party object (SSLCtx …)  │ no pickle support         │ B, C, or D
#  ─────────────────────────────┼──────────────────────────┼──────────────────
#  Workaround A  NO_CACHE       │ simplest; no caching at all
#  Workaround B  cache_key_fn   │ cache using only safe params; exclude bad ones
#  Workaround C  inside-task    │ cleanest; never pass resources as params
#  Workaround D  Pydantic       │ rich objects with custom serialiser
#  Workaround E  expiration     │ add expiry so bad keys don't persist forever


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("BROKEN EXAMPLES")
    print("=" * 60)
    broken_flow_demo()

    print("\n" + "=" * 60)
    print("FIXED EXAMPLES")
    print("=" * 60)
    fixed_flow_demo()
