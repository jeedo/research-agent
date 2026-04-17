"""
Unit tests for individual ETL tasks.

Strategy: call task.fn(...) to bypass the Prefect engine entirely.
Use unittest.mock / pytest-mock to replace DB dependencies.
No Prefect server or real database required.
"""

import sqlite3
from unittest.mock import MagicMock, call, patch

import pytest

from etl import extract_csv, load_to_sqlite, transform_rows


# ---------------------------------------------------------------------------
# extract_csv
# ---------------------------------------------------------------------------


class TestExtractCsv:
    def test_returns_rows_as_lists(self, tmp_csv):
        rows = extract_csv.fn(tmp_csv)
        assert rows == [["alpha", "1.5"], ["beta", "2.0"], ["gamma", "3.75"]]

    def test_skips_blank_lines(self, tmp_path):
        csv = tmp_path / "blanks.csv"
        csv.write_text("a,1\n\nb,2\n\n")
        rows = extract_csv.fn(str(csv))
        assert len(rows) == 2

    def test_raises_on_missing_file(self):
        with pytest.raises(FileNotFoundError):
            extract_csv.fn("/nonexistent/path/file.csv")


# ---------------------------------------------------------------------------
# transform_rows
# ---------------------------------------------------------------------------


class TestTransformRows:
    def test_converts_to_dicts(self):
        raw = [["alpha", "1.5"], ["beta", "2.0"]]
        result = transform_rows.fn(raw)
        assert result == [{"name": "alpha", "value": 1.5}, {"name": "beta", "value": 2.0}]

    def test_skips_rows_with_fewer_than_two_columns(self):
        raw = [["only_one_col"], ["good", "9.9"]]
        result = transform_rows.fn(raw)
        assert len(result) == 1
        assert result[0]["name"] == "good"

    def test_non_numeric_value_becomes_none(self):
        raw = [["item", "not_a_number"]]
        result = transform_rows.fn(raw)
        assert result[0]["value"] is None

    def test_empty_input_returns_empty_list(self):
        assert transform_rows.fn([]) == []

    def test_strips_whitespace_from_name(self):
        raw = [["  padded  ", "5.0"]]
        result = transform_rows.fn(raw)
        assert result[0]["name"] == "padded"


# ---------------------------------------------------------------------------
# load_to_sqlite — mocked
# ---------------------------------------------------------------------------


class TestLoadToSqliteMocked:
    """Mock sqlite3.connect so no real file I/O occurs."""

    def test_returns_record_count(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        records = [{"name": "a", "value": 1.0}, {"name": "b", "value": 2.0}]

        with patch("sqlite3.connect", return_value=mock_conn):
            count = load_to_sqlite.fn("fake.db", records)

        assert count == 2

    def test_creates_table_and_inserts(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        records = [{"name": "x", "value": 3.14}]

        with patch("sqlite3.connect", return_value=mock_conn):
            load_to_sqlite.fn("fake.db", records)

        # CREATE TABLE was called
        create_call = mock_cursor.execute.call_args_list[0]
        assert "CREATE TABLE IF NOT EXISTS records" in create_call.args[0]

        # executemany was called with our records
        mock_cursor.executemany.assert_called_once()
        _, args = mock_cursor.executemany.call_args
        assert list(args[0]) == records

    def test_connection_always_closed(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()

        with patch("sqlite3.connect", return_value=mock_conn):
            load_to_sqlite.fn("fake.db", [])

        mock_conn.close.assert_called_once()

    def test_connection_closed_even_on_error(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.executemany.side_effect = sqlite3.OperationalError("disk full")
        mock_conn.cursor.return_value = mock_cursor

        with patch("sqlite3.connect", return_value=mock_conn):
            with pytest.raises(sqlite3.OperationalError):
                load_to_sqlite.fn("fake.db", [{"name": "x", "value": 1.0}])

        mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# pytest-mock style (mocker fixture)
# ---------------------------------------------------------------------------


class TestLoadToSqlitePytestMock:
    def test_uses_mocker_fixture(self, mocker):
        mock_conn = mocker.MagicMock()
        mock_cursor = mocker.MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mocker.patch("sqlite3.connect", return_value=mock_conn)

        records = [{"name": "z", "value": 99.0}]
        result = load_to_sqlite.fn("test.db", records)

        assert result == 1
        mock_cursor.execute.assert_called()   # CREATE TABLE
        mock_cursor.executemany.assert_called_once_with(
            "INSERT INTO records (name, value) VALUES (:name, :value)",
            records,
        )
