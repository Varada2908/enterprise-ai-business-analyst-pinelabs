# Why did the success rate drop in the week of 2026-09-14 compared with the previous week?

## Answer
The overall transaction success rate dropped by 2.83 percentage points from 90.48% (13,651 / 15,088 terminal attempts) in the week of 2026-09-07 to 87.65% (13,994 / 15,965 terminal attempts) in the week of 2026-09-14. This drop was driven almost exclusively by SBI UPI payments, whose success rate fell by 24.66 percentage points from 87.28% to 62.61%, contributing -2.706 pp (95.91%) to the overall success rate decline. The drop was caused by an outage/degradation at SBI's UPI issuing infrastructure between Monday 2026-09-14 and Friday 2026-09-18 (hypothesised), manifesting as a sharp spike in BANK_TIMEOUT (+225 failures), NETWORK_ERROR (+106), and ISSUER_UNAVAILABLE (+62) codes; non-SBI UPI and SBI non-card/non-UPI success rates remained completely stable.

_✓ Every number in the answer is stated in a verified finding._

## Findings (shown by the data)
1. Overall business success rate declined from 90.48% (13,651 / 15,088 attempts) in 2026-09-07..2026-09-13 to 87.65% (13,994 / 15,965 attempts) in 2026-09-14..2026-09-20, an overall decrease of 2.83 pp.  
   _Evidence: Q3 · ✓ numbers verified against evidence_
2. SBI UPI transactions were the primary driver of the decline: success rate for SBI UPI dropped by 24.66 pp (from 87.28% on 1,501 attempts to 62.61% on 1,752 attempts), contributing -2.706 pp of impact on overall SR (95.91% of the net overall change).  
   _Evidence: Q1, Q6 · ✓ numbers verified against evidence_
3. Performance across all non-SBI UPI segments remained flat (88.65% baseline vs 88.67% current), as did SBI non-UPI performance (90.62% baseline vs 90.54% current) and all other payment methods/banks (92.55% baseline vs 92.34% current).  
   _Evidence: Q6 · ✓ numbers verified against evidence_
4. The failure spike was concentrated in technical bank failure codes: BANK_TIMEOUT increased by 225 failures (113 to 338), NETWORK_ERROR by 106 failures (49 to 155), and ISSUER_UNAVAILABLE by 62 failures (53 to 115).  
   _Evidence: Q2, Q4 · ✓ numbers verified against evidence_
5. The SBI UPI incident started on Monday 2026-09-14 (SR fell to 69.75%), reached its worst level on Tuesday 2026-09-15 (SR 50.97% with 54 BANK_TIMEOUTs, 24 NETWORK_ERRORs, 11 ISSUER_UNAVAILABLEs), remained degraded through Friday 2026-09-18 (SR 54.51%), and substantially recovered by Sunday 2026-09-20 (SR 83.84%).  
   _Evidence: Q5 · ✓ numbers verified against evidence_

## Hypotheses (possible drivers, not proven)
1. **SBI's core banking system or NPCI UPI handle server experienced severe latency/unavailability during weekdays of the week of 2026-09-14.**  
   Why plausible: The failure modes (BANK_TIMEOUT, NETWORK_ERROR, ISSUER_UNAVAILABLE) are bank/issuer technical errors, and the degradation was strictly confined to SBI UPI while non-SBI UPI and SBI non-UPI payments were unaffected.  
   Consistent with: Q4, Q5, Q6 · To verify: Cross-reference with NPCI UPI status reports, SBI IT maintenance logs, or gateway provider error logs for SBI UPI handles during 2026-09-14 to 2026-09-18.

## Caveats
- 78 failed attempts in the target week had NULL failure_codes, though this was consistent with the baseline week (73 NULLs) and does not alter the conclusion.
- The driver scan attributes -2.822 pp net change based on terminal attempt weights, matching the headline -2.83 pp shift.

## Suggested follow-ups
- Did the SBI UPI degradation impact order conversion rates or lead to customer abandonment/retry exhaustion?
- Were specific UPI handles/apps (e.g., @oksbi vs @ybl) or specific NPCI switch routes disproportionately affected during the incident?
- How did auto-retry routing perform during the SBI UPI outage window?

## Evidence: queries run
### Q1 · [driver scan] segments ranked by impact on SR change (2026-09-07..2026-09-13 vs 2026-09-14..2026-09-20)
```sql
WITH valid_txn AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT t.*, pm.method_code,
               CAST(t.created_at AS DATE) AS txn_date,
               row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn
        FROM transactions t
        JOIN payment_methods pm USING (payment_method_id)
    )
    WHERE _rn = 1
      AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING')
      AND amount > 0 AND amount <= 1000000
),
base AS (
    SELECT v.*, b.short_name AS bank, m.merchant_name, m.category AS merchant_category,
           CASE WHEN v.txn_date BETWEEN DATE '2026-09-14' AND DATE '2026-09-20' THEN 'cur'
                WHEN v.txn_date BETWEEN DATE '2026-09-07' AND DATE '2026-09-13' THEN 'base' END AS period
    FROM valid_txn v
    LEFT JOIN banks b ON v.bank_id = b.bank_id
    LEFT JOIN merchants m ON v.merchant_id = m.merchant_id
    WHERE v.status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED') 
),
seg AS (
    SELECT 'merchant' AS dimension, coalesce(merchant_id, '(none)') || coalesce(' ' || merchant_name, '') AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'device_platform' AS dimension, device_platform AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'merchant x platform' AS dimension, coalesce(merchant_id, '(none)') || ' / ' || device_platform AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'payment_method' AS dimension, method_code AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'issuing_bank' AS dimension, coalesce(bank, '(none)') AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'bank x method' AS dimension, coalesce(bank, '(none)') || ' / ' || method_code AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'merchant_category' AS dimension, coalesce(merchant_category, '(none)') AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'channel' AS dimension, channel AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
),
piv AS (
    SELECT dimension, segment,
           coalesce(sum(n) FILTER (WHERE period = 'base'), 0) AS n_base,
           coalesce(sum(s) FILTER (WHERE period = 'base'), 0) AS s_base,
           coalesce(sum(n) FILTER (WHERE period = 'cur'), 0) AS n_cur,
           coalesce(sum(s) FILTER (WHERE period = 'cur'), 0) AS s_cur
    FROM seg GROUP BY dimension, segment
),
tot AS (
    SELECT count_if(period = 'cur') AS total_cur,
           (count_if(period = 'cur' AND status = 'SUCCESS')::DOUBLE / nullif(count_if(period = 'cur'), 0)
            - count_if(period = 'base' AND status = 'SUCCESS')::DOUBLE / nullif(count_if(period = 'base'), 0)) * 100
           AS overall_change_pp
    FROM base
),
scored AS (
    SELECT dimension, segment, n_base, round(s_base::DOUBLE / n_base, 4) AS sr_baseline,
           n_cur, round(s_cur::DOUBLE / n_cur, 4) AS sr_current,
           round(s_cur::DOUBLE / n_cur - s_base::DOUBLE / n_base, 4) AS sr_change,
           round(n_cur::DOUBLE / total_cur * (s_cur::DOUBLE / n_cur - s_base::DOUBLE / n_base) * 100, 3) AS impact_on_overall_sr_pp,
           round((s_base::DOUBLE / n_base - s_cur::DOUBLE / n_cur) * n_cur) AS excess_unsuccessful_attempts,
           round(overall_change_pp, 3) AS overall_sr_change_pp,
           -- the segment's rate effect as a share of the overall SR change (mix shifts are not included,
           -- so shares within a dimension need not add up to 1)
           round(CASE WHEN abs(overall_change_pp) >= 0.01 THEN
                 n_cur::DOUBLE / total_cur * (s_cur::DOUBLE / n_cur - s_base::DOUBLE / n_base) * 100 / overall_change_pp
                 END, 4) AS share_of_overall_change
    FROM piv CROSS JOIN tot
    WHERE n_base >= 100 AND n_cur >= 100
)
SELECT * FROM scored
QUALIFY row_number() OVER (PARTITION BY dimension ORDER BY impact_on_overall_sr_pp ASC) <= 4
ORDER BY impact_on_overall_sr_pp ASC
LIMIT 40
```
| dimension | segment | n_base | sr_baseline | n_cur | sr_current | sr_change | impact_on_overall_sr_pp | excess_unsuccessful_attempts | overall_sr_change_pp | share_of_overall_change |
|---|---|---|---|---|---|---|---|---|---|---|
| issuing_bank | SBI | 2994 | 0.8894 | 3358 | 0.7597 | -0.1298 | -2.729 | 436 | -2.822 | 0.9673 |
| payment_method | UPI | 6761 | 0.8834 | 7162 | 0.823 | -0.0605 | -2.714 | 433 | -2.822 | 0.9618 |
| bank x method | SBI / UPI | 1501 | 0.8728 | 1752 | 0.6261 | -0.2466 | -2.706 | 432 | -2.822 | 0.9591 |
| channel | ONLINE | 8538 | 0.8972 | 8961 | 0.8688 | -0.0284 | -1.594 | 255 | -2.822 | 0.565 |
| device_platform | POS_TERMINAL | 6550 | 0.9147 | 7004 | 0.8865 | -0.0282 | -1.236 | 197 | -2.822 | 0.4379 |
| channel | POS | 6550 | 0.9147 | 7004 | 0.8865 | -0.0282 | -1.236 | 197 | -2.822 | 0.4379 |
| device_platform | ANDROID | 4675 | 0.8963 | 4910 | 0.865 | -0.0313 | -0.962 | 154 | -2.822 | 0.341 |
| merchant_category | ECOMMERCE | 2673 | 0.9016 | 2799 | 0.8678 | -0.0338 | -0.593 | 95 | -2.822 | 0.21 |
| merchant_category | RESTAURANTS | 2063 | 0.9166 | 2234 | 0.8841 | -0.0326 | -0.456 | 73 | -2.822 | 0.1615 |
| merchant_category | GROCERY | 2262 | 0.9067 | 2440 | 0.882 | -0.0248 | -0.378 | 60 | -2.822 | 0.1341 |
| device_platform | IOS | 1522 | 0.9067 | 1668 | 0.8801 | -0.0266 | -0.278 | 44 | -2.822 | 0.0985 |
| merchant_category | APPAREL | 1852 | 0.9044 | 1930 | 0.8819 | -0.0226 | -0.273 | 44 | -2.822 | 0.0967 |
| issuing_bank | AXIS | 1373 | 0.9272 | 1403 | 0.9009 | -0.0262 | -0.231 | 37 | -2.822 | 0.0817 |
| payment_method | DEBIT_CARD | 3034 | 0.9235 | 3279 | 0.9137 | -0.0098 | -0.202 | 32 | -2.822 | 0.0716 |
| device_platform | MOBILE_WEB | 1361 | 0.8898 | 1354 | 0.8678 | -0.022 | -0.186 | 30 | -2.822 | 0.0661 |
_15 of 30 rows shown_

### Q2 · [driver scan] failure-reason shift (2026-09-07..2026-09-13 vs 2026-09-14..2026-09-20)
```sql
WITH valid_txn AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT t.*, pm.method_code,
               CAST(t.created_at AS DATE) AS txn_date,
               row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn
        FROM transactions t
        JOIN payment_methods pm USING (payment_method_id)
    )
    WHERE _rn = 1
      AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING')
      AND amount > 0 AND amount <= 1000000
),
base AS (
    SELECT v.*, b.short_name AS bank, m.merchant_name, m.category AS merchant_category,
           CASE WHEN v.txn_date BETWEEN DATE '2026-09-14' AND DATE '2026-09-20' THEN 'cur'
                WHEN v.txn_date BETWEEN DATE '2026-09-07' AND DATE '2026-09-13' THEN 'base' END AS period
    FROM valid_txn v
    LEFT JOIN banks b ON v.bank_id = b.bank_id
    LEFT JOIN merchants m ON v.merchant_id = m.merchant_id
    WHERE v.status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED') 
),
fc AS (
    SELECT coalesce(failure_code, '(missing)') AS failure_code,
           count_if(period = 'base' AND status = 'FAILED') AS failed_base,
           count_if(period = 'cur' AND status = 'FAILED') AS failed_cur
    FROM base WHERE period IS NOT NULL GROUP BY 1
),
tot AS (SELECT count_if(period = 'base') AS n_base, count_if(period = 'cur') AS n_cur FROM base),
seg_fail AS (
    SELECT coalesce(failure_code, '(missing)') AS failure_code,
           coalesce(merchant_id, '(none)') || ' / ' || device_platform AS segment, count(*) AS n
    FROM base WHERE period = 'cur' AND status = 'FAILED' GROUP BY 1, 2
),
top_seg AS (
    SELECT * FROM seg_fail
    QUALIFY row_number() OVER (PARTITION BY failure_code ORDER BY n DESC, segment) = 1
)
SELECT fc.failure_code, failed_base, round(failed_base::DOUBLE / n_base * 100, 3) AS pct_of_attempts_base,
       failed_cur, round(failed_cur::DOUBLE / n_cur * 100, 3) AS pct_of_attempts_cur,
       round((failed_cur::DOUBLE / n_cur - failed_base::DOUBLE / n_base) * 100, 3) AS change_pp,
       top_seg.segment AS top_merchant_platform_cur, top_seg.n AS top_segment_failed_cur
FROM fc CROSS JOIN tot LEFT JOIN top_seg ON fc.failure_code = top_seg.failure_code
ORDER BY change_pp DESC
```
| failure_code | failed_base | pct_of_attempts_base | failed_cur | pct_of_attempts_cur | change_pp | top_merchant_platform_cur | top_segment_failed_cur |
|---|---|---|---|---|---|---|---|
| BANK_TIMEOUT | 113 | 0.749 | 338 | 2.117 | 1.368 | M0218 / ANDROID | 10 |
| NETWORK_ERROR | 49 | 0.325 | 155 | 0.971 | 0.646 | M0003 / ANDROID | 4 |
| ISSUER_UNAVAILABLE | 53 | 0.351 | 115 | 0.72 | 0.369 | M0045 / ANDROID | 5 |
| AUTH_FAILED | 98 | 0.65 | 123 | 0.77 | 0.121 | M0248 / POS_TERMINAL | 5 |
| INCORRECT_PIN | 128 | 0.848 | 154 | 0.965 | 0.116 | M0248 / POS_TERMINAL | 4 |
| GATEWAY_ERROR | 62 | 0.411 | 81 | 0.507 | 0.096 | M0003 / ANDROID | 3 |
| BANK_DECLINED | 116 | 0.769 | 132 | 0.827 | 0.058 | M0032 / POS_TERMINAL | 3 |
| INSUFFICIENT_FUNDS | 217 | 1.438 | 237 | 1.484 | 0.046 | M0137 / ANDROID | 6 |
| LIMIT_EXCEEDED | 76 | 0.504 | 86 | 0.539 | 0.035 | M0316 / POS_TERMINAL | 3 |
| INVALID_VPA | 26 | 0.172 | 33 | 0.207 | 0.034 | M0107 / POS_TERMINAL | 2 |
| CARD_EXPIRED | 7 | 0.046 | 10 | 0.063 | 0.016 | M0232 / POS_TERMINAL | 2 |
| (missing) | 73 | 0.484 | 78 | 0.489 | 0.005 | M0175 / POS_TERMINAL | 4 |
| RISK_DECLINED | 53 | 0.351 | 54 | 0.338 | -0.013 | M0003 / ANDROID | 2 |

### Q3 · Headline success rate and status metrics for previous week vs target week
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT CASE WHEN txn_date BETWEEN '2026-09-07' AND '2026-09-13' THEN '2026-09-07 to 2026-09-13' WHEN txn_date BETWEEN '2026-09-14' AND '2026-09-20' THEN '2026-09-14 to 2026-09-20' END AS period, COUNT(*) FILTER (WHERE status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED')) AS terminal_attempts, COUNT(*) FILTER (WHERE status = 'SUCCESS') AS success_count, COUNT(*) FILTER (WHERE status = 'FAILED') AS failed_count, COUNT(*) FILTER (WHERE status = 'USER_CANCELLED') AS cancelled_count, ROUND(COUNT(*) FILTER (WHERE status = 'SUCCESS')::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED')), 0), 4) AS success_rate, ROUND(COUNT(*) FILTER (WHERE status = 'FAILED')::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED')), 0), 4) AS failure_rate, ROUND(COUNT(*) FILTER (WHERE status = 'USER_CANCELLED')::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED')), 0), 4) AS cancellation_rate FROM valid_txn WHERE txn_date BETWEEN '2026-09-07' AND '2026-09-20' GROUP BY 1 ORDER BY 1
```
| period | terminal_attempts | success_count | failed_count | cancelled_count | success_rate | failure_rate | cancellation_rate |
|---|---|---|---|---|---|---|---|
| 2026-09-07 to 2026-09-13 | 15088 | 13651 | 1071 | 366 | 0.9048 | 0.071 | 0.0243 |
| 2026-09-14 to 2026-09-20 | 15965 | 13994 | 1596 | 375 | 0.8765 | 0.1 | 0.0235 |

### Q4 · Failure code comparison between previous week and target week
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT failure_code, COUNT(*) FILTER (WHERE txn_date BETWEEN '2026-09-07' AND '2026-09-13') AS failed_prev_week, COUNT(*) FILTER (WHERE txn_date BETWEEN '2026-09-14' AND '2026-09-20') AS failed_curr_week, COUNT(*) FILTER (WHERE txn_date BETWEEN '2026-09-14' AND '2026-09-20') - COUNT(*) FILTER (WHERE txn_date BETWEEN '2026-09-07' AND '2026-09-13') AS failed_diff FROM valid_txn WHERE status = 'FAILED' AND txn_date BETWEEN '2026-09-07' AND '2026-09-20' GROUP BY failure_code ORDER BY failed_diff DESC
```
| failure_code | failed_prev_week | failed_curr_week | failed_diff |
|---|---|---|---|
| BANK_TIMEOUT | 113 | 338 | 225 |
| NETWORK_ERROR | 49 | 155 | 106 |
| ISSUER_UNAVAILABLE | 53 | 115 | 62 |
| INCORRECT_PIN | 128 | 154 | 26 |
| AUTH_FAILED | 98 | 123 | 25 |
| INSUFFICIENT_FUNDS | 217 | 237 | 20 |
| GATEWAY_ERROR | 62 | 81 | 19 |
| BANK_DECLINED | 116 | 132 | 16 |
| LIMIT_EXCEEDED | 76 | 86 | 10 |
| INVALID_VPA | 26 | 33 | 7 |
| NULL | 73 | 78 | 5 |
| CARD_EXPIRED | 7 | 10 | 3 |
| RISK_DECLINED | 53 | 54 | 1 |

### Q5 · Daily success rate and failure code breakdown for SBI UPI transactions
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, b.short_name AS bank_name, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id) LEFT JOIN banks b USING (bank_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT txn_date, COUNT(*) FILTER (WHERE status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED')) AS terminal_attempts, COUNT(*) FILTER (WHERE status = 'SUCCESS') AS success_count, ROUND(COUNT(*) FILTER (WHERE status = 'SUCCESS')::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED')), 0), 4) AS success_rate, COUNT(*) FILTER (WHERE failure_code = 'BANK_TIMEOUT') AS bank_timeouts, COUNT(*) FILTER (WHERE failure_code = 'NETWORK_ERROR') AS network_errors, COUNT(*) FILTER (WHERE failure_code = 'ISSUER_UNAVAILABLE') AS issuer_unavailable FROM valid_txn WHERE bank_name = 'SBI' AND method_code = 'UPI' AND txn_date BETWEEN '2026-09-07' AND '2026-09-20' GROUP BY txn_date ORDER BY txn_date
```
| txn_date | terminal_attempts | success_count | success_rate | bank_timeouts | network_errors | issuer_unavailable |
|---|---|---|---|---|---|---|
| 2026-09-07 | 206 | 177 | 0.8592 | 2 | 0 | 2 |
| 2026-09-08 | 181 | 160 | 0.884 | 3 | 3 | 0 |
| 2026-09-09 | 222 | 195 | 0.8784 | 1 | 1 | 2 |
| 2026-09-10 | 200 | 176 | 0.88 | 2 | 2 | 3 |
| 2026-09-11 | 226 | 204 | 0.9027 | 0 | 1 | 0 |
| 2026-09-12 | 230 | 204 | 0.887 | 3 | 3 | 2 |
| 2026-09-13 | 236 | 194 | 0.822 | 5 | 2 | 2 |
| 2026-09-14 | 238 | 166 | 0.6975 | 21 | 11 | 4 |
| 2026-09-15 | 257 | 131 | 0.5097 | 54 | 24 | 11 |
| 2026-09-16 | 213 | 112 | 0.5258 | 43 | 15 | 13 |
| 2026-09-17 | 259 | 146 | 0.5637 | 47 | 26 | 13 |
| 2026-09-18 | 277 | 151 | 0.5451 | 47 | 30 | 16 |
| 2026-09-19 | 279 | 199 | 0.7133 | 30 | 12 | 9 |
| 2026-09-20 | 229 | 192 | 0.8384 | 8 | 4 | 2 |

### Q6 · Comparison of SBI UPI vs Non-SBI UPI vs SBI non-UPI across both weeks
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, b.short_name AS bank_name, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id) LEFT JOIN banks b USING (bank_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT CASE WHEN bank_name = 'SBI' AND method_code = 'UPI' THEN 'SBI - UPI' WHEN bank_name = 'SBI' AND method_code != 'UPI' THEN 'SBI - Non-UPI' WHEN (bank_name != 'SBI' OR bank_name IS NULL) AND method_code = 'UPI' THEN 'Non-SBI - UPI' ELSE 'Other' END AS segment, COUNT(*) FILTER (WHERE txn_date BETWEEN '2026-09-07' AND '2026-09-13' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS base_attempts, ROUND(COUNT(*) FILTER (WHERE txn_date BETWEEN '2026-09-07' AND '2026-09-13' AND status = 'SUCCESS')::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE txn_date BETWEEN '2026-09-07' AND '2026-09-13' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS base_sr, COUNT(*) FILTER (WHERE txn_date BETWEEN '2026-09-14' AND '2026-09-20' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS cur_attempts, ROUND(COUNT(*) FILTER (WHERE txn_date BETWEEN '2026-09-14' AND '2026-09-20' AND status = 'SUCCESS')::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE txn_date BETWEEN '2026-09-14' AND '2026-09-20' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS cur_sr FROM valid_txn WHERE txn_date BETWEEN '2026-09-07' AND '2026-09-20' GROUP BY 1 ORDER BY 1
```
| segment | base_attempts | base_sr | cur_attempts | cur_sr |
|---|---|---|---|---|
| Non-SBI - UPI | 5260 | 0.8865 | 5410 | 0.8867 |
| Other | 6834 | 0.9255 | 7197 | 0.9234 |
| SBI - Non-UPI | 1493 | 0.9062 | 1606 | 0.9054 |
| SBI - UPI | 1501 | 0.8728 | 1752 | 0.6261 |

---
_3 round(s) · 6/6 queries succeeded · 85.3s · gemini-3.6-flash · tokens in/out 30051/3708_