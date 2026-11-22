"""Ask the Analyst service: routes a question to the Text-to-SQL pipeline (quick answer) or the
multi-step investigator (deep investigation) and keeps one conversation history for both.

Backend objects are used as-is:
- quick answers  -> ai_analyst.pipeline.TextToSQLPipeline.ask
- investigations -> ai_analyst.investigator.InvestigationSession.ask (its turns are the conversation memory)

Quick answers are added to the same InvestigationSession history (as a plain answer, without findings),
so a deep follow-up knows what a quick answer said, and quick follow-ups get that history as context.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Literal

import pandas as pd
import streamlit as st

from ai_analyst.investigator import Investigation, InvestigationSession, Investigator
from ai_analyst.llm import GeminiJSONClient
from ai_analyst.pipeline import PipelineResult, TextToSQLPipeline
from ai_analyst.sql_generator import GeminiSQLGenerator

from .backend import decimals_to_float, get_executor, get_schema

Mode = Literal["auto", "quick", "deep"]
Kind = Literal["quick", "deep"]

MODE_LABELS = {"auto": "Auto", "quick": "Quick answer", "deep": "Deep investigation"}
MODE_HELP = {
    "auto": "Picks quick answer for 'what / how many' questions and deep investigation for 'why' and multi-part "
            "comparison questions.",
    "quick": "One validated SQL query: fast (about 5–20 s), returns a table and chart.",
    "deep": "Several rounds of queries with checked evidence: slower (about 1–3 min).",
}

# starter questions written for this UI (not taken from any evaluation set)
SUGGESTED = [
    ("quick", "What is the success rate and TPV by payment method?"),
    ("quick", "Show the weekly success rate for UPI over the last 8 weeks."),
    ("quick", "Which 10 merchants had the highest TPV in October 2026?"),
    ("deep", "Why did the payment success rate drop in the last two weeks?"),
    ("deep", "Which banks are causing the most failed transactions, and why?"),
    ("deep", "Is Net Banking underperforming other methods? What is driving it?"),
]

_DEEP = re.compile(
    r"\b(why|investigat\w*|root cause|driv(e|es|er|ers|ing)|cause[ds]?|explain\w*|what happened|"
    r"diagnos\w*|deep dive|reason for|behind|anomal\w*|spike|dip|drop(ped|s)?|declin\w*|degrad\w*|"
    r"underperform\w*|went wrong|impact of|attribut\w*)\b", re.I)
# several asks in one question ("which bank is lowest, and how does it compare by method?") need a written answer
# per part, which the deep mode gives; a single query + table leaves the reader to work it out
_MULTI_PART = re.compile(
    r"\b(compar\w*|versus|vs\.?|relative to)\b"
    r"|[,;]?\s+and\s+(how|which|what|where|when|is|are|does|did|break)\b"
    r"|\?.+\?", re.I)
_FOLLOW_UP = re.compile(r"^(and|also|what about|how about|then|so|is it|was it|does it|did it|"
                        r"which of (them|those)|break (it|that) down|drill)\b|\b(it|that|this|those|them)\b", re.I)


def route(question: str, mode: Mode, last_kind: Kind | None = None) -> tuple[Kind, str]:
    """Pick quick or deep for a question. Returns (kind, short reason shown to the user)."""
    if mode == "quick":
        return "quick", "Quick answer selected"
    if mode == "deep":
        return "deep", "Deep investigation selected"
    if _DEEP.search(question):
        return "deep", "Auto: asks why / what is driving a change"
    if _MULTI_PART.search(question):
        return "deep", "Auto: a multi-part comparison"
    if last_kind == "deep" and _FOLLOW_UP.search(question) and len(question.split()) <= 14:
        return "deep", "Auto: follow-up to an investigation"
    return "quick", "Auto: a direct data question"


# ----------------------------------------------------------------------------- backend objects
@st.cache_resource(show_spinner=False)
def get_pipeline(model: str) -> TextToSQLPipeline:
    schema = get_schema()
    return TextToSQLPipeline(generator=GeminiSQLGenerator(schema, model=model), schema=schema,
                             executor=get_executor())


@st.cache_resource(show_spinner=False)
def get_investigator(model: str, max_rounds: int) -> Investigator:
    return Investigator(schema=get_schema(), llm=GeminiJSONClient(model=model), max_rounds=max_rounds)


# ----------------------------------------------------------------------------- conversation
@dataclass
class Turn:
    question: str
    kind: Kind
    route_reason: str
    model: str
    quick: PipelineResult | None = None
    deep: Investigation | None = None
    elapsed_s: float = 0.0
    follow_ups: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        r = self.quick or self.deep
        return bool(r and r.success)

    @property
    def error(self) -> str | None:
        r = self.quick or self.deep
        return None if r is None else r.error


def quick_df(r: PipelineResult) -> pd.DataFrame:
    return decimals_to_float(r.to_dataframe())


def _quick_summary(r: PipelineResult, max_rows: int = 8) -> str:
    """Short text version of a quick answer for the conversation memory."""
    if not r.success:
        return ""
    df = quick_df(r)
    head = df.head(max_rows).to_string(index=False, max_colwidth=40)
    more = f"\n({r.row_count} rows in total)" if r.row_count > max_rows else ""
    return f"{r.explanation}\nResult:\n{head}{more}\nSQL used:\n{r.sql}"


class Conversation:
    """One chat: the visible turns plus the InvestigationSession that carries context to the model."""

    def __init__(self, max_history: int = 3):
        self.turns: list[Turn] = []
        self.session = InvestigationSession(investigator=_NoInvestigator(),
                                            max_history=max_history)

    @property
    def last_kind(self) -> Kind | None:
        return self.turns[-1].kind if self.turns else None

    def context(self) -> str:
        return self.session.history_text()

    def ask(self, question: str, kind: Kind, route_reason: str, model: str, max_rounds: int,
            on_tick: Callable[[float], None] | None = None) -> Turn:
        start = time.perf_counter()
        try:
            turn = self._ask(question, kind, route_reason, model, max_rounds, on_tick)
        except Exception as e:  # noqa: BLE001 - unexpected failure: show it as a failed turn, keep the chat usable
            turn = Turn(question, kind, route_reason, model)
            if kind == "quick":
                turn.quick = PipelineResult(question=question, success=False, error=str(e))
            else:
                turn.deep = Investigation(question=question, success=False, error=str(e))
        turn.elapsed_s = time.perf_counter() - start
        self.turns.append(turn)
        return turn

    def _ask(self, question, kind, route_reason, model, max_rounds, on_tick) -> Turn:
        if kind == "quick":
            history = self.context()
            prompt = question if not history else (
                "Conversation so far (use it to resolve references like 'it' or 'those'):\n"
                f"{history}\n\nCurrent question: {question}")
            result = _run_with_ticks(lambda: get_pipeline(model).ask(prompt), on_tick)
            result.question = question
            self.session.turns.append(Investigation(question=question, success=result.success,
                                                    answer=_quick_summary(result), error=result.error))
            return Turn(question, kind, route_reason, model, quick=result)
        self.session.investigator = get_investigator(model, max_rounds)
        inv = _run_with_ticks(lambda: self.session.ask(question), on_tick)
        return Turn(question, kind, route_reason, model, deep=inv, follow_ups=list(inv.suggested_follow_ups or [])[:4])


class _NoInvestigator:
    """Placeholder until the first deep question picks a model (avoids creating an API client early)."""

    def investigate(self, question: str, history: str = ""):  # pragma: no cover - never used
        raise RuntimeError("investigator not configured")


def _run_with_ticks(fn, on_tick: Callable[[float], None] | None, every_s: float = 1.0):
    """Run a slow backend call in a worker thread so the page can show elapsed time meanwhile."""
    if on_tick is None:
        return fn()
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        while True:
            try:
                return fut.result(timeout=every_s)
            except TimeoutError:
                on_tick(time.perf_counter() - start)


def friendly_error(message: str | None) -> tuple[str, str]:
    """(title, advice) for an error coming back from the backend."""
    m = (message or "").lower()
    if "429" in m or "resource_exhausted" in m or "quota" in m or "rate" in m:
        return ("The Gemini free-tier limit was reached.",
                "Wait a minute and retry, or switch to the Lite model in the sidebar (it has a larger daily quota).")
    if "api key" in m or "api_key" in m:
        return ("No Gemini API key is configured.", "Add GEMINI_API_KEY to ai_analyst/.env and restart the app.")
    if "not answerable" in m:
        return ("This question can't be answered from the available data.", message or "")
    if "503" in m or "unavailable" in m or "overloaded" in m:
        return ("The Gemini service is busy right now.", "Retry in a moment.")
    return ("The analyst could not answer this question.", message or "Unknown error.")
