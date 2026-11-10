# Artefact — Larkfield Home support agent

## 1. What prepare found and did

`python -m src.prepare` reads `dataroom/` read-only and writes `build/larkfield.db`,
`build/prepare_report.json` and the cleaned tables. Real counts from the report:

- Tables: 53 customers, 47 orders, 57 order items, 3 refunds, 47 shipments, 40 tickets,
  3 policy documents, 15 eval labels.
- Orders: 49 rows in, 47 after dedupe; duplicates `LF-100216` and `LF-100239`;
  raw total $8,031.50 vs deduped $7,536.50, i.e. $495.00 inflation.
- Money: raw `Order Total` forms were 17 `$`-prefixed, 16 `USD ` prefixed and 16 plain;
  49 parsed, 0 unparseable; 47 remain after dedupe.
- Dates: 36 ISO and 13 `DD/MM/YYYY` order dates; 3 would be ambiguous if read as `MM/DD`.
  Naive timestamps were assumed UTC: 47 `shipped_at`, 34 `delivered_at`,
  44 `last_event_at`, plus 3 refund timestamps.
- Emails: 0 customer emails and 8 ticket emails needed `strip().lower()`
  (6 support inbox, 2 public).
- Shipments: 4 rows had `last_event_at < shipped_at` and were kept with
  `timestamp_anomaly=1` (`SHP-5010`, `SHP-5011`, `SHP-5029`, `SHP-5039`);
  13 `delivered_at` and 3 `last_event_at` values were empty.
- Tickets: 4 duplicate-body clusters (`T002/T027`, `T005/T028`, `T009/T029`,
  `T014/T030`), 10 attachments all with empty base64, 2 non-existent order references
  (`T022` `LF-109777`, `P04` `LF-116511`), 1 owner mismatch (`T023`), 0 missing references.
- Referential integrity: 0 orphan order items, shipments, refunds or customers.

The four shipment anomalies are flagged, not repaired, because the carrier feed is the
authoritative record. Dedupe, email normalisation, money parsing and UTC date parsing are
applied in [src/prepare.py](src/prepare.py).

## 2. Policy used and why

- **RP-3.2 v3.2** (effective 2026-07-01) governs refunds, returns, delivery and order-status
  requests. Its header says it replaces RP-3.1 and all earlier customer-care guidance.
- **HC-FAQ v2** (published 2025-10-14) is superseded: its 14-day window, $250 auto threshold
  and 5-day stale rule conflict with the newer dedicated Customer Care policy.
- **WH-PICK-1.4** (effective 2026-08-15) is out of scope: its own Scope says it does not set
  Customer Care return, refund or approval rules, and it deals in stock cost, not payments.

Only the ten policy reason codes are emitted. The engine is in [src/rules.py](src/rules.py).

## 3. Evaluation before and after O12

- **Before** (`evals/results/results_20261110T135537Z.json`): 20/21 passed (95.24%).
  `NEG-DUP-T002` failed: it returned `PROCEED` instead of `REJECT`/`ALREADY_REFUNDED`;
  the ledger assertion already showed exactly one refund row.
- **After** (`evals/results/results_20261110T140115Z.json`): 21/21 passed (100%).
  O12 now fingerprints normalised sender, order, affected line numbers and action. A
  matching PROPOSED/APPROVED REFUND makes `CHK_PROCESS_ONCE` fail, returns
  `REJECT`/`ALREADY_REFUNDED`, records ledger `REPLY`, and the reply references the earlier
  run. Reason-code matches: 8/8; amount matches: 4/4.
- **Live deployed run** (`evals/results/results_20261110T141715Z.json`): 21/21 passed
  (100%), median cost $0.000968, median latency 463.030 ms.
- Cases: [evals/cases.json](evals/cases.json); runner and pass rule:
  [evals/run_eval.py](evals/run_eval.py).

## 4. Cost and latency measurement

- Cost: for each model call, measured `input_tokens` and `output_tokens` are multiplied by the per-model prices in `src/pricing.py`.
- Latency: wall-clock time from `POST /tickets` entry to persistence is recorded as `latency_ms`.
- Median cost per request in the local AFTER eval: $0.000968.

## 5. Open-case rules (O1–O19)

1. **O1 — Change of mind within 30 days:** refund affected line value; after 30 days reject with `OUT_OF_WINDOW`.
2. **O2 — Shipping fees:** never refund `shipping_fee`; refund affected line value only and explain with `PARTIAL_LINES`.
3. **O3 — Day counting:** count whole UTC calendar days from `delivered_at`, inclusive of day 30; days 31–45 are the damage exception; >45 is out.
4. **O4 — Photo evidence:** accept any attachment whose `content_type` starts with `image/`; never decode base64.
5. **O5 — Partial quantity:** refund `affected_qty × unit_price`, never more than ordered quantity; leave undamaged units untouched.
6. **O6 — Partial prior refund:** subtract the sum of `ISSUED` amounts for the line and propose the remaining balance; zero balance is `ALREADY_REFUNDED`.
7. **O7 — Non-ISSUED refunds:** count only `ISSUED` refunds against a line.
8. **O8 — Order vs carrier status:** carrier `delivered_at` and shipment status are authoritative.
9. **O9 — Multiple orders:** process one order per request; several valid orders returns `PEND` with `SUPPORT_LEAD`.
10. **O10 — Carrier says DELIVERED but customer disputes:** `ESCALATE` to `CARRIER_CLAIMS` with empty `reason_codes`; propose no refund.
11. **O11 — Damage claimed before delivery:** propose no refund; reply with current status and re-evaluate after delivery.
12. **O12 — Duplicate/forwarded ticket:** fingerprint sender, order, affected lines and action; a matching PROPOSED/APPROVED REFUND returns `REJECT`/`ALREADY_REFUNDED`, ledger `REPLY`, and no second refund.
13. **O13 — Exactly $150.00:** proceed without approval; only amounts above $150.00 require the Support Lead.
14. **O14 — No order reference:** parse subject and body; use the sender's sole order, ask which order if several, and `INVALID_REQUEST` if the sender is not on file.
15. **O15 — Non-USD currency:** return `INVALID_REQUEST`; the data and contract are USD-only.
16. **O16 — Status query for UNAVAILABLE shipment:** return `UNABLE_TO_VERIFY` with `CARRIER_STATUS_UNAVAILABLE`.
17. **O17 — Lost-in-transit clock:** evaluate the >10-day silence against `ticket.received_at` using `last_event_at`, and record that `as_of`.
18. **O18 — Override plus identity mismatch:** policy override wins: `ESCALATE` to `TRUST_SAFETY`, noting the identity discrepancy.
19. **O19 — Ledger mapping:** PEND → REFUND/PROPOSED then APPROVED/REJECTED; auto PROCEED/PROCEED_MODIFIED → REFUND/PROPOSED; REJECT/INVALID_REQUEST/UNABLE_TO_VERIFY/status → REPLY; ESCALATE → ESCALATION.

## 6. What is not built

- No payment execution: the service never moves money; refunds stay `PROPOSED`.
- No LLM-authored customer replies; replies are templates in [src/reply.py](src/reply.py).
- No automatic splitting of multi-order tickets; O9 sends them to human triage.
- No persistent runtime disk: `build/runtime.db` resets on redeploy.
- No dashboard, authentication, queueing or production monitoring.
- No image-content analysis; only attachment presence and `content_type`.
- No non-USD support and no fuzzy order matching.
