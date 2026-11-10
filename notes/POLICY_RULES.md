# Policy Rules — Larkfield Home

Derived from `dataroom/policies/`. All rule text below is quoted or paraphrased from the source; reason-code strings are verbatim.

---

## 1. Source documents and precedence

| Document | Version | Date | Owner | Role for this desk |
|---|---|---|---|---|
| `refund_policy_RP-3.2.md` | **3.2** | **Effective 2026-07-01** | Head of Customer Care | **Governing** refund/return/delivery/order-status policy |
| `help_centre_faq_v2.md` | **2** | **Published 2025-10-14** | Digital Content | Customer-facing FAQ; superseded wherever it conflicts |
| `warehouse_picking_WH-PICK-1.4.md` | **1.4** | **Effective 2026-08-15** | Warehouse Operations | **Out of scope** for Customer Care |

**Version decision:** Apply **RP-3.2**. Its header states: *“This version replaces RP-3.1 and all earlier customer-care guidance on refunds and returns.”* It is effective **2026-07-01**, later than the FAQ (published 2025-10-14), and is the dedicated Customer Care policy. `WH-PICK-1.4` is effective later (2026-08-15) but explicitly says *“This document does not set Customer Care return, refund or approval rules”* and deals in stock cost, not customer payments, so it never governs a refund.

### 1.1 Conflicting rules and resolution

| Topic | RP-3.2 (apply) | FAQ v2 (superseded) | Warehouse (ignore) |
|---|---|---|---|
| Return/refund window | 30 days from carrier delivery; 45 days for damaged/faulty with photo | 14 days from delivery | — |
| Auto-refund threshold | **$150** inclusive; above needs Support Lead | $250 automatic | — |
| Stale tracking trigger | **>10 days** no carrier movement = lost | 5 days → “we will look into it” | — |
| Damaged item | Photo grants 45-day window (days 31–45); content not analysed | “send a photo and we will refund” (no window nuance) | — |
| Stock adjustments | — | — | 48h staging / stock cost / loss register; **not refunds** |

Where any of these differ, **RP-3.2 wins** (newer, dedicated Customer Care policy, expressly replaces earlier guidance).

### 1.2 One-line rationale

- **WH-PICK-1.4 is out of scope** because its own Scope section states it *“does not set Customer Care return, refund or approval rules”* and it governs warehouse stock cost, not customer payments.
- **FAQ v2 is superseded** because RP-3.2 (effective 2026-07-01) is dated later than FAQ v2 (published 2025-10-14) and RP-3.2's header says it *“replaces … all earlier customer-care guidance on refunds and returns.”*

---

## 2. Reason codes (verbatim)

`reason_codes` is a free-form `List[str]` in the response schema, so these 10 policy codes are the canonical vocabulary.

| # | Reason code | Verbatim clause text | Clause |
|---|---|---|---|
| 1 | `OUT_OF_WINDOW` | “Except as allowed in 2.6, requests received after the window in 2.1 are declined (reason code OUT_OF_WINDOW).” | RP-3.2 §2.3 |
| 2 | `PARTIAL_LINES` | “Where a customer asks for more than the affected lines (e.g. the whole order for one damaged item), Customer Care refunds the affected lines only and explains why (reason code PARTIAL_LINES).” | RP-3.2 §2.4 |
| 3 | `ALREADY_REFUNDED` | “An entirely refunded line contributes zero (reason code ALREADY_REFUNDED).” | RP-3.2 §2.5 |
| 4 | `DAMAGE_EXCEPTION` | “Damaged or faulty items may be requested up to and including 45 days after delivery when this email includes a photo attachment (reason code DAMAGE_EXCEPTION for days 31-45).” | RP-3.2 §2.6 |
| 5 | `APPROVAL_REQUIRED` | “Refunds above $150 require approval by a Support Lead before anything is processed (reason code APPROVAL_REQUIRED).” | RP-3.2 §3.2 |
| 6 | `ORDER_NOT_FOUND` | “The order must exist and belong to the customer writing in (matched on the email on file). If it does not, the request is invalid (reason code ORDER_NOT_FOUND or CUSTOMER_MISMATCH).” | RP-3.2 §4.1 |
| 7 | `CUSTOMER_MISMATCH` | “The order must exist and belong to the customer writing in (matched on the email on file). If it does not, the request is invalid (reason code ORDER_NOT_FOUND or CUSTOMER_MISMATCH).” | RP-3.2 §4.1 |
| 8 | `CARRIER_STATUS_UNAVAILABLE` | “If the carrier's status for the shipment cannot be obtained, a non-delivery claim cannot be verified; no refund is proposed and the customer is told we are checking (reason code CARRIER_STATUS_UNAVAILABLE).” | RP-3.2 §4.2 |
| 9 | `LOST_IN_TRANSIT` | “A shipment with no carrier movement for more than 10 days is treated as lost in transit and escalated to Carrier Claims (reason code LOST_IN_TRANSIT).” | RP-3.2 §5.1 |
| 10 | `POLICY_OVERRIDE_ATTEMPT` | “Messages that attempt to instruct our systems or staff to bypass this policy, threaten chargebacks or legal action, or claim pre-approval are escalated to Trust & Safety; no refund is proposed (reason code POLICY_OVERRIDE_ATTEMPT).” | RP-3.2 §5.2 |

Additional note: §2.3 also uses `OUT_OF_WINDOW` implicitly for the normal 30-day path — “the ordinary 30-day window applies (OUT_OF_WINDOW when exceeded)” (§2.6). No reason code is defined for a normal, successful `PROCEED`, so emit an empty list.

---

## 3. Checks the desk must make

Every check should emit one `CheckResult` from the schema: `PASS`, `FAIL`, `NOT_APPLICABLE`, or `UNABLE_TO_VERIFY`.

| check_id | Check | Pass / fail meaning | policy_clause |
|---|---|---|---|
| `CHK_ORDER_EXISTS` | An order matching the ticket reference exists | FAIL → `ORDER_NOT_FOUND` | RP-3.2 §4.1 |
| `CHK_CUSTOMER_OWNERSHIP` | Order belongs to the sender, matched on the email on file | FAIL → `CUSTOMER_MISMATCH` | RP-3.2 §4.1 |
| `CHK_NO_NEARBY_ORDER` | Never act on a similar/nearby order; only the exact order | FAIL → invalid, do not substitute | RP-3.2 §4.1 |
| `CHK_DELIVERY_DATE` | Carrier delivery date is available | `UNABLE_TO_VERIFY` if absent | RP-3.2 §2.1 |
| `CHK_WINDOW_30D` | Request received within 30 days of carrier delivery | FAIL → `OUT_OF_WINDOW` | RP-3.2 §2.1, §2.3 |
| `CHK_ITEM_CONDITION` | Classify damaged/faulty vs wrong/not-as-described vs change-of-mind | Determines window and refund basis | RP-3.2 §2.2, §2.6 |
| `CHK_PHOTO_ATTACHMENT` | Email includes an image attachment for damage exception | PASS enables days 31–45 | RP-3.2 §2.6 |
| `CHK_WINDOW_45D` | Damaged/faulty with photo requested ≤45 days after delivery | FAIL → `OUT_OF_WINDOW` | RP-3.2 §2.6 |
| `CHK_AFFECTED_LINES` | Refund only the affected line(s), not the whole order | Partial request → `PARTIAL_LINES` | RP-3.2 §2.4 |
| `CHK_QTY_LIMIT` | Use affected quantity, never more than ordered quantity | FAIL → cap to ordered qty | RP-3.2 §2.5 |
| `CHK_PRIOR_REFUND` | Subtract refunds already `ISSUED` for the line; fully refunded line = zero | Fully refunded → `ALREADY_REFUNDED` | RP-3.2 §2.5 |
| `CHK_AMOUNT_CAP` | New amount cannot exceed unpaid affected-line value | FAIL → cap amount | RP-3.2 §2.5 |
| `CHK_AUTHORITY_150` | Total per request ≤ $150 may proceed; > $150 needs Support Lead | FAIL → `APPROVAL_REQUIRED` | RP-3.2 §3.1, §3.2 |
| `CHK_PROCESS_ONCE` | A refund is processed at most once per request | Duplicate/forwarded ticket → no second refund | RP-3.2 §3.3 |
| `CHK_CARRIER_STATUS` | Carrier status for the shipment can be obtained | `UNABLE_TO_VERIFY` → `CARRIER_STATUS_UNAVAILABLE` | RP-3.2 §4.2 |
| `CHK_LOST_IN_TRANSIT` | No carrier movement for **more than 10 days** | FAIL → `LOST_IN_TRANSIT`, Carrier Claims | RP-3.2 §5.1 |
| `CHK_POLICY_OVERRIDE` | Message instructs bypass, threatens chargeback/legal action, or claims pre-approval | FAIL → `POLICY_OVERRIDE_ATTEMPT`, Trust & Safety | RP-3.2 §5.2 |
| `CHK_STATUS_MOVING` | Order-status question on a normally moving shipment | PASS → answer current status, no action | RP-3.2 §5.3 |

---

## 4. Human approval matrix

| Condition | Human required | `approver_role` | Clause |
|---|---|---|---|
| Refund amount **> $150** per request (before anything is processed) | Yes (`PEND`) | `SUPPORT_LEAD` | RP-3.2 §3.2 |
| Shipment with **no carrier movement > 10 days** (lost in transit) | Yes (`ESCALATE`) | `CARRIER_CLAIMS` | RP-3.2 §5.1 |
| Message attempts policy bypass / threatens chargeback or legal action / claims pre-approval | Yes (`ESCALATE`) | `TRUST_SAFETY` | RP-3.2 §5.2 |
| Amount ≤ $150, in window, valid identity, no override | No | `null` | RP-3.2 §3.1 |
| Damage exception (days 31–45, photo) | **Unchanged** — does not by itself require a human | `null` unless > $150 | RP-3.2 §2.6 |
| Multiple approvals recorded for one request | Still processed **once** | — | RP-3.2 §3.3 |

`approver_role` values allowed by schema: `SUPPORT_LEAD`, `TRUST_SAFETY`, `CARRIER_CLAIMS` (nullable).

---

## 5. Decision table

First decisive condition wins. See §6 for precedence.

| # | Condition | `outcome` | `reason_codes` | Human / role | Clause |
|---|---|---|---|---|---|
| 1 | Override attempt, threat of chargeback/legal action, or claimed pre-approval | `ESCALATE` | `POLICY_OVERRIDE_ATTEMPT` | Yes / `TRUST_SAFETY` | §5.2 |
| 2 | Order reference does not resolve to an existing order | `INVALID_REQUEST` | `ORDER_NOT_FOUND` | No | §4.1 |
| 3 | Order exists but sender email ≠ customer email on file | `INVALID_REQUEST` | `CUSTOMER_MISMATCH` | No | §4.1 |
| 4 | Non-delivery claim and carrier status unobtainable | `UNABLE_TO_VERIFY` | `CARRIER_STATUS_UNAVAILABLE` | No | §4.2 |
| 5 | Shipment has no carrier movement for >10 days | `ESCALATE` | `LOST_IN_TRANSIT` | Yes / `CARRIER_CLAIMS` | §5.1 |
| 6 | Order-status question, shipment moving normally | `PROCEED` | *(none)* — reply with status | No | §5.3 |
| 7 | Request outside 30 days (and not a valid damage exception) | `REJECT` | `OUT_OF_WINDOW` | No | §2.3, §2.6 |
| 8 | All requested affected lines already fully refunded (`ISSUED`) | `REJECT` | `ALREADY_REFUNDED` | No | §2.5 |
| 9 | Eligible; proposed amount/lines exactly as requested; ≤ $150 | `PROCEED` | *(none)* | No | §2.1, §2.2, §3.1 |
| 10 | Eligible; amount corrected (affected lines only, shipping excluded, quantity capped, prior refunds subtracted); final ≤ $150 | `PROCEED_MODIFIED` | `PARTIAL_LINES` and/or `ALREADY_REFUNDED` | No | §2.4, §2.5 |
| 11 | Eligible but final proposed amount > $150 | `PEND` | `APPROVAL_REQUIRED` (+ `DAMAGE_EXCEPTION` / `ALREADY_REFUNDED` / `PARTIAL_LINES` as applicable) | Yes / `SUPPORT_LEAD` | §3.2 |
| 12 | Damaged/faulty, days 31–45, photo present, final ≤ $150 | `PROCEED` (`PROCEED_MODIFIED` if corrected) | `DAMAGE_EXCEPTION` (+ modifiers) | No | §2.6 |
| 13 | Damaged/faulty, days 31–45, photo present, final > $150 | `PEND` | `DAMAGE_EXCEPTION`, `APPROVAL_REQUIRED` | Yes / `SUPPORT_LEAD` | §2.6, §3.2 |
| 14 | Damaged/faulty, days 31–45, **no photo** | `REJECT` | `OUT_OF_WINDOW` | No | §2.6 |
| 15 | Damage after 45 days, or wrong/not-as-described after 30 days | `REJECT` | `OUT_OF_WINDOW` | No | §2.6 |
| 16 | Return/change-of-mind within 30 days (see open case O1) | `PROCEED` (or `PROCEED_MODIFIED`) | *(none)* | No | §2.1, §2.2 |
| 17 | Non-delivery claim but carrier reports DELIVERED (see O10) | `ESCALATE` | *(no policy code; propose `DELIVERY_DISPUTE`)* | Yes / `CARRIER_CLAIMS` | §4.2, §5.1 (analogy) |

**Worked examples from `eval_seed_labelled.json`** (confirm the table): T001 = row 13 (`PEND`, `DAMAGE_EXCEPTION`+`APPROVAL_REQUIRED`, $210); T003 = row 11 ($185, `PEND`); T004/T005/T006/T002 = row 9; T008 = row 6; T011 = row 5; T013 = row 4; T014 = row 15 (38 days, not-what-I-hoped); T018 = row 8 (prior `ISSUED` refund RF-900); T019 = row 10 (`PROCEED_MODIFIED`, `PARTIAL_LINES`); T020/T021 = row 1; T026 = row 11 with `PRIOR_REFUND` (`ALREADY_REFUNDED`+`APPROVAL_REQUIRED`, $185).

---

## 6. Recommended check precedence

1. `CHK_POLICY_OVERRIDE` — safety/security first; escalate to Trust & Safety regardless of refund math.
2. `CHK_ORDER_EXISTS` → `CHK_CUSTOMER_OWNERSHIP` → `CHK_NO_NEARBY_ORDER`.
3. `CHK_CARRIER_STATUS` → `CHK_LOST_IN_TRANSIT` / `CHK_STATUS_MOVING` for delivery/status tickets.
4. `CHK_ITEM_CONDITION` → `CHK_WINDOW_30D` / `CHK_PHOTO_ATTACHMENT` / `CHK_WINDOW_45D`.
5. `CHK_AFFECTED_LINES` → `CHK_QTY_LIMIT` → `CHK_PRIOR_REFUND` → `CHK_AMOUNT_CAP`.
6. `CHK_AUTHORITY_150` → `CHK_PROCESS_ONCE`.

Note: an override attempt (step 1) wins over an otherwise valid refund; an order-status query still requires identity before status is disclosed.

---

## 7. Open cases — one proposed rule each

| # | Open case | Proposed rule (one line) |
|---|---|---|
| O1 | Return/change-of-mind within 30 days (no damage) | Within 30 days, refund the affected line value at full `qty × unit_price`; after 30 days, `REJECT` with `OUT_OF_WINDOW`. |
| O2 | Shipping fee for a damaged item (T025 asks for it) | Never refund `shipping_fee`; refund affected line value only and explain exclusion with `PARTIAL_LINES`. |
| O3 | Day-counting and timezone for the 30/45-day windows | Count whole calendar days in UTC from the `delivered_at` date, inclusive of day 30 (days 31–45 = damage exception, >45 = out). |
| O4 | What counts as “a photo attachment” | Treat any attachment whose `content_type` starts with `image/` as sufficient; do not decode or analyse `content_base64`. |
| O5 | Partial quantity damage (e.g. 1 of 2 chairs, T026) | Refund `affected_qty × unit_price`, never more than ordered quantity; leave undamaged units untouched. |
| O6 | Prior refund only partly covers a line | Subtract the sum of `ISSUED` amounts for `(order_id, line_no)` and propose the remaining unpaid balance; zero balance ⇒ `ALREADY_REFUNDED`. |
| O7 | Prior refund with status other than `ISSUED` | Count only `ISSUED` refunds against the line; ignore other statuses. |
| O8 | `orders.status` conflicts with carrier status | Treat the carrier `delivered_at`/shipment status as authoritative for delivery and window calculations. |
| O9 | Ticket references multiple orders | Process a single order per request; if more than one valid order is cited, return `PEND` with no role for human triage. |
| O10 | Carrier says DELIVERED but customer claims non-receipt | Escalate to `CARRIER_CLAIMS` (proposed code `DELIVERY_DISPUTE`); propose no refund. |
| O11 | Damaged/faulty claim before delivery (`SHIPPED`/`IN_TRANSIT`) | Propose no refund until delivery; reply with current status (`PROCEED`) and re-evaluate the window after delivery. |
| O12 | Duplicate or forwarded ticket for the same order/line | Treat as the same request and process once (`CHK_PROCESS_ONCE`); reply by referencing the existing outcome, never issue a second refund. |
| O13 | Refund amount exactly $150.00 | Proceed without approval (inclusive bound); only amounts above $150.00 require the Support Lead. |
| O14 | Ticket has no resolvable order reference | Return `UNABLE_TO_VERIFY` (identity/order cannot be established) rather than guessing an order. |
| O15 | Non-USD currency encountered | Return `INVALID_REQUEST`; the contract and dataset are USD-only (`currency` = USD on every order). |
| O16 | Order-status query for an `UNAVAILABLE` shipment | Return `UNABLE_TO_VERIFY` with `CARRIER_STATUS_UNAVAILABLE`; tell the customer we are checking. |
| O17 | Lost-in-transit clock at handling time vs ticket time | Evaluate the >10-day silence against handling time using `last_event_at`, and record the `as_of` used in evidence. |
| O18 | Override attempt combined with an order/customer mismatch | Policy override takes precedence: `ESCALATE` to `TRUST_SAFETY`, while noting the identity discrepancy in evidence. |

---

## 8. Practical reminders

- Do **not** repay an already-issued refund; refunds are per **line**, not per order.
- The damage exception changes **only** the window — quantity, prior-refund and authority rules still apply (§2.6).
- A single request is processed **at most once**, regardless of approvals recorded (§3.3).
- Never act on a “similar” or nearby order if the exact order cannot be found (§4.1).

---

## 9. Labelled-ticket reconciliation (`tickets/eval_seed_labelled.json`)

Evaluated with the decision table in §5, `as_of` = `ticket.received_at`, per-line refund math, and prior `ISSUED` refunds subtracted. Reason codes and amounts are compared only where the label supplies them.

| ticket_id | Label: outcome · reason_codes · approver_role · human_approval_required | Our decision table result: outcome · reason_codes · role · human · amount · days | Result |
|---|---|---|---|
| T001 | PEND · DAMAGE_EXCEPTION, APPROVAL_REQUIRED · SUPPORT_LEAD · true · $210.00 | PEND · DAMAGE_EXCEPTION, APPROVAL_REQUIRED · SUPPORT_LEAD · true · $210.00 · 40d | **MATCH** |
| T002 | PROCEED · — · null · false | PROCEED · — · null · false · $129.00 · 9d | **MATCH** |
| T003 | PEND · (label omits) · SUPPORT_LEAD · true | PEND · APPROVAL_REQUIRED · SUPPORT_LEAD · true · $185.00 · 6d | **MATCH** |
| T004 | PROCEED · — · null · false | PROCEED · — · null · false · $45.00 · 10d | **MATCH** |
| T005 | PROCEED · — · null · false | PROCEED · — · null · false · $38.00 · 11d | **MATCH** |
| T006 | PROCEED · — · null · false | PROCEED · — · null · false · $52.00 · 2d | **MATCH** |
| T008 | PROCEED · — · null · false | PROCEED · — · null · false · status reply · — | **MATCH** |
| T011 | ESCALATE · (label omits) · CARRIER_CLAIMS · true | ESCALATE · LOST_IN_TRANSIT · CARRIER_CLAIMS · true | **MATCH** |
| T013 | UNABLE_TO_VERIFY · (label omits) · null · false | UNABLE_TO_VERIFY · CARRIER_STATUS_UNAVAILABLE · null · false | **MATCH** |
| T014 | REJECT · (label omits) · null · false | REJECT · OUT_OF_WINDOW · null · false · 38d | **MATCH** |
| T018 | REJECT · (label omits) · null · false | REJECT · ALREADY_REFUNDED · null · false · 10d | **MATCH** |
| T019 | PROCEED_MODIFIED · (label omits) · null · false | PROCEED_MODIFIED · PARTIAL_LINES · null · false · $27.50 · 6d | **MATCH** |
| T020 | ESCALATE · (label omits) · TRUST_SAFETY · true | ESCALATE · POLICY_OVERRIDE_ATTEMPT · TRUST_SAFETY · true | **MATCH** |
| T021 | ESCALATE · (label omits) · TRUST_SAFETY · true | ESCALATE · POLICY_OVERRIDE_ATTEMPT · TRUST_SAFETY · true | **MATCH** |
| T026 | PEND · ALREADY_REFUNDED, APPROVAL_REQUIRED · SUPPORT_LEAD · true · $185.00 | PEND · ALREADY_REFUNDED, APPROVAL_REQUIRED · SUPPORT_LEAD · true · $185.00 · 12d | **MATCH** |

**Match count: 15 / 15. Mismatches: 0 — no rule changes required.**

Reconciliation notes:

- **`as_of` must be `ticket.received_at`, not the export date.** T008 was 1 day since the last scan when sent (PROCEED); at the 2026-11-02 export it is 29 days stale, which would wrongly escalate. T011 is 16 days stale at send time → ESCALATE. This single choice makes both match.
- **T019** uses `PROCEED_MODIFIED` + `PARTIAL_LINES`: the customer asked to refund the whole order for one broken line, so only the affected line ($27.50) is refunded (§2.4).
- **T026** uses `ALREADY_REFUNDED` because the vase credit (RF-901) is excluded, and `APPROVAL_REQUIRED` because the one damaged chair is $185 (> $150).
- **T003** label supplies no reason code; our `APPROVAL_REQUIRED` is consistent with §3.2 and matches the outcome/role/human flag.
