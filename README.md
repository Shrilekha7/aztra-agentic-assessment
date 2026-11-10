# Larkfield Home support agent

Deterministic Customer Care service for refunds, returns, delivery issues and order-status
questions. The LLM extracts facts only; Python policy code makes every money and approval
decision. See [APPROACH.md](APPROACH.md) and [ARTEFACT.md](ARTEFACT.md).

## Prepare the data

```bash
python -m src.prepare
```

This reads `dataroom/` read-only and writes `build/larkfield.db` plus
`build/prepare_report.json`. It is safe to rerun; the build directory is generated.
Latest report: 49 order rows deduped to 47, 8 ticket emails normalised, 4 shipment
chronology flags, 3 policy documents copied.

## Run locally

```bash
python -m src.prepare
PORT=8000 python -m src.main
```

On Windows PowerShell:

```powershell
$env:PORT=8000; python -m src.main
```

Endpoints: `GET /health`, `POST /tickets`, `GET /runs/{run_id}`,
`POST /runs/{run_id}/decision`, `GET /ledger`.

Environment variables:

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Groq key for LLM extraction. If absent the service falls back to regex. |
| `MODEL_ID` | Model id; code default `llama-3.1-8b-instant` (this workspace sets `qwen/qwen3.8-27b` locally). |

## Deploy on Render

Render config lives in `render.yaml`. Create a Blueprint from the repository.
The build command is `python -m pip install -r requirements.txt && python -m src.prepare`;
the start command is `uvicorn src.main:app --host 0.0.0.0 --port $PORT`. Set `GROQ_API_KEY`
and `MODEL_ID` as secret environment variables (`sync: false`).

## Evaluate

```bash
python evals/run_eval.py --base-url http://127.0.0.1:8000
python evals/run_eval.py --base-url https://aztra-agentic-assessment.onrender.com
```

Results are written to `evals/results/results_<UTC timestamp>.json`. The runner executes
cases in order, matches `outcome`, `human_approval_required` and `approver_role` exactly,
and scores `reason_codes` and `amount` separately. Duplicate and double-approve cases also
assert ledger state.

## Run fields

`POST /tickets` returns the contract `TicketResponse`. `GET /runs/{run_id}` adds:

| Field | Meaning |
|---|---|
| `status` | `OPEN`, `AWAITING_APPROVAL`, `APPROVED`, `REJECTED`, or `CLOSED` |
| `decision` | `{decision, approver, decided_at}`, or `null` before a PEND decision |
| `amount_usd` | Proposed refund amount, or `null` for reply/escalation outcomes |
| `latency_ms` | Whole-request processing time |
| `fallback_reason` | Why the LLM call fell back to regex, or `null` |

Runs and the ledger live in `build/runtime.db`; cleaned source tables live in
`build/larkfield.db`. Both are generated and ignored by git.

## Known limits

- Render free tier spins down; the first request after idle can be slow (cold start).
- `build/runtime.db` is not a persistent disk, so runs and the ledger reset on redeploy.
- The service never moves money. Auto-approved refunds are recorded as `REFUND/PROPOSED`.
- Replies are template-built; the model never authors customer-visible text.
- Multi-order tickets are sent to human triage rather than split automatically.
