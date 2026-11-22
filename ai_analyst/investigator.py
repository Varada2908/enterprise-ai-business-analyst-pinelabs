"""Multi-step investigation agent: drills into a question with several safe SQL queries.

Where ``pipeline.TextToSQLPipeline`` answers with one query, the investigator works in rounds:

    1. Gemini plans up to N queries (e.g. "SR by bank for NET_BANKING", "failure reasons vs other methods").
    2. Every query passes the same sqlglot safety validator and runs on the read-only, sandboxed
       DuckDB executor. Results (or errors) are sent back to the model.
    3. The model either plans more queries or writes a report.

The report separates **findings** (facts shown by a cited query) from **hypotheses** (plausible
explanations the data does not prove). The code enforces that split: a finding that cites no
successful query is demoted to a hypothesis, and every number in a finding is traced back to
the cited result tables (see ``evidence.py``).

``InvestigationSession`` keeps earlier questions and answers so follow-ups ("why is that?",
"break it down by bank") build on them.

    python -m ai_analyst.investigator "Which payment method has the lowest success rate, and why?"
    python -m ai_analyst.investigator          # interactive: ask follow-ups at the prompt
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from . import config
from .driver_scan import (DriverScanError, DriverScanRequest, build_change_point_query, build_driver_scan,
                          periods_from_change)
from .evidence import answer_claim_issues, check_answer_numbers, check_numbers, unhedged_causal_sentences
from .executor import ExecutionError, QueryExecutor
from .llm import GeminiJSONClient, GenerationError
from .schema_context import SchemaContext, load_schema_context
from .sql_validator import SQLValidator

# ----------------------------------------------------------------------------- prompt
SYSTEM_TEMPLATE = """You are a senior payments data analyst investigating questions about a payments business.
You work in rounds: plan read-only DuckDB queries, read their results, then dig deeper or write a report.

## How to investigate
- First establish the headline number the question is about, with its denominator (terminal attempts).
- Then decompose it. Useful cuts: issuing bank, merchant, merchant category/size, failure_code and
  failure_category / responsible_party, status (FAILED vs USER_CANCELLED), week (date_trunc('week', created_at)),
  device_platform, channel, card_network, attempt_number (first attempt vs retry).
- Always compare a segment against a benchmark (the rest of the population, other payment methods, or an earlier
  period). A segment is only "a driver" if it is worse than the benchmark AND large enough to matter.
- Rank segments by contribution, not only by rate: excess_failures = terminal_attempts * (benchmark_sr - segment_sr).
- Check the time trend to tell a structural gap (flat every week) from an incident (a step change). For a change
  between periods, separate mix effects (segment shares moved) from rate effects (segment SRs moved).
- When a failure_code or a period spikes, drill one level further by merchant_id and device_platform before
  concluding: a single merchant integration or app release can look like a broad gateway or bank problem.
- Treat segments with fewer than 100 terminal attempts as too small to conclude from; say so if they look extreme.
- Note data-quality issues that affect the answer (unattributable bank/merchant, FAILED rows missing failure_code).
- Use as few rounds as you need; stop when further cuts would not change the conclusion.

## Driver scan (built-in, deterministic)
For any question about a CHANGE over time (rose, fell, dropped, spiked, got worse, "since <date>", compared with an
earlier period), request a driver_scan in your FIRST round.
- If the question gives the timing (e.g. "since 12 October", "last two weeks vs the two before", "in week X"), give a
  baseline period just before the change and the current period (equal lengths; dates YYYY-MM-DD, inclusive).
- If the timing is vague ("recently", "lately", "these days") or you are unsure when it started, leave ALL FOUR dates
  null: the scan first detects, per segment, the day the success rate broke (ranked by statistical clarity) and then
  compares equal periods around the strongest break. Report the detected change date as part of the answer.
  Each detected break has a status: ONGOING, RECOVERED (sr_first_7d shows the incident, sr_last_7d the recovery) or
  UNCLEAR. Never describe a recovered incident as a current problem. If no break is detected, say so plainly.
The comparison returns two evidence queries:
(a) segments ranked by impact on the success-rate change across merchant, device_platform, merchant x platform,
payment_method, issuing_bank, bank x method, merchant_category and channel, and (b) the shift in each failure_code
with the merchant x platform contributing most to it. Then drill into the top-ranked segments with your own queries
(e.g. that segment's failure codes, its daily trend around the change, and whether the rest of the business moved).
Each segment row also carries overall_sr_change_pp (the net change of the whole population between the two periods)
and share_of_overall_change (the segment's impact_on_overall_sr_pp divided by that net change).
- A segment's own decline (sr_change, e.g. -27 pp inside the segment) is NOT its contribution to the overall decline
  (impact_on_overall_sr_pp, e.g. -0.5 pp of overall SR). Always state both, and label which is which.
- share_of_overall_change above 1 means the segment alone moved overall SR more than the net change, because other
  segments moved the other way; say that, rather than "explains 616% of the drop".
- Only call a segment the primary/main driver when a finding quantifies its contribution to the overall change AND
  shows it is larger than the other segments' contributions.

## Query rules
- Each query: ONE SELECT (WITH ... SELECT allowed), no semicolons, only the tables/columns below, no table functions.
- Start transaction metrics from the standard valid_txn CTE and use the metric definitions exactly.
- Return compact, aggregated results (at most ~50 rows): include the counts (terminal_attempts, success_count) next to
  every rate so results are checkable, round rates to 4 decimals, order rows meaningfully, use clear column names.
- Each query needs a short purpose. Queries are numbered Q1, Q2, ... in the order you plan them.
- If a query fails you will see the error; fix it in the next round.

## Report rules
- findings: facts that a successful query result directly shows. Cite the query ids in evidence_query_ids. Quote
  numbers exactly as they appear in the results (a ratio 0.8407 may be written 84.07%; a gap between two result
  values may be written in pp). Do not put numbers in a finding that are not in the cited results.
- hypotheses: explanations the data is consistent with but does not prove (causes outside the data, such as bank
  downtime, UX friction or customer behaviour, or patterns from too-small samples). Give the rationale, any
  supporting query ids, and how someone could verify it. Never present correlation as proven cause.
- caveats: data-quality issues, small samples, assumptions about time windows or definitions.
- suggested_follow_ups: 2-4 natural next questions.
- root causes: the data shows WHAT changed (which segment, which failure codes, when). WHY it changed (an app release,
  bank downtime, a gateway incident) is a hypothesis unless a query result shows it directly. Never write "root
  cause", "caused by", "because of" or "triggered by" as fact in findings or the answer; hedge it and list it as a
  hypothesis.
- answer: 2-5 sentences answering the question directly. If the question has several parts, answer each part in
  order, with its key number, the comparison it asks for and a one-line conclusion; the reader should not need to
  open a table or chart. It summarises the findings: every number in it must be
  copied exactly from a finding (the same window and the same query; never mix a figure from one query's window with
  another's). Distinguish a segment's own decline from its contribution to the overall change. If a cause is
  mentioned, mark it as a hypothesis.

## Each response
Return JSON with: notes (your brief reasoning for this round), queries (the queries to run now, may be empty),
driver_scans (0 or 1 scan request for this round), and
report (null while still investigating; the final report when done). When you return a report, queries are ignored.

{schema}"""


# ----------------------------------------------------------------------------- model I/O schemas
class PlannedQuery(BaseModel):
    purpose: str = Field(description="What this query checks, in a few words")
    sql: str = Field(description="One read-only DuckDB SELECT statement")


class ReportFinding(BaseModel):
    statement: str = Field(description="A fact shown directly by the cited query results")
    evidence_query_ids: list[str] = Field(description="Ids like Q3 of the queries that show it")


class ReportHypothesis(BaseModel):
    statement: str = Field(description="A possible explanation the data does not prove")
    rationale: str = Field(description="Why it is plausible")
    supporting_query_ids: list[str] = Field(description="Query ids consistent with it; may be empty")
    how_to_verify: str = Field(description="Data or check that would confirm or reject it")


class Report(BaseModel):
    answer: str
    findings: list[ReportFinding]
    hypotheses: list[ReportHypothesis]
    caveats: list[str]
    suggested_follow_ups: list[str]


class InvestigationStep(BaseModel):
    notes: str = Field(description="Brief reasoning for this round")
    queries: list[PlannedQuery] = Field(description="Queries to run now; empty when reporting")
    driver_scans: list[DriverScanRequest] = Field(default_factory=list,
                                                  description="0 or 1 driver scans to run this round")
    report: Report | None = Field(default=None, description="Null while investigating; the final report when done")


# ----------------------------------------------------------------------------- result objects
@dataclass
class EvidenceQuery:
    id: str
    round: int
    purpose: str
    sql: str
    status: str                      # "ok" | "rejected" | "error"
    error: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class Finding:
    statement: str
    evidence: list[str]
    numbers_verified: list[str] = field(default_factory=list)
    numbers_unverified: list[str] = field(default_factory=list)
    claim_notes: list[str] = field(default_factory=list)    # wording problems the numbers check cannot catch

    @property
    def check_status(self) -> str:
        if not self.numbers_verified and not self.numbers_unverified:
            return "no-numbers"
        return "verified" if not self.numbers_unverified else "partially-verified"


@dataclass
class Hypothesis:
    statement: str
    rationale: str
    supporting_evidence: list[str]
    how_to_verify: str
    demoted_from_finding: bool = False


@dataclass
class Investigation:
    question: str
    success: bool
    answer: str = ""
    findings: list[Finding] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    suggested_follow_ups: list[str] = field(default_factory=list)
    # answer checks: numbers must come from verified findings; driver / cause wording must be supported
    answer_numbers_verified: list[str] = field(default_factory=list)
    answer_numbers_unverified: list[str] = field(default_factory=list)
    answer_issues: list[str] = field(default_factory=list)
    answer_revised: bool = False
    original_answer: str | None = None
    queries: list[EvidenceQuery] = field(default_factory=list)
    round_notes: list[str] = field(default_factory=list)
    error: str | None = None
    model: str | None = None
    rounds: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0

    def query(self, qid: str) -> EvidenceQuery | None:
        return next((q for q in self.queries if q.id == qid), None)

    def to_dict(self) -> dict:
        d = asdict(self)
        for q in d["queries"]:
            q["rows"] = [list(r) for r in q["rows"]]
        return d

    def to_markdown(self, max_rows: int = 15) -> str:
        return render_markdown(self, max_rows=max_rows)


# ----------------------------------------------------------------------------- the agent
class Investigator:
    def __init__(self, schema: SchemaContext | None = None, llm: GeminiJSONClient | None = None,
                 executor: QueryExecutor | None = None, max_rounds: int = 5, max_queries_per_round: int = 4,
                 rows_shown_to_model: int = 30, round_delay_s: float = 0.0):
        self.schema = schema or load_schema_context()
        self.llm = llm or GeminiJSONClient()
        self.validator = SQLValidator(self.schema.allowed_tables())
        self.executor = executor or QueryExecutor(max_rows=200)
        self.max_rounds = max_rounds
        self.max_queries_per_round = max_queries_per_round
        self.rows_shown_to_model = rows_shown_to_model
        self.round_delay_s = round_delay_s          # optional pause between calls on tight free-tier limits
        self.system_prompt = SYSTEM_TEMPLATE.format(schema=self.schema.to_prompt())

    # -- public
    def investigate(self, question: str, history: str = "") -> Investigation:
        start = time.perf_counter()
        inv = Investigation(question=question, success=False)
        contents: list[dict] = [_turn("user", _opening_message(question, history, self.max_rounds))]

        report: Report | None = None
        for round_no in range(1, self.max_rounds + 1):
            if round_no > 1 and self.round_delay_s:
                time.sleep(self.round_delay_s)
            try:
                resp = self.llm.generate(self.system_prompt, contents, InvestigationStep)
            except GenerationError as e:
                inv.error = str(e)
                break
            step: InvestigationStep = resp.parsed
            inv.model, inv.rounds = resp.model, round_no
            inv.input_tokens += resp.input_tokens
            inv.output_tokens += resp.output_tokens
            if step.notes:
                inv.round_notes.append(step.notes)
            contents.append(_turn("model", resp.text))

            if step.report is not None:
                report = step.report
                break

            planned = list(step.queries[: self.max_queries_per_round])
            executed: list[EvidenceQuery] = []

            def run(pq: PlannedQuery) -> EvidenceQuery:
                eq = self._run(len(inv.queries) + 1, round_no, pq)
                inv.queries.append(eq)
                executed.append(eq)
                return eq

            scan_notes = self._run_driver_scans(step.driver_scans[:1], run)
            for pq in planned:
                run(pq)
            if not executed:
                msg = "\n".join(scan_notes + ["You planned no queries. Either plan queries or return the report now."])
                contents.append(_turn("user", msg))
                continue

            feedback = "\n\n".join(scan_notes + [self._describe(q) for q in executed])
            if len(step.queries) > self.max_queries_per_round:
                feedback += (f"\n\n(Only the first {self.max_queries_per_round} queries were run; "
                             "plan the rest next round if still needed.)")
            remaining = self.max_rounds - round_no
            if remaining == 1:
                feedback += "\n\nThis is your LAST round: return the final report now (report must not be null)."
            else:
                feedback += f"\n\n{remaining} round(s) left. Investigate further or return the report."
            contents.append(_turn("user", feedback))

        if report is None and inv.error is None:
            inv.error = f"No report after {self.max_rounds} rounds."
        if report is not None:
            self._apply_report(inv, report)
            self._check_answer(inv, contents)
            inv.success = True
        inv.elapsed_ms = int((time.perf_counter() - start) * 1000)
        return inv

    # -- internals
    def _run_driver_scans(self, requests: list[DriverScanRequest], run) -> list[str]:
        """Run driver scans; with no dates given, first detect where and when the success rate broke."""
        notes = []
        for req in requests:
            try:
                if req.auto:
                    cq = build_change_point_query(self.schema, req.method_code)
                    cp = run(PlannedQuery(purpose=cq.purpose, sql=cq.sql))
                    if not cp.ok:
                        notes.append("Change-date detection failed; request a driver_scan with explicit periods instead.")
                        continue
                    if not cp.rows:
                        notes.append(f"Change-date detection ({cp.id}) found NO statistically clear break in any segment "
                                     "(threshold z <= -5, calibrated on null data). Do not claim that a segment broke "
                                     "unless your own queries show it; say the data shows no clear deterioration.")
                        continue
                    breaks = [dict(zip(cp.columns, r)) for r in cp.rows]
                    # explain the strongest break that is still going on; fall back to a recovered incident
                    top = next((b for b in breaks if b["status"] == "ongoing"), breaks[0])
                    req = periods_from_change(top["change_date"], top["data_end"], req.method_code, top["status"])
                    others = "; ".join(f"{b['segment']} from {str(b['change_date'])[:10]} ({b['status']})"
                                       for b in breaks if b is not top)
                    notes.append(f"Detected change point (from {cp.id}): {top['segment']} ({top['dimension']}) broke on "
                                 f"{str(top['change_date'])[:10]}, status {top['status'].upper()}. Driver scan compares "
                                 f"{req.baseline_start}..{req.baseline_end} with {req.current_start}..{req.current_end}."
                                 + (f" Other significant breaks: {others}." if others else ""))
                for sq in build_driver_scan(self.schema, req):
                    run(PlannedQuery(purpose=sq.purpose, sql=sq.sql))
            except DriverScanError as e:
                notes.append(f"Driver scan request rejected: {e}")
        return notes

    def _run(self, n: int, round_no: int, planned: PlannedQuery) -> EvidenceQuery:
        q = EvidenceQuery(id=f"Q{n}", round=round_no, purpose=planned.purpose, sql=planned.sql.strip(), status="ok")
        check = self.validator.validate(planned.sql)
        if not check.ok:
            q.status, q.error = "rejected", f"Rejected by the safety validator: {check.error_message}"
            return q
        q.sql = check.sql
        try:
            r = self.executor.run(check.sql)
        except ExecutionError as e:
            q.status, q.error = "error", f"DuckDB error: {e}"
            return q
        q.columns, q.rows, q.row_count, q.truncated, q.elapsed_ms = r.columns, r.rows, r.row_count, r.truncated, r.elapsed_ms
        return q

    def _describe(self, q: EvidenceQuery) -> str:
        head = f"[{q.id}] {q.purpose}"
        if not q.ok:
            return f"{head}\nFAILED: {q.error}\nSQL was:\n{q.sql}"
        shown = q.rows[: self.rows_shown_to_model]
        more = ""
        if q.row_count > len(shown) or q.truncated:
            more = f"\n(showing {len(shown)} of {q.row_count}{'+' if q.truncated else ''} rows; aggregate further if you need the rest)"
        return f"{head}\n{_table_text(q.columns, shown)}{more}"

    def _apply_report(self, inv: Investigation, report: Report) -> None:
        ok_ids = {q.id for q in inv.queries if q.ok}
        inv.answer = report.answer.strip()
        inv.caveats = [c for c in report.caveats if c.strip()]
        inv.suggested_follow_ups = [s for s in report.suggested_follow_ups if s.strip()]

        for f in report.findings:
            cited = [_norm_id(i) for i in f.evidence_query_ids]
            valid = [i for i in cited if i in ok_ids]
            if not valid:
                inv.hypotheses.append(Hypothesis(
                    statement=f.statement,
                    rationale="Stated as a finding, but it cites no successful query, so it is treated as unproven.",
                    supporting_evidence=[], how_to_verify="Run a query that shows this directly.",
                    demoted_from_finding=True))
                continue
            results = [(inv.query(i).columns, inv.query(i).rows) for i in valid]
            nc = check_numbers(f.statement, results, inv.question)
            causal = unhedged_causal_sentences(
                f.statement, lambda clause: not check_numbers(clause, results).unverified)
            if causal and not nc.verified:
                # a cause stated as fact with nothing from the cited results behind it is a hypothesis
                inv.hypotheses.append(Hypothesis(
                    statement=f.statement,
                    rationale="Stated as a finding, but it asserts a cause the cited results do not show.",
                    supporting_evidence=valid, how_to_verify="Find data that shows the cause directly.",
                    demoted_from_finding=True))
                continue
            notes = [f"States a cause as fact (\"{c[:120]}\"); the cited results show what changed, not why."
                     for c in causal]
            inv.findings.append(Finding(f.statement, valid, nc.verified, nc.unverified, notes))

        for h in report.hypotheses:
            support = [i for i in (_norm_id(x) for x in h.supporting_query_ids) if i in ok_ids]
            inv.hypotheses.append(Hypothesis(h.statement, h.rationale, support, h.how_to_verify))


    def _answer_problems(self, inv: Investigation, answer: str):
        verified = [f.statement for f in inv.findings if f.check_status == "verified" and not f.claim_notes]
        return (check_answer_numbers(answer, verified, inv.question),
                answer_claim_issues(answer, verified, inv.question))

    def _check_answer(self, inv: Investigation, contents: list[dict]) -> None:
        """The answer is a summary: its numbers must come from verified findings and its wording must not claim more
        than they show. On a problem the model gets one chance to rewrite the answer only (findings stay as checked);
        whatever is still wrong afterwards is reported next to the answer."""
        nums, issues = self._answer_problems(inv, inv.answer)
        if nums.unverified or issues:
            problems = ([f"- These numbers are not stated in any verified finding: {', '.join(nums.unverified)}. "
                         "Use the exact figure from the finding (same query, same window) or drop it."]
                        if nums.unverified else []) + [f"- {i}" for i in issues]
            findings = "\n".join(f"- {f.statement}" for f in inv.findings
                                  if f.check_status == "verified" and not f.claim_notes) or "- (none)"
            contents.append(_turn("user", (
                "Answer check failed:\n" + "\n".join(problems)
                + f"\n\nVerified findings:\n{findings}\n\nReturn the report again with ONLY the answer rewritten "
                "(2-4 sentences, numbers copied exactly from the verified findings, a segment's own decline kept "
                "separate from its contribution to the overall change, causes marked as hypotheses). Findings, "
                "hypotheses and caveats you return are ignored.")))
            try:
                resp = self.llm.generate(self.system_prompt, contents, InvestigationStep)
                inv.input_tokens += resp.input_tokens
                inv.output_tokens += resp.output_tokens
                revised = resp.parsed.report.answer.strip() if resp.parsed.report else ""
            except GenerationError:
                revised = ""
            if revised and revised != inv.answer:
                r_nums, r_issues = self._answer_problems(inv, revised)
                if len(r_nums.unverified) + len(r_issues) < len(nums.unverified) + len(issues):
                    inv.original_answer, inv.answer, inv.answer_revised = inv.answer, revised, True
                    nums, issues = r_nums, r_issues
        inv.answer_numbers_verified, inv.answer_numbers_unverified = nums.verified, nums.unverified
        inv.answer_issues = ([f"Numbers in the answer not stated in a verified finding: {', '.join(nums.unverified)}."]
                             if nums.unverified else []) + issues


class InvestigationSession:
    """Keeps the conversation so follow-up questions can build on earlier answers."""

    def __init__(self, investigator: Investigator | None = None, max_history: int = 3):
        self.investigator = investigator or Investigator()
        self.max_history = max_history
        self.turns: list[Investigation] = []

    def ask(self, question: str) -> Investigation:
        inv = self.investigator.investigate(question, history=self.history_text())
        self.turns.append(inv)
        return inv

    def history_text(self) -> str:
        parts = []
        for i, t in enumerate(self.turns[-self.max_history:], 1):
            lines = [f"Earlier question {i}: {t.question}"]
            if not t.success:
                lines.append(f"(could not be answered: {t.error})")
            else:
                lines.append(f"Answer: {t.answer}")
                lines += [f"- Finding: {f.statement}" for f in t.findings]
                lines += [f"- Hypothesis (unproven): {h.statement}" for h in t.hypotheses]
                key = {e for f in t.findings for e in f.evidence}
                for q in t.queries:
                    if q.id in key:
                        lines.append(f"- SQL used for '{q.purpose}':\n{q.sql}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)


# ----------------------------------------------------------------------------- helpers
def _turn(role: str, text: str) -> dict:
    return {"role": role, "parts": [{"text": text}]}


def _opening_message(question: str, history: str, max_rounds: int) -> str:
    msg = ""
    if history:
        msg += ("Conversation so far (the new question may be a follow-up; resolve words like 'it', 'that', "
                "'why' against it, and reuse its filters and definitions):\n\n" + history + "\n\n---\n\n")
    msg += (f"Question: {question}\n\nYou have at most {max_rounds} rounds. Plan the first queries now "
            "(report should be null in this response unless the question cannot be answered from this data, in which "
            "case return a report that says so and has no findings).")
    return msg


def _norm_id(qid: str) -> str:
    s = qid.strip().strip("[]").upper()
    return s if s.startswith("Q") else f"Q{s}"


def _fmt(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.6g}" if abs(v) < 1e6 else f"{v:,.2f}"
    return str(v)


def _table_text(columns: list[str], rows: list[tuple]) -> str:
    if not rows:
        return "(no rows)\n| " + " | ".join(columns) + " |"
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_fmt(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


_CHECK_LABEL = {"verified": "numbers verified against evidence",
                "partially-verified": "some numbers not found in evidence",
                "no-numbers": "no numbers to check"}


def render_markdown(inv: Investigation, max_rows: int = 15) -> str:
    out = [f"# {inv.question}", ""]
    if not inv.success:
        out += [f"**Investigation failed:** {inv.error}", ""]
    if inv.answer:
        out += ["## Answer", inv.answer, ""]
        if inv.answer_issues:
            out += ["_⚠ Answer check:_"] + [f"- _{i}_" for i in inv.answer_issues] + [""]
        elif inv.answer_numbers_verified:
            out += ["_✓ Every number in the answer is stated in a verified finding._", ""]
        if inv.answer_revised:
            out += ["_The first draft of this answer failed the answer check and was rewritten._", ""]

    if inv.findings:
        out += ["## Findings (shown by the data)"]
        for i, f in enumerate(inv.findings, 1):
            mark = "✓" if f.check_status in ("verified", "no-numbers") else "⚠"
            line = f"{i}. {f.statement}  \n   _Evidence: {', '.join(f.evidence)} · {mark} {_CHECK_LABEL[f.check_status]}"
            if f.numbers_unverified:
                line += f" ({', '.join(f.numbers_unverified)})"
            line += "_"
            for n in f.claim_notes:
                line += f"  \n   _⚠ {n}_"
            out.append(line)
        out.append("")

    if inv.hypotheses:
        out += ["## Hypotheses (possible drivers, not proven)"]
        for i, h in enumerate(inv.hypotheses, 1):
            tag = " _(stated as a finding but had no supporting query)_" if h.demoted_from_finding else ""
            out.append(f"{i}. **{h.statement}**{tag}  \n   Why plausible: {h.rationale}  \n"
                       f"   Consistent with: {', '.join(h.supporting_evidence) or 'no direct query'} · "
                       f"To verify: {h.how_to_verify}")
        out.append("")

    if inv.caveats:
        out += ["## Caveats"] + [f"- {c}" for c in inv.caveats] + [""]
    if inv.suggested_follow_ups:
        out += ["## Suggested follow-ups"] + [f"- {s}" for s in inv.suggested_follow_ups] + [""]

    if inv.queries:
        out += ["## Evidence: queries run"]
        for q in inv.queries:
            out += [f"### {q.id} · {q.purpose}", "```sql", q.sql, "```"]
            if q.ok:
                shown = q.rows[:max_rows]
                out.append(_table_text(q.columns, shown))
                if q.row_count > len(shown) or q.truncated:
                    out.append(f"_{len(shown)} of {q.row_count}{'+' if q.truncated else ''} rows shown_")
            else:
                out.append(f"**{q.status.upper()}:** {q.error}")
            out.append("")

    ok = sum(q.ok for q in inv.queries)
    out.append(f"---\n_{inv.rounds} round(s) · {ok}/{len(inv.queries)} queries succeeded · {inv.elapsed_ms / 1000:.1f}s · "
               f"{inv.model} · tokens in/out {inv.input_tokens}/{inv.output_tokens}_")
    return "\n".join(out)


# ----------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Investigate a payments question with multiple safe SQL queries.")
    ap.add_argument("question", nargs="*", help="first question; omit for interactive mode")
    ap.add_argument("--follow-up", action="append", default=[], help="follow-up question (repeatable)")
    ap.add_argument("--model", default=config.GEMINI_MODEL)
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--out", help="write the markdown report(s) to this file")
    ap.add_argument("--json", help="write the full investigation(s) as JSON to this file")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    session = InvestigationSession(Investigator(llm=GeminiJSONClient(model=args.model), max_rounds=args.max_rounds))

    questions = ([" ".join(args.question)] if args.question else []) + args.follow_up
    interactive = not questions
    reports = []
    while True:
        if interactive:
            try:
                q = input("\nAsk (blank to quit)> ").strip()
            except EOFError:
                break
            if not q:
                break
        elif questions:
            q = questions.pop(0)
        else:
            break
        print(f"\nInvestigating: {q} ...", flush=True)
        inv = session.ask(q)
        md = inv.to_markdown()
        reports.append(md)
        print("\n" + md)

    if args.out:
        Path(args.out).write_text("\n\n".join(reports), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps([t.to_dict() for t in session.turns], indent=1, default=str),
                                   encoding="utf-8")
    return 0 if session.turns and all(t.success for t in session.turns) else 1


if __name__ == "__main__":
    sys.exit(main())
