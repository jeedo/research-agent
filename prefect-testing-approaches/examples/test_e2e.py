"""
End-to-end flow tests using the Prefect test harness.

Demonstrates:
  - Normal result assertions on flows
  - State-based assertions via return_state=True
  - Task state access from within a flow
  - Retry behaviour under test conditions
  - Task mapping (parallel fan-out)
  - On-failure hooks
  - Async flows with pytest-asyncio
  - Log capture with caplog

The session-scoped prefect_test_harness fixture in conftest.py is active
automatically (autouse=True), so no extra setup is needed here.
"""

import asyncio
import sqlite3

import pytest

from prefect import flow, task
from prefect.logging import disable_run_logger

from etl import csv_to_sqlite_pipeline


# ---------------------------------------------------------------------------
# Basic flow result assertions
# ---------------------------------------------------------------------------


class TestCsvToSqliteFlowResult:
    def test_returns_success_dict(self, tmp_csv, sqlite_db):
        result = csv_to_sqlite_pipeline(tmp_csv, sqlite_db)
        assert result == {"processed": 3, "status": "success"}

    def test_missing_source_raises(self, sqlite_db):
        with pytest.raises(Exception):
            csv_to_sqlite_pipeline("/no/such/file.csv", sqlite_db)


# ---------------------------------------------------------------------------
# State-based assertions
# ---------------------------------------------------------------------------


class TestStateAssertions:
    def test_successful_flow_state(self, tmp_csv, sqlite_db):
        state = csv_to_sqlite_pipeline(tmp_csv, sqlite_db, return_state=True)

        assert state.is_completed()
        result = state.result()
        assert result["processed"] == 3

    def test_failed_flow_state(self, sqlite_db):
        state = csv_to_sqlite_pipeline("/no/file.csv", sqlite_db, return_state=True)

        assert state.is_failed()

    def test_task_return_state_inside_flow(self, tmp_csv, sqlite_db):
        """Access individual task states from within a flow."""
        from etl import extract_csv, transform_rows, load_to_sqlite

        @flow
        def inspecting_flow(source_path: str, db_path: str):
            extract_state = extract_csv(source_path, return_state=True)
            assert extract_state.is_completed()

            rows = extract_state.result()
            transform_state = transform_rows(rows, return_state=True)
            assert transform_state.is_completed()

            records = transform_state.result()
            load_state = load_to_sqlite(db_path, records, return_state=True)
            assert load_state.is_completed()

            return load_state.result()

        count = inspecting_flow(tmp_csv, sqlite_db)
        assert count == 3


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


class TestRetries:
    def test_task_succeeds_after_retry(self):
        """Task fails once then succeeds — verify final state is COMPLETED."""
        attempt = {"count": 0}

        @task(retries=1, retry_delay_seconds=0)
        def flaky_task():
            attempt["count"] += 1
            if attempt["count"] < 2:
                raise RuntimeError("transient error")
            return "ok"

        @flow
        def flow_with_retry():
            return flaky_task(return_state=True)

        state = flow_with_retry()
        assert state.is_completed()
        assert state.result() == "ok"
        assert attempt["count"] == 2

    def test_task_exhausts_retries_and_fails(self):
        @task(retries=2, retry_delay_seconds=0)
        def always_fails():
            raise ValueError("permanent error")

        @flow
        def flow_exhausting_retries():
            always_fails()

        # Use return_state=True on the flow call; the flow itself fails when
        # the task exhausts retries and raises.
        state = flow_exhausting_retries(return_state=True)
        assert state.is_failed()


# ---------------------------------------------------------------------------
# Task mapping (parallel fan-out)
# ---------------------------------------------------------------------------


class TestTaskMapping:
    def test_map_doubles_values(self):
        @task
        def double(x: int) -> int:
            return x * 2

        @flow
        def mapping_flow(items: list[int]) -> list[int]:
            # Resolve PrefectFutureList to plain values before returning
            futures = double.map(items)
            return [f.result() for f in futures]

        result = mapping_flow([1, 2, 3, 4, 5])
        assert result == [2, 4, 6, 8, 10]

    def test_map_over_db_records(self, sqlite_db):
        """Map a validation task over each record after loading."""
        from etl import extract_csv, transform_rows, load_to_sqlite

        @task
        def validate_record(record: dict) -> bool:
            return record["value"] is not None and record["value"] > 0

        @flow
        def validate_pipeline(source_path: str, db_path: str) -> list[bool]:
            rows = extract_csv(source_path)
            records = transform_rows(rows)
            load_to_sqlite(db_path, records)
            return validate_record.map(records)

        # tmp_csv fixture not available here — build inline
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("a,1.0\nb,2.0\nc,3.0\n")
            csv_path = f.name
        try:
            results = validate_pipeline(csv_path, sqlite_db)
            assert all(results)
        finally:
            os.unlink(csv_path)


# ---------------------------------------------------------------------------
# On-failure hooks
# ---------------------------------------------------------------------------


class TestOnFailureHook:
    def test_failure_hook_is_called(self):
        hook_calls = []

        def capture_failure(task_obj, task_run, state):
            hook_calls.append({"task": task_obj.name, "state": state.type})

        @task(on_failure=[capture_failure])
        def intentionally_broken():
            raise RuntimeError("deliberate")

        @flow
        def flow_with_hook():
            try:
                intentionally_broken()
            except RuntimeError:
                pass  # swallow so flow itself completes

        flow_with_hook()
        assert len(hook_calls) == 1
        # Prefect 3.x auto-generates task names with underscores (not hyphens)
        assert hook_calls[0]["task"] == "intentionally_broken"


# ---------------------------------------------------------------------------
# Async flows
# ---------------------------------------------------------------------------


class TestAsyncFlows:
    @pytest.mark.asyncio
    async def test_async_task_and_flow(self):
        @task
        async def async_fetch() -> str:
            await asyncio.sleep(0)  # yield to event loop
            return "fetched"

        @flow
        async def async_pipeline() -> str:
            return await async_fetch()

        result = await async_pipeline()
        assert result == "fetched"

    @pytest.mark.asyncio
    async def test_async_flow_state(self):
        @flow
        async def async_failing_flow():
            raise ValueError("async failure")

        state = await async_failing_flow(return_state=True)
        assert state.is_failed()


# ---------------------------------------------------------------------------
# Log capture
# ---------------------------------------------------------------------------


class TestLogCapture:
    def test_flow_logs_are_captured(self, tmp_csv, sqlite_db, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="etl"):
            csv_to_sqlite_pipeline(tmp_csv, sqlite_db)

        assert any("Extracted" in msg for msg in caplog.messages)
