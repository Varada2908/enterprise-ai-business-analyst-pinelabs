# Payment failures seem to have gone up since mid-October. What is driving it?

## Answer
Payment failures since mid-October were primarily driven by a sharp drop in success rate for merchant M0137 (Zestora Online Mart) on Android, where failure rates jumped from ~7-9% in August-September to over 23-25% from October 12 onwards due to a surge in gateway errors.

## Findings (shown by the data)
1. Overall payment success rate dropped after mid-October, with the change heavily concentrated in merchant M0137 (Zestora Online Mart), where success rate fell by 17.32 pp from 90.42% to 73.10%.  
   _Evidence: Q1 · ✓ numbers verified against evidence_
2. The issue at merchant M0137 was primarily concentrated on the Android platform, where the success rate dropped by 28.72 pp from 90.77% to 62.05%.  
   _Evidence: Q1 · ✓ numbers verified against evidence_
3. Failure code GATEWAY_ERROR surged by 0.458 pp as a share of attempts after mid-October, with M0137 on Android contributing the highest volume (142 failed attempts).  
   _Evidence: Q2 · ✓ numbers verified against evidence_
4. Weekly failure rates for merchant M0137 spiked dramatically starting the week of 2026-10-12 to 25.14% and the week of 2026-10-19 to 23.23%, compared to historical levels around 5-10%.  
   _Evidence: Q4 · ✓ numbers verified against evidence_

## Hypotheses (possible drivers, not proven)
1. **A recent Android app release or API integration change by merchant M0137 introduced gateway errors.**  
   Why plausible: The failure spike is isolated to a single merchant (M0137) and a specific platform (Android), coinciding with the week of October 12.  
   Consistent with: Q1, Q4 · To verify: Check merchant M0137's deployment logs and API integration release notes around October 12.
2. **Gateway routing issues or capacity limits affected requests originating from M0137's Android app.**  
   Why plausible: GATEWAY_ERROR and GATEWAY_TIMEOUT were the primary failure codes driving the surge.  
   Consistent with: Q2 · To verify: Inspect payment gateway logs for requests originating from M0137 during the affected weeks.

## Caveats
- Analysis is bounded to the 12-week extract window ending 2026-10-25.
- Attribution relies on valid transaction records populated with merchant and device platform metadata.

## Suggested follow-ups
- Contact merchant M0137 technical support regarding their Android app version updates around October 12.
- Review payment gateway server logs for M0137 integration requests.
- Check whether other merchants using similar SDK versions experienced similar error spikes.

## Evidence: queries run
### Q1 · [driver scan] segments ranked by impact on SR change (2026-08-03..2026-10-11 vs 2026-10-12..2026-10-25)
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
           CASE WHEN v.txn_date BETWEEN DATE '2026-10-12' AND DATE '2026-10-25' THEN 'cur'
                WHEN v.txn_date BETWEEN DATE '2026-08-03' AND DATE '2026-10-11' THEN 'base' END AS period
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
tot AS (SELECT count_if(period = 'cur') AS total_cur FROM base),
scored AS (
    SELECT dimension, segment, n_base, round(s_base::DOUBLE / n_base, 4) AS sr_baseline,
           n_cur, round(s_cur::DOUBLE / n_cur, 4) AS sr_current,
           round(s_cur::DOUBLE / n_cur - s_base::DOUBLE / n_base, 4) AS sr_change,
           round(n_cur::DOUBLE / total_cur * (s_cur::DOUBLE / n_cur - s_base::DOUBLE / n_base) * 100, 3) AS impact_on_overall_sr_pp,
           round((s_base::DOUBLE / n_base - s_cur::DOUBLE / n_cur) * n_cur) AS excess_unsuccessful_attempts
    FROM piv CROSS JOIN tot
    WHERE n_base >= 100 AND n_cur >= 100
)
SELECT * FROM scored
QUALIFY row_number() OVER (PARTITION BY dimension ORDER BY impact_on_overall_sr_pp ASC) <= 4
ORDER BY impact_on_overall_sr_pp ASC
LIMIT 40
```
| dimension | segment | n_base | sr_baseline | n_cur | sr_current | sr_change | impact_on_overall_sr_pp | excess_unsuccessful_attempts |
|---|---|---|---|---|---|---|---|---|
| merchant | M0137 Zestora Online Mart | 5064 | 0.9042 | 1104 | 0.731 | -0.1732 | -0.589 | 191 |
| merchant x platform | M0137 / ANDROID | 2775 | 0.9077 | 643 | 0.6205 | -0.2872 | -0.569 | 185 |
| channel | ONLINE | 93086 | 0.8914 | 18490 | 0.8819 | -0.0096 | -0.544 | 177 |
| merchant_category | ECOMMERCE | 29078 | 0.8935 | 5755 | 0.8636 | -0.0299 | -0.529 | 172 |
| device_platform | ANDROID | 50826 | 0.89 | 10198 | 0.8769 | -0.0131 | -0.411 | 134 |
| payment_method | UPI | 80949 | 0.8766 | 14811 | 0.8702 | -0.0065 | -0.295 | 96 |
| payment_method | CREDIT_CARD | 30158 | 0.947 | 6495 | 0.9404 | -0.0066 | -0.133 | 43 |
| device_platform | MOBILE_WEB | 14161 | 0.8862 | 2729 | 0.8739 | -0.0123 | -0.103 | 34 |
| payment_method | NET_BANKING | 10828 | 0.843 | 2258 | 0.8295 | -0.0135 | -0.094 | 30 |
| bank x method | ICICI / UPI | 11324 | 0.8961 | 2145 | 0.8844 | -0.0117 | -0.077 | 25 |
| merchant_category | RESTAURANTS | 22926 | 0.9122 | 4587 | 0.9071 | -0.0051 | -0.072 | 23 |
| bank x method | SBI / DEBIT_CARD | 6994 | 0.9254 | 1478 | 0.91 | -0.0154 | -0.07 | 23 |
| issuing_bank | ICICI | 23037 | 0.9147 | 4660 | 0.9099 | -0.0048 | -0.069 | 23 |
| bank x method | AXIS / UPI | 7191 | 0.8849 | 1346 | 0.8707 | -0.0141 | -0.059 | 19 |
| issuing_bank | YES | 3021 | 0.9096 | 587 | 0.8773 | -0.0323 | -0.058 | 19 |
_15 of 30 rows shown_

### Q2 · [driver scan] failure-reason shift (2026-08-03..2026-10-11 vs 2026-10-12..2026-10-25)
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
           CASE WHEN v.txn_date BETWEEN DATE '2026-10-12' AND DATE '2026-10-25' THEN 'cur'
                WHEN v.txn_date BETWEEN DATE '2026-08-03' AND DATE '2026-10-11' THEN 'base' END AS period
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
| GATEWAY_ERROR | 754 | 0.46 | 298 | 0.918 | 0.458 | M0137 / ANDROID | 142 |
| GATEWAY_TIMEOUT | 0 | 0 | 33 | 0.102 | 0.102 | M0137 / ANDROID | 33 |
| INCORRECT_PIN | 1513 | 0.922 | 327 | 1.007 | 0.085 | M0045 / ANDROID | 8 |
| AUTH_FAILED | 1077 | 0.657 | 230 | 0.708 | 0.052 | M0248 / POS_TERMINAL | 6 |
| CARD_EXPIRED | 89 | 0.054 | 27 | 0.083 | 0.029 | M0237 / POS_TERMINAL | 3 |
| (missing) | 748 | 0.456 | 148 | 0.456 | -0 | M0137 / ANDROID | 20 |
| INVALID_VPA | 415 | 0.253 | 75 | 0.231 | -0.022 | M0248 / POS_TERMINAL | 3 |
| RISK_DECLINED | 558 | 0.34 | 102 | 0.314 | -0.026 | M0175 / ANDROID | 5 |
| LIMIT_EXCEEDED | 867 | 0.529 | 155 | 0.477 | -0.051 | M0045 / ANDROID | 4 |
| INSUFFICIENT_FUNDS | 2386 | 1.455 | 453 | 1.395 | -0.06 | M0003 / ANDROID | 10 |
| ISSUER_UNAVAILABLE | 670 | 0.408 | 110 | 0.339 | -0.07 | M0030 / ANDROID | 3 |
| NETWORK_ERROR | 617 | 0.376 | 88 | 0.271 | -0.105 | M0175 / POS_TERMINAL | 4 |
| BANK_DECLINED | 1199 | 0.731 | 200 | 0.616 | -0.115 | M0218 / POS_TERMINAL | 5 |
| BANK_TIMEOUT | 1527 | 0.931 | 262 | 0.807 | -0.124 | M0137 / ANDROID | 5 |

### Q3 · Check weekly terminal attempts, failure rates, and user cancellation rates across the 12 weeks to see the exact trend around mid-October.
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT date_trunc('week', created_at) AS txn_week, COUNT(*) AS terminal_attempts, COUNT_IF(status = 'SUCCESS') AS success_count, COUNT_IF(status = 'FAILED') AS failed_count, ROUND(COUNT_IF(status = 'FAILED')::DOUBLE / NULLIF(COUNT(*), 0), 4) AS failure_rate, ROUND(COUNT_IF(status = 'SUCCESS')::DOUBLE / NULLIF(COUNT(*), 0), 4) AS success_rate FROM valid_txn WHERE status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED') GROUP BY 1 ORDER BY 1
```
| txn_week | terminal_attempts | success_count | failed_count | failure_rate | success_rate |
|---|---|---|---|---|---|
| 2026-08-03 00:00:00 | 15286 | 13893 | 1070 | 0.07 | 0.9089 |
| 2026-08-10 00:00:00 | 14837 | 13449 | 1027 | 0.0692 | 0.9065 |
| 2026-08-17 00:00:00 | 15297 | 13843 | 1077 | 0.0704 | 0.9049 |
| 2026-08-24 00:00:00 | 15281 | 13820 | 1111 | 0.0727 | 0.9044 |
| 2026-08-31 00:00:00 | 15421 | 13899 | 1173 | 0.0761 | 0.9013 |
| 2026-09-07 00:00:00 | 15088 | 13651 | 1071 | 0.071 | 0.9048 |
| 2026-09-14 00:00:00 | 15965 | 13994 | 1596 | 0.1 | 0.8765 |
| 2026-09-21 00:00:00 | 16141 | 14613 | 1147 | 0.0711 | 0.9053 |
| 2026-09-28 00:00:00 | 16602 | 15057 | 1147 | 0.0691 | 0.9069 |
| 2026-10-05 00:00:00 | 24111 | 21525 | 2001 | 0.083 | 0.8927 |
| 2026-10-12 00:00:00 | 16217 | 14548 | 1260 | 0.0777 | 0.8971 |
| 2026-10-19 00:00:00 | 16255 | 14601 | 1248 | 0.0768 | 0.8982 |

### Q4 · Check weekly terminal attempts and failure rates specifically for merchant M0137 across the 12 weeks.
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT date_trunc('week', created_at) AS txn_week, COUNT(*) AS terminal_attempts, COUNT_IF(status = 'SUCCESS') AS success_count, COUNT_IF(status = 'FAILED') AS failed_count, ROUND(COUNT_IF(status = 'FAILED')::DOUBLE / NULLIF(COUNT(*), 0), 4) AS failure_rate FROM valid_txn WHERE merchant_id = 'M0137' AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED') GROUP BY 1 ORDER BY 1
```
| txn_week | terminal_attempts | success_count | failed_count | failure_rate |
|---|---|---|---|---|
| 2026-08-03 00:00:00 | 471 | 424 | 34 | 0.0722 |
| 2026-08-10 00:00:00 | 447 | 415 | 24 | 0.0537 |
| 2026-08-17 00:00:00 | 499 | 454 | 38 | 0.0762 |
| 2026-08-24 00:00:00 | 515 | 471 | 35 | 0.068 |
| 2026-08-31 00:00:00 | 500 | 455 | 36 | 0.072 |
| 2026-09-07 00:00:00 | 470 | 430 | 30 | 0.0638 |
| 2026-09-14 00:00:00 | 478 | 419 | 49 | 0.1025 |
| 2026-09-21 00:00:00 | 518 | 468 | 40 | 0.0772 |
| 2026-09-28 00:00:00 | 497 | 453 | 36 | 0.0724 |
| 2026-10-05 00:00:00 | 669 | 590 | 61 | 0.0912 |
| 2026-10-12 00:00:00 | 553 | 400 | 139 | 0.2514 |
| 2026-10-19 00:00:00 | 551 | 407 | 128 | 0.2323 |

---
_3 round(s) · 4/4 queries succeeded · 11.4s · gemini-3.5-flash-lite · tokens in/out 25083/1741_