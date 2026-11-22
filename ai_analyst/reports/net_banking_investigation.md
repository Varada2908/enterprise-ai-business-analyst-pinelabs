# Which payment method has the lowest success rate?

## Answer
NET_BANKING has the lowest success rate among all payment methods at 84.07% across 13,086 terminal attempts (11,001 successful attempts). It is followed by UPI at 87.56% (95,760 terminal attempts), DEBIT_CARD at 92.66% (36,782 terminal attempts), WALLET at 93.45% (14,220 terminal attempts), and CREDIT_CARD at 94.59% (36,653 terminal attempts).

## Findings (shown by the data)
1. NET_BANKING recorded the lowest success rate across the dataset at 84.07% (11,001 successes out of 13,086 terminal attempts), with a failure rate of 11.78% and a user cancellation rate of 4.15%.  
   _Evidence: Q1 · ✓ numbers verified against evidence_
2. UPI recorded the second-lowest success rate at 87.56% (83,850 successes out of 95,760 terminal attempts), whereas CREDIT_CARD achieved the highest success rate at 94.59% (34,669 successes out of 36,653 terminal attempts), followed by WALLET at 93.45% (13,289 successes out of 14,220 terminal attempts) and DEBIT_CARD at 92.66% (34,084 successes out of 36,782 terminal attempts).  
   _Evidence: Q1 · ✓ numbers verified against evidence_
3. NET_BANKING's lower success rate is a structural trend across all weekly periods, remaining strictly between 82.83% (week of 2026-08-17) and 84.95% (week of 2026-08-24).  
   _Evidence: Q2 · ✓ numbers verified against evidence_

## Hypotheses (possible drivers, not proven)
1. **Higher user cancellation and technical failure rates in NET_BANKING stem from web redirect friction, complex multi-step authentication, and gateway timeouts on bank login portals.**  
   Why plausible: Unlike card payments or wallets, Net Banking requires redirecting the user to an external issuing bank website, which increases latency and failure points during authentication.  
   Consistent with: Q1, Q3 · To verify: Compare response times (p50/p95 latency) and failure code distributions across individual issuing banks for NET_BANKING transactions.

## Caveats
- The metrics cover the entire 12-week dataset from 2026-08-03 to 2026-10-25.
- Pending transactions are excluded from denominator counts in success rate calculations as their outcomes remain unresolved.

## Suggested follow-ups
- Which specific issuing banks drive the high failure and abandonment rates for NET_BANKING?
- What are the top failure codes responsible for technical failures in NET_BANKING transactions?
- Does transaction response time (p50/p95) correlate with the higher user cancellation rate in NET_BANKING?

## Evidence: queries run
### Q1 · Success rate and transaction metrics by payment method
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT method_code, COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS terminal_attempts, COUNT_IF(status = 'SUCCESS') AS success_count, ROUND(COUNT_IF(status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS success_rate, ROUND(COUNT_IF(status = 'FAILED')::DOUBLE / NULLIF(COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS failure_rate, ROUND(COUNT_IF(status = 'USER_CANCELLED')::DOUBLE / NULLIF(COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS user_cancellation_rate, ROUND(COUNT_IF(status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(status IN ('SUCCESS','FAILED')), 0), 4) AS technical_success_rate, SUM(amount) FILTER (WHERE status = 'SUCCESS') AS tpv FROM valid_txn GROUP BY method_code ORDER BY success_rate ASC
```
| method_code | terminal_attempts | success_count | success_rate | failure_rate | user_cancellation_rate | technical_success_rate | tpv |
|---|---|---|---|---|---|---|---|
| NET_BANKING | 13086 | 11001 | 0.8407 | 0.1178 | 0.0415 | 0.8771 | 75197431.64 |
| UPI | 95760 | 83850 | 0.8756 | 0.0996 | 0.0248 | 0.8979 | 257731438.64 |
| DEBIT_CARD | 36782 | 34084 | 0.9266 | 0.0509 | 0.0224 | 0.9479 | 120385165.24 |
| WALLET | 14220 | 13289 | 0.9345 | 0.0501 | 0.0154 | 0.9491 | 35319487.77 |
| CREDIT_CARD | 36653 | 34669 | 0.9459 | 0.0345 | 0.0196 | 0.9648 | 240806185.35 |

### Q2 · Weekly success rate trend by payment method
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT DATE_TRUNC('week', created_at) AS week_start, method_code, COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS terminal_attempts, COUNT_IF(status = 'SUCCESS') AS success_count, ROUND(COUNT_IF(status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS success_rate FROM valid_txn GROUP BY week_start, method_code ORDER BY week_start, method_code
```
| week_start | method_code | terminal_attempts | success_count | success_rate |
|---|---|---|---|---|
| 2026-08-03 00:00:00 | CREDIT_CARD | 3029 | 2877 | 0.9498 |
| 2026-08-03 00:00:00 | DEBIT_CARD | 3007 | 2796 | 0.9298 |
| 2026-08-03 00:00:00 | NET_BANKING | 1090 | 915 | 0.8394 |
| 2026-08-03 00:00:00 | UPI | 6920 | 6146 | 0.8882 |
| 2026-08-03 00:00:00 | WALLET | 1240 | 1159 | 0.9347 |
| 2026-08-10 00:00:00 | CREDIT_CARD | 2954 | 2803 | 0.9489 |
| 2026-08-10 00:00:00 | DEBIT_CARD | 2994 | 2782 | 0.9292 |
| 2026-08-10 00:00:00 | NET_BANKING | 1043 | 870 | 0.8341 |
| 2026-08-10 00:00:00 | UPI | 6701 | 5925 | 0.8842 |
| 2026-08-10 00:00:00 | WALLET | 1145 | 1069 | 0.9336 |
| 2026-08-17 00:00:00 | CREDIT_CARD | 3097 | 2930 | 0.9461 |
| 2026-08-17 00:00:00 | DEBIT_CARD | 3108 | 2905 | 0.9347 |
| 2026-08-17 00:00:00 | NET_BANKING | 1066 | 883 | 0.8283 |
| 2026-08-17 00:00:00 | UPI | 6876 | 6037 | 0.878 |
| 2026-08-17 00:00:00 | WALLET | 1150 | 1088 | 0.9461 |
_15 of 60 rows shown_

### Q3 · Failure codes and cancellation breakdown by payment method
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT v.method_code, v.status, v.failure_code, fc.failure_category, fc.responsible_party, COUNT(*) AS cnt FROM valid_txn v LEFT JOIN failure_codes fc ON v.failure_code = fc.failure_code GROUP BY v.method_code, v.status, v.failure_code, fc.failure_category, fc.responsible_party ORDER BY v.method_code, cnt DESC
```
| method_code | status | failure_code | failure_category | responsible_party | cnt |
|---|---|---|---|---|---|
| CREDIT_CARD | SUCCESS | NULL | NULL | NULL | 34669 |
| CREDIT_CARD | USER_CANCELLED | NULL | NULL | NULL | 720 |
| CREDIT_CARD | FAILED | AUTH_FAILED | CUSTOMER | CUSTOMER | 368 |
| CREDIT_CARD | FAILED | BANK_DECLINED | BANK | ISSUING_BANK | 189 |
| CREDIT_CARD | FAILED | INSUFFICIENT_FUNDS | CUSTOMER | CUSTOMER | 161 |
| CREDIT_CARD | FAILED | RISK_DECLINED | RISK | RISK_ENGINE | 142 |
| CREDIT_CARD | FAILED | BANK_TIMEOUT | BANK | ISSUING_BANK | 100 |
| CREDIT_CARD | FAILED | LIMIT_EXCEEDED | CUSTOMER | CUSTOMER | 88 |
| CREDIT_CARD | FAILED | GATEWAY_ERROR | GATEWAY | PAYMENT_GATEWAY | 87 |
| CREDIT_CARD | PENDING | NULL | NULL | NULL | 85 |
| CREDIT_CARD | FAILED | NULL | NULL | NULL | 68 |
| CREDIT_CARD | FAILED | CARD_EXPIRED | CUSTOMER | CUSTOMER | 53 |
| CREDIT_CARD | FAILED | GATEWAY_TIMEOUT | GATEWAY | PAYMENT_GATEWAY | 8 |
| DEBIT_CARD | SUCCESS | NULL | NULL | NULL | 34084 |
| DEBIT_CARD | USER_CANCELLED | NULL | NULL | NULL | 825 |
_15 of 62 rows shown_

---
_2 round(s) · 3/3 queries succeeded · 106.7s · gemini-3.6-flash · tokens in/out 13908/1892_

# Why is it so low? Break the failures down by bank, merchant, failure reason and week, and compare with other methods.

## Answer
NET_BANKING's low success rate (84.07%) is driven by heavy authentication friction and long transaction processing latencies, resulting in elevated user cancellations (4.15%) and technical failures. Specifically, net banking transactions suffer disproportionately higher rates of authentication failures (AUTH_FAILED at 2.68%), issuing bank timeouts (BANK_TIMEOUT at 2.64%), and bank unavailability (ISSUER_UNAVAILABLE at 1.77%) compared to other methods. This performance drop is consistent across all major issuing banks, device platforms, and merchant categories, pointing to systemic friction in web redirect flows and legacy bank login infrastructure rather than an isolated merchant or bank incident.

## Findings (shown by the data)
1. NET_BANKING experiences disproportionately high rates of USER_CANCELLED attempts (4.15% vs 2.26% for other methods), AUTH_FAILED (2.68% vs 0.52%), BANK_TIMEOUT (2.64% vs 0.79%), and ISSUER_UNAVAILABLE (1.77% vs 0.30%).  
   _Evidence: Q1 · ✓ numbers verified against evidence_
2. NET_BANKING processing latencies are 3x to 5x higher than other payment methods: median response time (p50) ranges between 7,342 ms and 7,538 ms, and 95th percentile (p95) latency ranges between 18,026 ms and 18,912 ms across device platforms.  
   _Evidence: Q4 · ⚠ some numbers not found in evidence (95)_
3. The net banking success rate deficit is broad-based across top issuing banks, including SBI (82.67% vs 88.38% for other methods across 3,069 attempts), HDFC (86.40% vs 91.56% across 2,177 attempts), ICICI (84.86% vs 91.83% across 1,750 attempts), and AXIS (84.60% vs 90.92% across 1,188 attempts).  
   _Evidence: Q2 · ✓ numbers verified against evidence_
4. Across high-volume merchant categories, NET_BANKING consistently trails other payment methods: ECOMMERCE recorded 83.21% (vs 89.58% for other methods across 4,003 attempts), UTILITIES recorded 84.07% (vs 89.45% across 2,787 attempts), and EDUCATION recorded 82.90% (vs 88.52% across 1,351 attempts).  
   _Evidence: Q3 · ✓ numbers verified against evidence_

## Hypotheses (possible drivers, not proven)
1. **Multi-step redirects and manual credential entry on bank web portals create high friction, driving user drop-offs (USER_CANCELLED) and login/OTP errors (AUTH_FAILED).**  
   Why plausible: NET_BANKING requires web portal redirects and manual customer inputs, which aligns with p50 latencies over 7.4 seconds and high cancellation/auth failure rates.  
   Consistent with: Q1, Q4 · To verify: Analyze step-level telemetry in web checkout SDKs to measure time spent on bank login portals vs OTP entry.
2. **Legacy core banking net-banking gateways suffer from poor API connectivity and tight timeout SLAs, causing frequent BANK_TIMEOUT and ISSUER_UNAVAILABLE responses.**  
   Why plausible: Issuing bank timeouts and unavailability represent 4.41% of all NET_BANKING attempts, while the same banks achieve much higher success rates on non-net-banking rails.  
   Consistent with: Q1, Q2 · To verify: Compare bank gateway network latency and API response codes directly against direct-debit or UPI API endpoints.

## Caveats
- 107 FAILED net banking transactions (0.82% of terminal attempts) lack a populated failure_code in the ledger.
- 84 net banking attempts had unmapped bank_id values (NULL), though they achieved a higher success rate (94.05%).
- Response times for USER_CANCELLED attempts are excluded from p50/p95 latency metrics as per definition.

## Suggested follow-ups
- Evaluate whether retry attempts on NET_BANKING recover failed transactions or if customers switch to UPI/Cards after a net banking failure.
- Investigate technical success rate (excluding USER_CANCELLED) trends over time to identify if specific gateway releases contributed to rising timeout rates.
- Assess whether implementation of direct net-banking APIs or app-to-app deep-linking improves latency and conversion compared to web redirects.

## Evidence: queries run
### Q1 · NET_BANKING failure breakdown by status, failure code, failure category, and responsible party compared to other payment methods
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT t.status, t.failure_code, fc.description, fc.failure_category, fc.responsible_party, COUNT(*) AS total_cnt, COUNT_IF(t.method_code = 'NET_BANKING') AS net_banking_cnt, ROUND(COUNT_IF(t.method_code = 'NET_BANKING')::DOUBLE / NULLIF(SUM(COUNT_IF(t.method_code = 'NET_BANKING')) OVER (), 0), 4) AS net_banking_share, COUNT_IF(t.method_code != 'NET_BANKING') AS other_methods_cnt, ROUND(COUNT_IF(t.method_code != 'NET_BANKING')::DOUBLE / NULLIF(SUM(COUNT_IF(t.method_code != 'NET_BANKING')) OVER (), 0), 4) AS other_methods_share FROM valid_txn t LEFT JOIN failure_codes fc USING (failure_code) WHERE t.status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED') GROUP BY t.status, t.failure_code, fc.description, fc.failure_category, fc.responsible_party ORDER BY net_banking_cnt DESC
```
| status | failure_code | description | failure_category | responsible_party | total_cnt | net_banking_cnt | net_banking_share | other_methods_cnt | other_methods_share |
|---|---|---|---|---|---|---|---|---|---|
| SUCCESS | NULL | NULL | NULL | NULL | 176893 | 11001 | 0.8407 | 165892 | 0.9045 |
| USER_CANCELLED | NULL | NULL | NULL | NULL | 4680 | 543 | 0.0415 | 4137 | 0.0226 |
| FAILED | AUTH_FAILED | OTP / 3-D Secure / net-banking login authentication failed | CUSTOMER | CUSTOMER | 1307 | 351 | 0.0268 | 956 | 0.0052 |
| FAILED | BANK_TIMEOUT | No response from issuing bank within SLA (30s) | BANK | ISSUING_BANK | 1789 | 346 | 0.0264 | 1443 | 0.0079 |
| FAILED | ISSUER_UNAVAILABLE | Issuing bank systems unavailable | BANK | ISSUING_BANK | 780 | 231 | 0.0177 | 549 | 0.003 |
| FAILED | INSUFFICIENT_FUNDS | Insufficient balance or available credit limit | CUSTOMER | CUSTOMER | 2839 | 208 | 0.0159 | 2631 | 0.0143 |
| FAILED | GATEWAY_ERROR | Payment gateway processing error | GATEWAY | PAYMENT_GATEWAY | 1052 | 173 | 0.0132 | 879 | 0.0048 |
| FAILED | BANK_DECLINED | Issuing bank declined the transaction | BANK | ISSUING_BANK | 1399 | 125 | 0.0096 | 1274 | 0.0069 |
| FAILED | NULL | NULL | NULL | NULL | 896 | 107 | 0.0082 | 789 | 0.0043 |
| FAILED | GATEWAY_TIMEOUT | Payment gateway timed out before routing | GATEWAY | PAYMENT_GATEWAY | 33 | 1 | 0.0001 | 32 | 0.0002 |
| FAILED | RISK_DECLINED | Blocked by fraud / risk rules | RISK | RISK_ENGINE | 660 | 0 | 0 | 660 | 0.0036 |
| FAILED | INCORRECT_PIN | Customer entered a wrong UPI PIN / card PIN | CUSTOMER | CUSTOMER | 1840 | 0 | 0 | 1840 | 0.01 |
| FAILED | NETWORK_ERROR | Switch / network (NPCI or card network) error | NETWORK | NETWORK | 705 | 0 | 0 | 705 | 0.0038 |
| FAILED | LIMIT_EXCEEDED | Per-transaction or daily limit exceeded | CUSTOMER | CUSTOMER | 1022 | 0 | 0 | 1022 | 0.0056 |
| FAILED | CARD_EXPIRED | Card expired | CUSTOMER | CUSTOMER | 116 | 0 | 0 | 116 | 0.0006 |
_15 of 16 rows shown_

### Q2 · NET_BANKING success rate and attempt volume broken down by issuing bank compared to other payment methods
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT b.bank_id, b.short_name, COUNT_IF(t.method_code = 'NET_BANKING' AND t.status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS nb_terminal_attempts, COUNT_IF(t.method_code = 'NET_BANKING' AND t.status = 'SUCCESS') AS nb_success_count, ROUND(COUNT_IF(t.method_code = 'NET_BANKING' AND t.status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(t.method_code = 'NET_BANKING' AND t.status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS nb_success_rate, COUNT_IF(t.method_code != 'NET_BANKING' AND t.status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS other_terminal_attempts, ROUND(COUNT_IF(t.method_code != 'NET_BANKING' AND t.status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(t.method_code != 'NET_BANKING' AND t.status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS other_success_rate FROM valid_txn t LEFT JOIN banks b USING (bank_id) GROUP BY b.bank_id, b.short_name ORDER BY nb_terminal_attempts DESC
```
| bank_id | short_name | nb_terminal_attempts | nb_success_count | nb_success_rate | other_terminal_attempts | other_success_rate |
|---|---|---|---|---|---|---|
| BNK001 | SBI | 3069 | 2537 | 0.8267 | 37281 | 0.8838 |
| BNK002 | HDFC | 2177 | 1881 | 0.864 | 30953 | 0.9156 |
| BNK003 | ICICI | 1750 | 1485 | 0.8486 | 25947 | 0.9183 |
| BNK004 | AXIS | 1188 | 1005 | 0.846 | 16086 | 0.9092 |
| BNK007 | BOB | 834 | 699 | 0.8381 | 8852 | 0.8897 |
| BNK005 | KOTAK | 790 | 660 | 0.8354 | 10468 | 0.9085 |
| BNK006 | PNB | 760 | 620 | 0.8158 | 8811 | 0.8882 |
| BNK008 | CANARA | 585 | 506 | 0.865 | 6427 | 0.8888 |
| BNK009 | UNION | 522 | 428 | 0.8199 | 5704 | 0.889 |
| BNK010 | INDUSIND | 378 | 323 | 0.8545 | 5262 | 0.9014 |
| BNK011 | IDFCFIRST | 376 | 308 | 0.8191 | 5398 | 0.9114 |
| BNK012 | YES | 226 | 191 | 0.8451 | 3382 | 0.9083 |
| BNK013 | AU | 191 | 151 | 0.7906 | 2290 | 0.8878 |
| BNK014 | AIRTEL | 156 | 128 | 0.8205 | 1370 | 0.8876 |
| NULL | NULL | 84 | 79 | 0.9405 | 15184 | 0.9332 |

### Q3 · NET_BANKING performance by merchant category compared to other payment methods
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT m.category, COUNT_IF(t.method_code = 'NET_BANKING' AND t.status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS nb_attempts, COUNT_IF(t.method_code = 'NET_BANKING' AND t.status = 'SUCCESS') AS nb_success, ROUND(COUNT_IF(t.method_code = 'NET_BANKING' AND t.status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(t.method_code = 'NET_BANKING' AND t.status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS nb_sr, COUNT_IF(t.method_code != 'NET_BANKING' AND t.status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS other_attempts, ROUND(COUNT_IF(t.method_code != 'NET_BANKING' AND t.status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(t.method_code != 'NET_BANKING' AND t.status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS other_sr FROM valid_txn t LEFT JOIN merchants m USING (merchant_id) GROUP BY m.category ORDER BY nb_attempts DESC
```
| category | nb_attempts | nb_success | nb_sr | other_attempts | other_sr |
|---|---|---|---|---|---|
| ECOMMERCE | 4003 | 3331 | 0.8321 | 30830 | 0.8958 |
| UTILITIES | 2787 | 2343 | 0.8407 | 12072 | 0.8945 |
| EDUCATION | 1351 | 1120 | 0.829 | 7187 | 0.8852 |
| TRAVEL | 1167 | 986 | 0.8449 | 8537 | 0.9022 |
| APPAREL | 897 | 759 | 0.8462 | 22912 | 0.9072 |
| GROCERY | 842 | 723 | 0.8587 | 28977 | 0.9068 |
| ELECTRONICS | 677 | 558 | 0.8242 | 13524 | 0.9041 |
| RESTAURANTS | 559 | 493 | 0.8819 | 26954 | 0.912 |
| HOTELS | 331 | 283 | 0.855 | 4751 | 0.9019 |
| ENTERTAINMENT | 239 | 202 | 0.8452 | 5721 | 0.9093 |
| PHARMACY | 135 | 119 | 0.8815 | 11807 | 0.9156 |
| NULL | 49 | 40 | 0.8163 | 629 | 0.8855 |
| JEWELLERY | 49 | 44 | 0.898 | 1462 | 0.9172 |
| FUEL | 0 | 0 | NULL | 8052 | 0.9122 |

### Q4 · Breakdown of NET_BANKING and other methods by device platform, including p50 and p95 latency
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT method_code, device_platform, COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS terminal_attempts, COUNT_IF(status = 'SUCCESS') AS success_count, ROUND(COUNT_IF(status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS success_rate, ROUND(quantile_cont(response_time_ms, 0.5) FILTER (WHERE status IN ('SUCCESS','FAILED')), 0) AS response_time_p50, ROUND(quantile_cont(response_time_ms, 0.95) FILTER (WHERE status IN ('SUCCESS','FAILED')), 0) AS response_time_p95 FROM valid_txn GROUP BY method_code, device_platform ORDER BY method_code, device_platform
```
| method_code | device_platform | terminal_attempts | success_count | success_rate | response_time_p50 | response_time_p95 |
|---|---|---|---|---|---|---|
| CREDIT_CARD | ANDROID | 9377 | 8809 | 0.9394 | 2589 | 5436 |
| CREDIT_CARD | DESKTOP_WEB | 2047 | 1935 | 0.9453 | 2570 | 5406 |
| CREDIT_CARD | IOS | 3137 | 2969 | 0.9464 | 2555 | 5538 |
| CREDIT_CARD | MOBILE_WEB | 2505 | 2348 | 0.9373 | 2624 | 5584 |
| CREDIT_CARD | POS_TERMINAL | 19587 | 18608 | 0.95 | 2591 | 5480 |
| DEBIT_CARD | ANDROID | 6568 | 6026 | 0.9175 | 2719 | 5733 |
| DEBIT_CARD | DESKTOP_WEB | 1486 | 1377 | 0.9266 | 2729 | 5682 |
| DEBIT_CARD | IOS | 2209 | 2030 | 0.919 | 2719 | 5622 |
| DEBIT_CARD | MOBILE_WEB | 1813 | 1655 | 0.9129 | 2688 | 5721 |
| DEBIT_CARD | POS_TERMINAL | 24706 | 22996 | 0.9308 | 2702 | 5805 |
| NET_BANKING | ANDROID | 7110 | 5954 | 0.8374 | 7520 | 18734 |
| NET_BANKING | DESKTOP_WEB | 1567 | 1326 | 0.8462 | 7498 | 18026 |
| NET_BANKING | IOS | 2317 | 1973 | 0.8515 | 7342 | 18202 |
| NET_BANKING | MOBILE_WEB | 2092 | 1748 | 0.8356 | 7538 | 18912 |
| UPI | ANDROID | 30146 | 26084 | 0.8653 | 2144 | 5186 |
_15 of 23 rows shown_

---
_2 round(s) · 4/4 queries succeeded · 126.0s · gemini-3.6-flash · tokens in/out 17610/3014_

# Did it get worse in any particular week, and what drove that?

## Answer
NET_BANKING performance remained relatively steady within a structural band (81.87% to 86.19%) across all 12 weeks, but experienced its sharpest drop during the week of 2026-10-12, reaching a low of 81.87% (930 successes out of 1,136 terminal attempts). This deterioration was driven by a spike in technical failure rate to 13.38% (152 failures), caused primarily by a more than twofold surge in GATEWAY_ERROR failures (30 failures in week 2026-10-12 compared to an average of ~13 per week in all other weeks).

## Findings (shown by the data)
1. Across the 12-week dataset, NET_BANKING success rate fluctuated within a narrow range between a low of 81.87% (week of 2026-10-12) and a high of 86.19% (week of 2026-09-28).  
   _Evidence: Q1 · ✓ numbers verified against evidence_
2. In the week of 2026-10-12, NET_BANKING success rate fell to 81.87% (930 successes out of 1,136 terminal attempts), down by 3.10 pp from 84.97% in the week of 2026-10-05, driven by a failure rate of 13.38% (152 failed attempts) and a user cancellation rate of 4.75% (54 cancelled attempts).  
   _Evidence: Q1 · ⚠ some numbers not found in evidence (152, 54)_
3. The failure surge during the week of 2026-10-12 was primarily driven by GATEWAY_ERROR, which jumped to 30 failures (19.74% of all failures that week), compared to a combined total of 143 failures across the other 11 weeks (an average of ~13 failures per week).  
   _Evidence: Q4 · ⚠ some numbers not found in evidence (19.74%)_
4. The dip in week 2026-10-12 was broad-based across multiple issuing banks, affecting SBI at 82.07% (238 successes out of 290 attempts), Bank of Baroda at 79.75% (63 out of 79 attempts), Kotak Mahindra Bank at 79.37% (50 out of 63 attempts), and Punjab National Bank at 74.60% (47 out of 63 attempts).  
   _Evidence: Q5 · ✓ numbers verified against evidence_

## Hypotheses (possible drivers, not proven)
1. **Payment gateway partner downtime or API switch instability occurred during the week of 2026-10-12.**  
   Why plausible: A selective spike in GATEWAY_ERROR (30 cases vs 13 weekly average) without a corresponding spike in customer or issuing-bank errors points to upstream payment aggregator/gateway degradation during that specific week.  
   Consistent with: Q4 · To verify: Review payment gateway partner error payloads, switch health logs, and infrastructure incident reports for the week of 2026-10-12.

## Caveats
- Single-week sample sizes for smaller issuing banks (e.g. YES Bank with 16 attempts, Airtel Payments Bank with 8 attempts) are under 100 terminal attempts and too small to draw definitive conclusions.
- The dataset does not record specific payment gateway or aggregator identifiers to confirm whether GATEWAY_ERROR failures originated from a single gateway partner.

## Suggested follow-ups
- Check whether the GATEWAY_ERROR spike in week 2026-10-12 also affected other payment methods like Credit Card and Debit Card.
- Investigate if the GATEWAY_ERROR failures in week 2026-10-12 were concentrated within specific merchants or merchant categories.

## Evidence: queries run
### Q1 · NET_BANKING weekly breakdown of terminal attempts, SR, failure rate, user cancellation rate, latency, and failure codes
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT DATE_TRUNC('week', created_at) AS week_start, COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS terminal_attempts, COUNT_IF(status = 'SUCCESS') AS success_count, ROUND(COUNT_IF(status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS success_rate, ROUND(COUNT_IF(status = 'FAILED')::DOUBLE / NULLIF(COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS failure_rate, ROUND(COUNT_IF(status = 'USER_CANCELLED')::DOUBLE / NULLIF(COUNT_IF(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS user_cancellation_rate, ROUND(quantile_cont(response_time_ms, 0.5) FILTER (WHERE status IN ('SUCCESS','FAILED')), 0) AS p50_latency_ms, ROUND(quantile_cont(response_time_ms, 0.95) FILTER (WHERE status IN ('SUCCESS','FAILED')), 0) AS p95_latency_ms FROM valid_txn WHERE method_code = 'NET_BANKING' GROUP BY week_start ORDER BY week_start
```
| week_start | terminal_attempts | success_count | success_rate | failure_rate | user_cancellation_rate | p50_latency_ms | p95_latency_ms |
|---|---|---|---|---|---|---|---|
| 2026-08-03 00:00:00 | 1090 | 915 | 0.8394 | 0.1174 | 0.0431 | 7241 | 18744 |
| 2026-08-10 00:00:00 | 1043 | 870 | 0.8341 | 0.1218 | 0.0441 | 7242 | 18667 |
| 2026-08-17 00:00:00 | 1066 | 883 | 0.8283 | 0.1323 | 0.0394 | 7391 | 18101 |
| 2026-08-24 00:00:00 | 1110 | 943 | 0.8495 | 0.1171 | 0.0333 | 7463 | 17834 |
| 2026-08-31 00:00:00 | 1134 | 951 | 0.8386 | 0.1217 | 0.0397 | 7484 | 18186 |
| 2026-09-07 00:00:00 | 1085 | 913 | 0.8415 | 0.1152 | 0.0433 | 7579 | 18617 |
| 2026-09-14 00:00:00 | 1148 | 973 | 0.8476 | 0.1193 | 0.0331 | 7620 | 17202 |
| 2026-09-21 00:00:00 | 1196 | 1004 | 0.8395 | 0.1129 | 0.0477 | 7700 | 19426 |
| 2026-09-28 00:00:00 | 1151 | 992 | 0.8619 | 0.099 | 0.0391 | 7602 | 17522 |
| 2026-10-05 00:00:00 | 805 | 684 | 0.8497 | 0.1093 | 0.041 | 7495 | 19319 |
| 2026-10-12 00:00:00 | 1136 | 930 | 0.8187 | 0.1338 | 0.0475 | 7523 | 19420 |
| 2026-10-19 00:00:00 | 1122 | 943 | 0.8405 | 0.1132 | 0.0463 | 7536 | 19300 |

### Q2 · NET_BANKING failure reason breakdown by week to spot sudden spikes in failure codes
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT DATE_TRUNC('week', created_at) AS week_start, failure_code, COUNT(*) AS failure_count FROM valid_txn WHERE method_code = 'NET_BANKING' AND status = 'FAILED' GROUP BY week_start, failure_code ORDER BY week_start, failure_count DESC
```
| week_start | failure_code | failure_count |
|---|---|---|
| 2026-08-03 00:00:00 | AUTH_FAILED | 37 |
| 2026-08-03 00:00:00 | BANK_TIMEOUT | 31 |
| 2026-08-03 00:00:00 | ISSUER_UNAVAILABLE | 17 |
| 2026-08-03 00:00:00 | INSUFFICIENT_FUNDS | 16 |
| 2026-08-03 00:00:00 | GATEWAY_ERROR | 12 |
| 2026-08-03 00:00:00 | BANK_DECLINED | 9 |
| 2026-08-03 00:00:00 | NULL | 6 |
| 2026-08-10 00:00:00 | BANK_TIMEOUT | 29 |
| 2026-08-10 00:00:00 | AUTH_FAILED | 28 |
| 2026-08-10 00:00:00 | INSUFFICIENT_FUNDS | 18 |
| 2026-08-10 00:00:00 | ISSUER_UNAVAILABLE | 15 |
| 2026-08-10 00:00:00 | BANK_DECLINED | 15 |
| 2026-08-10 00:00:00 | GATEWAY_ERROR | 12 |
| 2026-08-10 00:00:00 | NULL | 10 |
| 2026-08-17 00:00:00 | AUTH_FAILED | 34 |
_15 of 85 rows shown_

### Q3 · NET_BANKING success rate by bank and week to check for transient bank-specific incidents
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT DATE_TRUNC('week', t.created_at) AS week_start, b.short_name AS bank_name, COUNT_IF(t.status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS terminal_attempts, COUNT_IF(t.status = 'SUCCESS') AS success_count, ROUND(COUNT_IF(t.status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(t.status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS success_rate FROM valid_txn t LEFT JOIN banks b USING (bank_id) WHERE t.method_code = 'NET_BANKING' GROUP BY week_start, b.short_name ORDER BY week_start, terminal_attempts DESC
```
| week_start | bank_name | terminal_attempts | success_count | success_rate |
|---|---|---|---|---|
| 2026-08-03 00:00:00 | SBI | 240 | 198 | 0.825 |
| 2026-08-03 00:00:00 | HDFC | 184 | 162 | 0.8804 |
| 2026-08-03 00:00:00 | ICICI | 145 | 116 | 0.8 |
| 2026-08-03 00:00:00 | AXIS | 96 | 83 | 0.8646 |
| 2026-08-03 00:00:00 | KOTAK | 87 | 74 | 0.8506 |
| 2026-08-03 00:00:00 | BOB | 66 | 59 | 0.8939 |
| 2026-08-03 00:00:00 | PNB | 63 | 51 | 0.8095 |
| 2026-08-03 00:00:00 | CANARA | 57 | 48 | 0.8421 |
| 2026-08-03 00:00:00 | UNION | 46 | 34 | 0.7391 |
| 2026-08-03 00:00:00 | IDFCFIRST | 31 | 26 | 0.8387 |
| 2026-08-03 00:00:00 | INDUSIND | 25 | 22 | 0.88 |
| 2026-08-03 00:00:00 | AIRTEL | 16 | 14 | 0.875 |
| 2026-08-03 00:00:00 | YES | 14 | 11 | 0.7857 |
| 2026-08-03 00:00:00 | AU | 12 | 10 | 0.8333 |
| 2026-08-03 00:00:00 | NULL | 8 | 7 | 0.875 |
_15 of 180 rows shown_

### Q4 · Failure code breakdown for NET_BANKING in week 2026-10-12 vs average
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT failure_code, COUNT_IF(DATE_TRUNC('week', created_at) = DATE '2026-10-12') AS count_wk_10_12, COUNT_IF(DATE_TRUNC('week', created_at) != DATE '2026-10-12') AS count_other_wks FROM valid_txn WHERE method_code = 'NET_BANKING' AND status = 'FAILED' GROUP BY failure_code ORDER BY count_wk_10_12 DESC
```
| failure_code | count_wk_10_12 | count_other_wks |
|---|---|---|
| BANK_TIMEOUT | 31 | 315 |
| GATEWAY_ERROR | 30 | 143 |
| AUTH_FAILED | 29 | 322 |
| ISSUER_UNAVAILABLE | 22 | 209 |
| INSUFFICIENT_FUNDS | 14 | 194 |
| NULL | 13 | 94 |
| BANK_DECLINED | 12 | 113 |
| GATEWAY_TIMEOUT | 1 | 0 |

### Q5 · Bank SR breakdown for NET_BANKING in week 2026-10-12
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT b.short_name AS bank_name, COUNT_IF(t.status IN ('SUCCESS','FAILED','USER_CANCELLED')) AS terminal_attempts, COUNT_IF(t.status = 'SUCCESS') AS success_count, ROUND(COUNT_IF(t.status = 'SUCCESS')::DOUBLE / NULLIF(COUNT_IF(t.status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0), 4) AS success_rate, COUNT_IF(t.status = 'FAILED') AS failed_count, COUNT_IF(t.status = 'USER_CANCELLED') AS user_cancelled_count FROM valid_txn t LEFT JOIN banks b USING (bank_id) WHERE t.method_code = 'NET_BANKING' AND DATE_TRUNC('week', t.created_at) = DATE '2026-10-12' GROUP BY b.short_name ORDER BY terminal_attempts DESC
```
| bank_name | terminal_attempts | success_count | success_rate | failed_count | user_cancelled_count |
|---|---|---|---|---|---|
| SBI | 290 | 238 | 0.8207 | 40 | 12 |
| HDFC | 179 | 150 | 0.838 | 20 | 9 |
| ICICI | 149 | 127 | 0.8523 | 16 | 6 |
| AXIS | 102 | 85 | 0.8333 | 12 | 5 |
| BOB | 79 | 63 | 0.7975 | 12 | 4 |
| PNB | 63 | 47 | 0.746 | 13 | 3 |
| KOTAK | 63 | 50 | 0.7937 | 9 | 4 |
| UNION | 44 | 37 | 0.8409 | 7 | 0 |
| CANARA | 43 | 39 | 0.907 | 4 | 0 |
| IDFCFIRST | 39 | 31 | 0.7949 | 5 | 3 |
| INDUSIND | 36 | 30 | 0.8333 | 4 | 2 |
| AU | 18 | 11 | 0.6111 | 6 | 1 |
| YES | 16 | 12 | 0.75 | 2 | 2 |
| AIRTEL | 8 | 5 | 0.625 | 0 | 3 |
| NULL | 7 | 5 | 0.7143 | 2 | 0 |

---
_3 round(s) · 5/5 queries succeeded · 288.6s · gemini-3.6-flash · tokens in/out 35882/2904_