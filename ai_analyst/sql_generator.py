"""Turns a natural-language question into DuckDB SQL using Google Gemini.

The schema context goes in the system instruction (it is identical for every question), the
question goes in the user turn, and Gemini returns structured JSON matching ``SQLDraft``: the
SQL plus the assumptions it made. If a previous attempt failed validation or execution, the
error is sent back so the model can repair its query.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field

from . import config
from .llm import GeminiJSONClient, GenerationError  # noqa: F401 (re-exported)
from .schema_context import SchemaContext

DEFAULT_MODEL = config.GEMINI_MODEL

SYSTEM_TEMPLATE = """You are a senior payments data analyst who writes DuckDB SQL.

Translate the user's business question into ONE read-only DuckDB query over the schema below.

Requirements:
- Output a single SELECT statement (a WITH ... SELECT is fine). No other statement types, no semicolons.
- Use only the tables and columns listed in the schema. Do not read files or call table functions.
- For transaction metrics, start from the standard valid_txn CTE shown in the schema and follow the metric definitions exactly.
- Return the rows a business user needs to see: group, order and name columns clearly; round rates to 4 decimals.
- Add LIMIT 200 unless the result is naturally small (e.g. aggregated to a handful of rows).
- If the question has several parts (e.g. "which is lowest, and how does it compare by method?"), return one result
  that answers every part: the headline figure, the comparison figures and a rank or label column, ordered so the
  first row answers the main ask. In explanation, say which rows or columns answer each part.
- If the question is ambiguous (time window, denominator, segment), choose the most reasonable interpretation and record it in assumptions.
- If the question cannot be answered from this data, set sql to an empty string and explain why in explanation.

{schema}"""


class SQLDraft(BaseModel):
    """Structured output returned by the model."""
    sql: str = Field(description="One DuckDB SELECT statement, or empty string if unanswerable")
    explanation: str = Field(description="One or two sentences on how the query answers the question")
    assumptions: list[str] = Field(description="Interpretations chosen for ambiguous parts; empty if none")
    tables_used: list[str] = Field(description="Tables referenced by the query")


@dataclass
class GenerationResult:
    draft: SQLDraft
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    raw_stop_reason: str | None = None
    notes: list[str] = field(default_factory=list)


class SQLGenerator(Protocol):
    def generate(self, question: str, previous_sql: str | None = None,
                 previous_error: str | None = None) -> GenerationResult: ...


def build_user_message(question: str, previous_sql: str | None, previous_error: str | None) -> str:
    msg = f"Question: {question}"
    if previous_sql is not None and previous_error:
        msg += (f"\n\nYour previous query failed.\nQuery:\n{previous_sql}\n\nError:\n{previous_error}\n\n"
                "Return a corrected query that avoids this error.")
    return msg


class GeminiSQLGenerator:
    """SQL generator backed by the Gemini API (see llm.GeminiJSONClient for key/retry handling)."""

    def __init__(self, schema: SchemaContext, model: str = DEFAULT_MODEL, client=None,
                 temperature: float = config.GEMINI_TEMPERATURE,
                 max_output_tokens: int = config.MAX_OUTPUT_TOKENS,
                 retries: int = config.RATE_LIMIT_RETRIES, backoff_s: float = config.RATE_LIMIT_BACKOFF_S):
        self.llm = GeminiJSONClient(model=model, client=client, temperature=temperature,
                                    max_output_tokens=max_output_tokens, retries=retries, backoff_s=backoff_s)
        self.model = model
        self.system_prompt = SYSTEM_TEMPLATE.format(schema=schema.to_prompt())

    def generate(self, question: str, previous_sql: str | None = None,
                 previous_error: str | None = None) -> GenerationResult:
        r = self.llm.generate(self.system_prompt, build_user_message(question, previous_sql, previous_error), SQLDraft)
        return GenerationResult(draft=r.parsed, model=r.model, input_tokens=r.input_tokens,
                                output_tokens=r.output_tokens, cache_read_tokens=r.cache_read_tokens,
                                raw_stop_reason=r.finish_reason)
