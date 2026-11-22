# Synthetic payments dataset

A synthetic, internally consistent payments dataset used by the AI Business Analyst: about 198K payment
attempts over 12 weeks (3 Aug – 25 Oct 2026, IST) for 400 fictional merchants. All merchants, customers and
IDs are fictional; bank names are used only as dimension values.

This folder ships the **prebuilt data** the app and the analyst use:

```
output/
├── payments.duckdb          # all tables, typed — the app opens this read-only
├── data/                    # the same tables as CSV
└── catalog/                 # analyst-facing documentation
    ├── data_catalog.yaml    # machine-readable: tables, columns, join keys, validity rules, metric SQL
    └── data_catalog.md
```

The generator and its evaluator-only material (ground truth and the known-answer question set) are
intentionally **not** included in this repository.

## Tables

| Table | Rows | Grain |
|---|---|---|
| `transactions` | 198,308 | one payment attempt (a retry is a new row with the same `order_id`) |
| `merchants` | 400 | one merchant: category, size, onboarding date, MDR discount, settlement terms |
| `banks` | 14 | one issuing / settlement bank |
| `payment_methods` | 5 | UPI, credit card, debit card, net banking, wallet, with standard MDR |
| `failure_codes` | 13 | failure reason with category and responsible party |
| `settlements` | 26,268 | one payout batch per merchant per transaction date |

```
merchants 1───* transactions *───1 payment_methods
    │                │  *───1 banks (issuing; NULL for WALLET by design)
    │                │  *───1 failure_codes
    │                └── parent_transaction_id → transactions (retry chain)
    └─1───* settlements
```

## Data quality

Like real extracts, the raw data contains defects that the catalog's validity rules (R1–R8) describe:
duplicated transaction IDs, invalid statuses and amounts, missing or unknown merchant and bank references,
FAILED rows without a failure code, and settlement batches that do not reconcile. Every metric is computed on
**valid transactions** (latest `updated_at` per `transaction_id`, valid status, `0 < amount <= 1,000,000`);
the app's Data Quality page measures each defect live.

## Canonical metrics (full list in the catalog)

| Metric | Definition |
|---|---|
| Success rate | `SUCCESS / (SUCCESS + FAILED + USER_CANCELLED)` on valid rows; PENDING excluded |
| TPV | `SUM(amount)` over SUCCESS valid rows |
| Expected fee | `ROUND(amount * mdr_rate_pct * (100 - mdr_discount_pct) * 0.0001, 2)`, plus 18% GST |
| Settlement gap | `net_amount - expected_net`; negative = merchant under-paid; \|gap\| <= ₹1 is rounding |

## Quick check

```powershell
pip install -r requirements.txt
python test_database.py      # run from this folder; lists the tables in output/payments.duckdb
```
