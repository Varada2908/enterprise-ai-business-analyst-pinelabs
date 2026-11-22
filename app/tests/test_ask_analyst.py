"""Ask the Analyst tests, offline: the real pipeline, investigator, validator and DuckDB run end to end,
only the Gemini calls are replaced by scripted fakes (no API key or quota needed).

    cd app
    python -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1]
for p in (str(APP), str(APP.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from ai_analyst.investigator import (InvestigationStep, Investigator, PlannedQuery, Report,  # noqa: E402
                                     ReportFinding, ReportHypothesis)
from ai_analyst.llm import LLMResponse  # noqa: E402
from ai_analyst.pipeline import TextToSQLPipeline  # noqa: E402
from ai_analyst.schema_context import load_schema_context  # noqa: E402
from ai_analyst.sql_generator import GenerationResult, SQLDraft  # noqa: E402

SCHEMA = load_schema_context()
CTE = "WITH " + SCHEMA.VALID_TRANSACTIONS_CTE
T = "count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED'))"
SR = f"round(count_if(status = 'SUCCESS')::DOUBLE / {T}, 4)"
SR_BY_METHOD = f"{CTE} SELECT method_code, {T} AS terminal_attempts, {SR} AS success_rate FROM valid_txn GROUP BY 1 ORDER BY 3"
WEEKLY = (f"{CTE} SELECT date_trunc('week', created_at)::DATE AS week_start, {SR} AS success_rate "
          "FROM valid_txn GROUP BY 1 ORDER BY 1")


class FakeGenerator:
    """Stands in for GeminiSQLGenerator; records the prompts it receives."""

    def __init__(self):
        self.questions: list[str] = []

    def generate(self, question, previous_sql=None, previous_error=None):
        self.questions.append(question)
        sql = WEEKLY if "weekly" in question.lower().split("current question:")[-1] else SR_BY_METHOD
        return GenerationResult(SQLDraft(sql=sql, explanation="Success rate per group on valid transactions.",
                                         assumptions=["Whole data period"], tables_used=["transactions"]), "fake")


class FakeLLM:
    """Two-round investigation: one query, then a report with a finding and a hypothesis."""

    def __init__(self):
        self.calls = 0

    def generate(self, system, contents, schema):
        self.calls += 1
        if self.calls % 2 == 1:
            step = InvestigationStep(notes="compare methods", queries=[PlannedQuery(purpose="SR by method",
                                                                                    sql=SR_BY_METHOD)])
        else:
            step = InvestigationStep(notes="report", queries=[], report=Report(
                answer="Net Banking has the lowest success rate.",
                findings=[ReportFinding(statement="Net Banking success rate is 84.07%.", evidence_query_ids=["Q1"])],
                hypotheses=[ReportHypothesis(statement="Bank-side timeouts may drive it.", rationale="Typical for NB",
                                             supporting_query_ids=[], how_to_verify="Break failures down by bank")],
                caveats=["Synthetic data"], suggested_follow_ups=["Which banks drive the Net Banking failures?"]))
        return LLMResponse(parsed=step, text=step.model_dump_json(), model="fake", input_tokens=10, output_tokens=5)


@pytest.fixture
def fakes(monkeypatch):
    from services import analyst, backend

    gen = FakeGenerator()
    monkeypatch.setattr(analyst, "get_pipeline", lambda model: TextToSQLPipeline(generator=gen, schema=SCHEMA,
                                                                                 executor=backend.get_executor()))
    monkeypatch.setattr(analyst, "get_investigator",
                        lambda model, rounds: Investigator(schema=SCHEMA, llm=FakeLLM(), max_rounds=rounds))
    monkeypatch.setattr(backend, "api_key_configured", lambda: True)
    monkeypatch.chdir(APP)
    return gen


def type_and_send(at, text, mode=None):
    """Type into the composer box and press Send, like a user would."""
    if mode:
        at.segmented_control(key="ask_mode").set_value(mode)
    at.text_area(key="ask_text").input(text)
    next(b for b in at.button if b.label == "Send").click().run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def _app():
    at = AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=180)
    at.run()
    at.switch_page("views/ask_analyst.py").run()
    assert not at.exception, [e.message for e in at.exception]
    return at


# ----------------------------------------------------------------------------- routing
@pytest.mark.parametrize("q,expected", [
    ("What is the success rate by payment method?", "quick"),
    ("Show TPV for the top 10 merchants", "quick"),
    ("Why did the success rate drop last week?", "deep"),
    ("What is driving the failures at SBI?", "deep"),
    ("Investigate Net Banking performance", "deep"),
    # several asks in one question get a written answer per part
    ("Which bank has the lowest success rate overall, and how does it compare across payment methods?", "deep"),
    ("Compare UPI and card success rates by week", "deep"),
    ("What is the success rate and TPV by payment method?", "quick"),
    ("Which 10 merchants had the highest TPV in October 2026?", "quick"),
])
def test_auto_routing(q, expected):
    from services.analyst import route

    assert route(q, "auto")[0] == expected
    assert route(q, "quick")[0] == "quick" and route(q, "deep")[0] == "deep"


def test_auto_routing_keeps_investigation_follow_ups_deep():
    from services.analyst import route

    assert route("And what about Android?", "auto", last_kind="deep")[0] == "deep"
    assert route("And what about Android?", "auto", last_kind="quick")[0] == "quick"


# ----------------------------------------------------------------------------- charts
def test_auto_chart_choices():
    from components.answers import auto_chart

    ranking = pd.DataFrame({"method_code": ["UPI", "WALLET", "NET_BANKING"], "success_rate": [0.87, 0.93, 0.84]})
    weekly = pd.DataFrame({"week_start": pd.date_range("2026-08-03", periods=5, freq="7D"),
                           "success_rate": [0.9, 0.91, 0.88, 0.9, 0.89]})
    assert auto_chart(ranking).data[0].type == "scatter"                    # rates: dot plot, not cut-off bars
    tpv = pd.DataFrame({"merchant": ["A", "B", "C"], "tpv": [3e6, 2e6, 1e6]})
    assert auto_chart(tpv).data[0].type == "bar"
    assert auto_chart(weekly).data[0].type == "scatter"
    assert auto_chart(pd.DataFrame({"a": ["x"], "n": [1]})) is None          # single row -> tiles, not a chart


# ----------------------------------------------------------------------------- page, end to end
def test_empty_state_shows_suggestions(fakes):
    at = _app()
    labels = [b.label for b in at.button]
    assert labels.count("Ask this") == 6           # examples are optional shortcuts...
    assert "Send" in labels                        # ...next to a free-text box
    assert at.text_area(key="ask_text").value in ("", None)


def test_quick_answer_then_follow_up_keeps_context(fakes):
    at = _app()
    at.session_state["ask_mode"] = "auto"
    type_and_send(at, "What is the success rate by payment method?")
    assert not at.exception, [e.message for e in at.exception]
    convo = at.session_state["conversation"]
    assert len(convo.turns) == 1 and convo.turns[0].kind == "quick" and convo.turns[0].success
    assert "Investigate deeper" in [b.label for b in at.button]

    type_and_send(at, "And the weekly trend of it?")
    assert not at.exception, [e.message for e in at.exception]
    assert len(convo.turns) == 2 and convo.turns[1].success
    # the follow-up was sent with the earlier question and its answer as context
    assert "What is the success rate by payment method?" in fakes.questions[-1]
    assert "Current question: And the weekly trend of it?" in fakes.questions[-1]


def test_deep_investigation_renders_findings_and_follow_ups(fakes):
    at = _app()
    type_and_send(at, "Why is Net Banking underperforming?")
    assert not at.exception, [e.message for e in at.exception]
    turn = at.session_state["conversation"].turns[0]
    assert turn.kind == "deep" and turn.success
    assert turn.deep.findings and turn.deep.findings[0].check_status == "verified"
    assert turn.deep.hypotheses
    labels = [b.label for b in at.button]
    assert "Which banks drive the Net Banking failures?" in labels

    # clicking a suggested follow-up runs it as a new turn with the investigation as history
    next(b for b in at.button if b.label.startswith("Which banks")).click().run()
    assert not at.exception, [e.message for e in at.exception]
    convo = at.session_state["conversation"]
    assert len(convo.turns) == 2 and convo.turns[1].kind == "deep"
    assert "Net Banking has the lowest success rate." in convo.context()


class AnswerLLM(FakeLLM):
    """FakeLLM whose report answer is given; a rewrite request gets a non-report step back (rewrite fails)."""

    def __init__(self, answer):
        super().__init__()
        self.answer = answer

    def generate(self, system, contents, schema):
        resp = super().generate(system, contents, schema)
        if resp.parsed.report is not None:
            resp.parsed.report.answer = self.answer
        return resp


@pytest.mark.parametrize("answer,expected,absent", [
    ("Net Banking has the lowest success rate at 84.70%.", ["Answer check", "84.70%"], "Numbers match"),
    ("Net Banking has the lowest success rate at 84.07%.", ["Numbers match the verified findings"], "Answer check"),
])
def test_deep_answer_shows_answer_check(fakes, monkeypatch, answer, expected, absent):
    from services import analyst
    monkeypatch.setattr(analyst, "get_investigator",
                        lambda model, rounds: Investigator(schema=SCHEMA, llm=AnswerLLM(answer), max_rounds=rounds))
    at = _app()
    type_and_send(at, "Why is Net Banking underperforming?", mode="deep")
    inv = at.session_state["conversation"].turns[0].deep
    assert inv.success and inv.answer == answer
    html = " ".join(str(getattr(h, "proto", "")) for h in at.get("html"))
    for text in expected:
        assert text in html
    assert absent not in html


def test_pending_question_from_overview_runs_as_investigation(fakes):
    at = AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=180)
    at.run()
    at.session_state["pending_question"] = "Why did the success rate drop for M0137 / ANDROID?"
    at.switch_page("views/ask_analyst.py").run()
    assert not at.exception, [e.message for e in at.exception]
    turn = at.session_state["conversation"].turns[0]
    assert turn.kind == "deep" and turn.question.startswith("Why did")


def test_backend_failure_is_shown_not_raised(fakes, monkeypatch):
    from services import analyst

    def boom(model):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(analyst, "get_pipeline", boom)
    at = _app()
    type_and_send(at, "What is TPV by method?")
    assert not at.exception, [e.message for e in at.exception]
    turn = at.session_state["conversation"].turns[0]
    assert not turn.success and "Retry" in [b.label for b in at.button]
    assert analyst.friendly_error(turn.error)[0].startswith("The Gemini free-tier limit")


def test_typed_custom_question_respects_mode_and_clears_box(fakes):
    at = _app()
    type_and_send(at, "How many merchants processed weekly payments?", mode="deep")
    convo = at.session_state["conversation"]
    assert [t.kind for t in convo.turns] == ["deep"]             # the Deep mode choice is honoured
    assert convo.turns[0].question == "How many merchants processed weekly payments?"
    assert at.text_area(key="ask_text").value in ("", None)      # box cleared for the next question
    assert "Ask this" not in [b.label for b in at.button]        # examples hide once the chat starts
    assert "Send" in [b.label for b in at.button]                # but the box is still there

    type_and_send(at, "Show the weekly success rate", mode="quick")
    assert [t.kind for t in convo.turns] == ["deep", "quick"] and convo.turns[1].success
    assert "Net Banking has the lowest success rate." in fakes.questions[-1]   # follow-up carries context


def test_empty_question_is_not_sent(fakes):
    at = _app()
    next(b for b in at.button if b.label == "Send").click().run()
    assert not at.exception
    assert not at.session_state["conversation"].turns
