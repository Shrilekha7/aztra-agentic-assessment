# Decisions — Architecture Locked

This file freezes the design **before any service code is written**. It records the architecture, the reason for each choice, and the rules to copy into `APPROACH.md` / `ARTEFACT.md`.

---

## 1. Architecture (locked)

```
ticket JSON (untrusted)
        │
        ▼
[1] LLM extraction  ── structured fields only ──►  ExtractionResult
        │                                           (ticket_type, order refs,
        │                                            requested action, amounts,
        │                                            items, condition, flags)
        ▼
[2] Normalisers (deterministic, Python)  ── email, order-id, dates, money
        │
        ▼
[3] Rules engine (deterministic, POLICY_RULES.md §3–§6)
        │   identity → carrier/status → window → lines/qty → prior refunds
        │   → amount authority → override
        ▼
[4] Decision object  ──► outcome, reason_codes, human_approval_required,
        │                 approver_role, checks[], resolved{}, amount
        ├──► [5] SQLite: run + ledger (idempotent)
        └──► [6] Template reply (values only from decision) → TicketResponse
```

### 1.1 The LLM is used ONLY to extract structured fields; email body is untrusted data

**Decision:** call the model once per ticket to produce a strict JSON object of observed facts. The model never computes eligibility, amounts, approvals, or replies.

**Why:** the corpus contains direct instruction attacks (T020, T021, P05) that try to change policy, approvals and amounts. Treating the body as data means injection text can at most be *extracted* into an `injection_flags` field, never *executed*. Extraction is also the only part where free-text ambiguity needs model judgement (e.g. “the whole order”, “one of my two chairs”, “not what I hoped”).

**Extraction contract (fields):**

| Field | Type | Purpose |
|---|---|---|
| `ticket_type` | `REFUND`/`RETURN`/`ORDER_STATUS`/`DELIVERY_ISSUE`/`OTHER` | routing/classification |
| `order_refs` | `list[str]` | every order reference as written (raw) |
| `requested_action` | string | refund / return / status / delivery complaint |
| `requested_items` | `list[str]` | product names mentioned |
| `requested_amount` | `float?` | amount the customer asks for, if any |
| `requested_whole_order` | `bool` | “whole order / full refund” phrasing |
| `requested_shipping` | `bool` | shipping/delivery charge requested |
| `condition` | `damaged`/`faulty`/`wrong`/`change_of_mind`/`not_delivered`/`unknown` | window selection |
| `has_image_attachment` | `bool` | from attachment metadata, not body claims |
| `injection_flags` | `list[str]` | override/instruction/threat text detected |
| `requested_quantity` | `int?` | e.g. “one of my two” |

**Guardrails on extraction:** the model receives a fixed schema; output is validated; any missing field defaults conservatively; raw text is retained as evidence. The model **cannot** set `outcome`, `reason_codes`, `human_approval_required`, `approver_role`, or refund `amount`.

### 1.2 A deterministic rules engine runs every check and makes every decision

**Decision:** after extraction + normalisation, Python executes the checks in `POLICY_RULES.md` §3 in the §6 precedence and emits `outcome`, `reason_codes`, `human_approval_required`, `approver_role`, `checks[]` and the computed `amount`.

**Why:** policy is arithmetic and thresholds, not language. Determinism gives reproducibility, auditability (every check carries evidence + `policy_clause`) and exact reconciliation — the locked rules reproduce **15/15** labelled tickets. It also prevents prompt injection from influencing money.

**Fixed mechanics:**
- Refund is **per line**: `affected_qty × unit_price − Σ(ISSUED refunds for that line)`, never more than ordered qty, never negative.
- `> $150` → `PEND` / `SUPPORT_LEAD`; `<= $150` passes (§3.1–3.2 inclusive bound).
- Damage exception only widens the window to 45 days when an image attachment exists; it does not change quantity/prior-refund/authority.
- **`as_of` = `ticket.received_at`** (not wall clock / export date). This is required: T008 is 1 day since last scan at send time (`PROCEED`) but 29 days stale at the 2026-11-02 export; T011 is 16 days stale at send time (`ESCALATE`). Locking `as_of` to the ticket timestamp makes both labels match.
- Missing carrier status (`UNAVAILABLE` / no `last_event_at`) → `UNABLE_TO_VERIFY` / `CARRIER_STATUS_UNAVAILABLE`, never a guessed refund.
- Override/threat detection short-circuits to `ESCALATE` / `TRUST_SAFETY` before amount logic.
- **Deterministic injection scan (never LLM-only):** regex/keyword scan `subject + body` case-insensitively for `ignore previous instructions`, `[system]`, `pre-approved` (`preapproved`/`pre approved`), `charged?back`, `lawyer`, `legal action`, `authorised to process`, `mark human_approval_required`; OR the hit with the model's `injection_flags`. Either source forces `POLICY_OVERRIDE_ATTEMPT` → `ESCALATE` / `TRUST_SAFETY` and cannot change amount or approval.

### 1.3 `draft_reply` is template-built from the decision — facts and amounts always match

**Decision:** compose the customer reply from fixed templates (one per `outcome` + `ticket_type`), inserting only values produced by the rules engine (`order_id`, computed `amount`, reason phrases, next step). The LLM does not author customer-visible text.

**Why:** the reply can never contradict the ledger. There is no second chance for the model to restate a wrong amount, invent an approval, or obey injected instructions. It also guarantees consistent tone and policy language across all tickets.

### 1.4 SQLite holds runs and the ledger; decisions are idempotent

**Decision:** persist to SQLite:
- `runs(run_id PK, ticket_id, received_at, extraction_json, decision_json, created_at)`
- `ledger(entry_id PK, run_id, ticket_id, order_id, line_no, action, amount_usd, status, recorded_at)`
- idempotency uniqueness on `(order_id, line_no, action='REFUND')` for `APPROVED`/`PROPOSED`, plus a normalised ticket fingerprint `(sender_lower, order_id, sorted_lines, action)`.

**Why:** RP-3.2 §3.3 says a refund is processed **at most once per request**, and the inbox has **4 forwarded duplicate tickets** (T027–T030) with *new* `ticket_id`s. Keying only on `ticket_id` would allow a second refund. The ledger is the source of truth for “already refunded”, so a re-sent or repeated request becomes `ALREADY_REFUNDED`/no-op rather than a second payment.

### 1.5 Code lives in `src/`, building on the template skeleton

**Decision:** implement inside `src/` (`src/solution.py` entry point, new modules under `src/`). Do not move or rename `ARTEFACT.html`.

**Why:** the template already defines `src/solution.py::main()` as the runnable entry point and `src/__init__.py`; the grader expects the template layout. `ARTEFACT.html` is the graded written artefact and must stay in place.

### 1.6 Normalisation decisions (locked from findings)

- Emails: `strip().lower()` on both sides; 8 tickets need it.
- Order refs: canonicalise `LF######`, `#\d{6}`, `order [order] \d{6}` → `LF-\d{6}`; never fuzzy-match; missing/unknown order → `ORDER_NOT_FOUND`.
- Dates: accept ISO `YYYY-MM-DD` and `DD/MM/YYYY`; never `MM/DD`. Treat naive carrier/refund timestamps as UTC; use ticket `received_at` as `as_of`.
- Money: strip `$`, `USD `, commas; `Order Total` = line value excluding `shipping_fee`.
- Dedupe orders by `order_id` (47 unique, 2 duplicate rows).
- Attachments: image `content_type` + presence only; never decode empty base64.

---

## 2. Response mapping (contract)

`TicketResponse` is assembled only from engine output:

| Response field | Source |
|---|---|
| `ticket_id`, `run_id` | input + generated run id |
| `ticket_type` | extraction (validated enum) |
| `resolved.order_id`, `resolved.customer_id` | normaliser + customer lookup (or null) |
| `outcome` | rules engine |
| `reason_codes` | rules engine (policy codes only) |
| `checks[]` | one `Check` per executed check with result + evidence + `policy_clause` |
| `human_approval_required` | rules engine |
| `approver_role` | rules engine (`SUPPORT_LEAD`/`TRUST_SAFETY`/`CARRIER_CLAIMS` or null) |
| `draft_reply` | template rendered from decision values |
| `trace` | model calls, tools, documents (`RP-3.2` v3.2), cost |

---

## 3. Open cases — copy-ready one-line rules (`COPY → APPROACH.md / ARTEFACT.md`)

> **BEGIN COPY BLOCK — 19 open-case rules**

1. **O1 — Change-of-mind return within 30 days:** refund the affected line value (`qty × unit_price`); after 30 days → `REJECT` with `OUT_OF_WINDOW`.
2. **O2 — Shipping fees:** never refund `shipping_fee`; refund affected line value only and explain with `PARTIAL_LINES`.
3. **O3 — Day counting:** count whole calendar days in UTC from the `delivered_at` date, inclusive of day 30; days 31–45 are the damage exception; >45 is out.
4. **O4 — Photo evidence:** accept any attachment whose `content_type` starts with `image/`; never decode or analyse `content_base64`.
5. **O5 — Partial quantity:** refund `affected_qty × unit_price`, never more than ordered quantity; leave undamaged units untouched.
6. **O6 — Partial prior refund:** subtract the sum of `ISSUED` amounts for `(order_id, line_no)` and propose the remaining balance; zero balance → `ALREADY_REFUNDED`.
7. **O7 — Non-ISSUED refunds:** count only `ISSUED` refunds against a line; ignore other statuses.
8. **O8 — Order vs carrier status conflict:** treat the carrier `delivered_at` / shipment status as authoritative for delivery and windows.
9. **O9 — Multiple orders in one ticket:** process one order per request; if several valid orders are cited, return `PEND` with `human_approval_required` true and `approver_role` `SUPPORT_LEAD` for human triage.
10. **O10 — Carrier says DELIVERED but customer disputes:** `ESCALATE` to `CARRIER_CLAIMS` with empty `reason_codes` (policy codes only — never invent `DELIVERY_DISPUTE`); propose no refund.
11. **O11 — Damage claimed before delivery:** propose no refund until delivery; reply with current status (`PROCEED`) and re-evaluate the window after delivery.
12. **O12 — Duplicate/forwarded ticket:** treat as the same request and process once; reply referencing the existing outcome, never issue a second refund.
13. **O13 — Exactly $150.00:** proceed without approval (inclusive bound); only amounts above $150.00 require the Support Lead.
14. **O14 — No resolvable order reference:** parse `subject + body`; if the sender has exactly one order on file, use it; if several, return `UNABLE_TO_VERIFY` and ask which order; if the sender email is not on file, return `INVALID_REQUEST`; never guess or fuzzy-match an order.
15. **O15 — Non-USD currency:** return `INVALID_REQUEST`; the contract and dataset are USD-only.
16. **O16 — Status query for `UNAVAILABLE` shipment:** return `UNABLE_TO_VERIFY` with `CARRIER_STATUS_UNAVAILABLE`.
17. **O17 — Lost-in-transit clock:** evaluate the >10-day silence against `ticket.received_at` using `last_event_at`, and record that `as_of` in evidence.
18. **O18 — Override + identity mismatch together:** policy override wins — `ESCALATE` to `TRUST_SAFETY`, while recording the identity discrepancy in evidence.
19. **O19 — Ledger action/status per outcome:** `PEND` → `REFUND`/`PROPOSED`, then `APPROVED` or `REJECTED` by decision; auto `PROCEED`/`PROCEED_MODIFIED` refunds → `REFUND`/`PROPOSED` (the service never processes money); `REJECT`/`INVALID_REQUEST`/`UNABLE_TO_VERIFY`/status answers → `REPLY`; `ESCALATE` → `ESCALATION`.

> **END COPY BLOCK**

---

## 4. What exists in `src/` and the repo today

| Path | Contents | Action |
|---|---|---|
| `src/solution.py` | 10-line stub: `main()` raises `NotImplementedError` | Build the entry point here |
| `src/__init__.py` | empty | Keep |
| `ARTEFACT.html` | 13-line placeholder; grader reads this exact file | Fill later; do not rename/move |
| `ARTEFACT.md` | placeholder deliverable | Optional mirror |
| `APPROACH.md` | 4 empty section headings | Fill with architecture/reasons |
| `requirements.txt` | numpy, pandas, scikit-learn, scipy, matplotlib, joblib (no web/pydantic/sqlite libs) | Add `pydantic` (schema); SQLite is stdlib |
| `opencode.json` | Cadra provider config | Leave |
| `dataroom/` | all source data | Read-only |

No service code exists yet beyond the empty stub.
