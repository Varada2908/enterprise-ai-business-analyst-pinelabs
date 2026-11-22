# Final evaluation report — AI Business Analyst

Model: Gemini standard model (`gemini-3.5-flash` requested; the API served `gemini-3.6-flash`), temperature 0.
Runs: 21–22 Nov 2026, on the free tier. Every question was asked once through the production code path; only the
question text reaches the model. Grading was automatic: a separate grader compared results with the known
answers using fixed tolerances (the grader and the evaluator-only question set are not published in this
repository), plus `ai_analyst/tests/grade_m0137_live.py` for the merchant-incident investigations.

## Headline

| | Result |
|---|---|
| **Quick mode (one SQL query), 18 questions** | **14 / 18 pass (78%)**, 0 API errors, 0 SQL repairs needed |
| Simple questions (metric, comparison, trend, cohort, ambiguity: 11) | **11 / 11** |
| Multi-step questions (root cause, data quality, reconciliation: 7) | **3 / 7** in quick mode |
| **Deep investigation on the 3 root-cause questions** | **3 / 3** correct cause found, all findings number-verified |
| Root-cause questions as the app's Auto mode answers them ("why…" → deep) | 3 / 3, so **16 / 18 overall** in the mode a user gets |
| M0137 merchant incident (4 different wordings, Lite model) | **4 / 4 wordings, 4 / 4 checks each** |
| Response time, quick mode | median **32 s**, mean 48 s, p90 86 s, max 153 s |
| Response time, deep investigation | 85–126 s (3 rounds, 6–8 queries) |

## Per-question results (quick mode)

| Q | category | verdict | what was checked | time (s) |
|---|---|---|---|---|
| Q01 | metric | PASS | overall success rate 0.9002 | 28 |
| Q02 | metric | PASS | TPV ₹72.94 Cr (729,439,708.64) | 104 |
| Q03 | comparison | PASS | credit card highest (0.9459), net banking lowest (0.8407) | 26 |
| Q04 | trend | PASS | worst week 2026-09-14 (0.8765) | 31 |
| Q05 | root cause | **FAIL** | SBI × UPI not in the result (see below) | 86 |
| Q06 | root cause | **FAIL** (manual review) | UPI share 0.448 → 0.742 shown, but the cause was not named | 46 |
| Q07 | root cause | PASS | M0137 first | 153 |
| Q08 | metric | PASS | SBI UPI success rate 0.6261 | 62 |
| Q09 | metric | PASS | UPI share 0.7419 | 32 |
| Q10 | metric | PASS | top failure reason, with the missing-code caveat | 26 |
| Q11 | data quality | PASS | 1,070 duplicated IDs (conflicting statuses not mentioned) | 21 |
| Q12 | reconciliation | PASS | M0218 from 2026-09-07, gap −₹1,01,036.47 over 49 batches | 44 |
| Q13 | reconciliation | **FAIL** | totals right, but not broken down by reason as asked | 37 |
| Q14 | metric | PASS | p50 / p95 latency for all 5 methods | 31 |
| Q15 | metric | PASS | order conversion 0.9305 vs attempt success 0.9002 | 27 |
| Q16 | cohort | PASS | existing 0.9007 vs new 0.8900 | 67 |
| Q17 | ambiguity | PASS | "last month" = September, assumption stated | 22 |
| Q18 | data quality | **FAIL** | total 680 right, split (420 NULL + 260 unknown) missing | 21 |

## Failure analysis

All four failures are **incomplete answers, not wrong numbers**: no failing answer reports an incorrect figure.

* **Q05 – why did the success rate drop in the week of 14 Sep?** Quick mode wrote one broad query (method ×
  status × failure code) that never cuts by bank, so the SBI × UPI outage is not visible. A single query is the
  wrong tool for a "why" question. The deep investigation of the same question found it: SBI UPI success rate
  87.28% → 62.61% (−24.66 pp), contributing −2.71 pp (95.9%) of the −2.83 pp overall drop, with BANK_TIMEOUT,
  NETWORK_ERROR and ISSUER_UNAVAILABLE spikes and every other segment flat.
* **Q06 – why did the success rate fall in the week of 5 Oct?** The quick result contains the right evidence
  (every method's success rate flat, UPI attempts 7,444 → 17,889) but its explanation does not say "mix shift".
  The deep investigation names it: UPI share 44.93% → 74.38% with UPI success rate flat (88.08% → 88.05%),
  higher-success methods losing share, and the step change on 2026-10-05.
* **Q13 – pending / failed settlement batches.** Correct counts by status (977 pending, 186 failed, 98 overdue
  pending worth ₹33.65 L) but grouped by status rather than by `status_reason`.
* **Q18 – unattributable transactions.** Correct total (680) but not split into NULL merchant (420) vs merchant
  ID not in the merchants table (260).

Since these runs, the prompts ask for multi-part answers to cover every part, and Auto mode routes "why" and
multi-part comparison questions to deep investigation. Q13 and Q18 would still need a breakdown the question
only implies.

## Deep investigations (standard model)

| Question | Rounds / queries | Found | Answer check |
|---|---|---|---|
| Q05 week of 14 Sep drop | 3 / 6, 85 s | SBI × UPI outage, 95.9% of the drop, failure codes, daily timeline | passed first time |
| Q06 week of 5 Oct drop | 3 / 8, 126 s | UPI mix shift, per-method rates flat, step change on 5 Oct | passed after one rewrite |
| Q07 worst merchant, last 2 weeks | 3 / 8, 123 s | M0137, Android only (−27.01 pp), GATEWAY_ERROR / TIMEOUT, from 12 Oct | passed after one rewrite |

All findings in Q05 and Q06 had every number traced to the cited query results. In each investigation, causes
outside the data (an outage, a promotion, an app release) were reported as hypotheses, not findings. Two Q07
findings were flagged: one restated the question's "≥150 attempts" threshold (a checker false positive, now
fixed) and one quoted a total (147) not shown by its cited query.

### M0137 incident detection across wordings

| Wording | Model | Checks (merchant, Android, gateway failures, 12 Oct) |
|---|---|---|
| "complaints about more declined payments recently… root cause?" | Lite | 4 / 4 |
| "failures gone up since mid-October. What is driving it?" | Lite | 4 / 4 |
| "success rate in the last two weeks looks worse… What changed?" | Lite | 4 / 4 |
| "Something seems off with payments lately. Is anything getting worse?" | Lite | 4 / 4 |
| Q07 (explicit window) | Standard | 4 / 4 |

Earlier versions without the deterministic change-point detection scored 1–3 / 4 on the same wordings: vague
timing ("recently", "lately") led the model to compare the wrong periods. The standard-model runs of the four
wordings could not complete on the free-tier daily quota.

## Standard vs Lite

The 18 questions were only graded on the standard model. For investigations, the Lite model with the current
driver scan found the M0137 incident on all four wordings in 7–27 s, versus ~2 minutes on the standard model.
The standard model wrote richer reports: more rounds, segment comparisons, contribution shares and daily
timelines.

## Reliability notes

* **API availability was the biggest practical issue.** The first standard-model re-run lost 9 of 12 questions
  to Gemini 503 "high demand" errors and the daily free-tier quota. A retry with backoff after the quota reset
  answered all 9. Deep investigations use 4–6 calls each, so the free tier allows only a few per day.
* No SQL had to be repaired, and no query was rejected by the safety validator in these runs.
* The answer check worked as intended on live runs: in Q06 and Q07 the first summary used a number or a
  "driven by" claim the findings did not support, and the rewrite fixed it.

## Limitations of this evaluation

* One run per question: run-to-run variation at temperature 0 is small but not measured.
* Runs are spread over two days and code versions. Q01, Q06, Q09, Q10, Q13–Q15, Q17 and Q18 ran after the
  final prompt changes; Q02–Q05, Q07, Q08, Q11, Q12 and Q16 ran earlier (all of those passed except Q05).
* Root-cause and narrative grading partly needs human judgement (Q06 is marked for manual review).
* The dataset is synthetic with planted scenarios; real data would bring messier schemas and ambiguous definitions.
