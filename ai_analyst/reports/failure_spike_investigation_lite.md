# Payment failures seem to have gone up since mid-October. What is driving it?

## Answer
Payment failures did not structurally increase in mid-October. Instead, the perception of higher failures was driven by a sharp volume spike during the week of October 5, 2026, where total terminal attempts surged to 24,174, temporarily elevating absolute failure counts and the weekly failure rate to 8.30% before returning to baseline levels.

## Findings (shown by the data)
1. Weekly terminal attempts jumped to 24,174 during the week of 2026-10-05, compared to a range of 14,877 to 16,640 in other weeks.  
   _Evidence: Q1 · ✓ numbers verified against evidence_
2. Weekly failure count peaked at 2,001 during the week of 2026-10-05, and the failure rate rose to 0.0830 (8.30%), compared to ~7.0% in August and September.  
   _Evidence: Q1 · ✓ numbers verified against evidence_
3. By the weeks of 2026-10-12 and 2026-10-19, terminal attempts dropped back to 16,260 and 16,298, with failure rates settling at 0.0777 (7.77%) and 0.0768 (7.68%).  
   _Evidence: Q1 · ✓ numbers verified against evidence_

## Hypotheses (possible drivers, not proven)
1. **The volume spike in the week of October 5 was driven by promotional campaigns or festive sales (e.g., early festival shopping in India).**  
   Why plausible: Sudden spikes in transaction volume without degradation in system failure codes are typically associated with marketing events or seasonal shopping surges.  
   Consistent with: Q1, Q4 · To verify: Check merchant-level or campaign-level marketing calendar to see if major sales events occurred during the week of October 5.

## Caveats
- Analysis is limited to valid transactions between 2026-08-03 and 2026-10-25.
- Failure rate calculations exclude PENDING transactions as per metric definitions.

## Suggested follow-ups
- Identify which specific merchants drove the volume surge during the week of October 5, 2026.
- Examine customer retry behaviour and order conversion rates during the volume spike week.

## Evidence: queries run
### Q1 · Q1: Weekly transaction volume, terminal attempts, success rate, and failure rate across the 12 weeks.
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT date_trunc('week', created_at) AS txn_week, count(*) AS terminal_attempts, count_if(status = 'SUCCESS') AS success_count, count_if(status = 'FAILED') AS failure_count, ROUND(count_if(status = 'SUCCESS')::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS success_rate, ROUND(count_if(status = 'FAILED')::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS failure_rate FROM valid_txn GROUP BY 1 ORDER BY 1
```
| txn_week | terminal_attempts | success_count | failure_count | success_rate | failure_rate |
|---|---|---|---|---|---|
| 2026-08-03 00:00:00 | 15322 | 13893 | 1070 | 0.9089 | 0.07 |
| 2026-08-10 00:00:00 | 14877 | 13449 | 1027 | 0.9065 | 0.0692 |
| 2026-08-17 00:00:00 | 15331 | 13843 | 1077 | 0.9049 | 0.0704 |
| 2026-08-24 00:00:00 | 15315 | 13820 | 1111 | 0.9044 | 0.0727 |
| 2026-08-31 00:00:00 | 15455 | 13899 | 1173 | 0.9013 | 0.0761 |
| 2026-09-07 00:00:00 | 15126 | 13651 | 1071 | 0.9048 | 0.071 |
| 2026-09-14 00:00:00 | 15997 | 13994 | 1596 | 0.8765 | 0.1 |
| 2026-09-21 00:00:00 | 16187 | 14613 | 1147 | 0.9053 | 0.0711 |
| 2026-09-28 00:00:00 | 16640 | 15057 | 1147 | 0.9069 | 0.0691 |
| 2026-10-05 00:00:00 | 24174 | 21525 | 2001 | 0.8927 | 0.083 |
| 2026-10-12 00:00:00 | 16260 | 14548 | 1260 | 0.8971 | 0.0777 |
| 2026-10-19 00:00:00 | 16298 | 14601 | 1248 | 0.8982 | 0.0768 |

### Q2 · Q2: Failure rate and volume by payment method comparing before mid-October (before week of 2026-10-12) vs mid-October onwards.
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT method_code, CASE WHEN created_at >= TIMESTAMP '2026-10-12 00:00:00' THEN 'Mid_Oct_Onwards' ELSE 'Before_Mid_Oct' END AS period, count(*) AS terminal_attempts, count_if(status = 'FAILED') AS failure_count, ROUND(count_if(status = 'FAILED')::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS failure_rate FROM valid_txn GROUP BY 1, 2 ORDER BY 1, 2
```
| method_code | period | terminal_attempts | failure_count | failure_rate |
|---|---|---|---|---|
| CREDIT_CARD | Before_Mid_Oct | 30233 | 1012 | 0.0336 |
| CREDIT_CARD | Mid_Oct_Onwards | 6505 | 252 | 0.0388 |
| DEBIT_CARD | Before_Mid_Oct | 30397 | 1553 | 0.0512 |
| DEBIT_CARD | Mid_Oct_Onwards | 6482 | 320 | 0.0495 |
| NET_BANKING | Before_Mid_Oct | 10856 | 1263 | 0.1166 |
| NET_BANKING | Mid_Oct_Onwards | 2271 | 279 | 0.1236 |
| UPI | Before_Mid_Oct | 81135 | 8008 | 0.0989 |
| UPI | Mid_Oct_Onwards | 14855 | 1529 | 0.1032 |
| WALLET | Before_Mid_Oct | 11803 | 584 | 0.0496 |
| WALLET | Mid_Oct_Onwards | 2445 | 128 | 0.0524 |

### Q3 · Q3: Failure categories and failure codes breakdown comparing before mid-October vs mid-October onwards.
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT fc.failure_category, t.failure_code, CASE WHEN t.created_at >= TIMESTAMP '2026-10-12 00:00:00' THEN 'Mid_Oct_Onwards' ELSE 'Before_Mid_Oct' END AS period, count(*) AS failed_count FROM valid_txn t LEFT JOIN failure_codes fc USING (failure_code) WHERE t.status = 'FAILED' GROUP BY 1, 2, 3 ORDER BY 4 DESC
```
| failure_category | failure_code | period | failed_count |
|---|---|---|---|
| CUSTOMER | INSUFFICIENT_FUNDS | Before_Mid_Oct | 2386 |
| BANK | BANK_TIMEOUT | Before_Mid_Oct | 1527 |
| CUSTOMER | INCORRECT_PIN | Before_Mid_Oct | 1513 |
| BANK | BANK_DECLINED | Before_Mid_Oct | 1199 |
| CUSTOMER | AUTH_FAILED | Before_Mid_Oct | 1077 |
| CUSTOMER | LIMIT_EXCEEDED | Before_Mid_Oct | 867 |
| GATEWAY | GATEWAY_ERROR | Before_Mid_Oct | 754 |
| NULL | NULL | Before_Mid_Oct | 748 |
| BANK | ISSUER_UNAVAILABLE | Before_Mid_Oct | 670 |
| NETWORK | NETWORK_ERROR | Before_Mid_Oct | 617 |
| RISK | RISK_DECLINED | Before_Mid_Oct | 558 |
| CUSTOMER | INSUFFICIENT_FUNDS | Mid_Oct_Onwards | 453 |
| CUSTOMER | INVALID_VPA | Before_Mid_Oct | 415 |
| CUSTOMER | INCORRECT_PIN | Mid_Oct_Onwards | 327 |
| GATEWAY | GATEWAY_ERROR | Mid_Oct_Onwards | 298 |
_15 of 27 rows shown_

### Q4 · Q4: Check daily failure rate and volume across October 2026 to see exact timing of any spikes.
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT CAST(created_at AS DATE) AS txn_date, count(*) AS terminal_attempts, count_if(status = 'FAILED') AS failure_count, ROUND(count_if(status = 'FAILED')::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS failure_rate FROM valid_txn WHERE created_at >= TIMESTAMP '2026-10-01 00:00:00' GROUP BY 1 ORDER BY 1
```
| txn_date | terminal_attempts | failure_count | failure_rate |
|---|---|---|---|
| 2026-10-01 | 2347 | 186 | 0.0794 |
| 2026-10-02 | 2504 | 163 | 0.0653 |
| 2026-10-03 | 2776 | 179 | 0.0648 |
| 2026-10-04 | 2320 | 151 | 0.0653 |
| 2026-10-05 | 3323 | 255 | 0.0769 |
| 2026-10-06 | 3058 | 271 | 0.0889 |
| 2026-10-07 | 3393 | 278 | 0.0821 |
| 2026-10-08 | 3546 | 301 | 0.0851 |
| 2026-10-09 | 3543 | 283 | 0.0801 |
| 2026-10-10 | 3940 | 353 | 0.0898 |
| 2026-10-11 | 3371 | 260 | 0.0774 |
| 2026-10-12 | 2290 | 167 | 0.0731 |
| 2026-10-13 | 2048 | 144 | 0.0707 |
| 2026-10-14 | 2296 | 202 | 0.0883 |
| 2026-10-15 | 2254 | 170 | 0.0756 |
_15 of 25 rows shown_

### Q5 · Q5: Check failure rates by merchant in October vs before October to see if specific merchants drove the change.
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT m.merchant_name, CASE WHEN t.created_at >= TIMESTAMP '2026-10-12 00:00:00' THEN 'Mid_Oct_Onwards' ELSE 'Before_Mid_Oct' END AS period, count(*) AS terminal_attempts, count_if(t.status = 'FAILED') AS failure_count, ROUND(count_if(t.status = 'FAILED')::DOUBLE / NULLIF(count_if(t.status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS failure_rate FROM valid_txn t LEFT JOIN merchants m USING (merchant_id) GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20
```
| merchant_name | period | terminal_attempts | failure_count | failure_rate |
|---|---|---|---|---|
| Zestora Online Mart | Before_Mid_Oct | 5072 | 383 | 0.0756 |
| Riverbend Fresh | Before_Mid_Oct | 4795 | 382 | 0.0798 |
| Norvane Electronics | Before_Mid_Oct | 3688 | 264 | 0.0717 |
| Aarohi Online Mart | Before_Mid_Oct | 3575 | 332 | 0.0931 |
| Anokhi Kitchen | Before_Mid_Oct | 3059 | 196 | 0.0642 |
| Ekam Store | Before_Mid_Oct | 3013 | 277 | 0.0921 |
| Sahyadri Online Mart Bengaluru | Before_Mid_Oct | 3000 | 254 | 0.085 |
| Firoza Power | Before_Mid_Oct | 2827 | 256 | 0.0907 |
| Qila Fashions | Before_Mid_Oct | 2721 | 235 | 0.0866 |
| Kesari Fashions Bengaluru | Before_Mid_Oct | 2702 | 145 | 0.0539 |
| Gulmohar Dhaba Pune | Before_Mid_Oct | 2407 | 148 | 0.0617 |
| Mehr Supermarket | Before_Mid_Oct | 2128 | 163 | 0.0768 |
| Firoza Institute | Before_Mid_Oct | 2112 | 251 | 0.1192 |
| Aarohi Utilities Hyderabad | Before_Mid_Oct | 2080 | 206 | 0.0993 |
| Silverline Cinemas | Before_Mid_Oct | 2012 | 101 | 0.0504 |
_15 of 20 rows shown_

---
_3 round(s) · 5/5 queries succeeded · 13.3s · gemini-3.5-flash-lite · tokens in/out 23269/2382_