# Prefect Caching Pickle Fix

> When and why Prefect task caching fails with pickle/serialisation errors, and the workarounds.

## Status

**Status**: Complete  
**Last updated**: 2026-04-20

## Key Findings

- **Cache-key generation is the primary failure site** *(established)* — Prefect uses cloudpickle to serialise every task *parameter* in order to build a hash (the cache key). If any parameter cannot be pickled the key cannot be built and caching fails.
- **Un-picklable types are those that hold OS resources** *(established)* — threading locks, database connections, file handles, and SSL/socket objects all fail because the underlying OS file descriptor or mutex cannot be copied across process boundaries.
- **Best fix: create resources inside the task** *(established)* — if you pass only plain values (strings, ints, lists) as parameters and open connections/files inside the task body, Prefect never needs to pickle them.
- **Custom `cache_key_fn` lets you cache while ignoring bad params** *(established)* — supply a function that hashes only the safe parameters; the un-picklable ones are excluded from the key.
- **`NO_CACHE` policy is the simplest escape hatch** *(established)* — disables caching entirely for a task at the cost of losing cache benefits.
- **Pydantic `@field_serializer` can hide bad fields** *(likely)* — return `None` for un-picklable fields so Prefect sees a serialisable model; requires Pydantic v2.

## Open Questions

- Does Prefect 3.x change the default cache policy in a way that makes pickle failures less common?
- Is there a way to swap cloudpickle for `dill` globally to extend picklability without per-task changes?
- Do remote result backends (S3, GCS) behave identically to local storage when a serialisation error occurs?

## Files

| File | Description |
|------|-------------|
| `findings.md` | Detailed findings with confidence labels and workaround code snippets |
| `examples/cache_pickle_demo.py` | Runnable demo: broken examples followed by all four workarounds |
