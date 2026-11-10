"""Load the response contract without modifying the data-room file."""

from __future__ import annotations

import importlib.util
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "dataroom" / "schema" / "response_schema.py"

warnings.filterwarnings(
    "ignore",
    message='Field "model_calls" in Trace has conflict with protected namespace "model_".',
)

_spec = importlib.util.spec_from_file_location("larkfield_response_schema", SCHEMA_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load response schema from {SCHEMA_PATH}")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

Attachment = _module.Attachment
Ticket = _module.Ticket
TicketType = _module.TicketType
Outcome = _module.Outcome
CheckResult = _module.CheckResult
ApproverRole = _module.ApproverRole
Resolved = _module.Resolved
Evidence = _module.Evidence
Check = _module.Check
DraftReply = _module.DraftReply
ModelCall = _module.ModelCall
DocumentUsed = _module.DocumentUsed
Trace = _module.Trace
TicketResponse = _module.TicketResponse
Decision = _module.Decision
LedgerEntry = _module.LedgerEntry
