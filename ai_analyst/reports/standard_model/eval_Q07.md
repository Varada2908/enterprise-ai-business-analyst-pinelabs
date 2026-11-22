# Which merchant's success rate deteriorated the most in the last two weeks compared with the two weeks before (merchants with at least 150 attempts in each window), and why?

## Answer
Merchant M0137 experienced the largest success rate deterioration, with its daily success rate dropping sharply on October 12, 2026 to 72.37% from 90.32% on October 11, 2026. The decline was concentrated on the ANDROID platform, where the success rate dropped 27.01 percentage points from 89.07% (554/622) to 62.05% (399/643), compared to minor changes on iOS (down 0.31 percentage points from 90.69% to 90.37%), Mobile Web (down 2.14 percentage points from 87.30% to 85.16%), and Desktop Web (down 2.14 percentage points from 92.05% to 89.92%). For M0137 overall, GATEWAY_ERROR failures spiked from 5 in the baseline period to 147 in the current period (+142), and GATEWAY_TIMEOUT spiked from 0 to 33 (+33). It is hypothesized that an Android-specific application or gateway integration release triggered these failure spikes.

_✓ Every number in the answer is stated in a verified finding._

_The first draft of this answer failed the answer check and was rewritten._

## Findings (shown by the data)
1. M0137 (Zestora Online Mart) had the highest SR deterioration among qualifying merchants (>=150 attempts in both periods), declining 16.35 percentage points from 89.45% (1,043/1,166) to 73.10% (807/1,104). The second worst merchant was M0027 (Riverbend Threads) with a 3.21 percentage point decline (91.03% to 87.82%).  
   _Evidence: Q3 · ⚠ some numbers not found in evidence (150)_
2. M0137's SR deterioration was isolated to the ANDROID platform, which dropped 27.01 percentage points from 89.07% (554/622) to 62.05% (399/643). iOS dropped slightly by 0.31 percentage points (90.69% to 90.37%), Mobile Web by 2.14 percentage points (87.30% to 85.16%), and Desktop Web by 2.14 percentage points (92.05% to 89.92%).  
   _Evidence: Q5 · ✓ numbers verified against evidence_
3. For M0137 overall, GATEWAY_ERROR failures spiked from 5 in the baseline period to 147 in the current period (+142), and GATEWAY_TIMEOUT spiked from 0 to 33 (+33).  
   _Evidence: Q6 · ✓ numbers verified against evidence_
4. Out of 147 total GATEWAY_ERROR failures for M0137 in the current period, 142 occurred on ANDROID; all 33 GATEWAY_TIMEOUT failures occurred on ANDROID.  
   _Evidence: Q7 · ⚠ some numbers not found in evidence (147)_
5. M0137's daily success rate dropped sharply on October 12, 2026 to 72.37% (with 9 GATEWAY_ERROR and 1 GATEWAY_TIMEOUT failures) from 90.32% on October 11, 2026, remaining depressed throughout the current period.  
   _Evidence: Q8 · ✓ numbers verified against evidence_

## Hypotheses (possible drivers, not proven)
1. **An Android application update or SDK integration release by Zestora Online Mart on or around October 12, 2026 introduced network/gateway request payload or timeout defects.**  
   Why plausible: The failure surge was isolated specifically to the ANDROID platform starting on October 12, 2026, manifesting as gateway errors and timeouts, while iOS and web surfaces on the same merchant were unaffected.  
   Consistent with: Q5, Q7, Q8 · To verify: Check Zestora Online Mart's Android app release logs and SDK telemetry around October 11–12, 2026.

## Caveats
- In the current period, 21 FAILED transactions for M0137 (20 of which occurred on ANDROID) were missing a failure_code.
- The metrics represent attempt-level success rates (retries count as separate terminal attempts).

## Suggested follow-ups
- Drill down into M0137 ANDROID transactions by app version if available in client metadata.
- Check if any other merchants using the same payment gateway SDK version on Android experienced similar GATEWAY_ERROR spikes.
- Evaluate order conversion rate for M0137 to assess how effectively retries recovered these gateway failures.

## Evidence: queries run
### Q1 · [driver scan] segments ranked by impact on SR change (2026-09-28..2026-10-11 vs 2026-10-12..2026-10-25)
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
                WHEN v.txn_date BETWEEN DATE '2026-09-28' AND DATE '2026-10-11' THEN 'base' END AS period
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
| merchant | M0137 Zestora Online Mart | 1166 | 0.8945 | 1104 | 0.731 | -0.1635 | -0.556 | 181 | -0.087 | 6.4057 |
| merchant x platform | M0137 / ANDROID | 622 | 0.8907 | 643 | 0.6205 | -0.2701 | -0.535 | 174 | -0.087 | 6.1632 |
| payment_method | UPI | 25333 | 0.8806 | 14811 | 0.8702 | -0.0104 | -0.476 | 154 | -0.087 | 5.4792 |
| merchant_category | ECOMMERCE | 7218 | 0.8904 | 5755 | 0.8636 | -0.0268 | -0.475 | 154 | -0.087 | 5.4756 |
| channel | ONLINE | 23137 | 0.8894 | 18490 | 0.8819 | -0.0076 | -0.43 | 140 | -0.087 | 4.959 |
| device_platform | ANDROID | 12711 | 0.8885 | 10198 | 0.8769 | -0.0116 | -0.364 | 118 | -0.087 | 4.1919 |
| issuing_bank | SBI | 8587 | 0.8951 | 6507 | 0.8787 | -0.0163 | -0.327 | 106 | -0.087 | 3.7697 |
| bank x method | SBI / UPI | 5900 | 0.8815 | 3374 | 0.8577 | -0.0238 | -0.247 | 80 | -0.087 | 2.8479 |
| payment_method | NET_BANKING | 1956 | 0.8569 | 2258 | 0.8295 | -0.0274 | -0.19 | 62 | -0.087 | 2.1916 |
| payment_method | CREDIT_CARD | 5528 | 0.9474 | 6495 | 0.9404 | -0.0069 | -0.139 | 45 | -0.087 | 1.6 |
| bank x method | SBI / DEBIT_CARD | 1267 | 0.9361 | 1478 | 0.91 | -0.0261 | -0.119 | 39 | -0.087 | 1.3664 |
| bank x method | ICICI / UPI | 3492 | 0.8958 | 2145 | 0.8844 | -0.0114 | -0.075 | 24 | -0.087 | 0.866 |
| bank x method | AXIS / UPI | 2225 | 0.8885 | 1346 | 0.8707 | -0.0178 | -0.074 | 24 | -0.087 | 0.8506 |
| device_platform | DESKTOP_WEB | 2885 | 0.8984 | 2101 | 0.8886 | -0.0098 | -0.064 | 21 | -0.087 | 0.7317 |
| payment_method | DEBIT_CARD | 5707 | 0.931 | 6467 | 0.9286 | -0.0024 | -0.048 | 16 | -0.087 | 0.5511 |
_15 of 30 rows shown_

### Q2 · [driver scan] failure-reason shift (2026-09-28..2026-10-11 vs 2026-10-12..2026-10-25)
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
                WHEN v.txn_date BETWEEN DATE '2026-09-28' AND DATE '2026-10-11' THEN 'base' END AS period
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
| GATEWAY_ERROR | 185 | 0.454 | 298 | 0.918 | 0.463 | M0137 / ANDROID | 142 |
| AUTH_FAILED | 181 | 0.445 | 230 | 0.708 | 0.264 | M0248 / POS_TERMINAL | 6 |
| GATEWAY_TIMEOUT | 0 | 0 | 33 | 0.102 | 0.102 | M0137 / ANDROID | 33 |
| CARD_EXPIRED | 18 | 0.044 | 27 | 0.083 | 0.039 | M0237 / POS_TERMINAL | 3 |
| (missing) | 185 | 0.454 | 148 | 0.456 | 0.001 | M0137 / ANDROID | 20 |
| BANK_TIMEOUT | 342 | 0.84 | 262 | 0.807 | -0.033 | M0137 / ANDROID | 5 |
| ISSUER_UNAVAILABLE | 152 | 0.373 | 110 | 0.339 | -0.035 | M0030 / ANDROID | 3 |
| RISK_DECLINED | 146 | 0.359 | 102 | 0.314 | -0.044 | M0175 / ANDROID | 5 |
| INCORRECT_PIN | 452 | 1.11 | 327 | 1.007 | -0.103 | M0045 / ANDROID | 8 |
| LIMIT_EXCEEDED | 242 | 0.594 | 155 | 0.477 | -0.117 | M0045 / ANDROID | 4 |
| INVALID_VPA | 142 | 0.349 | 75 | 0.231 | -0.118 | M0248 / POS_TERMINAL | 3 |
| BANK_DECLINED | 305 | 0.749 | 200 | 0.616 | -0.133 | M0218 / POS_TERMINAL | 5 |
| INSUFFICIENT_FUNDS | 623 | 1.53 | 453 | 1.395 | -0.135 | M0003 / ANDROID | 10 |
| NETWORK_ERROR | 175 | 0.43 | 88 | 0.271 | -0.159 | M0175 / POS_TERMINAL | 4 |

### Q3 · Identify merchants with >=150 attempts in both windows and rank by SR deterioration
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000), merchant_periods AS (SELECT v.merchant_id, m.merchant_name, m.category, count_if(txn_date BETWEEN '2026-09-28' AND '2026-10-11' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS baseline_attempts, count_if(txn_date BETWEEN '2026-09-28' AND '2026-10-11' AND status = 'SUCCESS') AS baseline_success, count_if(txn_date BETWEEN '2026-10-12' AND '2026-10-25' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS current_attempts, count_if(txn_date BETWEEN '2026-10-12' AND '2026-10-25' AND status = 'SUCCESS') AS current_success FROM valid_txn v LEFT JOIN merchants m ON v.merchant_id = m.merchant_id GROUP BY v.merchant_id, m.merchant_name, m.category) SELECT merchant_id, merchant_name, category, baseline_attempts, baseline_success, ROUND(baseline_success::DOUBLE / NULLIF(baseline_attempts, 0), 4) AS baseline_sr, current_attempts, current_success, ROUND(current_success::DOUBLE / NULLIF(current_attempts, 0), 4) AS current_sr, ROUND((current_success::DOUBLE / NULLIF(current_attempts, 0)) - (baseline_success::DOUBLE / NULLIF(baseline_attempts, 0)), 4) AS sr_change, ROUND((baseline_success::DOUBLE / NULLIF(baseline_attempts, 0)) - (current_success::DOUBLE / NULLIF(current_attempts, 0)), 4) AS sr_deterioration FROM merchant_periods WHERE baseline_attempts >= 150 AND current_attempts >= 150 ORDER BY sr_deterioration DESC LIMIT 20
```
| merchant_id | merchant_name | category | baseline_attempts | baseline_success | baseline_sr | current_attempts | current_success | current_sr | sr_change | sr_deterioration |
|---|---|---|---|---|---|---|---|---|---|---|
| M0137 | Zestora Online Mart | ECOMMERCE | 1166 | 1043 | 0.8945 | 1104 | 807 | 0.731 | -0.1635 | 0.1635 |
| M0027 | Riverbend Threads | APPAREL | 301 | 274 | 0.9103 | 238 | 209 | 0.8782 | -0.0321 | 0.0321 |
| M0172 | Amber Electronics | ELECTRONICS | 412 | 385 | 0.9345 | 323 | 293 | 0.9071 | -0.0273 | 0.0273 |
| M0280 | Jharokha Bazaar | ECOMMERCE | 260 | 230 | 0.8846 | 202 | 174 | 0.8614 | -0.0232 | 0.0232 |
| M0079 | Riverbend Gadgets | ELECTRONICS | 312 | 274 | 0.8782 | 277 | 237 | 0.8556 | -0.0226 | 0.0226 |
| M0308 | Tulsi Travels Hyderabad | TRAVEL | 189 | 174 | 0.9206 | 170 | 153 | 0.9 | -0.0206 | 0.0206 |
| M0077 | Ekam Dhaba | RESTAURANTS | 202 | 181 | 0.896 | 199 | 175 | 0.8794 | -0.0166 | 0.0166 |
| M0004 | Banyan Residency | HOTELS | 388 | 349 | 0.8995 | 291 | 258 | 0.8866 | -0.0129 | 0.0129 |
| M0144 | Sunrise Medicos Hyderabad | PHARMACY | 224 | 210 | 0.9375 | 184 | 171 | 0.9293 | -0.0082 | 0.0082 |
| M0031 | Rangoli Learning Pune | EDUCATION | 454 | 410 | 0.9031 | 354 | 317 | 0.8955 | -0.0076 | 0.0076 |
| M0251 | Aarohi Travels | TRAVEL | 247 | 220 | 0.8907 | 198 | 175 | 0.8838 | -0.0068 | 0.0068 |
| M0381 | Sunrise Gadgets | ELECTRONICS | 227 | 210 | 0.9251 | 184 | 169 | 0.9185 | -0.0066 | 0.0066 |
| M0117 | Hilltop Academy | EDUCATION | 195 | 167 | 0.8564 | 180 | 153 | 0.85 | -0.0064 | 0.0064 |
| M0283 | Anokhi Kitchen | RESTAURANTS | 730 | 661 | 0.9055 | 567 | 510 | 0.8995 | -0.006 | 0.006 |
| M0232 | Chandan Fashions | APPAREL | 442 | 395 | 0.8937 | 351 | 312 | 0.8889 | -0.0048 | 0.0048 |
_15 of 20 rows shown_

### Q4 · Verify dataset transaction date boundaries and total valid transactions count
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT MIN(created_at) AS min_created_at, MAX(created_at) AS max_created_at, COUNT(*) AS total_valid_txns FROM valid_txn
```
| min_created_at | max_created_at | total_valid_txns |
|---|---|---|
| 2026-08-03 00:00:58.829000 | 2026-10-25 23:59:49.105000 | 196982 |

### Q5 · M0137 breakdown by device platform across baseline and current period
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT device_platform, count_if(txn_date BETWEEN '2026-09-28' AND '2026-10-11' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS base_attempts, count_if(txn_date BETWEEN '2026-09-28' AND '2026-10-11' AND status = 'SUCCESS') AS base_success, ROUND(count_if(txn_date BETWEEN '2026-09-28' AND '2026-10-11' AND status = 'SUCCESS')::DOUBLE / NULLIF(count_if(txn_date BETWEEN '2026-09-28' AND '2026-10-11' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS base_sr, count_if(txn_date BETWEEN '2026-10-12' AND '2026-10-25' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS cur_attempts, count_if(txn_date BETWEEN '2026-10-12' AND '2026-10-25' AND status = 'SUCCESS') AS cur_success, ROUND(count_if(txn_date BETWEEN '2026-10-12' AND '2026-10-25' AND status = 'SUCCESS')::DOUBLE / NULLIF(count_if(txn_date BETWEEN '2026-10-12' AND '2026-10-25' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS cur_sr, ROUND((count_if(txn_date BETWEEN '2026-10-12' AND '2026-10-25' AND status = 'SUCCESS')::DOUBLE / NULLIF(count_if(txn_date BETWEEN '2026-10-12' AND '2026-10-25' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0)) - (count_if(txn_date BETWEEN '2026-09-28' AND '2026-10-11' AND status = 'SUCCESS')::DOUBLE / NULLIF(count_if(txn_date BETWEEN '2026-09-28' AND '2026-10-11' AND status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0)), 4) AS sr_change FROM valid_txn WHERE merchant_id = 'M0137' GROUP BY device_platform ORDER BY base_attempts DESC
```
| device_platform | base_attempts | base_success | base_sr | cur_attempts | cur_success | cur_sr | sr_change |
|---|---|---|---|---|---|---|---|
| ANDROID | 622 | 554 | 0.8907 | 643 | 399 | 0.6205 | -0.2701 |
| IOS | 204 | 185 | 0.9069 | 187 | 169 | 0.9037 | -0.0031 |
| MOBILE_WEB | 189 | 165 | 0.873 | 155 | 132 | 0.8516 | -0.0214 |
| DESKTOP_WEB | 151 | 139 | 0.9205 | 119 | 107 | 0.8992 | -0.0214 |

### Q6 · M0137 failure codes breakdown across baseline and current period
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT status, COALESCE(failure_code, '(NONE/MISSING)') AS failure_code, count_if(txn_date BETWEEN '2026-09-28' AND '2026-10-11') AS base_count, count_if(txn_date BETWEEN '2026-10-12' AND '2026-10-25') AS cur_count, count_if(txn_date BETWEEN '2026-10-12' AND '2026-10-25') - count_if(txn_date BETWEEN '2026-09-28' AND '2026-10-11') AS count_change FROM valid_txn WHERE merchant_id = 'M0137' AND status IN ('FAILED', 'USER_CANCELLED') GROUP BY status, COALESCE(failure_code, '(NONE/MISSING)') ORDER BY cur_count DESC
```
| status | failure_code | base_count | cur_count | count_change |
|---|---|---|---|---|
| FAILED | GATEWAY_ERROR | 5 | 147 | 142 |
| FAILED | GATEWAY_TIMEOUT | 0 | 33 | 33 |
| USER_CANCELLED | (NONE/MISSING) | 26 | 30 | 4 |
| FAILED | INSUFFICIENT_FUNDS | 28 | 21 | -7 |
| FAILED | (NONE/MISSING) | 6 | 21 | 15 |
| FAILED | INCORRECT_PIN | 13 | 9 | -4 |
| FAILED | BANK_DECLINED | 15 | 9 | -6 |
| FAILED | BANK_TIMEOUT | 8 | 8 | 0 |
| FAILED | AUTH_FAILED | 1 | 5 | 4 |
| FAILED | LIMIT_EXCEEDED | 4 | 5 | 1 |
| FAILED | ISSUER_UNAVAILABLE | 6 | 3 | -3 |
| FAILED | RISK_DECLINED | 5 | 2 | -3 |
| FAILED | INVALID_VPA | 3 | 2 | -1 |
| FAILED | NETWORK_ERROR | 3 | 2 | -1 |
| FAILED | CARD_EXPIRED | 0 | 0 | 0 |

### Q7 · M0137 failure codes by device platform in current period
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT device_platform, COALESCE(failure_code, status) AS failure_or_status, COUNT(*) AS failed_cnt FROM valid_txn WHERE merchant_id = 'M0137' AND status IN ('FAILED', 'USER_CANCELLED') AND txn_date BETWEEN '2026-10-12' AND '2026-10-25' GROUP BY device_platform, COALESCE(failure_code, status) ORDER BY device_platform, failed_cnt DESC
```
| device_platform | failure_or_status | failed_cnt |
|---|---|---|
| ANDROID | GATEWAY_ERROR | 142 |
| ANDROID | GATEWAY_TIMEOUT | 33 |
| ANDROID | FAILED | 20 |
| ANDROID | USER_CANCELLED | 12 |
| ANDROID | INSUFFICIENT_FUNDS | 10 |
| ANDROID | INCORRECT_PIN | 5 |
| ANDROID | BANK_TIMEOUT | 5 |
| ANDROID | AUTH_FAILED | 4 |
| ANDROID | BANK_DECLINED | 4 |
| ANDROID | LIMIT_EXCEEDED | 4 |
| ANDROID | INVALID_VPA | 2 |
| ANDROID | NETWORK_ERROR | 2 |
| ANDROID | RISK_DECLINED | 1 |
| DESKTOP_WEB | USER_CANCELLED | 4 |
| DESKTOP_WEB | GATEWAY_ERROR | 2 |
_15 of 36 rows shown_

### Q8 · M0137 daily SR trend to check timing of deterioration
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT txn_date, count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS attempts, count_if(status = 'SUCCESS') AS success_cnt, count_if(status = 'FAILED' AND failure_code = 'GATEWAY_ERROR') AS gateway_error_cnt, count_if(status = 'FAILED' AND failure_code = 'GATEWAY_TIMEOUT') AS gateway_timeout_cnt, ROUND(count_if(status = 'SUCCESS')::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS daily_sr FROM valid_txn WHERE merchant_id = 'M0137' AND txn_date BETWEEN '2026-09-28' AND '2026-10-25' GROUP BY txn_date ORDER BY txn_date
```
| txn_date | attempts | success_cnt | gateway_error_cnt | gateway_timeout_cnt | daily_sr |
|---|---|---|---|---|---|
| 2026-09-28 | 71 | 66 | 0 | 0 | 0.9296 |
| 2026-09-29 | 57 | 54 | 0 | 0 | 0.9474 |
| 2026-09-30 | 66 | 57 | 1 | 0 | 0.8636 |
| 2026-10-01 | 61 | 56 | 0 | 0 | 0.918 |
| 2026-10-02 | 70 | 62 | 0 | 0 | 0.8857 |
| 2026-10-03 | 107 | 99 | 0 | 0 | 0.9252 |
| 2026-10-04 | 65 | 59 | 1 | 0 | 0.9077 |
| 2026-10-05 | 90 | 76 | 0 | 0 | 0.8444 |
| 2026-10-06 | 81 | 74 | 1 | 0 | 0.9136 |
| 2026-10-07 | 85 | 78 | 0 | 0 | 0.9176 |
| 2026-10-08 | 88 | 77 | 1 | 0 | 0.875 |
| 2026-10-09 | 100 | 85 | 0 | 0 | 0.85 |
| 2026-10-10 | 132 | 116 | 1 | 0 | 0.8788 |
| 2026-10-11 | 93 | 84 | 0 | 0 | 0.9032 |
| 2026-10-12 | 76 | 55 | 9 | 1 | 0.7237 |
_15 of 28 rows shown_

---
_3 round(s) · 8/8 queries succeeded · 123.4s · gemini-3.6-flash · tokens in/out 51966/5207_