"""Deterministic policy engine for the Larkfield support desk."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from src import data
from src.contracts import Check, Evidence
from src.extract import ExtractionResult


@dataclass
class DecisionResult:
    ticket_type: str
    outcome: str
    reason_codes: list[str]
    human_approval_required: bool
    approver_role: str | None
    checks: list[Check]
    resolved_order_id: str | None
    resolved_customer_id: str | None
    amount_usd: float | None
    status: str
    refund_lines: list[dict[str, Any]] = field(default_factory=list)
    order: dict[str, Any] | None = None
    shipment: dict[str, Any] | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    condition: str = "unknown"
    requested_items: list[str] = field(default_factory=list)
    requested_whole_order: bool = False
    requested_shipping: bool = False
    as_of: str = ""
    tools_called: list[str] = field(default_factory=list)
    ledger_action: str = "REPLY"
    ledger_status: str = "APPROVED"
    fingerprint: str = ""


def _evidence(source: str, ref: Any, value: Any) -> Evidence:
    return Evidence(source=source, ref=str(ref), value=str(value))


def _add(
    checks: list[Check],
    check_id: str,
    result: str,
    clause: str | None,
    evidence: list[Evidence] | None = None,
) -> None:
    checks.append(
        Check(
            check_id=check_id,
            result=result,
            policy_clause=clause,
            evidence=evidence or [],
        )
    )


def _days_between(start: str | None, end: datetime) -> int | None:
    start_dt = data.parse_iso(start)
    if start_dt is None:
        return None
    return (end.date() - start_dt.date()).days


def _silence_days(last_event: str | None, as_of: datetime) -> float | None:
    last_dt = data.parse_iso(last_event)
    if last_dt is None:
        return None
    return (as_of - last_dt).total_seconds() / 86400.0


def _fingerprint(sender: str, order_id: str | None, line_numbers: list[int], action: str) -> str:
    joined = ",".join(str(number) for number in sorted(line_numbers))
    raw = f"{data.normalise_email(sender)}|{order_id or ''}|{joined}|{action}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def evaluate(
    ticket: dict[str, Any],
    extraction: ExtractionResult,
    existing_refund_lookup: Callable[[str | None, int | None], dict[str, Any] | None] | None = None,
) -> DecisionResult:
    checks: list[Check] = []
    tools: list[str] = []
    reason_codes: list[str] = []
    sender = data.normalise_email(ticket.get("from"))
    try:
        as_of = data.parse_iso(ticket.get("received_at")) or datetime.now(timezone.utc)
    except Exception:
        as_of = datetime.now(timezone.utc)

    if extraction.injection_detected:
        _add(
            checks,
            "CHK_POLICY_OVERRIDE",
            "FAIL",
            "RP-3.2 §5.2",
            [
                _evidence(
                    "tickets",
                    ticket.get("ticket_id"),
                    "injection tokens: " + ", ".join(extraction.injection_flags),
                )
            ],
        )
    else:
        _add(
            checks,
            "CHK_POLICY_OVERRIDE",
            "PASS",
            "RP-3.2 §5.2",
            [_evidence("tickets", ticket.get("ticket_id"), "no override tokens detected")],
        )

    customer = data.find_customer(sender)
    tools.append("lookup_customer")

    order: dict[str, Any] | None = None
    valid_orders: list[dict[str, Any]] = []
    invalid_refs: list[str] = []
    no_reference_reason = ""
    if extraction.order_refs:
        for ref in extraction.order_refs:
            tools.append("lookup_order")
            found = data.get_order(ref)
            if found is None:
                invalid_refs.append(ref)
            else:
                valid_orders.append(found)
        if valid_orders:
            _add(
                checks,
                "CHK_ORDER_EXISTS",
                "PASS",
                "RP-3.2 §4.1",
                [_evidence("orders", item["order_id"], item["status"]) for item in valid_orders]
                + [_evidence("tickets", ticket.get("ticket_id"), "refs: " + ", ".join(extraction.order_refs))],
            )
        else:
            _add(
                checks,
                "CHK_ORDER_EXISTS",
                "FAIL",
                "RP-3.2 §4.1",
                [_evidence("tickets", ticket.get("ticket_id"), "unresolved refs: " + ", ".join(invalid_refs))],
            )
        if len(valid_orders) > 1:
            _add(
                checks,
                "CHK_SINGLE_ORDER",
                "FAIL",
                "RP-3.2 §4.1 (O9)",
                [_evidence("orders", item["order_id"], "multiple valid orders cited") for item in valid_orders],
            )
        elif len(valid_orders) == 1:
            order = valid_orders[0]
            _add(
                checks,
                "CHK_SINGLE_ORDER",
                "PASS",
                "RP-3.2 §4.1 (O9)",
                [_evidence("orders", order["order_id"], "single valid order cited")],
            )
    else:
        if customer is None:
            _add(
                checks,
                "CHK_ORDER_EXISTS",
                "UNABLE_TO_VERIFY",
                "RP-3.2 §4.1 (O14)",
                [_evidence("customers", sender, "sender email not on file")],
            )
            no_reference_reason = "sender_not_on_file"
        else:
            customer_orders = data.get_orders_for_customer(customer["customer_id"])
            tools.append("lookup_order")
            if len(customer_orders) == 1:
                order = customer_orders[0]
                _add(
                    checks,
                    "CHK_ORDER_EXISTS",
                    "PASS",
                    "RP-3.2 §4.1 (O14)",
                    [
                        _evidence("orders", order["order_id"], "sole order for sender"),
                        _evidence("customers", customer["customer_id"], customer["email"]),
                    ],
                )
            elif len(customer_orders) > 1:
                _add(
                    checks,
                    "CHK_ORDER_EXISTS",
                    "UNABLE_TO_VERIFY",
                    "RP-3.2 §4.1 (O14)",
                    [
                        _evidence(
                            "orders",
                            customer["customer_id"],
                            "several orders on file: "
                            + ", ".join(item["order_id"] for item in customer_orders),
                        )
                    ],
                )
                no_reference_reason = "multiple_orders"
            else:
                _add(
                    checks,
                    "CHK_ORDER_EXISTS",
                    "UNABLE_TO_VERIFY",
                    "RP-3.2 §4.1 (O14)",
                    [_evidence("orders", customer["customer_id"], "no orders on file")],
                )
                no_reference_reason = "no_orders"

    ownership_ok: bool | None = None
    if order is not None:
        if customer is None or order["customer_id"] != customer["customer_id"]:
            ownership_ok = False
            _add(
                checks,
                "CHK_CUSTOMER_OWNERSHIP",
                "FAIL",
                "RP-3.2 §4.1",
                [
                    _evidence("orders", order["order_id"], f"owner {order['customer_id']}"),
                    _evidence("customers", sender, f"sender {customer['customer_id'] if customer else 'not on file'}"),
                ],
            )
        else:
            ownership_ok = True
            _add(
                checks,
                "CHK_CUSTOMER_OWNERSHIP",
                "PASS",
                "RP-3.2 §4.1",
                [
                    _evidence("orders", order["order_id"], f"owner {order['customer_id']}"),
                    _evidence("customers", customer["customer_id"], customer["email"]),
                ],
            )
    elif customer is None:
        ownership_ok = False
        _add(
            checks,
            "CHK_CUSTOMER_OWNERSHIP",
            "FAIL",
            "RP-3.2 §4.1",
            [_evidence("customers", sender, "sender email not on file")],
        )
    else:
        _add(
            checks,
            "CHK_CUSTOMER_OWNERSHIP",
            "UNABLE_TO_VERIFY",
            "RP-3.2 §4.1",
            [_evidence("customers", customer["customer_id"], "no resolved order to compare")],
        )

    _add(
        checks,
        "CHK_NO_NEARBY_ORDER",
        "PASS",
        "RP-3.2 §4.1",
        [_evidence("orders", order["order_id"] if order else "none", "no nearby order substituted")],
    )

    resolved_order_id = order["order_id"] if order else None
    resolved_customer_id = order["customer_id"] if order else (customer["customer_id"] if customer else None)

    def result(
        *,
        outcome: str,
        codes: list[str] | None = None,
        human: bool = False,
        role: str | None = None,
        status: str,
        amount: float | None = None,
        ledger_action: str = "REPLY",
        ledger_status: str = "APPROVED",
        refund_lines: list[dict[str, Any]] | None = None,
        fingerprint_lines: list[int] | None = None,
    ) -> DecisionResult:
        action = ledger_action
        if action == "REFUND" and not fingerprint_lines:
            fingerprint_lines = [line["line_no"] for line in (refund_lines or [])]
        return DecisionResult(
            ticket_type=extraction.ticket_type,
            outcome=outcome,
            reason_codes=codes or [],
            human_approval_required=human,
            approver_role=role,
            checks=checks,
            resolved_order_id=resolved_order_id,
            resolved_customer_id=resolved_customer_id,
            amount_usd=None if amount is None else round(amount, 2),
            status=status,
            refund_lines=refund_lines or [],
            order=order,
            shipment=None,
            items=[],
            condition=extraction.condition,
            requested_items=extraction.requested_items,
            requested_whole_order=extraction.requested_whole_order,
            requested_shipping=extraction.requested_shipping,
            as_of=as_of.isoformat(),
            tools_called=tools,
            ledger_action=ledger_action,
            ledger_status=ledger_status,
            fingerprint=_fingerprint(sender, resolved_order_id, fingerprint_lines or [], action),
        )

    if extraction.injection_detected:
        return result(
            outcome="ESCALATE",
            codes=["POLICY_OVERRIDE_ATTEMPT"],
            human=True,
            role="TRUST_SAFETY",
            status="CLOSED",
            ledger_action="ESCALATION",
        )

    if customer is None:
        return result(
            outcome="INVALID_REQUEST",
            codes=["CUSTOMER_MISMATCH"],
            status="CLOSED",
            ledger_action="REPLY",
        )

    if extraction.order_refs and not valid_orders:
        return result(
            outcome="INVALID_REQUEST",
            codes=["ORDER_NOT_FOUND"],
            status="CLOSED",
            ledger_action="REPLY",
        )

    if ownership_ok is False:
        return result(
            outcome="INVALID_REQUEST",
            codes=["CUSTOMER_MISMATCH"],
            status="CLOSED",
            ledger_action="REPLY",
        )

    if len(valid_orders) > 1:
        return result(
            outcome="PEND",
            codes=[],
            human=True,
            role="SUPPORT_LEAD",
            status="AWAITING_APPROVAL",
            ledger_action="REFUND",
            ledger_status="PROPOSED",
        )

    if order is None:
        return result(
            outcome="UNABLE_TO_VERIFY",
            codes=[],
            status="CLOSED",
            ledger_action="REPLY",
        )

    if str(order.get("currency") or "USD") != "USD":
        _add(
            checks,
            "CHK_CURRENCY",
            "FAIL",
            "RP-3.2 (O15)",
            [_evidence("orders", order["order_id"], f"currency={order.get('currency')}")],
        )
        return result(
            outcome="INVALID_REQUEST",
            codes=[],
            status="CLOSED",
            ledger_action="REPLY",
        )

    shipment = data.get_shipment(order["order_id"])
    items = data.get_order_items(order["order_id"])
    refunds = data.get_refunds(order["order_id"])
    tools.extend(["lookup_shipment", "lookup_order_items", "lookup_refunds"])

    is_delivery = extraction.ticket_type in ("ORDER_STATUS", "DELIVERY_ISSUE") or extraction.condition in (
        "not_delivered",
        "status",
    )

    def with_context(base: DecisionResult) -> DecisionResult:
        base.shipment = shipment
        base.items = items
        base.tools_called = tools
        return base

    if shipment is None:
        _add(
            checks,
            "CHK_CARRIER_STATUS",
            "UNABLE_TO_VERIFY",
            "RP-3.2 §4.2",
            [_evidence("shipments", order["order_id"], "no shipment row")],
        )
        if is_delivery:
            return with_context(
                result(
                    outcome="UNABLE_TO_VERIFY",
                    codes=["CARRIER_STATUS_UNAVAILABLE"],
                    status="CLOSED",
                    ledger_action="REPLY",
                )
            )
        return with_context(
            result(
                outcome="UNABLE_TO_VERIFY",
                codes=[],
                status="CLOSED",
                ledger_action="REPLY",
            )
        )

    ship_status = str(shipment.get("status") or "")
    delivered_at = shipment.get("delivered_at")
    last_event_at = shipment.get("last_event_at")

    if ship_status == "UNAVAILABLE" or (is_delivery and not last_event_at):
        _add(
            checks,
            "CHK_CARRIER_STATUS",
            "UNABLE_TO_VERIFY",
            "RP-3.2 §4.2",
            [
                _evidence(
                    "shipments",
                    shipment["shipment_id"],
                    f"status={ship_status}; last_event_at={last_event_at}",
                )
            ],
        )
        return with_context(
            result(
                outcome="UNABLE_TO_VERIFY",
                codes=["CARRIER_STATUS_UNAVAILABLE"],
                status="CLOSED",
                ledger_action="REPLY",
            )
        )

    _add(
        checks,
        "CHK_CARRIER_STATUS",
        "PASS",
        "RP-3.2 §4.2",
        [_evidence("shipments", shipment["shipment_id"], f"status={ship_status}")],
    )

    if is_delivery:
        silence = _silence_days(last_event_at, as_of)
        if silence is None:
            _add(
                checks,
                "CHK_LOST_IN_TRANSIT",
                "UNABLE_TO_VERIFY",
                "RP-3.2 §5.1",
                [_evidence("shipments", shipment["shipment_id"], "no last_event_at")],
            )
            return with_context(
                result(
                    outcome="UNABLE_TO_VERIFY",
                    codes=["CARRIER_STATUS_UNAVAILABLE"],
                    status="CLOSED",
                    ledger_action="REPLY",
                )
            )
        lost = silence > 10.0
        _add(
            checks,
            "CHK_LOST_IN_TRANSIT",
            "FAIL" if lost else "PASS",
            "RP-3.2 §5.1",
            [
                _evidence(
                    "shipments",
                    shipment["shipment_id"],
                    f"last_event_at={last_event_at}; as_of={as_of.isoformat()}; silence_days={silence:.2f}",
                )
            ],
        )
        if lost:
            return with_context(
                result(
                    outcome="ESCALATE",
                    codes=["LOST_IN_TRANSIT"],
                    human=True,
                    role="CARRIER_CLAIMS",
                    status="CLOSED",
                    ledger_action="ESCALATION",
                )
            )
        if extraction.condition == "not_delivered" and (
            ship_status == "DELIVERED" or delivered_at
        ):
            return with_context(
                result(
                    outcome="ESCALATE",
                    codes=[],
                    human=True,
                    role="CARRIER_CLAIMS",
                    status="CLOSED",
                    ledger_action="ESCALATION",
                )
            )
        _add(
            checks,
            "CHK_STATUS_MOVING",
            "PASS",
            "RP-3.2 §5.3",
            [
                _evidence(
                    "shipments",
                    shipment["shipment_id"],
                    f"status={ship_status}; last_event_at={last_event_at}",
                )
            ],
        )
        return with_context(
            result(
                outcome="PROCEED",
                codes=[],
                status="CLOSED",
                ledger_action="REPLY",
            )
        )

    if not delivered_at:
        _add(
            checks,
            "CHK_DELIVERY_DATE",
            "UNABLE_TO_VERIFY",
            "RP-3.2 §2.1",
            [_evidence("shipments", shipment["shipment_id"], "delivered_at is empty")],
        )
        if extraction.condition in ("damaged", "faulty") and ship_status != "DELIVERED":
            _add(
                checks,
                "CHK_ITEM_CONDITION",
                "PASS",
                "RP-3.2 §2.2",
                [_evidence("tickets", ticket.get("ticket_id"), extraction.condition)],
            )
            return with_context(
                result(
                    outcome="PROCEED",
                    codes=[],
                    status="CLOSED",
                    ledger_action="REPLY",
                )
            )
        return with_context(
            result(
                outcome="UNABLE_TO_VERIFY",
                codes=[],
                status="CLOSED",
                ledger_action="REPLY",
            )
        )

    _add(
        checks,
        "CHK_DELIVERY_DATE",
        "PASS",
        "RP-3.2 §2.1",
        [_evidence("shipments", shipment["shipment_id"], f"delivered_at={delivered_at}")],
    )

    days = _days_between(delivered_at, as_of)
    if days is None:
        return with_context(
            result(
                outcome="UNABLE_TO_VERIFY",
                codes=[],
                status="CLOSED",
                ledger_action="REPLY",
            )
        )

    damaged = extraction.condition in ("damaged", "faulty")
    _add(
        checks,
        "CHK_ITEM_CONDITION",
        "PASS",
        "RP-3.2 §2.2",
        [
            _evidence("tickets", ticket.get("ticket_id"), extraction.condition),
            _evidence("order_items", order["order_id"], ", ".join(item["product_name"] for item in items)),
        ],
    )

    if days <= 30:
        _add(
            checks,
            "CHK_WINDOW_30D",
            "PASS",
            "RP-3.2 §2.1, §2.3",
            [_evidence("shipments", shipment["shipment_id"], f"days_since_delivery={days}")],
        )
        _add(
            checks,
            "CHK_PHOTO_ATTACHMENT",
            "NOT_APPLICABLE",
            "RP-3.2 §2.6",
            [_evidence("tickets", ticket.get("ticket_id"), f"image_attachment={extraction.has_image_attachment}")],
        )
        _add(
            checks,
            "CHK_WINDOW_45D",
            "NOT_APPLICABLE",
            "RP-3.2 §2.6",
            [_evidence("shipments", shipment["shipment_id"], f"days_since_delivery={days}")],
        )
        damage_exception = False
    else:
        _add(
            checks,
            "CHK_WINDOW_30D",
            "FAIL",
            "RP-3.2 §2.1, §2.3",
            [_evidence("shipments", shipment["shipment_id"], f"days_since_delivery={days}")],
        )
        photo_ok = extraction.has_image_attachment
        _add(
            checks,
            "CHK_PHOTO_ATTACHMENT",
            "PASS" if photo_ok else "FAIL",
            "RP-3.2 §2.6",
            [
                _evidence(
                    "tickets",
                    ticket.get("ticket_id"),
                    f"image_attachment={extraction.has_image_attachment}",
                )
            ],
        )
        damage_exception = bool(damaged and photo_ok and days <= 45)
        _add(
            checks,
            "CHK_WINDOW_45D",
            "PASS" if damage_exception else "FAIL",
            "RP-3.2 §2.6",
            [
                _evidence(
                    "shipments",
                    shipment["shipment_id"],
                    f"days_since_delivery={days}; damaged={damaged}; photo={photo_ok}",
                )
            ],
        )
        if not damage_exception:
            return with_context(
                result(
                    outcome="REJECT",
                    codes=["OUT_OF_WINDOW"],
                    status="CLOSED",
                    ledger_action="REPLY",
                )
            )

    text = f"{ticket.get('subject') or ''}\n{ticket.get('body') or ''}".lower()
    all_items = items
    matched_items = [
        item for item in all_items if str(item["product_name"]).lower() in text
    ]
    if not matched_items:
        if len(all_items) == 1:
            matched_items = all_items
        elif extraction.requested_whole_order and extraction.condition == "change_of_mind":
            matched_items = all_items
        else:
            _add(
                checks,
                "CHK_AFFECTED_LINES",
                "UNABLE_TO_VERIFY",
                "RP-3.2 §2.4",
                [_evidence("order_items", order["order_id"], "could not identify affected lines")],
            )
            return with_context(
                result(
                    outcome="UNABLE_TO_VERIFY",
                    codes=[],
                    status="CLOSED",
                    ledger_action="REPLY",
                )
            )

    refund_lines: list[dict[str, Any]] = []
    prior_evidence: list[Evidence] = []
    qty_evidence: list[Evidence] = []
    fully_refunded = 0
    partly_refunded = 0
    quantity_capped = False
    for item in matched_items:
        ordered_qty = int(item["qty"])
        refund_qty = ordered_qty
        if extraction.requested_quantity is not None:
            refund_qty = min(extraction.requested_quantity, ordered_qty)
            if extraction.requested_quantity > ordered_qty:
                quantity_capped = True
        gross = refund_qty * float(item["unit_price"])
        prior = sum(
            float(row["amount"])
            for row in refunds
            if row["status"] == "ISSUED" and int(row["line_no"]) == int(item["line_no"])
        )
        net = max(gross - prior, 0.0)
        qty_evidence.append(
            _evidence(
                "order_items",
                f"{order['order_id']}:{item['line_no']}",
                f"ordered_qty={ordered_qty}; refund_qty={refund_qty}; unit_price={item['unit_price']}",
            )
        )
        prior_evidence.append(
            _evidence(
                "refunds",
                order["order_id"],
                f"line {item['line_no']}: issued={prior:.2f}; gross={gross:.2f}; net={net:.2f}",
            )
        )
        if prior > 0 and net <= 0.0:
            fully_refunded += 1
        elif prior > 0:
            partly_refunded += 1
        if net > 0.0:
            refund_lines.append(
                {
                    "order_id": order["order_id"],
                    "line_no": int(item["line_no"]),
                    "product_name": item["product_name"],
                    "amount_usd": round(net, 2),
                    "qty": refund_qty,
                    "gross_usd": round(gross, 2),
                    "prior_issued_usd": round(prior, 2),
                }
            )

    overreached = extraction.requested_whole_order and len(matched_items) < len(all_items)
    partial_lines = bool(overreached or extraction.requested_shipping)
    _add(
        checks,
        "CHK_AFFECTED_LINES",
        "FAIL" if overreached else "PASS",
        "RP-3.2 §2.4",
        [
            _evidence(
                "order_items",
                order["order_id"],
                f"affected={len(matched_items)}; order_lines={len(all_items)}; whole_order={extraction.requested_whole_order}",
            )
        ],
    )
    _add(
        checks,
        "CHK_QTY_LIMIT",
        "FAIL" if quantity_capped else "PASS",
        "RP-3.2 §2.5",
        qty_evidence,
    )
    _add(
        checks,
        "CHK_PRIOR_REFUND",
        "FAIL" if fully_refunded else "PASS",
        "RP-3.2 §2.5",
        prior_evidence,
    )

    total = round(sum(line["amount_usd"] for line in refund_lines), 2)
    _add(
        checks,
        "CHK_AMOUNT_CAP",
        "PASS",
        "RP-3.2 §2.5",
        [
            _evidence(
                "refunds",
                order["order_id"],
                f"proposed={total:.2f}; capped at unpaid affected-line value",
            )
        ],
    )

    process_once_lookup = existing_refund_lookup
    conflict = None
    if process_once_lookup is not None:
        for line in refund_lines:
            conflict = conflict or process_once_lookup(line["order_id"], line["line_no"])
    if conflict is not None:
        _add(
            checks,
            "CHK_PROCESS_ONCE",
            "FAIL",
            "RP-3.2 §3.3",
            [
                _evidence(
                    "ledger",
                    conflict.get("run_id"),
                    f"existing {conflict.get('action')} {conflict.get('status')} for {conflict.get('order_id')} line {conflict.get('line_no')}",
                )
            ],
        )
    else:
        _add(
            checks,
            "CHK_PROCESS_ONCE",
            "PASS",
            "RP-3.2 §3.3",
            [_evidence("ledger", order["order_id"], "no prior refund proposal for affected line(s)")],
        )

    if fully_refunded and total <= 0.0:
        return with_context(
            result(
                outcome="REJECT",
                codes=["ALREADY_REFUNDED"],
                status="CLOSED",
                ledger_action="REPLY",
            )
        )

    if partial_lines:
        reason_codes.append("PARTIAL_LINES")
    if fully_refunded:
        reason_codes.append("ALREADY_REFUNDED")
    if damage_exception:
        reason_codes.insert(0, "DAMAGE_EXCEPTION")

    modified = bool(
        partial_lines
        or fully_refunded
        or partly_refunded
        or quantity_capped
        or (extraction.requested_quantity is not None)
        or (
            extraction.requested_amount is not None
            and abs(extraction.requested_amount - total) > 0.01
        )
        or (extraction.requested_shipping)
    )

    if total > 150.0:
        _add(
            checks,
            "CHK_AUTHORITY_150",
            "FAIL",
            "RP-3.2 §3.1, §3.2",
            [_evidence("refunds", order["order_id"], f"proposed_amount={total:.2f}")],
        )
        reason_codes.append("APPROVAL_REQUIRED")
        return with_context(
            result(
                outcome="PEND",
                codes=reason_codes,
                human=True,
                role="SUPPORT_LEAD",
                status="AWAITING_APPROVAL",
                amount=total,
                ledger_action="REFUND",
                ledger_status="PROPOSED",
                refund_lines=refund_lines,
            )
        )

    _add(
        checks,
        "CHK_AUTHORITY_150",
        "PASS",
        "RP-3.2 §3.1, §3.2",
        [_evidence("refunds", order["order_id"], f"proposed_amount={total:.2f} <= 150.00")],
    )
    return with_context(
        result(
            outcome="PROCEED_MODIFIED" if modified else "PROCEED",
            codes=reason_codes,
            status="OPEN",
            amount=total,
            ledger_action="REFUND",
            ledger_status="PROPOSED",
            refund_lines=refund_lines,
        )
    )
