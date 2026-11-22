"""Single connection point between the UI and the ai_analyst backend.

Heavy objects are created once per server process (st.cache_resource); query results are cached
per SQL text (st.cache_data) because the dataset is a read-only snapshot. Every query the UI runs
goes through the backend's own SQLValidator and sandboxed QueryExecutor - the UI never opens its
own database connection and never reads the evaluator-only files in output/eval/.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_analyst import config  # noqa: E402
from ai_analyst.executor import ExecutionError, QueryExecutor  # noqa: E402
from ai_analyst.schema_context import SchemaContext, load_schema_context  # noqa: E402
from ai_analyst.sql_validator import SQLValidator  # noqa: E402

MODEL_OPTIONS = {
    "gemini-3.5-flash": "Standard · gemini-3.5-flash",
    "gemini-3.5-flash-lite": "Lite · gemini-3.5-flash-lite (faster)",
}


class QueryError(RuntimeError):
    """A dashboard query was rejected by the validator or failed in DuckDB."""


@dataclass(frozen=True)
class QueryOutcome:
    df: pd.DataFrame
    sql: str
    elapsed_ms: int
    truncated: bool


# ----------------------------------------------------------------------------- shared backend objects
@st.cache_resource(show_spinner=False)
def get_schema() -> SchemaContext:
    return load_schema_context()


@st.cache_resource(show_spinner=False)
def get_validator() -> SQLValidator:
    return SQLValidator(get_schema().allowed_tables())


@st.cache_resource(show_spinner=False)
def get_executor() -> QueryExecutor:
    return QueryExecutor(max_rows=5000)


def metric_sql(name: str) -> str:
    """SQL expression of a metric exactly as defined in the data catalog."""
    for m in get_schema().metrics:
        if m["name"] == name:
            return m["sql"]
    raise KeyError(f"metric {name!r} is not defined in the data catalog")


# ----------------------------------------------------------------------------- safe query execution
@st.cache_data(show_spinner=False, max_entries=256)
def _cached_run(sql: str) -> tuple[pd.DataFrame, str, int, bool]:
    check = get_validator().validate(sql)
    if not check.ok:
        raise QueryError(f"Rejected by the SQL safety validator: {check.error_message}")
    try:
        r = get_executor().run(check.sql)
    except ExecutionError as e:
        raise QueryError(str(e)) from e
    return decimals_to_float(r.to_dataframe()), check.sql, r.elapsed_ms, r.truncated


def decimals_to_float(df: pd.DataFrame) -> pd.DataFrame:
    """DuckDB returns DECIMAL (amounts) as Python Decimal objects; charts and formatting need floats."""
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna()
            if not sample.empty and isinstance(sample.iloc[0], Decimal):
                df[col] = df[col].astype(float)
    return df


def run_query(sql: str) -> QueryOutcome:
    """Validate (read-only, allowed tables only) and run a query; results are cached by SQL text."""
    df, safe_sql, ms, truncated = _cached_run(sql)
    return QueryOutcome(df.copy(), safe_sql, ms, truncated)


# ----------------------------------------------------------------------------- AI settings (read-only view)
def api_key_configured() -> bool:
    """Whether a Gemini key is available. The key itself is never read into the UI."""
    return bool(config.gemini_api_key())


def default_model() -> str:
    return config.GEMINI_MODEL
