"""Offline tests for the answer checks: range parsing in the number checker, summary numbers traced to verified
findings, driver / cause wording, and the driver scan's contribution columns (no API key needed).

    python -m pytest ai_analyst/tests/test_answer_checks.py -q
"""
from __future__ import annotations

from ai_analyst.driver_scan import DriverScanRequest, build_driver_scan
from ai_analyst.evidence import (answer_claim_issues, check_answer_numbers, check_numbers, extract_numbers,
                                 has_contribution_share, unhedged_causal_sentences)
from ai_analyst.executor import QueryExecutor
from ai_analyst.investigator import InvestigationStep, Investigator, Report, ReportFinding, ReportHypothesis
from ai_analyst.llm import GenerationError
from ai_analyst.sql_validator import SQLValidator
from ai_analyst.tests.test_investigator import SCHEMA, ScriptedLLM, _step, _user_text

# one query that measures the same segment over two windows, as the change-point detection (full pre-break
# period) and the period comparison (equal-length baseline) do
TWO_WINDOWS = ("SELECT 0.8869 AS sr_before_break, 0.8907 AS sr_baseline_14d, 0.6205 AS sr_after, -0.2663 AS sr_change, "
               "-0.535 AS impact_on_overall_sr_pp, -0.087 AS overall_sr_change_pp FROM banks LIMIT 1")
SEGMENT_FINDING = "Segment X / ANDROID success rate fell from 88.69% to 62.05% (-26.63 pp) after the break."


# ----------------------------------------------------------------------- ranges in the number checker
def _vals(text):
    return [(q.text, q.value) for q in extract_numbers(text)]


def test_range_unit_applies_to_both_ends_and_dash_is_not_a_minus():
    assert _vals("daily SR stayed in 90.4–90.7%") == [("90.4%", 90.4), ("90.7%", 90.7)]
    assert _vals("daily SR stayed in 90.4%-90.7%") == [("90.4%", 90.4), ("90.7%", 90.7)]
    assert _vals("gap of 1.2—1.5 pp") == [("1.2 pp", 1.2), ("1.5 pp", 1.5)]
    # a real negative number and a from/to pair are unchanged
    assert _vals("fell from 88.69% to 62.05% (-26.63 pp)") == [("88.69%", 88.69), ("62.05%", 62.05),
                                                                ("-26.63 pp", -26.63)]


def test_ranges_verify_against_ratio_cells_without_loosening_the_check():
    results = [(["day", "sr"], [("a", 0.9044), ("b", 0.9071), ("c", 0.9040)])]
    for text in ("SR stayed in 90.4%–90.7%", "SR stayed in 90.4–90.7%", "SR stayed in 90.4%-90.7%",
                 "SR between 90.40% and 90.71%"):
        assert check_numbers(text, results).unverified == [], text
    # a range end that is not in the results is still flagged, and rounding is not widened
    assert check_numbers("SR stayed in 90.4–95.0%", results).unverified == ["95.0%"]
    assert check_numbers("SR stayed in 90.4–90.8%", results).unverified == ["90.8%"]


# ----------------------------------------------------------------------- answer numbers vs findings
def test_answer_numbers_must_come_from_verified_findings():
    # 89.07% is a real cell (the 14-day baseline) but the finding quotes 88.69%: a summary that mixes the two
    # windows contradicts the finding, so it is flagged even though the number exists somewhere in the evidence
    bad = check_answer_numbers("Segment X / ANDROID SR fell from 89.07% to 62.05%.", [SEGMENT_FINDING])
    assert bad.unverified == ["89.07%"] and bad.verified == ["62.05%"]
    # restating a finding's figure in another form, or the gap between two of its figures, is fine
    ok = check_answer_numbers("SR went from 0.8869 to 62.05%, a 26.63 pp fall.", [SEGMENT_FINDING])
    assert ok.unverified == [] and len(ok.verified) == 3
    # numbers from an unverified finding do not count
    assert check_answer_numbers("SR fell to 62.05%.", []).unverified == ["62.05%"]


def test_investigator_rewrites_answer_whose_numbers_mix_windows():
    finding = ReportFinding(statement=SEGMENT_FINDING, evidence_query_ids=["Q1"])
    first = Report(answer="Segment X / ANDROID success rate fell from 89.07% to 62.05%.", findings=[finding],
                   hypotheses=[], caveats=[], suggested_follow_ups=[])
    fixed = first.model_copy(update={"answer": "Segment X / ANDROID success rate fell from 88.69% to 62.05% "
                                               "(-26.63 pp) after the break."})
    llm = ScriptedLLM(_step(("two windows", TWO_WINDOWS)), _step(report=first), _step(report=fixed))
    inv = Investigator(schema=SCHEMA, llm=llm).investigate("What changed?")
    assert inv.success and inv.findings[0].check_status == "verified"
    assert inv.answer_revised and inv.original_answer == first.answer and "88.69%" in inv.answer
    assert inv.answer_issues == [] and inv.answer_numbers_unverified == []
    sent = _user_text(llm.calls[2])
    assert "Answer check failed" in sent and "89.07%" in sent and SEGMENT_FINDING in sent
    assert inv.rounds == 2                                    # the rewrite is not an investigation round
    md = inv.to_markdown()
    assert "rewritten" in md and "Every number in the answer is stated in a verified finding" in md


def test_answer_issue_is_reported_when_rewrite_fails_or_errors():
    finding = ReportFinding(statement=SEGMENT_FINDING, evidence_query_ids=["Q1"])
    first = Report(answer="SR fell from 89.07% to 62.05%.", findings=[finding], hypotheses=[], caveats=[],
                   suggested_follow_ups=[])
    for third in (_step(report=first), GenerationError("quota")):      # same answer again / API error
        llm = ScriptedLLM(_step(("two windows", TWO_WINDOWS)), _step(report=first), third)
        inv = Investigator(schema=SCHEMA, llm=llm).investigate("What changed?")
        assert inv.success and inv.answer == first.answer and not inv.answer_revised
        assert inv.answer_numbers_unverified == ["89.07%"]
        assert any("89.07%" in i for i in inv.answer_issues)
        assert "⚠ Answer check" in inv.to_markdown()


def test_clean_answer_needs_no_rewrite():
    finding = ReportFinding(statement=SEGMENT_FINDING, evidence_query_ids=["Q1"])
    report = Report(answer="Segment X / ANDROID SR fell from 88.69% to 62.05%.", findings=[finding],
                    hypotheses=[], caveats=[], suggested_follow_ups=[])
    llm = ScriptedLLM(_step(("two windows", TWO_WINDOWS)), _step(report=report))
    inv = Investigator(schema=SCHEMA, llm=llm).investigate("What changed?")
    assert len(llm.calls) == 2 and not inv.answer_revised and inv.answer_issues == []
    assert inv.answer_numbers_verified == ["88.69%", "62.05%"]


# ----------------------------------------------------------------------- driver claims vs contribution
def test_driver_claim_needs_a_quantified_contribution_to_the_overall_change():
    decline_only = [SEGMENT_FINDING]
    issues = answer_claim_issues("The decline is primarily driven by segment X / ANDROID.", decline_only)
    assert len(issues) == 1 and "share of the overall change" in issues[0]
    assert answer_claim_issues("The fall is heavily concentrated in segment X.", decline_only)
    contribution = decline_only + ["Segment X / ANDROID lowered overall SR by 0.535 pp (impact on overall SR), "
                                   "more than any other segment; overall SR changed by -0.087 pp."]
    assert has_contribution_share(contribution[1]) and not has_contribution_share(SEGMENT_FINDING)
    assert answer_claim_issues("The decline is primarily driven by segment X / ANDROID.", contribution) == []
    # describing the segment's own decline without driver wording needs nothing extra
    assert answer_claim_issues("Segment X / ANDROID SR fell by 26.63 pp.", decline_only) == []
    # a hedged explanation is a hypothesis, not a driver claim (seen in a live run)
    assert answer_claim_issues("We hypothesize this pattern was caused by a client-side app release.",
                               decline_only) == []
    assert answer_claim_issues("It is likely driven by segment X.", decline_only) == []
    # a contribution without any comparison does not show the segment is the main driver
    lone = "Segment X / ANDROID SR dropped from 88.69% to 62.05%, impacting overall SR by -0.527 pp."
    assert answer_claim_issues("The decline is primarily driven by segment X / ANDROID.", [lone])
    ranked = "Segment X had the largest impact on overall success rate change (-0.556 pp)."
    assert answer_claim_issues("The decline is primarily driven by segment X.", [ranked]) == []
    # the comparison must be about the segment the answer names
    other = "UPI had the largest negative impact on overall success rate (-0.476 pp)."
    assert answer_claim_issues("The decline is primarily driven by merchant M0137.", [other])
    assert answer_claim_issues("The decline is primarily driven by UPI.", [other]) == []
    # contribution wording as the live findings phrase it
    assert has_contribution_share(lone)
    assert has_contribution_share("contributing -0.527 pp to the overall success rate drop")
    assert not has_contribution_share("M0137 / ANDROID contributing 142 failures in the current period")


def test_driver_claim_triggers_rewrite_in_investigator():
    finding = ReportFinding(statement=SEGMENT_FINDING, evidence_query_ids=["Q1"])
    first = Report(answer="The overall decline is primarily driven by segment X / ANDROID.", findings=[finding],
                   hypotheses=[], caveats=[], suggested_follow_ups=[])
    fixed = first.model_copy(update={"answer": "Segment X / ANDROID SR fell from 88.69% to 62.05% within the "
                                               "segment; the findings do not quantify its share of the overall change."})
    llm = ScriptedLLM(_step(("two windows", TWO_WINDOWS)), _step(report=first), _step(report=fixed))
    inv = Investigator(schema=SCHEMA, llm=llm).investigate("What is driving the drop?")
    assert inv.answer_revised and inv.answer_issues == [] and "driven" not in inv.answer
    assert "share of the overall change" in _user_text(llm.calls[2])


def test_driver_scan_reports_contribution_and_share_of_overall_change():
    req = DriverScanRequest(baseline_start="2026-09-28", baseline_end="2026-10-11",
                            current_start="2026-10-12", current_end="2026-10-25")
    seg_sql = build_driver_scan(SCHEMA, req)[0].sql
    check = SQLValidator(SCHEMA.allowed_tables()).validate(seg_sql)
    assert check.ok, check.error_message
    r = QueryExecutor(max_rows=200).run(check.sql)
    rows = [dict(zip(r.columns, row)) for row in r.rows]
    assert {"impact_on_overall_sr_pp", "overall_sr_change_pp", "share_of_overall_change"} <= set(r.columns)
    assert len({row["overall_sr_change_pp"] for row in rows}) == 1          # one net change for the population
    for row in rows:
        if row["share_of_overall_change"] is not None:
            expected = row["impact_on_overall_sr_pp"] / row["overall_sr_change_pp"]
            assert abs(row["share_of_overall_change"] - expected) < 0.05 * max(1, abs(expected))
        # a segment's own decline is far larger than its contribution to the overall rate
        if row["sr_change"] < -0.05:
            assert abs(row["impact_on_overall_sr_pp"]) < abs(row["sr_change"] * 100)


# ----------------------------------------------------------------------- findings vs hypotheses vs causes
def test_causal_wording_detection_respects_hedges():
    assert unhedged_causal_sentences("The drop was caused by an Android app release.")
    assert unhedged_causal_sentences("The root cause is a gateway outage.")
    assert unhedged_causal_sentences("It was caused primarily by an SDK bug.")
    assert not unhedged_causal_sentences("The drop may have been caused by an Android app release.")
    assert not unhedged_causal_sentences("A likely root cause, not confirmed by the data, is a release.")
    assert not unhedged_causal_sentences("GATEWAY_ERROR failures rose from 10 to 151.")


def test_causal_findings_are_demoted_or_flagged_and_hypotheses_stay_separate():
    llm = ScriptedLLM(
        _step(("two windows", TWO_WINDOWS)),
        _step(report=Report(
            answer="Segment X / ANDROID SR fell from 88.69% to 62.05%; a release is a possible cause (hypothesis).",
            findings=[
                ReportFinding(statement=SEGMENT_FINDING, evidence_query_ids=["Q1"]),
                # a cause with nothing from the results behind it -> hypothesis
                ReportFinding(statement="The root cause is a faulty Android app release.", evidence_query_ids=["Q1"]),
                # a real number plus an unproven cause -> stays a finding, flagged
                ReportFinding(statement="SR fell to 62.05% because of a faulty Android release.",
                              evidence_query_ids=["Q1"]),
            ],
            hypotheses=[ReportHypothesis(statement="A gateway timeout change", rationale="r",
                                         supporting_query_ids=["Q1"], how_to_verify="v")],
            caveats=[], suggested_follow_ups=[])),
    )
    inv = Investigator(schema=SCHEMA, llm=llm).investigate("Find the root cause")
    assert [f.statement for f in inv.findings] == [SEGMENT_FINDING, "SR fell to 62.05% because of a faulty Android "
                                                                     "release."]
    assert inv.findings[0].claim_notes == [] and inv.findings[1].claim_notes
    demoted = [h for h in inv.hypotheses if h.demoted_from_finding]
    assert len(demoted) == 1 and demoted[0].statement.startswith("The root cause") and demoted[0].supporting_evidence == ["Q1"]
    assert any(h.statement == "A gateway timeout change" and not h.demoted_from_finding for h in inv.hypotheses)
    # the hedged answer passes; a flagged finding does not count as support for the answer's numbers
    assert inv.answer_issues == [] and not inv.answer_revised
    md = inv.to_markdown()
    assert "⚠ States a cause as fact" in md and "## Hypotheses (possible drivers, not proven)" in md


# ----------------------------------------------------------------------- false positives fixed in review
def test_measured_cause_is_an_observed_breakdown_but_unmeasured_cause_is_not():
    finding = ["GATEWAY_ERROR failures for segment X rose from 10 to 142 after the break."]
    measured = "The drop was mostly caused by a rise in GATEWAY_ERROR from 10 to 142 failures."
    assert answer_claim_issues(measured, finding) == []
    # numbers that are not in the findings do not make a cause "measured"
    assert answer_claim_issues("The drop was caused by a rise in GATEWAY_ERROR from 10 to 999 failures.", finding)
    assert answer_claim_issues("The drop was caused by a faulty Android release.", finding)
    assert answer_claim_issues("SR fell to 62.05% because of a faulty release.", ["SR fell to 62.05%."])


def test_numbers_from_the_question_may_be_repeated():
    q = "Which merchant with at least 150 attempts deteriorated the most?"
    finding = ["Merchant X fell from 91.28% to 83.80%."]
    assert check_answer_numbers("Among merchants with at least 150 attempts, X fell to 83.80%.", finding, q).unverified == []
    assert check_answer_numbers("Among merchants with at least 150 attempts, X fell to 83.80%.", finding).unverified \
        == ["150"]


def test_findings_may_restate_numbers_from_the_question():
    results = [(["merchant", "sr_change"], [("M1", -0.1635)])]
    stmt = "Among merchants with at least 150 attempts, M1 fell by 16.35 pp."
    assert check_numbers(stmt, results).unverified == ["150"]
    q = "Which merchant with at least 150 attempts in each window deteriorated the most?"
    assert check_numbers(stmt, results, q).unverified == []
    # a number that is neither in the results nor in the question is still flagged
    assert check_numbers("M1 fell by 17.00 pp with at least 150 attempts.", results, q).unverified == ["17.00 pp"]


def test_share_of_all_attempts_counts_as_contribution():
    assert has_contribution_share("GATEWAY_ERROR rose by 0.463 pp of all attempts, the largest increase.")


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
