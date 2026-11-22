"""Text-to-SQL pipeline: question -> Gemini SQL draft -> safety validation -> DuckDB execution.

If validation or execution fails, the error is fed back to the model for up to
``max_repairs`` corrected attempts. Every attempt is recorded so the UI can show its work.

    python -m ai_analyst.pipeline "Which payment method has the lowest success rate?"
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import asdict, dataclass, field

import pandas as pd

from .executor import ExecutionError, QueryExecutor
from .schema_context import SchemaContext, load_schema_context
from .sql_generator import DEFAULT_MODEL, GeminiSQLGenerator, GenerationError, SQLGenerator
from .sql_validator import SQLValidator


@dataclass
class Attempt:
    sql: str
    stage: str              # "validation" | "execution" | "ok"
    error: str | None = None


@dataclass
class PipelineResult:
    question: str
    success: bool
    sql: str | None = None
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    error: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=self.columns)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rows"] = [list(r) for r in self.rows]
        return d


class TextToSQLPipeline:
    def __init__(self, generator: SQLGenerator | None = None, schema: SchemaContext | None = None,
                 executor: QueryExecutor | None = None, max_repairs: int = 2):
        self.schema = schema or load_schema_context()
        self.generator = generator or GeminiSQLGenerator(self.schema)
        self.validator = SQLValidator(self.schema.allowed_tables())
        self.executor = executor or QueryExecutor()
        self.max_repairs = max_repairs

    def ask(self, question: str) -> PipelineResult:
        start = time.perf_counter()
        result = PipelineResult(question=question, success=False)
        prev_sql: str | None = None
        prev_error: str | None = None

        for _ in range(1 + self.max_repairs):
            try:
                gen = self.generator.generate(question, prev_sql, prev_error)
            except GenerationError as e:
                result.error = str(e)
                break
            draft = gen.draft
            result.model = gen.model
            result.input_tokens += gen.input_tokens
            result.output_tokens += gen.output_tokens
            result.sql, result.explanation, result.assumptions = draft.sql, draft.explanation, draft.assumptions

            if not draft.sql.strip():                       # model says the data can't answer this
                result.error = f"Not answerable from the available data: {draft.explanation}"
                break

            check = self.validator.validate(draft.sql)
            if not check.ok:
                result.attempts.append(Attempt(draft.sql, "validation", check.error_message))
                prev_sql, prev_error = draft.sql, f"Rejected by the safety validator: {check.error_message}"
                continue

            try:
                qr = self.executor.run(check.sql)
            except ExecutionError as e:
                result.attempts.append(Attempt(check.sql, "execution", str(e)))
                prev_sql, prev_error = check.sql, f"DuckDB error: {e}"
                continue

            result.attempts.append(Attempt(check.sql, "ok"))
            result.sql = check.sql
            result.columns, result.rows = qr.columns, qr.rows
            result.row_count, result.truncated = qr.row_count, qr.truncated
            result.success, result.error = True, None
            break
        else:
            result.error = f"No valid query after {1 + self.max_repairs} attempts. Last error: {prev_error}"

        result.elapsed_ms = int((time.perf_counter() - start) * 1000)
        return result


def _print_result(r: PipelineResult) -> None:
    print(f"Question: {r.question}\n")
    if r.sql:
        print("SQL:\n" + r.sql + "\n")
    if r.explanation:
        print(f"Explanation: {r.explanation}")
    for a in r.assumptions:
        print(f"Assumption: {a}")
    if len(r.attempts) > 1:
        print(f"Attempts: {len(r.attempts)} (" + ", ".join(f"{a.stage}" for a in r.attempts) + ")")
    print()
    if r.success:
        with pd.option_context("display.max_columns", None, "display.width", 200):
            print(r.to_dataframe().to_string(max_rows=50))
        print(f"\n{r.row_count} row(s){' (truncated)' if r.truncated else ''} | {r.elapsed_ms} ms | "
              f"{r.model} | tokens in/out {r.input_tokens}/{r.output_tokens}")
    else:
        print(f"FAILED: {r.error}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ask the payments database a question in plain English.")
    ap.add_argument("question", nargs="+")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model (default from GEMINI_MODEL)")
    ap.add_argument("--max-rows", type=int, default=1000)
    args = ap.parse_args(argv)

    schema = load_schema_context()
    pipeline = TextToSQLPipeline(generator=GeminiSQLGenerator(schema, model=args.model),
                                 schema=schema, executor=QueryExecutor(max_rows=args.max_rows))
    result = pipeline.ask(" ".join(args.question))
    _print_result(result)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
