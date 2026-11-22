# Data catalog - payments analytics dataset

Synthetic dataset. All merchants are fictional; bank names are used only as realistic dimension values.

## Conventions

- **timezone**: All timestamps are IST (Asia/Kolkata), stored without offset.
- **currency**: All amounts are INR.
- **week**: Weeks run Monday-Sunday: date_trunc('week', created_at). The extract covers 12 full weeks.
- **date_range**: 2026-08-03 to 2026-10-25 (transactions); extract taken 2026-10-26 10:00 IST.
- **default_time_window**: If a question does not specify a period, state the assumption explicitly (e.g. 'last full week' = the latest Monday-Sunday week in the data).

## Relationships

- `transactions.merchant_id -> merchants.merchant_id (many-to-one)`
- `transactions.bank_id -> banks.bank_id (many-to-one; NULL for WALLET)`
- `transactions.payment_method_id -> payment_methods.payment_method_id (many-to-one)`
- `transactions.failure_code -> failure_codes.failure_code (many-to-one)`
- `transactions.parent_transaction_id -> transactions.transaction_id (retry chain)`
- `transactions.order_id groups attempts of one checkout`
- `settlements.merchant_id -> merchants.merchant_id (many-to-one)`
- `settlements (merchant_id, transaction_date) <- transactions (merchant_id, DATE(created_at)) for status = SUCCESS`
- `merchants.settlement_bank_id / settlements.settlement_bank_id -> banks.bank_id`

## Validity rules

- **R1** - transaction_id is expected to be unique. If several rows share one, keep only the row with the latest updated_at (the most recent state of the attempt).
- **R2** - A valid status is one of SUCCESS, FAILED, USER_CANCELLED, PENDING (exact, upper case). Rows with any other value are excluded from metrics and reported as data-quality issues.
- **R3** - A valid amount satisfies 0 < amount <= 1,000,000. Rows with NULL or out-of-range amounts are excluded from metrics.
- **R4** - Rows passing R1-R3 are 'valid transactions'. All metrics below are computed on valid transactions.
- **R5** - merchant_id / bank_id that are NULL (non-wallet) or not present in the dimension table are data-quality issues. Such rows still count in company-wide metrics but cannot be attributed to a merchant / bank; report how many were unattributable.
- **R6** - bank_id IS NULL for WALLET payments is expected, not a defect.
- **R7** - A FAILED row should have a failure_code; SUCCESS, USER_CANCELLED and PENDING rows should not.
- **R8** - Settlement reconciliation tolerance is INR 1.00 per batch (per-transaction rounding).

## Tables

### `transactions`

Payment attempt ledger. One row per payment attempt (a retry is a new attempt).

Grain: one row per transaction_id (attempt). Primary key: `transaction_id`.

| Column | Type | Nullable | Description | Allowed values / notes |
|---|---|---|---|---|
| `transaction_id` | VARCHAR | no | Unique identifier of the payment attempt. | Expected unique; see validity rule R1 |
| `order_id` | VARCHAR | no | Checkout / order the attempt belongs to. All retries of one checkout share it. |  |
| `attempt_number` | INTEGER | no | 1 for the first attempt of an order, 2+ for retries. | 1-3 |
| `parent_transaction_id` | VARCHAR | yes | The attempt this one retries. NULL when attempt_number = 1. | FK -> transactions.transaction_id |
| `customer_id` | VARCHAR | no | Pseudonymous payer identifier. |  |
| `merchant_id` | VARCHAR | yes | Merchant receiving the payment. | FK -> merchants.merchant_id |
| `payment_method_id` | VARCHAR | no | Payment instrument used. | FK -> payment_methods.payment_method_id |
| `bank_id` | VARCHAR | yes | Issuing bank of the payer's instrument. NULL by design for WALLET payments. | FK -> banks.bank_id |
| `card_network` | VARCHAR | yes | Card scheme for card payments; NULL otherwise. | VISA, MASTERCARD, RUPAY |
| `channel` | VARCHAR | no | Where the payment was initiated. | ONLINE, POS |
| `device_platform` | VARCHAR | no | Customer device / acceptance surface. | ANDROID, IOS, MOBILE_WEB, DESKTOP_WEB (ONLINE); POS_TERMINAL (POS) |
| `amount` | DECIMAL(14,2) | yes | Attempted amount in INR. | Valid range 0 < amount <= 1,000,000; UPI capped at 100,000 |
| `currency` | VARCHAR | no | Currency code. | INR |
| `status` | VARCHAR | no | Final status of the attempt. | SUCCESS, FAILED, USER_CANCELLED, PENDING |
| `failure_code` | VARCHAR | yes | Reason for failure. Populated only when status = FAILED. | FK -> failure_codes.failure_code |
| `response_time_ms` | INTEGER | yes | Milliseconds from initiation to final status. NULL while PENDING. For USER_CANCELLED it is time until abandonment. |  |
| `created_at` | TIMESTAMP | no | Attempt initiation time, IST (Asia/Kolkata), no offset stored. | 2026-08-03 to 2026-10-25 |
| `updated_at` | TIMESTAMP | no | Time of the last status change of this record, IST. | >= created_at |

### `merchants`

Merchant master: profile, commercial terms and settlement terms.

Grain: one row per merchant. Primary key: `merchant_id`.

| Column | Type | Nullable | Description | Allowed values / notes |
|---|---|---|---|---|
| `merchant_id` | VARCHAR | no | Merchant identifier. |  |
| `merchant_name` | VARCHAR | no | Trading name (fictional). |  |
| `category` | VARCHAR | no | Business category. | GROCERY, RESTAURANTS, FUEL, APPAREL, ELECTRONICS, PHARMACY, TRAVEL, HOTELS, EDUCATION, UTILITIES, ECOMMERCE, ENTERTAINMENT, JEWELLERY |
| `mcc` | VARCHAR | no | ISO 18245 merchant category code. |  |
| `business_model` | VARCHAR | no | Acceptance model. | ONLINE, OFFLINE, OMNICHANNEL |
| `city` | VARCHAR | no | Registered city. |  |
| `state` | VARCHAR | no | Registered state. |  |
| `region` | VARCHAR | no | Sales region. | NORTH, SOUTH, EAST, WEST, CENTRAL |
| `city_tier` | INTEGER | no | 1 = metro, 2 = tier-2 city. | 1, 2 |
| `merchant_size` | VARCHAR | no | Segment by processed volume. | ENTERPRISE, MID_MARKET, SMALL |
| `onboarding_date` | DATE | no | Date the merchant went live. No transactions exist before it. |  |
| `pricing_plan` | VARCHAR | no | Commercial plan. | ENTERPRISE, GROWTH, STANDARD |
| `mdr_discount_pct` | INTEGER | no | Contracted discount (%) on the payment method's standard MDR. | 0-30 |
| `settlement_cycle_days` | INTEGER | no | Payout cycle: settlement_date = transaction_date + N days (Sunday rolls to Monday). | 1, 2 |
| `settlement_bank_id` | VARCHAR | no | Bank holding the merchant's payout account. | FK -> banks.bank_id |
| `kyc_status` | VARCHAR | no | KYC status. | VERIFIED |
| `is_active` | BOOLEAN | no | Merchant currently active. |  |

### `banks`

Issuing / settlement banks.

Grain: one row per bank. Primary key: `bank_id`.

| Column | Type | Nullable | Description | Allowed values / notes |
|---|---|---|---|---|
| `bank_id` | VARCHAR | no | Bank identifier. |  |
| `bank_name` | VARCHAR | no | Bank name. |  |
| `short_name` | VARCHAR | no | Common abbreviation (e.g. SBI). |  |
| `bank_type` | VARCHAR | no | Ownership / licence type. | PUBLIC_SECTOR, PRIVATE_SECTOR, SMALL_FINANCE, PAYMENTS_BANK |
| `ifsc_prefix` | VARCHAR | no | First four characters of the bank's IFSC codes. |  |
| `supports_upi` | BOOLEAN | no | Issues UPI-linked accounts. |  |
| `supports_credit_card` | BOOLEAN | no | Issues credit cards. |  |
| `supports_debit_card` | BOOLEAN | no | Issues debit cards. |  |
| `supports_net_banking` | BOOLEAN | no | Offers net banking. |  |
| `is_active` | BOOLEAN | no | Bank active. |  |

### `payment_methods`

Payment instruments and their standard merchant discount rate (MDR).

Grain: one row per payment method. Primary key: `payment_method_id`.

| Column | Type | Nullable | Description | Allowed values / notes |
|---|---|---|---|---|
| `payment_method_id` | VARCHAR | no | Method identifier used in transactions. |  |
| `method_code` | VARCHAR | no | Stable method code - use this in analysis. | UPI, CREDIT_CARD, DEBIT_CARD, NET_BANKING, WALLET |
| `method_name` | VARCHAR | no | Display name. |  |
| `method_family` | VARCHAR | no | Grouping of methods. | UPI, CARD, BANK_TRANSFER, WALLET |
| `mdr_rate_pct` | DECIMAL(5,2) | no | Standard MDR charged to merchants, % of amount, before merchant discount. |  |
| `requires_issuing_bank` | BOOLEAN | no | Whether transactions on this method carry a bank_id. |  |
| `supported_channels` | VARCHAR | no | Channels where the method is accepted (comma separated). |  |

### `failure_codes`

Reference list of failure reasons.

Grain: one row per failure code. Primary key: `failure_code`.

| Column | Type | Nullable | Description | Allowed values / notes |
|---|---|---|---|---|
| `failure_code` | VARCHAR | no | Failure reason code. | INCORRECT_PIN, AUTH_FAILED, INSUFFICIENT_FUNDS, CARD_EXPIRED, INVALID_VPA, LIMIT_EXCEEDED, BANK_DECLINED, BANK_TIMEOUT, ISSUER_UNAVAILABLE, NETWORK_ERROR, GATEWAY_ERROR, GATEWAY_TIMEOUT, RISK_DECLINED |
| `description` | VARCHAR | no | Meaning of the code. |  |
| `failure_category` | VARCHAR | no | Who/what caused the failure. | CUSTOMER, BANK, NETWORK, GATEWAY, RISK |
| `is_retriable` | BOOLEAN | no | Whether an immediate retry can reasonably succeed. |  |
| `responsible_party` | VARCHAR | no | Party accountable for the failure. | CUSTOMER, ISSUING_BANK, NETWORK, PAYMENT_GATEWAY, RISK_ENGINE |

### `settlements`

Merchant payout batches. One batch per merchant per transaction date covering that day's successful transactions.

Grain: one row per (merchant_id, transaction_date). Primary key: `settlement_id`.

| Column | Type | Nullable | Description | Allowed values / notes |
|---|---|---|---|---|
| `settlement_id` | VARCHAR | no | Payout batch identifier. |  |
| `merchant_id` | VARCHAR | no | Merchant being paid. | FK -> merchants.merchant_id |
| `transaction_date` | DATE | no | Business date of the transactions covered (DATE(created_at)). |  |
| `settlement_date` | DATE | no | Scheduled payout date. |  |
| `transaction_count` | INTEGER | no | Number of successful transactions in the batch, as recorded by the settlement system. |  |
| `gross_amount` | DECIMAL(14,2) | no | Sum of transaction amounts, as recorded by the settlement system. |  |
| `fee_amount` | DECIMAL(14,2) | no | MDR fees deducted, as recorded by the settlement system. |  |
| `tax_amount` | DECIMAL(14,2) | no | GST (18%) on fees, as recorded by the settlement system. |  |
| `net_amount` | DECIMAL(14,2) | no | Amount payable to the merchant = gross - fee - tax. |  |
| `status` | VARCHAR | no | Payout status. | SETTLED, PENDING, FAILED |
| `status_reason` | VARCHAR | yes | Reason for PENDING / FAILED. | AWAITING_PAYOUT_WINDOW, RISK_REVIEW_HOLD, BENEFICIARY_ACCOUNT_INVALID, BENEFICIARY_BANK_UNAVAILABLE, TRANSFER_LIMIT_EXCEEDED |
| `settled_at` | TIMESTAMP | yes | Time funds were credited. NULL unless SETTLED. |  |
| `utr_number` | VARCHAR | yes | Bank transfer reference. NULL unless SETTLED. |  |
| `settlement_bank_id` | VARCHAR | no | Bank receiving the payout. | FK -> banks.bank_id |

## Metric definitions

| Metric | Definition | SQL | Notes |
|---|---|---|---|
| **terminal_attempts** | Valid attempts that reached a final outcome. | `count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED'))` | PENDING attempts are unresolved and excluded from rate denominators. |
| **success_rate** | Canonical success rate: successful attempts / terminal attempts. This is the default meaning of 'success rate' / 'SR'. | `count_if(status = 'SUCCESS')::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0)` | Attempt-level: each retry counts as an attempt. |
| **technical_success_rate** | Success rate excluding customer abandonment: SUCCESS / (SUCCESS + FAILED). | `count_if(status = 'SUCCESS')::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED')), 0)` | Use only when explicitly asked; always name which denominator was used. |
| **failure_rate** | FAILED / terminal attempts. | `count_if(status = 'FAILED')::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0)` |  |
| **user_cancellation_rate** | USER_CANCELLED / terminal attempts. | `count_if(status = 'USER_CANCELLED')::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0)` |  |
| **tpv** | Total Processed Value: sum of amount over SUCCESS valid transactions, INR. | `SUM(amount) FILTER (WHERE status = 'SUCCESS')` | Never include failed, cancelled or pending attempts. |
| **success_count** | Number of SUCCESS valid transactions. | `count_if(status = 'SUCCESS')` |  |
| **average_ticket_size** | TPV / success_count. | `SUM(amount) FILTER (WHERE status = 'SUCCESS') / NULLIF(count_if(status = 'SUCCESS'), 0)` |  |
| **order_conversion_rate** | Share of orders (order_id) with at least one SUCCESS attempt, among orders whose attempts are all resolved. | `AVG(order_succeeded) over orders, where order_succeeded = bool_or(status = 'SUCCESS') per order_id` | Customer-level view of success; higher than success_rate because retries recover failures. |
| **retry_rate** | Share of terminal attempts with attempt_number > 1. | `count_if(attempt_number > 1 AND status IN ('SUCCESS','FAILED','USER_CANCELLED'))::DOUBLE / NULLIF(count_if(status IN ('SUCCESS','FAILED','USER_CANCELLED')), 0)` |  |
| **payment_method_share** | Terminal attempts on a method / all terminal attempts in the same slice. | `count_if(method_code = 'UPI' AND status IN (...))::DOUBLE / count_if(status IN (...))` | Mix metric; changes in mix move overall SR even when per-method SR is flat. |
| **failure_reason_share** | FAILED attempts with a given failure_code / FAILED attempts with a non-NULL failure_code. | `count(*) FILTER (WHERE failure_code = X) / count(*) FILTER (WHERE status='FAILED' AND failure_code IS NOT NULL)` | Report how many FAILED rows had no failure_code. |
| **response_time_p50 / p95** | Median / 95th percentile of response_time_ms over SUCCESS and FAILED attempts. | `quantile_cont(response_time_ms, 0.5) FILTER (WHERE status IN ('SUCCESS','FAILED'))` | USER_CANCELLED times measure abandonment, not system latency, and are excluded. |
| **expected_fee (per transaction)** | Fee the merchant should be charged on a SUCCESS transaction. | `ROUND(amount * mdr_rate_pct * (100 - mdr_discount_pct) * 0.0001, 2)` | mdr_rate_pct from payment_methods, mdr_discount_pct from merchants. Exact decimal arithmetic, rounded half-up to paise per transaction, then summed. |
| **expected_tax (per transaction)** | GST on the expected fee. | `ROUND(expected_fee * 0.18, 2)` |  |
| **expected_net_settlement** | Per (merchant, transaction_date): SUM(amount) - SUM(expected_fee) - SUM(expected_tax) over SUCCESS valid transactions. | `SUM(amount) - SUM(expected_fee) - SUM(expected_tax)` | Compare with settlements.net_amount. |
| **settlement_gap** | settlements.net_amount - expected_net_settlement. Negative = merchant under-paid. | `s.net_amount - e.expected_net` | |gap| <= 1.00 INR is rounding noise. |
| **aged_pending_settlement** | PENDING batch whose settlement_date is at least 2 days before the extract date. | `status = 'PENDING' AND settlement_date <= DATE '2026-10-26' - INTERVAL 2 DAY` |  |
| **merchant_onboarding_cohort** | Calendar quarter of merchants.onboarding_date, e.g. 2026-Q3. | `strftime(onboarding_date, '%Y') || '-Q' || quarter(onboarding_date)` |  |
