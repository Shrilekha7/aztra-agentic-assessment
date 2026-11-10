# Approach

## What we found

The data room had 49 order rows but 47 unique orders (`LF-100216` and `LF-100239` duplicated, $495 inflation), mixed ISO/DD-MM dates, money stored as `$`, `USD` and plain numbers, 8 ticket emails needing strip/lower, 4 shipments with `last_event_at` before `shipped_at`, 4 forwarded ticket clusters, 2 non-existent order references and 1 owner mismatch. Three tickets attempted prompt injection. RP-3.2 v3.2 governs; FAQ v2 is superseded and WH-PICK-1.4 is out of scope.

## Architecture and why

The LLM ([src/llm_extract.py](src/llm_extract.py)) only extracts facts inside untrusted delimiters; regex order references and attachment metadata stay authoritative. Deterministic policy code ([src/rules.py](src/rules.py)) makes every outcome, amount, approval and check decision because policy is arithmetic, reproducible and injection-resistant. Replies are templates ([src/reply.py](src/reply.py)) built only from decision values. Runs and an idempotent ledger persist in SQLite ([src/runtime.py](src/runtime.py)); O12 fingerprints sender, order, affected lines and action so a forwarded request cannot create a second refund. Data prep ([src/prepare.py](src/prepare.py)) reads `dataroom/` read-only.

## Evaluation

[evals/cases.json](evals/cases.json) has 15 labelled tickets plus 6 negative cases. [evals/run_eval.py](evals/run_eval.py) requires exact outcome, human flag and approver role; reason codes and amounts are scored separately. Before O12 it passed 20/21; after O12 it passed 21/21.
