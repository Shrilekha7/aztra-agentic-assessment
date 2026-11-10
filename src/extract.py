"""Deterministic ticket extraction. An LLM extractor can implement the same interface."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

ORDER_REF_RE = re.compile(
    r"\bLF-?(\d{6})\b|#(\d{6})\b|\border\s+(?:order\s+)?(\d{6})\b",
    re.IGNORECASE,
)
AMOUNT_RE = re.compile(r"(?:\$|USD\s*)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.IGNORECASE)
QUANTITY_PATTERNS = [
    re.compile(r"\bone\s+of\s+(?:my|the)\s+(?:two|2)\b", re.IGNORECASE),
    re.compile(r"\b(?:one|1)\s+of\s+(?:the\s+)?(?:two|2)\b", re.IGNORECASE),
    re.compile(r"\b(?:one|1)\s+chair\b", re.IGNORECASE),
]
INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore previous instructions", re.compile(r"ignore\s+(?:your\s+|all\s+|any\s+)?(?:previous|prior)\s+instructions", re.I)),
    ("[system]", re.compile(r"\[\s*system\s*\]", re.I)),
    ("pre-approved", re.compile(r"pre[\s-]?approved", re.I)),
    ("authorised to process", re.compile(r"authori[sz]ed\s+to\s+process", re.I)),
    ("mark human_approval_required", re.compile(r"mark\s+human[\s_-]*approval[\s_-]*required", re.I)),
    ("chargeback", re.compile(r"charge[\s-]?back", re.I)),
    ("lawyer", re.compile(r"\blawyer\b", re.I)),
    ("legal action", re.compile(r"legal\s+action", re.I)),
]


@dataclass
class ExtractionResult:
    ticket_type: str
    order_refs: list[str] = field(default_factory=list)
    order_refs_raw: list[str] = field(default_factory=list)
    requested_action: str = "other"
    requested_items: list[str] = field(default_factory=list)
    requested_amount: float | None = None
    requested_whole_order: bool = False
    requested_shipping: bool = False
    requested_quantity: int | None = None
    condition: str = "unknown"
    has_image_attachment: bool = False
    injection_flags: list[str] = field(default_factory=list)
    injection_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Extractor(Protocol):
    def extract(self, ticket: dict[str, Any], product_names: list[str] | None = None) -> ExtractionResult: ...


def coerce_ticket(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    ticket_id = str(payload.get("ticket_id") or f"unknown-{uuid.uuid4().hex[:8]}")
    received_at = str(payload.get("received_at") or datetime.now(timezone.utc).isoformat())
    raw_attachments = payload.get("attachments") or []
    attachments: list[dict[str, str]] = []
    if isinstance(raw_attachments, list):
        for item in raw_attachments:
            if not isinstance(item, dict):
                continue
            attachments.append(
                {
                    "file_name": str(item.get("file_name") or ""),
                    "content_type": str(item.get("content_type") or ""),
                    "content_base64": str(item.get("content_base64") or ""),
                }
            )
    sender = payload.get("from", payload.get("from_", ""))
    return {
        "ticket_id": ticket_id,
        "received_at": received_at,
        "channel": "email",
        "from": str(sender or ""),
        "to": str(payload.get("to") or ""),
        "subject": str(payload.get("subject") or ""),
        "body": str(payload.get("body") or ""),
        "attachments": attachments,
    }


def _detect_condition(text: str) -> str:
    t = text.lower()
    if any(word in t for word in ("damaged", "cracked", "broken", "broke", "smashed")):
        return "damaged"
    if "faulty" in t:
        return "faulty"
    if "wrong" in t or "received something else" in t:
        return "wrong"
    if any(
        phrase in t
        for phrase in (
            "not what i hoped",
            "not what i expected",
            "changed my mind",
            "no longer want",
            "do not want it",
            "don't want it",
        )
    ):
        return "change_of_mind"
    if any(
        phrase in t
        for phrase in ("where is my order", "order status", "status of my order", "status update")
    ):
        return "status"
    if any(
        phrase in t
        for phrase in (
            "never arrived",
            "hasn't arrived",
            "has not arrived",
            "not arrived",
            "didn't arrive",
            "did not arrive",
            "is it lost",
            "lost",
            "stuck",
            "hasn't moved",
            "has not moved",
            "hasn't changed",
            "has not changed",
            "still nothing",
            "gone missing",
        )
    ):
        return "not_delivered"
    return "unknown"


def _detect_ticket_type(text: str, condition: str) -> str:
    t = text.lower()
    if condition == "status":
        return "ORDER_STATUS"
    if condition == "not_delivered":
        return "DELIVERY_ISSUE"
    if condition == "change_of_mind" and "return" in t:
        return "RETURN"
    if condition in ("damaged", "faulty", "wrong") or "refund" in t:
        return "REFUND"
    if "return" in t:
        return "RETURN"
    return "OTHER"


def _detect_injection(text: str) -> list[str]:
    flags: list[str] = []
    for label, pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            flags.append(label)
    return list(dict.fromkeys(flags))


class RegexExtractor:
    def extract(
        self, ticket: dict[str, Any], product_names: list[str] | None = None
    ) -> ExtractionResult:
        subject = str(ticket.get("subject") or "")
        body = str(ticket.get("body") or "")
        text = f"{subject}\n{body}"
        lowered = text.lower()

        canonical_refs: list[str] = []
        raw_refs: list[str] = []
        for match in ORDER_REF_RE.finditer(text):
            digits = next(group for group in match.groups() if group)
            canonical_refs.append(f"LF-{digits}")
            raw_refs.append(match.group(0).strip())
        canonical_refs = list(dict.fromkeys(canonical_refs))
        raw_refs = list(dict.fromkeys(raw_refs))

        condition = _detect_condition(text)
        ticket_type = _detect_ticket_type(text, condition)

        matched_items: list[str] = []
        for name in sorted(product_names or [], key=len, reverse=True):
            if name.lower() in lowered:
                matched_items.append(name)

        requested_amount = None
        amount_match = AMOUNT_RE.search(text)
        if amount_match:
            requested_amount = float(amount_match.group(1).replace(",", ""))

        requested_quantity = None
        for pattern in QUANTITY_PATTERNS:
            if pattern.search(text):
                requested_quantity = 1
                break

        whole_order = any(
            phrase in lowered
            for phrase in (
                "whole order",
                "entire order",
                "full refund",
                "money back for the whole order",
                "refund my whole order",
            )
        )
        requested_shipping = any(
            phrase in lowered
            for phrase in ("delivery charge", "shipping fee", "shipping", "postage", "delivery fee")
        )

        injection_flags = _detect_injection(text)

        if condition == "not_delivered":
            action = "delivery complaint"
        elif condition == "status":
            action = "status"
        elif "return" in lowered and condition == "change_of_mind":
            action = "return"
        elif "refund" in lowered:
            action = "refund"
        elif "return" in lowered:
            action = "return"
        else:
            action = "other"

        has_image = any(
            str(attachment.get("content_type") or "").startswith("image/")
            for attachment in ticket.get("attachments") or []
            if isinstance(attachment, dict)
        )

        return ExtractionResult(
            ticket_type=ticket_type,
            order_refs=canonical_refs,
            order_refs_raw=raw_refs,
            requested_action=action,
            requested_items=matched_items,
            requested_amount=requested_amount,
            requested_whole_order=whole_order,
            requested_shipping=requested_shipping,
            requested_quantity=requested_quantity,
            condition=condition,
            has_image_attachment=has_image,
            injection_flags=injection_flags,
            injection_detected=bool(injection_flags),
        )


DEFAULT_EXTRACTOR: Extractor = RegexExtractor()


def extract_ticket(
    payload: Any, product_names: list[str] | None = None, extractor: Extractor | None = None
) -> tuple[dict[str, Any], ExtractionResult]:
    ticket = coerce_ticket(payload)
    engine = extractor or DEFAULT_EXTRACTOR
    return ticket, engine.extract(ticket, product_names)
