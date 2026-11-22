# Enterprise AI Business Analyst — Payments

An AI-powered analytics platform for a payments business. Ask a question in plain English and the analyst
writes DuckDB SQL with Google Gemini, checks every query with a SQL safety validator, runs it on a read-only
database, and answers with its evidence: the SQL, the result tables and numbers traced back to those results.
A Streamlit dashboard adds a KPI overview with automatic incident detection, a data-quality report and a data
catalog, all over a synthetic payments dataset.

## Problem statement

Payments teams constantly ask questions like *"Why did the success rate drop last week?"* or *"Which merchants
were under-paid?"*. Answering them needs SQL skills, knowledge of the data's quirks (duplicates, invalid rows,
definitions of success rate or TPV) and several queries per question. Plain text-to-SQL is fast but can return
confident, wrong or unverifiable answers. This project explores an analyst that:

* uses one shared semantic layer (metric definitions, cleaning rules, join keys),
* never runs unsafe SQL and never writes to the data,
* investigates "why" questions over several queries instead of one,
* separates facts shown by the data from hypotheses, and checks the numbers it quotes.

## Features

| Page | What it does |
|---|---|
| **Overview** | KPIs with change chips (success rate, TPV, attempts, order conversion, failure rate, p95 latency); date / method / channel filters; weekly trend; success rate by method; payment mix; top failure reasons; method scorecard; **detected incidents** (statistical change points) with one-click *Investigate*. |
| **Ask the Analyst** | Chat with **Quick answer**, **Deep investigation** and **Auto** modes; follow-up questions keep context; every answer shows its SQL and results, with charts where useful. |
| **Data Quality** | Validity rules R1–R8 measured live: health score, raw → valid funnel, affected rows by rule and severity, example bad records, weekly issue rates, raw-vs-valid impact on headline metrics, and settlement reconciliation by merchant and batch. |
| **Data Catalog** | The schema, join keys, metric definitions and rules the AI reads; live column profiles and value distributions; an ER diagram; the access policy; and a "try the SQL guard" box that validates (never runs) any SQL. |

## Architecture and workflow

```
Streamlit UI (app/)                      AI analyst backend (ai_analyst/)              Data
─────────────────────                     ──────────────────────────────               ─────────────
views/overview.py      ─┐                 schema_context.py  catalog + live DB schema ─┐ payments.duckdb
views/ask_analyst.py   ─┤ services/       sql_generator.py   question → SQL (Gemini)  │ (read-only)
views/data_quality.py  ─┤  backend.py ──► pipeline.py        generate → validate → run, auto-repair
views/data_catalog.py  ─┘  analyst.py     investigator.py    multi-round agent + answer checks
components/ (charts,       metrics.py     driver_scan.py     change-point + driver decomposition SQL
  KPI cards, theme)        quality.py     evidence.py        traces quoted numbers to query results
                           catalog.py     sql_validator.py   sqlglot allow-list (SELECT only, known tables)
                                          executor.py        read-only DuckDB, no file access, timeout, row cap
```

### AI analyst workflow

```
question ─► SQL generation (Gemini, schema + metric definitions + rules in the prompt)
         ─► validation (sqlglot: one SELECT, known tables/columns only, no DDL/DML, no file or extension functions)
         ─► execution (read-only DuckDB, external access disabled, 30 s timeout, row cap)
         ─► on error: the error goes back to the model for an automatic repair attempt
         ─► answer: quick = table + chart + computed headline; deep = findings / hypotheses / caveats
         ─► verification (deep): every number in a finding must appear in the cited results; the summary may
            only use numbers from verified findings; "driver" or cause wording must be backed by a finding
```

### Quick Answer vs Deep Investigation

| | Quick answer | Deep investigation |
|---|---|---|
| Queries | one validated query | several rounds (up to 4 queries per round) |
| Output | table, chart, computed headline, assumptions | answer, **verified findings**, **hypotheses** (not proven), caveats, follow-ups, all SQL evidence |
| Speed | typically 20–60 s | typically 1.5–2 min |
| Best for | "what / how many / show me" | "why", root cause, multi-part comparisons |

**Auto** routes "why / what is driving" and multi-part comparison questions to deep investigation and direct
questions to quick answers. A finding that cites no successful query is demoted to a hypothesis; if the summary
answer fails the checks, the model gets one rewrite and any remaining problem is shown next to the answer.

### Data quality and incident detection

* **Data quality**: one standard cleaning step (`valid_txn`: latest version of each transaction, valid status,
  valid amount) feeds every metric. The Data Quality page counts each defect live with fixed SQL.
* **Incident detection**: `driver_scan.py` finds, per segment (merchant, platform, bank, method, merchant ×
  platform, …), the day the success rate broke (a statistical change-point test), and ranks segments by their
  contribution to the overall change. The Overview shows these as incident cards; deep investigations use the
  same scan when a question is about a change over time.

## Tech stack

Python 3.11+ · Streamlit · Plotly · DuckDB · sqlglot · Google Gemini (`google-genai`) · Pydantic · pandas · pytest

## Synthetic dataset

`payments-analyst-dataset/output/` ships a prebuilt, fully synthetic dataset (fictional merchants and customers):
about 198K payment attempts over 12 weeks (3 Aug – 25 Oct 2026, IST), with realistic structure (method and bank
differences, seasonality, retries) and deliberate data-quality defects.

| Table | Rows | Grain |
|---|---|---|
| `transactions` | 198,308 | one payment attempt |
| `merchants` | 400 | one merchant, with commercial and settlement terms |
| `banks` | 14 | one issuing / settlement bank |
| `payment_methods` | 5 | UPI, credit card, debit card, net banking, wallet |
| `failure_codes` | 13 | failure reason with category and responsible party |
| `settlements` | 26,268 | one payout batch per merchant per transaction date |

The data catalog (`output/catalog/`) documents columns, join keys, validity rules R1–R8 and metric SQL. The
dataset generator and its evaluator-only answer key are not part of this repository.

## Evaluation results

An 18-question known-answer set was run once per question on the Gemini standard model (`gemini-3.5-flash`
requested; the API served `gemini-3.6-flash`) and graded automatically. Full details:
[`ai_analyst/reports/final_evaluation_report.md`](ai_analyst/reports/final_evaluation_report.md).

| | Result |
|---|---|
| Quick mode, all 18 questions | **14 / 18** |
| Simple questions (metric, comparison, trend, cohort, ambiguity) | **11 / 11** |
| Multi-step questions in quick mode (root cause, data quality, reconciliation) | 3 / 7 |
| Deep investigation of the 3 root-cause questions | **3 / 3** correct cause found |
| Merchant-incident (M0137) detection across 4 question wordings, Lite model | **4 / 4** wordings, all checks passed |
| Response time | quick: median 32 s, max 153 s; deep: 85–126 s |

The four quick-mode failures were incomplete rather than numerically wrong (e.g. a single query that did not cut
by bank, or a correct total without the requested breakdown). **Limitations of this evaluation:** one run per
question (run-to-run variation not measured); runs were spread over two days and code versions; root-cause
grading partly needs human judgement; the M0137 wording test used the Lite model because the standard model's
free-tier quota ran out; the dataset is synthetic.

## Installation and environment setup

Requires Python 3.11+ (developed on 3.12) and a free Gemini API key from https://aistudio.google.com/apikey.

```powershell
git clone https://github.com/Varada2908/enterprise-ai-business-analyst-pinelabs.git
cd enterprise-ai-business-analyst-pinelabs
python -m venv .venv
.venv\Scripts\Activate.ps1                  # macOS/Linux: source .venv/bin/activate
pip install -r app/requirements.txt         # also installs ai_analyst/requirements.txt
Copy-Item ai_analyst/.env.example ai_analyst/.env   # macOS/Linux: cp ai_analyst/.env.example ai_analyst/.env
```

Edit `ai_analyst/.env` and paste your key after `GEMINI_API_KEY=`. Optionally set `GEMINI_MODEL`
(default `gemini-3.5-flash`; `gemini-3.5-flash-lite` has higher free-tier limits). The model can also be
switched in the app's sidebar. Never commit `.env`; it is git-ignored.

## Launch the app

```powershell
cd app
python -m streamlit run streamlit_app.py
```

Open http://localhost:8501. Run it from the `app/` folder so Streamlit picks up `app/.streamlit/config.toml`.
Overview, Data Quality and Data Catalog work without an API key; only **Ask the Analyst** calls Gemini.

## Tests

All tests run offline: the LLM is replaced by scripted fakes, so no API quota is used.

```powershell
python -m pytest ai_analyst/tests -q        # from the repository root: validator, pipeline, investigator, answer checks, driver scan
cd app
python -m pytest tests -q                   # page smoke tests, chat flows, data-quality counts, catalog
```

Data-quality and catalog tests recompute displayed counts independently in pandas / DuckDB, so the numbers on
the pages are known to come from the data rather than constants.

## Demo flow (about 5 minutes)

1. **Overview**: check the KPIs and the *Detected incidents* card, then click **Investigate**.
2. **Ask the Analyst** runs a deep investigation: read the answer, the verified findings (✓ badges and evidence
   chips), the hypotheses ("not proven") and open the SQL evidence. Ask a follow-up, then a quick question such
   as *"What is the success rate and TPV by payment method?"*.
3. **Data Quality**: health score, raw → valid funnel; click a rule to see bad records; open the settlement
   reconciliation and click a merchant to see its batches.
4. **Data Catalog**: metric definitions, ER diagram; try `DELETE FROM transactions` in the SQL guard
   (blocked, never executed).

## Known limitations

* Answers depend on the LLM: wording can vary between runs and a question can still be misread. The SQL guard,
  number verification and answer checks reduce this but do not remove it; check the SQL evidence.
* The answer, driver and cause checks are rule-based wording checks, so an unusual paraphrase can slip past
  them. The number check itself is exact to the stated rounding.
* Gemini free-tier limits apply: a deep investigation takes several calls; quota or overload errors are shown
  clearly and the conversation is kept.
* Quick mode answers with one query, so a multi-part question in Quick mode returns a single table.
* The dashboards are built for this schema and this 12-week synthetic dataset, not for arbitrary databases.
* Single-user local app: no authentication; conversation history lives in the browser session only.

## Project layout

```
ai_analyst/                 backend (no UI); reports/ holds sample investigations and the evaluation report
app/                        Streamlit app: streamlit_app.py, views/, components/, services/, assets/, tests/
payments-analyst-dataset/   prebuilt synthetic data (DuckDB + CSV) and the analyst-facing data catalog
```

## Security

* The Gemini key is read from `ai_analyst/.env` or the environment and is never logged or shown in the UI.
  Only `ai_analyst/.env.example` (placeholder values) is in the repository.
* The database is opened read-only with external file access disabled; generated SQL cannot write, attach
  databases, read files or load extensions.
