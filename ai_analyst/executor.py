"""Runs validated SQL against the payments DuckDB file, read-only and sandboxed.

Each query gets its own connection opened with ``read_only=True`` and
``enable_external_access=False`` (no file, network or extension access even if a query
slipped past the validator), a wall-clock timeout, and a cap on returned rows.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from .schema_context import DEFAULT_DB_PATH


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    row_count: int          # rows returned (after the cap)
    truncated: bool         # True if the query produced more rows than max_rows
    elapsed_ms: int

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=self.columns)


class ExecutionError(RuntimeError):
    pass


class QueryExecutor:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, timeout_s: float = 30.0, max_rows: int = 1000):
        self.db_path = str(db_path)
        self.timeout_s = timeout_s
        self.max_rows = max_rows

    def run(self, sql: str) -> QueryResult:
        con = duckdb.connect(self.db_path, read_only=True, config={"enable_external_access": False})
        timed_out = threading.Event()

        def _interrupt():
            timed_out.set()
            con.interrupt()

        timer = threading.Timer(self.timeout_s, _interrupt)
        start = time.perf_counter()
        timer.start()
        try:
            cursor = con.execute(sql)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(self.max_rows + 1)
        except duckdb.Error as e:
            if timed_out.is_set():
                raise ExecutionError(f"Query exceeded the {self.timeout_s:.0f}s time limit.") from e
            raise ExecutionError(f"{type(e).__name__}: {str(e).splitlines()[0]}") from e
        finally:
            timer.cancel()
            con.close()

        truncated = len(rows) > self.max_rows
        rows = rows[: self.max_rows]
        return QueryResult(columns, rows, len(rows), truncated, int((time.perf_counter() - start) * 1000))
