"""FastAPI service for the Larkfield support desk."""

from __future__ import annotations

import os
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from src import data, runtime
from src.contracts import (
    Check,
    Decision,
    DocumentUsed,
    Evidence,
    LedgerEntry,
    ModelCall,
    Resolved,
    TicketResponse,
    Trace,
)
from src.extract import DEFAULT_EXTRACTOR, ExtractionResult, coerce_ticket
from src.llm_extract import LLMExtractor
from src.reply import build_draft_reply
from src.rules import DecisionResult, evaluate

EXTRACTOR = LLMExtractor()

@asynccontextmanager
async def lifespan(_: FastAPI):
    data.ensure_prepare()
    runtime.init()
    yield


app = FastAPI(title="Larkfield Home Support", lifespan=lifespan)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace(tools: list[str], extraction: ExtractionResult | None = None) -> Trace:
    model_calls: list[ModelCall] = []
    cost = 0.0
    tool_names = list(tools)
    if extraction is not None:
        for call in extraction.model_calls:
            model_calls.append(
                ModelCall(
                    model=str(call.get("model") or ""),
                    input_tokens=int(call.get("input_tokens") or 0),
                    output_tokens=int(call.get("output_tokens") or 0),
                )
            )
        cost = float(extraction.cost_usd or 0.0)
        tool_names.extend(extraction.extractor_tools)
    return Trace(
        model_calls=model_calls,
        tools_called=list(dict.fromkeys(tool_names)),
        documents_used=[DocumentUsed(document="RP-3.2", version="3.2")],
        cost_usd=round(cost, 8),
    )


def _fallback_decision(ticket: dict[str, Any], extraction: ExtractionResult | None) -> DecisionResult:
    return DecisionResult(
        ticket_type=extraction.ticket_type if extraction else "OTHER",
        outcome="UNABLE_TO_VERIFY",
        reason_codes=[],
        human_approval_required=False,
        approver_role=None,
        checks=[
            Check(
                check_id="CHK_INTERNAL_ERROR",
                result="UNABLE_TO_VERIFY",
                policy_clause=None,
                evidence=[Evidence(source="tickets", ref=str(ticket.get("ticket_id")), value="internal error")],
            )
        ],
        resolved_order_id=None,
        resolved_customer_id=None,
        amount_usd=None,
        status="CLOSED",
        condition=extraction.condition if extraction else "unknown",
        as_of=str(ticket.get("received_at")),
        ledger_action="REPLY",
        ledger_status="APPROVED",
        fingerprint="",
    )


def _assemble_response(
    ticket: dict[str, Any], extraction: ExtractionResult, decision: DecisionResult, run_id: str
) -> TicketResponse:
    reply = build_draft_reply(ticket, decision)
    return TicketResponse(
        ticket_id=str(ticket.get("ticket_id")),
        run_id=run_id,
        ticket_type=decision.ticket_type,
        resolved=Resolved(
            order_id=decision.resolved_order_id,
            customer_id=decision.resolved_customer_id,
        ),
        outcome=decision.outcome,
        reason_codes=decision.reason_codes,
        checks=decision.checks,
        human_approval_required=decision.human_approval_required,
        approver_role=decision.approver_role,
        draft_reply=reply,
        trace=_trace(decision.tools_called, extraction),
    )


def _persist(
    *,
    run_id: str,
    ticket: dict[str, Any],
    extraction: ExtractionResult | None,
    decision: DecisionResult,
    response: TicketResponse,
    latency_ms: float,
    created_at: str,
) -> None:
    runtime.create_run(
        run_id=run_id,
        ticket_id=str(ticket.get("ticket_id")),
        received_at=str(ticket.get("received_at")),
        response=response.model_dump(mode="json"),
        extraction=extraction.to_dict() if extraction else {},
        status=decision.status,
        amount_usd=decision.amount_usd,
        latency_ms=latency_ms,
        fingerprint=decision.fingerprint,
        created_at=created_at,
    )
    runtime.record_ledger(
        run_id=run_id,
        ticket_id=str(ticket.get("ticket_id")),
        action=decision.ledger_action,
        status=decision.ledger_status,
        amount_usd=decision.amount_usd,
        recorded_at=created_at,
        fingerprint=decision.fingerprint,
        refund_lines=decision.refund_lines,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tickets", response_model=TicketResponse)
def create_ticket(payload: Any = Body(default=None)) -> TicketResponse:
    started = time.perf_counter()
    run_id = uuid.uuid4().hex
    created_at = _now()
    ticket = coerce_ticket(payload)
    extraction: ExtractionResult | None = None

    try:
        extraction = EXTRACTOR.extract(ticket, data.product_names())
        decision = evaluate(ticket, extraction, runtime.existing_refund)
        response = _assemble_response(ticket, extraction, decision, run_id)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        _persist(
            run_id=run_id,
            ticket=ticket,
            extraction=extraction,
            decision=decision,
            response=response,
            latency_ms=latency_ms,
            created_at=created_at,
        )
        return response
    except Exception:
        traceback.print_exc()
        decision = _fallback_decision(ticket, extraction)
        response = _assemble_response(ticket, extraction or DEFAULT_EXTRACTOR.extract(ticket), decision, run_id)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        try:
            _persist(
                run_id=run_id,
                ticket=ticket,
                extraction=extraction,
                decision=decision,
                response=response,
                latency_ms=latency_ms,
                created_at=created_at,
            )
        except Exception:
            traceback.print_exc()
        return response


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> Any:
    run = runtime.get_run(run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"detail": "run not found"})
    return run


@app.post("/runs/{run_id}/decision")
def decide(run_id: str, body: Decision) -> Any:
    kind, run = runtime.decide_run(run_id, body.decision, body.approver, _now())
    if kind == "missing":
        return JSONResponse(status_code=404, content={"detail": "run not found"})
    if kind == "conflict":
        return JSONResponse(status_code=409, content=run)
    return run


@app.get("/ledger", response_model=list[LedgerEntry])
def get_ledger() -> list[LedgerEntry]:
    return [LedgerEntry(**entry) for entry in runtime.ledger_entries()]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
