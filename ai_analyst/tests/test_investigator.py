"""Offline tests for the multi-step investigator and the evidence checker (no API key needed).

    python -m ai_analyst.tests.test_investigator
"""
from __future__ import annotations

from ai_analyst.evidence import check_numbers, extract_numbers
from ai_analyst.investigator import (InvestigationSession, InvestigationStep, Investigator, PlannedQuery, Report,
                                     ReportFinding, ReportHypothesis)
from ai_analyst.llm import GenerationError, LLMResponse
from ai_analyst.schema_context import load_schema_context

SCHEMA = load_schema_context()
CTE = "WITH " + SCHEMA.VALID_TRANSACTIONS_CTE
SR = ("ROUND(count_if(status='SUCCESS')::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')),0),4)")
T = "count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED'))"
SR_BY_METHOD = f"{CTE} SELECT method_code, {T} AS terminal_attempts, {SR} AS success_rate FROM valid_txn GROUP BY 1 ORDER BY 3"
NB_BY_BANK = (f"{CTE} SELECT b.short_name, {T} AS terminal_attempts, {SR} AS success_rate FROM valid_txn v "
              "LEFT JOIN banks b USING (bank_id) WHERE method_code = 'NET_BANKING' GROUP BY 1 ORDER BY 3")


class ScriptedLLM:
    """Plays back InvestigationStep objects and records the conversation it was sent."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls: list[list[dict]] = []

    def generate(self, system, contents, schema):
        assert schema is InvestigationStep and "valid_txn" in system
        self.calls.append([dict(c) for c in contents])
        step = self.steps[min(len(self.calls) - 1, len(self.steps) - 1)]
        if isinstance(step, Exception):
            raise step
        return LLMResponse(parsed=step, text=step.model_dump_json(), model="fake", input_tokens=10, output_tokens=5)


def _step(*queries, report=None, notes="n"):
    return InvestigationStep(notes=notes, queries=[PlannedQuery(purpose=p, sql=s) for p, s in queries], report=report)


def _user_text(call, idx=-1):
    return call[idx]["parts"][0]["text"]


# ----------------------------------------------------------------------- evidence checker
def test_extract_numbers_skips_dates_ids_and_small_counts():
    nums = [q.text for q in extract_numbers(
        "On 2026-10-12 merchant M0137 (Q3) fell 27.1pp to 61.5%, across 13,086 attempts in 2 weeks, ₹1.2 Cr TPV")]
    assert nums == ["27.1pp", "61.5%", "13,086", "1.2 Cr"], nums
    assert [q.text for q in extract_numbers("95th percentile latency is 18,026 ms")] == ["18,026"]


def test_check_numbers_matches_cells_percentages_and_gaps():
    results = [(["method", "n", "sr"], [("NB", 13086, 0.8407), ("UPI", 95760, 0.8756)])]
    ok = check_numbers("NB SR is 84.07% (84.1%) on 13,086 attempts, 3.49pp below UPI's 0.8756", results)
    assert ok.status == "verified" and not ok.unverified, ok
    bad = check_numbers("NB SR is 79.5% on 20,000 attempts", results)
    assert bad.unverified == ["79.5%", "20,000"], bad


# ----------------------------------------------------------------------- investigator
def _report(findings=(), hypotheses=()):
    return Report(answer="Net Banking is lowest.", findings=list(findings), hypotheses=list(hypotheses),
                  caveats=["c"], suggested_follow_ups=["f"])


def test_multi_round_investigation_runs_queries_and_verifies_findings():
    llm = ScriptedLLM(
        _step(("SR by method", SR_BY_METHOD)),
        _step(("NB SR by bank", NB_BY_BANK)),
        _step(report=_report(
            findings=[ReportFinding(statement="NET_BANKING has the lowest SR at 84.07% over 13,086 attempts.",
                                    evidence_query_ids=["Q1"])],
            hypotheses=[ReportHypothesis(statement="Bank login friction", rationale="r",
                                         supporting_query_ids=["Q2", "Q99"], how_to_verify="v")])),
    )
    inv = Investigator(schema=SCHEMA, llm=llm).investigate("Which method is worst and why?")
    assert inv.success and inv.rounds == 3 and [q.id for q in inv.queries] == ["Q1", "Q2"]
    assert all(q.ok for q in inv.queries)
    f = inv.findings[0]
    assert f.evidence == ["Q1"] and f.check_status == "verified", f
    assert inv.hypotheses[0].supporting_evidence == ["Q2"]                 # unknown Q99 dropped
    assert "[Q1] SR by method" in _user_text(llm.calls[1]) and "NET_BANKING" in _user_text(llm.calls[1])
    md = inv.to_markdown()
    assert "## Findings (shown by the data)" in md and "## Hypotheses (possible drivers, not proven)" in md
    assert "```sql" in md and "Q2 · NB SR by bank" in md


def test_unsafe_and_broken_queries_are_blocked_and_reported_back():
    llm = ScriptedLLM(
        _step(("drop", "DROP TABLE transactions"), ("file", "SELECT * FROM read_csv('x.csv')"),
              ("typo", "SELECT no_such_col FROM banks")),
        _step(report=_report()),
    )
    inv = Investigator(schema=SCHEMA, llm=llm).investigate("q")
    assert [q.status for q in inv.queries] == ["rejected", "rejected", "error"]
    fed_back = _user_text(llm.calls[1])
    assert fed_back.count("FAILED:") == 3 and "safety validator" in fed_back and "DuckDB error" in fed_back


def test_finding_without_valid_evidence_is_demoted_and_unverified_numbers_flagged():
    llm = ScriptedLLM(
        _step(("SR by method", SR_BY_METHOD), ("bad", "DROP TABLE banks")),
        _step(report=_report(findings=[
            ReportFinding(statement="Cites only a failed query", evidence_query_ids=["Q2"]),
            ReportFinding(statement="Cites nothing", evidence_query_ids=[]),
            ReportFinding(statement="NB SR is 70.12% on 13,086 attempts", evidence_query_ids=["q1"]),
        ])),
    )
    inv = Investigator(schema=SCHEMA, llm=llm).investigate("q")
    assert len(inv.findings) == 1 and inv.findings[0].evidence == ["Q1"]
    assert inv.findings[0].numbers_unverified == ["70.12%"] and inv.findings[0].numbers_verified == ["13,086"]
    assert [h.demoted_from_finding for h in inv.hypotheses] == [True, True]
    assert "⚠ some numbers not found in evidence (70.12%)" in inv.to_markdown()


def test_round_budget_caps_queries_and_forces_report():
    many = [(f"q{i}", "SELECT count(*) AS n FROM banks") for i in range(6)]
    llm = ScriptedLLM(_step(*many), _step(*many))
    inv = Investigator(schema=SCHEMA, llm=llm, max_rounds=2, max_queries_per_round=4).investigate("q")
    assert not inv.success and "No report after 2 rounds" in inv.error
    assert len(inv.queries) == 8                                               # 4 per round
    assert "LAST round" in _user_text(llm.calls[1]) and "Only the first 4" in _user_text(llm.calls[1])


def test_generation_error_is_reported_with_partial_evidence():
    llm = ScriptedLLM(_step(("SR by method", SR_BY_METHOD)), GenerationError("quota"))
    inv = Investigator(schema=SCHEMA, llm=llm).investigate("q")
    assert not inv.success and inv.error == "quota" and len(inv.queries) == 1
    assert "Investigation failed" in inv.to_markdown()


def test_session_passes_history_to_follow_ups():
    llm = ScriptedLLM(
        _step(("SR by method", SR_BY_METHOD)),
        _step(report=_report(findings=[ReportFinding(statement="NET_BANKING is lowest at 84.07%",
                                                     evidence_query_ids=["Q1"])])),
        _step(report=_report()),
    )
    session = InvestigationSession(Investigator(schema=SCHEMA, llm=llm))
    session.ask("Which method has the lowest SR?")
    session.ask("Why is it so low?")
    opening = _user_text(llm.calls[2], 0)
    assert "Earlier question 1: Which method has the lowest SR?" in opening
    assert "Finding: NET_BANKING is lowest at 84.07%" in opening and "SQL used for 'SR by method'" in opening
    assert opening.rstrip().endswith("has no findings).") and "Question: Why is it so low?" in opening


# ----------------------------------------------------------------------- driver scan
def _scan_req(**kw):
    from ai_analyst.driver_scan import DriverScanRequest
    d = dict(baseline_start="2026-09-28", baseline_end="2026-10-11", current_start="2026-10-12", current_end="2026-10-25")
    d.update(kw)
    return DriverScanRequest(**d)


def test_driver_scan_finds_planted_merchant_android_gateway_issue():
    """Ground truth: from 2026-10-12, merchant M0137's ANDROID traffic fails with gateway errors."""
    from ai_analyst.driver_scan import build_driver_scan
    from ai_analyst.executor import QueryExecutor
    from ai_analyst.sql_validator import SQLValidator
    validator, ex = SQLValidator(SCHEMA.allowed_tables()), QueryExecutor(max_rows=200)
    seg_q, fail_q = build_driver_scan(SCHEMA, _scan_req())
    seg = ex.run(validator.validate(seg_q.sql).sql).to_dataframe()
    assert seg.iloc[0]["segment"].startswith("M0137")                      # top driver overall
    mp = seg[seg["dimension"] == "merchant x platform"].iloc[0]
    assert mp["segment"] == "M0137 / ANDROID" and mp["sr_change"] < -0.25
    fail = ex.run(validator.validate(fail_q.sql).sql).to_dataframe()
    assert fail.iloc[0]["failure_code"] == "GATEWAY_ERROR"
    assert fail.iloc[0]["top_merchant_platform_cur"] == "M0137 / ANDROID"


def test_driver_scan_rejects_bad_parameters():
    from ai_analyst.driver_scan import DriverScanError, build_driver_scan
    for bad in (dict(current_start="2026-10-12'; DROP TABLE x; --"), dict(baseline_start="2026-13-01"),
                dict(current_start="2026-10-30", current_end="2026-10-12"), dict(method_code="CASH")):
        try:
            build_driver_scan(SCHEMA, _scan_req(**bad))
        except DriverScanError:
            continue
        raise AssertionError(f"should reject {bad}")


def test_investigator_runs_requested_driver_scan_as_evidence():
    step = _step(("NB by bank", NB_BY_BANK))
    step.driver_scans = [_scan_req()]
    llm = ScriptedLLM(step, _step(report=_report(findings=[ReportFinding(
        statement="M0137 / ANDROID fell from 89.07% to 62.05%", evidence_query_ids=["Q1"])])))
    inv = Investigator(schema=SCHEMA, llm=llm).investigate("Why did failures rise since mid-October?")
    assert [q.id for q in inv.queries] == ["Q1", "Q2", "Q3"] and all(q.ok for q in inv.queries)
    assert inv.queries[0].purpose.startswith("[driver scan]") and inv.queries[2].purpose == "NB by bank"
    assert inv.findings[0].check_status == "verified"
    assert "M0137 / ANDROID" in _user_text(llm.calls[1])


def test_change_point_detection_finds_planted_break_date():
    """Without being told when, the scan must find M0137 / ANDROID breaking on 2026-10-12 (the planted start)."""
    from ai_analyst.driver_scan import build_change_point_query, periods_from_change
    from ai_analyst.executor import QueryExecutor
    from ai_analyst.sql_validator import SQLValidator
    q = build_change_point_query(SCHEMA)
    check = SQLValidator(SCHEMA.allowed_tables()).validate(q.sql)
    assert check.ok, check.errors
    df = QueryExecutor(max_rows=200).run(check.sql).to_dataframe()
    top = df.iloc[0]
    assert top["segment"] == "M0137 / ANDROID" and str(top["change_date"])[:10] == "2026-10-12", top
    assert top["sr_change"] < -0.25 and top["z_score"] < -10 and top["status"] == "ongoing"
    # only the planted incidents pass the calibrated significance threshold
    assert set(df["segment"]) <= {"M0137 / ANDROID", "M0137 Zestora Online Mart", "SBI / UPI", "SBI"}, set(df["segment"])
    sbi = df[df["segment"] == "SBI / UPI"].iloc[0]                  # the earlier planted outage, now recovered
    assert "2026-09-1" in str(sbi["change_date"]) and sbi["status"] == "recovered"
    assert abs(sbi["sr_first_7d"] - 0.6261) < 0.01 and sbi["sr_last_7d"] > 0.85
    req = periods_from_change(top["change_date"], top["data_end"], "upi")
    assert (req.baseline_start, req.baseline_end, req.current_start, req.current_end, req.method_code) == \
        ("2026-09-28", "2026-10-11", "2026-10-12", "2026-10-25", "upi")
    rec = periods_from_change(sbi["change_date"], sbi["data_end"], status="recovered")   # incident week only
    assert (rec.baseline_start, rec.current_start, rec.current_end) == ("2026-09-06", "2026-09-13", "2026-09-19")


def test_investigator_auto_detects_change_date_when_no_periods_given():
    from ai_analyst.driver_scan import DriverScanRequest
    step = _step()
    step.driver_scans = [DriverScanRequest()]                       # all dates null -> auto mode
    llm = ScriptedLLM(step, _step(report=_report()))
    inv = Investigator(schema=SCHEMA, llm=llm).investigate("Failures went up recently. Why?")
    assert [q.id for q in inv.queries] == ["Q1", "Q2", "Q3"] and all(q.ok for q in inv.queries)
    assert "change-date detection" in inv.queries[0].purpose
    assert "2026-09-28..2026-10-11 vs 2026-10-12..2026-10-25" in inv.queries[1].purpose
    fed_back = _user_text(llm.calls[1])
    assert "Detected change point (from Q1): M0137 / ANDROID (merchant x platform) broke on 2026-10-12" in fed_back


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
