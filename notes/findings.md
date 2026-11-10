# Data Room Findings — Larkfield Home

Profiling of `dataroom/` as exported on **2026-11-02**. Nothing below has been cleaned or modified; all counts are measured directly from the source files.

---

## 1. File inventory

| File | Rows / records | Columns / fields |
|---|---|---|
| `DATA_DICTIONARY.md` | 73 lines (documentation) | describes the 5 CSVs, 3 policies, 3 ticket files, schema |
| `data/customers.csv` | **53** data rows (+1 header) | 6: `customer_id`, `name`, `email`, `country`, `created_at`, `loyalty_tier` |
| `data/orders.csv` | **49** data rows (+1 header) | 8: `order_id`, `customer_id`, `order_date`, `status`, `Order Total`, `shipping_fee`, `currency`, `payment_method` |
| `data/order_items.csv` | **57** data rows (+1 header) | 6: `order_id`, `line_no`, `sku`, `product_name`, `qty`, `unit_price` |
| `data/refunds.csv` | **3** data rows (+1 header) | 7: `refund_id`, `order_id`, `line_no`, `amount`, `status`, `requested_at`, `approved_by` |
| `data/shipments.csv` | **47** data rows (+1 header) | 8: `shipment_id`, `order_id`, `carrier`, `tracking_no`, `status`, `shipped_at`, `delivered_at`, `last_event_at` |
| `tickets/support_inbox_export.json` | **30** records | 8: `ticket_id`, `received_at`, `channel`, `from`, `to`, `subject`, `body`, `attachments` |
| `tickets/eval_seed_labelled.json` | **15** records | 4 required + 3 optional: `ticket_id`, `expected_outcome`, `expected_human_approval_required`, `expected_approver_role`, (`expected_amount_usd`, `expected_reason_codes`, `note`) |
| `inbox/public_tickets.json` | **10** records | 8: same shape as support export |
| `policies/refund_policy_RP-3.2.md` | 28 lines | version 3.2, effective 2026-07-01 |
| `policies/help_centre_faq_v2.md` | 10 lines | version 2, published 2025-10-14 |
| `policies/warehouse_picking_WH-PICK-1.4.md` | 11 lines | version 1.4, effective 2026-08-15 |
| `schema/response_schema.py` | 84 lines | 12 models, 4 type aliases, 4 inline `Literal` types |

**Cardinality summary:** 53 customers ↔ 47 unique orders (`LF-100200`…`LF-100247`, with `LF-100223` absent) ↔ 57 order lines ↔ 47 shipments ↔ 3 refunds. Every unique order has exactly one shipment and at least one line.

---

## 2. Data-quality profile

### 2.1 Duplicates

| Finding | Count | Affected |
|---|---|---|
| Exact duplicate rows in `orders.csv` | **2** | `LF-100216` (lines 5 & 49), `LF-100239` (lines 19 & 50) |
| Duplicate primary keys in customers/order_items/shipments/refunds | **0** | — |
| Duplicate ticket bodies in support inbox (forwards) | **4** | T002=T027, T005=T028, T009=T029, T014=T030 |

- Order value: raw row sum **$8,031.50** vs unique-order sum **$7,536.50** → **$495.00** inflation.
- **What it breaks:** joining `order_items`/`shipments` to a deduped-orders view double-counts those two orders; loading into a PK-constrained table raises; refund/revenue exposure is overstated. The 4 forwarded duplicates can produce a second refund for the same line, violating RP-3.2 §3.3 (“processed at most once per request”).

### 2.2 ID formats

- CSV keys are consistent — **0 violations**: `order_id` `LF-\d{6}`, `customer_id` `C\d{5}`, `shipment_id` `SHP-\d{4}`, `refund_id` `RF-\d{3}`.
- Ticket free-text order references are inconsistent (non-exclusive counts):

| Pattern | support_inbox (30) | public_tickets (10) |
|---|---|---|
| `LF-######` | 12 | 4 |
| `LF######` (no hyphen) | 7 | 3 |
| `#\d{6}` | 5 | 2 |
| `order order \d{6}` | 6 | 1 |

- Non-existent order references in tickets: **2** → `LF-109777` (T022), `LF-116511` (P04).
- Owner mismatch in tickets: **1** → T023 cites `LF-100202` (owner `C10002`) but sender is `C10022` (`wen.novak22`).
- **What it breaks:** a canonical-only parser resolves many tickets to no order → false `INVALID_REQUEST`/`UNABLE_TO_VERIFY`. Missing orders must yield `ORDER_NOT_FOUND` (never fuzzy-match a nearby order). The owner mismatch must yield `CUSTOMER_MISMATCH`.

### 2.3 Emails — case and whitespace

| File | Uppercase | Leading/trailing whitespace | Duplicates | Malformed | Need normalisation |
|---|---|---|---|---|---|
| `customers.csv` (53) | 0 | 0 | 0 | 0 | 0 |
| `support_inbox_export.json` (30) | 6 | 6 | 0 | 0 | **6** |
| `public_tickets.json` (10) | 2 | 2 | 0 | 0 | **2** |

- Affected tickets: T001, T006, T011, T016, T021, T026, P01, P06 — all are `"  NAME@MAIL.EXAMPLE "` (uppercase + surrounding spaces).
- After `strip().lower()`, **all 40** ticket senders map to a customer.
- **What it breaks:** exact `from` → `customers.email` equality fails for 8 tickets → false `CUSTOMER_MISMATCH` / `UNABLE_TO_VERIFY`. Always strip + lowercase both sides.

### 2.4 Dates and timezones

**Mixed formats in `orders.order_date`:**

| Format | Count |
|---|---|
| ISO `YYYY-MM-DD` | 36 |
| `DD/MM/YYYY` | 13 |
| Other | 0 |

- Of the 13 `DD/MM/YYYY` values, **3 are ambiguous** if interpreted as `MM/DD`: `LF-100235` `08/10/2026`, `LF-100230` `06/10/2026`, `LF-100221` `08/10/2026`. The other 10 have day > 12.
- Carrier dates confirm `DD/MM` is intended (e.g. `LF-100235` order `08/10/2026` → shipped `2026-10-10`).

**Timezone / granularity mismatch:**

| Field | Records with explicit offset |
|---|---|
| `tickets.received_at` | **40/40** (`+00:00`) |
| `orders.order_date` | date-only |
| `shipments.shipped_at` / `delivered_at` / `last_event_at` | **0/47** (naive) |
| `refunds.requested_at` | naive (e.g. `2026-10-06T17:00`) |

**Chronology anomalies in `shipments.csv`:** `last_event_at` earlier than `shipped_at` in **4** rows:

| Shipment | Order | shipped_at | last_event_at |
|---|---|---|---|
| SHP-5010 | LF-100210 | 2026-10-03T10:00 | 2026-09-20T10:00 |
| SHP-5011 | LF-100211 | 2026-10-04T11:00 | 2026-09-19T11:00 |
| SHP-5029 | LF-100229 | 2026-10-28T15:00 | 2026-10-18T15:00 |
| SHP-5039 | LF-100239 | 2026-10-29T17:00 | 2026-10-29T09:00 |

`delivered_at` earlier than `shipped_at`: **0**.

**What it breaks:** naive `%m/%d` parsing silently swaps day/month and shifts the 30/45-day eligibility window; comparing aware ticket timestamps to naive carrier timestamps raises `TypeError` or assumes the wrong zone; the 4 negative intervals corrupt the “>10 days no movement = lost in transit” rule (RP-3.2 §5.1).

### 2.5 Missing / null values

| File | Empty cells |
|---|---|
| `customers.csv` | 0 |
| `orders.csv` | 0 |
| `order_items.csv` | 0 |
| `refunds.csv` | 0 |
| `shipments.csv` | `delivered_at` **13**, `last_event_at` **3**, `shipped_at` **0** |

- `delivered_at` empty = 10 `IN_TRANSIT` + 3 `UNAVAILABLE`.
- `last_event_at` empty = all 3 `UNAVAILABLE` shipments (`SHP-5012` LF-100212, `SHP-5032` LF-100232, `SHP-5038` LF-100238).
- Consistency checks pass: every `DELIVERED` row has `delivered_at`; no non-`DELIVERED` row has it.
- Attachments: **9/9** carry an empty `content_base64`.
- **What it breaks:** RP-3.2 §2.1 starts the window from the carrier delivery date — without `delivered_at` the window cannot be computed (treat as not delivered / unverifiable). `UNAVAILABLE` means the carrier returned no status → `CARRIER_STATUS_UNAVAILABLE`. Empty base64 means you may only trust the attachment’s presence, not decode it (policy only requires a photo exists).

### 2.6 Amounts stored as strings / with currency symbols

**`orders.csv` → `Order Total`:**

| Representation | Count |
|---|---|
| `"USD 24.00"` | 16 |
| `"$129.00"` | 17 |
| Plain numeric | 16 |
| Unparseable after stripping | 0 |

- The column name itself is `Order Total` (space + capitals), not snake_case.
- `shipping_fee` 0/49, `order_items.unit_price` 0/57, `refunds.amount` 0/3 are clean numeric.
- `currency` is `USD` 49/49.
- `Order Total` equals line-item value only; **shipping is separate**: 20 orders have a fee, totalling **$229.00**.
- **What it breaks:** `float("USD 24.00")` / `float("$129.00")` raises `ValueError`; exact column access breaks. Using `Order Total` as a refund cap misprices shipping-reimbursement requests (e.g. T025) — policy refunds affected line value only.

### 2.7 Orphan foreign keys

| Relationship | Orphans |
|---|---|
| `order_items.order_id` → `orders.order_id` | **0 / 57** |
| `shipments.order_id` → `orders.order_id` | **0 / 47** |
| `refunds.order_id` → `orders.order_id` | **0 / 3** |
| `orders.customer_id` → `customers.customer_id` | **0 / 49** |
| Orders with no order lines | **0** |
| Orders with multiple shipments | **0** |
| Ticket references to non-existent orders | **2** (`LF-109777`, `LF-116511`) |
| Ticket sender/order owner mismatches | **1** (T023) |

- Ticket-level orphans are the only referential defects; all CSV FKs are intact.

### 2.8 Refunds exceeding what was paid

| Refund | Order | Line | Amount | Line paid (qty × unit_price) | Exceeds? |
|---|---|---|---|---|---|
| RF-900 | LF-100217 | 1 | 185.00 | 185.00 | No |
| RF-901 | LF-100224 | 1 | 45.00 | 45.00 | No |
| RF-902 | LF-100243 | 1 | 45.00 | 45.00 | No |

- Refunds exceeding the affected-line value: **0 of 3**.
- Per-line aggregate exceeding paid: **0**.
- Refunds referencing a missing line: **0**.
- Trap (not a defect): RF-901 fully refunds `LF-100224` line 1 (vase). Line 2 is qty 2 × $185 = $370, so one chair ($185) remains eligible → the `ALREADY_REFUNDED` + `APPROVAL_REQUIRED` case T026 expects. Refund logic must be **per line**, not per order.

### 2.9 Cross-field consistency (bonus)

- `Order Total` vs `sum(qty × unit_price)`: **0 mismatches** across all 47 unique orders.
- `qty` all positive integers; `unit_price` all numeric; no duplicate `(order_id, line_no)`; no duplicate `tracking_no`.

---

## 3. Shipment staleness (temporal context)

As of the **2026-11-02** export date, **7** `IN_TRANSIT`/`UNAVAILABLE` shipments have had no movement for >10 days:

| Shipment | Order | Status | last_event_at | Days silent |
|---|---|---|---|---|
| SHP-5007 | LF-100207 | IN_TRANSIT | 2026-10-03T16:00 | 29 |
| SHP-5008 | LF-100208 | IN_TRANSIT | 2026-10-04T17:00 | 28 |
| SHP-5009 | LF-100209 | IN_TRANSIT | 2026-10-04T09:00 | 28 |
| SHP-5010 | LF-100210 | IN_TRANSIT | 2026-09-20T10:00 | 42 |
| SHP-5011 | LF-100211 | IN_TRANSIT | 2026-09-19T11:00 | 43 |
| SHP-5021 | LF-100221 | IN_TRANSIT | 2026-10-02T06:00 | 30 |
| SHP-5029 | LF-100229 | IN_TRANSIT | 2026-10-18T15:00 | 14 |

- “Lost in transit” depends on the reference clock: using the export date vs ticket `received_at` changes the set (e.g. T008/T009 were sent shortly after the last scan).
- **What it breaks:** the >10-day rule (RP-3.2 §5.1) is non-deterministic without an explicit `as_of` time and consistent timezones.

---

## 4. Schema reference — `schema/response_schema.py`

Pydantic v2 (`BaseModel`, `ConfigDict`, `Field`). 84 lines.

### 4.1 Models

| Model | Fields (name: type / default) |
|---|---|
| `Attachment` | `file_name: str`; `content_type: str`; `content_base64: str` |
| `Ticket` | `ticket_id: str`; `received_at: str`; `channel: Literal["email"]`; `from_: str` = `Field(alias="from")`; `to: str`; `subject: str`; `body: str`; `attachments: List[Attachment] = []`; config `populate_by_name=True` |
| `Resolved` | `order_id: Optional[str]`; `customer_id: Optional[str]` |
| `Evidence` | `source: str`; `ref: str`; `value: str` |
| `Check` | `check_id: str`; `result: CheckResult`; `evidence: List[Evidence] = []`; `policy_clause: Optional[str]` |
| `DraftReply` | `to: str`; `subject: str`; `body: str` |
| `ModelCall` | `model: str`; `input_tokens: int`; `output_tokens: int` |
| `DocumentUsed` | `document: str`; `version: str` |
| `Trace` | `model_calls: List[ModelCall]`; `tools_called: List[str]`; `documents_used: List[DocumentUsed]`; `cost_usd: float` |
| `TicketResponse` | `ticket_id: str`; `run_id: str`; `ticket_type: TicketType`; `resolved: Resolved`; `outcome: Outcome`; `reason_codes: List[str]`; `checks: List[Check]`; `human_approval_required: bool`; `approver_role: ApproverRole`; `draft_reply: DraftReply`; `trace: Trace` |
| `Decision` | `decision: Literal["approve", "reject"]`; `approver: str` |
| `LedgerEntry` | `run_id: str`; `ticket_id: str`; `action: Literal["REFUND", "REPLY", "ESCALATION"]`; `amount_usd: Optional[float]`; `status: Literal["PROPOSED", "APPROVED", "REJECTED"]`; `recorded_at: str` |

### 4.2 Enums / type aliases (all `Literal`)

| Name | Values |
|---|---|
| `TicketType` | `"REFUND"`, `"RETURN"`, `"ORDER_STATUS"`, `"DELIVERY_ISSUE"`, `"OTHER"` |
| `Outcome` | `"PROCEED"`, `"PROCEED_MODIFIED"`, `"PEND"`, `"ESCALATE"`, `"REJECT"`, `"INVALID_REQUEST"`, `"UNABLE_TO_VERIFY"` |
| `CheckResult` | `"PASS"`, `"FAIL"`, `"NOT_APPLICABLE"`, `"UNABLE_TO_VERIFY"` |
| `ApproverRole` | `Optional[Literal["SUPPORT_LEAD", "TRUST_SAFETY", "CARRIER_CLAIMS"]]` (nullable) |
| `Ticket.channel` (inline) | `"email"` |
| `Decision.decision` (inline) | `"approve"`, `"reject"` |
| `LedgerEntry.action` (inline) | `"REFUND"`, `"REPLY"`, `"ESCALATION"` |
| `LedgerEntry.status` (inline) | `"PROPOSED"`, `"APPROVED"`, `"REJECTED"` |

**Notes:** `Ticket.from_` is populated from JSON key `"from"` via alias. `approver_role`, `Resolved.order_id` and `Resolved.customer_id` are nullable. Evidence/attachments/checks default to empty lists. `cost_usd` and `amount_usd` are floats; all timestamps are typed `str` (no datetime parsing by the contract).

---

## 5. Labelled eval distribution (context)

From `tickets/eval_seed_labelled.json` (15 tickets):

| Expected outcome | Count | Tickets |
|---|---|---|
| `PROCEED` | 5 | T002, T004, T005, T006, T008 |
| `PEND` | 3 | T001, T003, T026 |
| `ESCALATE` | 3 | T011, T020, T021 |
| `REJECT` | 2 | T014, T018 |
| `PROCEED_MODIFIED` | 1 | T019 |
| `UNABLE_TO_VERIFY` | 1 | T013 |

---

## 6. Downstream implications checklist

Every defect above maps to a mandatory normalisation/validation step before any refund decision:

1. **Dedupe orders** by `order_id` before joins; pin expected unique count = 47.
2. **Normalise emails** (`strip().lower()`) on both ticket `from` and `customers.email`; all 40 ticket senders then resolve.
3. **Canonicalise order references** (`LF-100xxx`) across `LF-######`, `LF######`, `#\d{6}`, bare `\d{6}` and `order order` forms; do not fuzzy-match → `ORDER_NOT_FOUND` / `CUSTOMER_MISMATCH`.
4. **Parse dates explicitly**: ISO and `DD/MM/YYYY`; never `MM/DD`. Attach a timezone assumption to naive carrier/refund timestamps; use tz-aware UTC consistently.
5. **Parse money** by stripping `USD ` / `$` / commas; treat `Order Total` as line value excluding shipping; compute paid amount per line as `qty × unit_price`.
6. **Handle missing carrier data**: empty `delivered_at` ⇒ no delivery date; `UNAVAILABLE` ⇒ `CARRIER_STATUS_UNAVAILABLE`; empty `last_event_at` ⇒ cannot assess movement.
7. **Guard chronology**: clamp/flag negative `last_event_at − shipped_at`; compute lost-in-transit against an explicit `as_of` locked to `ticket.received_at`.
8. **Refund per line**: subtract existing `ISSUED` refunds per `(order_id, line_no)`; cap at unpaid affected-line value; never order-level.
9. **Dedupe forwarded tickets** (4 in inbox) before proposing refunds; enforce at-most-once per request.
10. **Schema conformance**: emit only the `TicketResponse` Literals; `approver_role` null unless approval required; include trace and checks.
