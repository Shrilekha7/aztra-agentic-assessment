from __future__ import annotations

from typing import Any

from src import data
from src.contracts import DraftReply
from src.rules import DecisionResult

REASON_PHRASES = {
    "OUT_OF_WINDOW": "the request is outside the allowed return window",
    "ORDER_NOT_FOUND": "we could not find that order",
    "CUSTOMER_MISMATCH": "the order does not match the email address on file",
    "CARRIER_STATUS_UNAVAILABLE": "the carrier has not given us a current status",
    "LOST_IN_TRANSIT": "the shipment has had no carrier movement for more than 10 days",
    "POLICY_OVERRIDE_ATTEMPT": "the message asks us to bypass our normal policy",
    "ALREADY_REFUNDED": "the affected line has already been refunded",
    "APPROVAL_REQUIRED": "the amount needs Support Lead approval",
    "PARTIAL_LINES": "only the affected lines can be refunded",
    "DAMAGE_EXCEPTION": "the photo supports the damaged-item window",
}


def _amount(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "the requested amount"


def _items(decision: DecisionResult) -> str:
    if decision.refund_lines:
        return ", ".join(line["product_name"] for line in decision.refund_lines)
    if decision.requested_items:
        return ", ".join(decision.requested_items)
    if decision.items and len(decision.items) == 1:
        return str(decision.items[0]["product_name"])
    return "the affected item"


def _order(decision: DecisionResult) -> str:
    return decision.resolved_order_id or "your order"


def _reason(decision: DecisionResult) -> str:
    phrases = [REASON_PHRASES[code] for code in decision.reason_codes if code in REASON_PHRASES]
    return "; ".join(dict.fromkeys(phrases)) if phrases else "our policy checks"


def build_draft_reply(ticket: dict[str, Any], decision: DecisionResult) -> DraftReply:
    sender = data.normalise_email(ticket.get("from"))
    order = _order(decision)
    items = _items(decision)
    amount = _amount(decision.amount_usd)
    outcome = decision.outcome
    role = decision.approver_role
    shipment = decision.shipment or {}
    ship_status = str(shipment.get("status") or "")

    if decision.ledger_action == "REPLY" and outcome == "PROCEED":
        status_text = ship_status or "being tracked"
        last_event = shipment.get("last_event_at") or "no recent scan"
        body = (
            f"Hello,\n\nThank you for your message about {order}. The carrier status is "
            f"{status_text}; the latest scan we have is {last_event}. No action is needed "
            f"from you at this stage.\n\nKind regards,\nLarkfield Home Support"
        )
    elif outcome in ("PROCEED", "PROCEED_MODIFIED") and decision.amount_usd is not None:
        extra = ""
        if outcome == "PROCEED_MODIFIED" or "PARTIAL_LINES" in decision.reason_codes:
            extra = " This covers only the affected line(s); shipping and unaffected items are not included."
        if "ALREADY_REFUNDED" in decision.reason_codes:
            extra += " Any amount already issued for the affected line has been subtracted."
        if "DAMAGE_EXCEPTION" in decision.reason_codes:
            extra += " The photo supports the damaged-item window."
        body = (
            f"Hello,\n\nWe have approved a refund of {amount} for {items} on {order}."
            f"{extra}\n\nKind regards,\nLarkfield Home Support"
        )
    elif outcome == "PEND":
        body = (
            f"Hello,\n\nWe have reviewed your request for {order}. A refund of {amount} for "
            f"{items} is ready but needs Support Lead approval. We will write again once the "
            f"decision is recorded. Reason: {_reason(decision)}.\n\nKind regards,\nLarkfield Home Support"
        )
    elif outcome == "ESCALATE":
        if role == "CARRIER_CLAIMS":
            team = "Carrier Claims"
            detail = "The shipment has had no carrier movement for more than 10 days, so we have escalated it as lost in transit."
        else:
            team = "Trust & Safety"
            detail = "The message includes language that asks us to bypass policy, so we have escalated it for review."
        body = (
            f"Hello,\n\nWe have escalated {order} to {team}. {detail} No refund is being "
            f"proposed while that review is under way.\n\nKind regards,\nLarkfield Home Support"
        )
    elif outcome == "REJECT" and decision.duplicate_of_run_id:
        body = (
            f"Hello,\n\nThis matches your earlier request {decision.duplicate_of_run_id} for "
            f"{order}. We have kept that existing refund request and will not propose a second "
            f"refund. If you need an update, please reply to this message.\n\n"
            f"Kind regards,\nLarkfield Home Support"
        )
    elif outcome == "REJECT":
        body = (
            f"Hello,\n\nWe are unable to approve a refund for {items} on {order}. Reason: "
            f"{_reason(decision)}.\n\nKind regards,\nLarkfield Home Support"
        )
    elif outcome == "INVALID_REQUEST":
        body = (
            f"Hello,\n\nWe could not process this request for {order}. Reason: "
            f"{_reason(decision)}. Please reply with the correct order number and the email "
            f"address on your account.\n\nKind regards,\nLarkfield Home Support"
        )
    else:
        if "CARRIER_STATUS_UNAVAILABLE" in decision.reason_codes:
            detail = "The carrier has not returned a current status, so we cannot verify the delivery yet."
        else:
            detail = "We need a little more information before we can verify the order."
        body = (
            f"Hello,\n\nWe are checking {order}. {detail} Please reply with the order number "
            f"if you have it, and we will continue as soon as we can.\n\nKind regards,\nLarkfield Home Support"
        )

    subject = f"Re: {order}" if decision.resolved_order_id else "Re: your Larkfield Home request"
    return DraftReply(to=sender, subject=subject, body=body)
