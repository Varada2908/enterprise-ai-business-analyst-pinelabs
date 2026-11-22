"""Offline tests: no API key or network needed. The LLM is replaced by scripted fakes.

    python -m ai_analyst.tests.test_offline        (or: pytest ai_analyst/tests)
"""
from __future__ import annotations

from types import SimpleNamespace

from ai_analyst.executor import ExecutionError, QueryExecutor
from ai_analyst.pipeline import TextToSQLPipeline
from ai_analyst.schema_context import load_schema_context
from ai_analyst.sql_generator import GeminiSQLGenerator, GenerationResult, SQLDraft
from ai_analyst.sql_validator import SQLValidator

SCHEMA = load_schema_context()
VALIDATOR = SQLValidator(SCHEMA.allowed_tables())

SR_SQL = f"""WITH {SCHEMA.VALID_TRANSACTIONS_CTE}
SELECT method_code,
       ROUND(count_if(status='SUCCESS')::DOUBLE / count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 4) AS success_rate
FROM valid_txn GROUP BY 1 ORDER BY 2"""


class ScriptedGenerator:
    """Returns pre-written drafts in order and records what feedback it received."""

    def __init__(self, *sqls: str):
        self.sqls = list(sqls)
        self.calls: list[tuple[str | None, str | None]] = []

    def generate(self, question, previous_sql=None, previous_error=None):
        self.calls.append((previous_sql, previous_error))
        sql = self.sqls[min(len(self.calls) - 1, len(self.sqls) - 1)]
        return GenerationResult(SQLDraft(sql=sql, explanation="scripted", assumptions=[], tables_used=[]), "fake")


# ----------------------------------------------------------------------- validator
def test_validator_accepts_safe_queries():
    for sql in [SR_SQL, "SELECT count(*) FROM transactions;", "SELECT bank_id FROM banks UNION SELECT bank_id FROM merchants",
                "SELECT * FROM main.banks LIMIT 5"]:
        r = VALIDATOR.validate(sql)
        assert r.ok, (sql, r.errors)


def test_validator_rejects_unsafe_queries():
    unsafe = [
        "DROP TABLE transactions",
        "DELETE FROM transactions",
        "INSERT INTO banks VALUES ('x')",
        "UPDATE merchants SET is_active = false",
        "CREATE TABLE x AS SELECT * FROM banks",
        "SELECT 1; DROP TABLE banks",
        "SELECT * INTO copy_banks FROM banks",
        "ATTACH 'other.db'",
        "COPY banks TO 'out.csv'",
        "PRAGMA database_list",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SET threads = 1",
        "SELECT * FROM read_csv('secrets.csv')",
        "SELECT * FROM 'payments.csv'",
        "SELECT getenv('HOME')",
        "SELECT * FROM duckdb_settings()",
        "SELECT * FROM customers",
        "SELECT * FROM other_db.main.banks",
        "",
    ]
    for sql in unsafe:
        r = VALIDATOR.validate(sql)
        assert not r.ok, f"should be rejected: {sql!r}"


# ----------------------------------------------------------------------- executor
def test_executor_runs_and_caps_rows():
    ex = QueryExecutor(max_rows=10)
    r = ex.run("SELECT transaction_id FROM transactions")
    assert r.row_count == 10 and r.truncated and r.columns == ["transaction_id"]


def test_executor_blocks_file_access_even_without_validator():
    try:
        QueryExecutor().run("SELECT * FROM read_csv('payments-analyst-dataset/output/data/banks.csv')")
    except ExecutionError:
        return
    raise AssertionError("external file access should be blocked")


def test_executor_is_read_only():
    try:
        QueryExecutor().run("DELETE FROM banks")
    except ExecutionError:
        return
    raise AssertionError("writes should fail on a read-only connection")


# ----------------------------------------------------------------------- pipeline
def test_pipeline_happy_path_matches_reference_success_rate():
    result = TextToSQLPipeline(generator=ScriptedGenerator(SR_SQL), schema=SCHEMA).ask("SR by method?")
    assert result.success, result.error
    rates = dict(result.rows)
    assert rates["NET_BANKING"] < rates["UPI"] < rates["CREDIT_CARD"]
    assert abs(float(rates["UPI"]) - 0.8756) < 1e-4      # matches the dataset's ground truth


def test_pipeline_repairs_after_validation_and_execution_errors():
    gen = ScriptedGenerator("DROP TABLE banks", "SELECT no_such_column FROM banks", "SELECT count(*) AS n FROM banks")
    result = TextToSQLPipeline(generator=gen, schema=SCHEMA).ask("How many banks?")
    assert result.success and result.rows == [(14,)]
    assert [a.stage for a in result.attempts] == ["validation", "execution", "ok"]
    assert "safety validator" in gen.calls[1][1] and "DuckDB error" in gen.calls[2][1]


def test_pipeline_gives_up_after_max_repairs():
    result = TextToSQLPipeline(generator=ScriptedGenerator("DROP TABLE banks"), schema=SCHEMA, max_repairs=2).ask("x")
    assert not result.success and len(result.attempts) == 3 and "No valid query" in result.error


def test_pipeline_reports_unanswerable_questions():
    result = TextToSQLPipeline(generator=ScriptedGenerator(""), schema=SCHEMA).ask("What is the weather?")
    assert not result.success and "Not answerable" in result.error


# ----------------------------------------------------------------------- Gemini generator (mocked client)
def _fake_client(finish_reason="STOP", parsed=None, text=None, error=None):
    parsed = parsed if parsed is not None or text is not None else SQLDraft(
        sql="SELECT 1", explanation="e", assumptions=["a"], tables_used=[])
    captured = {"calls": 0}

    def generate_content(**kwargs):
        captured["calls"] += 1
        captured.update(kwargs)
        if error is not None and captured["calls"] == 1:
            raise error
        usage = SimpleNamespace(prompt_token_count=100, candidates_token_count=20, cached_content_token_count=80)
        cand = SimpleNamespace(finish_reason=SimpleNamespace(name=finish_reason))
        return SimpleNamespace(parsed=parsed, text=text, candidates=[cand], usage_metadata=usage,
                               prompt_feedback=None, model_version="gemini-test")

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return client, captured


def test_gemini_generator_builds_request_and_parses_response():
    client, captured = _fake_client()
    gen = GeminiSQLGenerator(SCHEMA, model="gemini-2.5-flash", client=client)
    out = gen.generate("How many banks?", previous_sql="SELECT x", previous_error="bad column")
    assert out.draft.sql == "SELECT 1" and out.cache_read_tokens == 80 and out.input_tokens == 100
    cfg = captured["config"]
    assert captured["model"] == "gemini-2.5-flash" and cfg.response_schema is SQLDraft
    assert cfg.response_mime_type == "application/json" and cfg.temperature == 0
    assert "valid_txn" in cfg.system_instruction and "transactions" in cfg.system_instruction
    assert "bad column" in captured["contents"]


def test_gemini_generator_falls_back_to_json_text():
    client, _ = _fake_client(text='{"sql": "SELECT 2", "explanation": "e", "assumptions": [], "tables_used": []}')
    assert GeminiSQLGenerator(SCHEMA, client=client).generate("q").draft.sql == "SELECT 2"


def test_gemini_generator_handles_safety_block_and_truncation():
    from ai_analyst.sql_generator import GenerationError
    for reason in ("SAFETY", "MAX_TOKENS"):
        client, _ = _fake_client(finish_reason=reason)
        try:
            GeminiSQLGenerator(SCHEMA, client=client).generate("q")
        except GenerationError:
            continue
        raise AssertionError(f"{reason} should raise GenerationError")


def test_gemini_generator_retries_rate_limit():
    from google.genai import errors
    client, captured = _fake_client(error=errors.ClientError(429, {"error": {"message": "quota"}}))
    out = GeminiSQLGenerator(SCHEMA, client=client, backoff_s=0).generate("q")
    assert out.draft.sql == "SELECT 1" and captured["calls"] == 2


def test_gemini_generator_reports_missing_key():
    import os
    from ai_analyst.sql_generator import GenerationError
    saved = {k: os.environ.pop(k) for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY") if k in os.environ}
    try:
        GeminiSQLGenerator(SCHEMA).generate("q")
    except GenerationError as e:
        assert "GEMINI_API_KEY" in str(e)
        return
    finally:
        os.environ.update(saved)
    raise AssertionError("missing key should raise GenerationError")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 - report every failure
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
