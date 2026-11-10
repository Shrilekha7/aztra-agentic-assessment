"""Build the cleaned Larkfield data store from the read-only data room.

Run from the repository root:

    python -m src.prepare

Reads ``dataroom/`` only. Writes ``build/larkfield.db`` and
``build/prepare_report.json``. The data room is never modified.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DATAROOM = ROOT / "dataroom"
DATA = DATAROOM / "data"
BUILD = ROOT / "build"
DB_PATH = BUILD / "larkfield.db"
REPORT_PATH = BUILD / "prepare_report.json"

DOC_RE = re.compile(r"Document:\s*([^|]+?)\s*\|\s*Version\s*([^|]+?)\s*\|")
ORDER_REF_RE = re.compile(
    r"\bLF-?(\d{6})\b|#(\d{6})\b|\border\s+(?:order\s+)?(\d{6})\b",
    re.IGNORECASE,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_csv(name: str) -> list[dict[str, str]]:
    with (DATA / name).open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def read_json(name: str) -> Any:
    with (DATAROOM / name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_amount(raw: str) -> tuple[float, str]:
    """Parse ``$1.00``, ``USD 1.00`` or plain numeric strings to float."""
    text = str(raw).strip()
    if text.startswith("$"):
        representation = "dollar_prefix"
        text = text[1:]
    elif text.upper().startswith("USD"):
        representation = "usd_prefix"
        text = text[3:]
    else:
        representation = "plain"
    text = text.replace(",", "").strip()
    return float(text), representation


def parse_utc(raw: str | None) -> tuple[str | None, str]:
    """Parse ISO/date/DD-MM-YYYY into UTC ISO.

    Supports ISO date (``YYYY-MM-DD``), ISO datetime (naive or aware) and
    ``DD/MM/YYYY``. Never interprets ``MM/DD/YYYY``. Naive values are assumed
    to be UTC.
    """
    if raw is None:
        return None, "missing"
    text = str(raw).strip()
    if not text:
        return None, "missing"

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.isoformat(), "iso_date_assumed_utc"

    if "/" in text:
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if not match:
            raise ValueError(f"Unrecognised date: {raw!r}")
        day, month, year = (int(part) for part in match.groups())
        dt = datetime(year, month, day, tzinfo=timezone.utc)
        return dt.isoformat(), "ddmmyyyy_assumed_utc"

    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat(), "naive_assumed_utc"
    return dt.astimezone(timezone.utc).isoformat(), "aware_converted_utc"


def parse_dt(raw: str | None) -> datetime | None:
    text, kind = parse_utc(raw)
    if text is None:
        return None
    return datetime.fromisoformat(text)


def normalise_email(raw: str) -> str:
    return str(raw).strip().lower()


def dedupe_orders(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    seen: set[str] = set()
    kept: list[dict[str, str]] = []
    duplicates: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        order_id = row["order_id"].strip()
        if order_id in seen:
            duplicates.append({"order_id": order_id, "source_line": index})
            continue
        seen.add(order_id)
        kept.append(row)
    return kept, duplicates


def extract_order_refs(text: str) -> list[str]:
    refs: list[str] = []
    for match in ORDER_REF_RE.finditer(text):
        digits = next(group for group in match.groups() if group)
        refs.append(f"LF-{digits}")
    return list(dict.fromkeys(refs))


def parse_policy_docs() -> list[dict[str, str]]:
    docs: list[dict[str, str]] = []
    for path in sorted((DATAROOM / "policies").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = DOC_RE.search(text)
        if not match:
            raise ValueError(f"Could not find name/version header in {path.name}")
        docs.append(
            {
                "name": match.group(1).strip(),
                "version": match.group(2).strip(),
                "source_file": path.name,
            }
        )
    return docs


def build() -> dict[str, Any]:
    BUILD.mkdir(parents=True, exist_ok=True)

    fixes: list[dict[str, Any]] = []
    issues: dict[str, Any] = {}

    # ------------------------------------------------------------------ orders
    raw_orders = read_csv("orders.csv")
    raw_total_sum = sum(parse_amount(row["Order Total"])[0] for row in raw_orders)
    raw_money_reps = {"dollar_prefix": 0, "usd_prefix": 0, "plain": 0}
    raw_order_date_formats: dict[str, int] = {}
    for row in raw_orders:
        _, representation = parse_amount(row["Order Total"])
        raw_money_reps[representation] += 1
        _, date_kind = parse_utc(row["order_date"])
        raw_order_date_formats[date_kind] = raw_order_date_formats.get(date_kind, 0) + 1

    orders, duplicate_orders = dedupe_orders(raw_orders)
    raw_rows = raw_orders
    money_reps = {"dollar_prefix": 0, "usd_prefix": 0, "plain": 0}
    orders_clean: list[dict[str, Any]] = []
    order_date_formats: dict[str, int] = {}
    ambiguous_ddmmyyyy: list[dict[str, str]] = []
    for row in orders:
        total, representation = parse_amount(row["Order Total"])
        money_reps[representation] += 1
        order_date, date_kind = parse_utc(row["order_date"])
        order_date_formats[date_kind] = order_date_formats.get(date_kind, 0) + 1
        if date_kind == "ddmmyyyy_assumed_utc":
            day_text, month_text, _ = row["order_date"].split("/")
            if int(day_text) <= 12 and int(month_text) <= 12:
                ambiguous_ddmmyyyy.append(
                    {
                        "order_id": row["order_id"],
                        "raw": row["order_date"],
                        "parsed_as": order_date,
                    }
                )
        orders_clean.append(
            {
                "order_id": row["order_id"].strip(),
                "customer_id": row["customer_id"].strip(),
                "order_date": order_date,
                "status": row["status"].strip(),
                "order_total": total,
                "shipping_fee": float(row["shipping_fee"]),
                "currency": row["currency"].strip(),
                "payment_method": row["payment_method"].strip(),
            }
        )

    deduped_total_sum = sum(row["order_total"] for row in orders_clean)
    issues["orders"] = {
        "rows_in": len(raw_rows),
        "rows_out": len(orders_clean),
        "duplicate_order_rows": len(duplicate_orders),
        "duplicate_order_ids": duplicate_orders,
        "order_total_raw_sum_usd": round(raw_total_sum, 2),
        "order_total_deduped_sum_usd": round(deduped_total_sum, 2),
        "order_total_inflation_usd": round(raw_total_sum - deduped_total_sum, 2),
        "non_usd_orders": sum(1 for row in orders_clean if row["currency"] != "USD"),
    }
    fixes.append(
        {
            "fix": "dedupe_orders_by_order_id",
            "rows_removed": len(duplicate_orders),
            "rows_kept": len(orders_clean),
        }
    )

    issues["money"] = {
        "order_total_representations_raw": raw_money_reps,
        "order_total_representations_after_dedupe": money_reps,
        "order_total_values_parsed_raw": len(raw_orders),
        "order_total_values_parsed_after_dedupe": len(orders_clean),
        "order_total_unparseable": 0,
    }
    fixes.append(
        {
            "fix": "parse_order_total_to_float",
            "representations_handled": ["$ prefix", "USD prefix", "plain numeric"],
            "values_parsed": len(orders_clean),
        }
    )

    issues["dates"] = {
        "order_date_formats": raw_order_date_formats,
        "order_date_formats_after_dedupe": order_date_formats,
        "orders_ambiguous_if_read_as_mmdd": len(ambiguous_ddmmyyyy),
        "ambiguous_ddmmyyyy_examples": ambiguous_ddmmyyyy,
    }

    # --------------------------------------------------------------- customers
    raw_customers = read_csv("customers.csv")
    customers: list[dict[str, Any]] = []
    customer_emails_changed = 0
    for row in raw_customers:
        email = normalise_email(row["email"])
        if email != row["email"]:
            customer_emails_changed += 1
        created_at, _ = parse_utc(row["created_at"])
        customers.append(
            {
                "customer_id": row["customer_id"].strip(),
                "name": row["name"].strip(),
                "email": email,
                "country": row["country"].strip(),
                "created_at": created_at,
                "loyalty_tier": row["loyalty_tier"].strip(),
            }
        )

    # ------------------------------------------------------------- order items
    order_items: list[dict[str, Any]] = []
    for row in read_csv("order_items.csv"):
        order_items.append(
            {
                "order_id": row["order_id"].strip(),
                "line_no": int(row["line_no"]),
                "sku": row["sku"].strip(),
                "product_name": row["product_name"].strip(),
                "qty": int(row["qty"]),
                "unit_price": float(row["unit_price"]),
            }
        )

    # ----------------------------------------------------------------- refunds
    refunds: list[dict[str, Any]] = []
    refund_naive = 0
    for row in read_csv("refunds.csv"):
        requested_at, date_kind = parse_utc(row["requested_at"])
        if date_kind == "naive_assumed_utc":
            refund_naive += 1
        refunds.append(
            {
                "refund_id": row["refund_id"].strip(),
                "order_id": row["order_id"].strip(),
                "line_no": int(row["line_no"]),
                "amount": float(row["amount"]),
                "status": row["status"].strip(),
                "requested_at": requested_at,
                "approved_by": row["approved_by"].strip(),
            }
        )

    # --------------------------------------------------------------- shipments
    shipments: list[dict[str, Any]] = []
    shipment_flags: list[dict[str, Any]] = []
    shipment_naive_counts = {"shipped_at": 0, "delivered_at": 0, "last_event_at": 0}
    missing_delivered = 0
    missing_last_event = 0
    for row in read_csv("shipments.csv"):
        parsed_dates: dict[str, str | None] = {}
        for column in ("shipped_at", "delivered_at", "last_event_at"):
            value, date_kind = parse_utc(row[column])
            parsed_dates[column] = value
            if date_kind == "naive_assumed_utc":
                shipment_naive_counts[column] += 1
            if column == "delivered_at" and value is None:
                missing_delivered += 1
            if column == "last_event_at" and value is None:
                missing_last_event += 1

        shipped_dt = parse_dt(row["shipped_at"])
        last_event_dt = parse_dt(row["last_event_at"])
        chronology_anomaly = int(
            shipped_dt is not None
            and last_event_dt is not None
            and last_event_dt < shipped_dt
        )
        shipments.append(
            {
                "shipment_id": row["shipment_id"].strip(),
                "order_id": row["order_id"].strip(),
                "carrier": row["carrier"].strip(),
                "tracking_no": row["tracking_no"].strip(),
                "status": row["status"].strip(),
                "shipped_at": parsed_dates["shipped_at"],
                "delivered_at": parsed_dates["delivered_at"],
                "last_event_at": parsed_dates["last_event_at"],
                "timestamp_anomaly": chronology_anomaly,
            }
        )
        if chronology_anomaly:
            shipment_flags.append(
                {
                    "shipment_id": row["shipment_id"],
                    "order_id": row["order_id"],
                    "shipped_at": row["shipped_at"],
                    "last_event_at": row["last_event_at"],
                }
            )

    issues["dates"]["shipments_naive_timestamps_assumed_utc"] = shipment_naive_counts
    issues["dates"]["refunds_naive_timestamps_assumed_utc"] = refund_naive
    issues["shipments"] = {
        "rows": len(shipments),
        "last_event_before_shipped": len(shipment_flags),
        "last_event_before_shipped_details": shipment_flags,
        "missing_delivered_at": missing_delivered,
        "missing_last_event_at": missing_last_event,
    }
    fixes.append(
        {
            "fix": "flag_shipment_chronology_anomalies",
            "rows_flagged": len(shipment_flags),
            "action": "kept rows and added timestamp_anomaly column",
        }
    )
    fixes.append(
        {
            "fix": "parse_dates_to_utc_iso",
            "accepted_formats": ["ISO YYYY-MM-DD", "DD/MM/YYYY", "ISO datetime"],
            "never_used": "MM/DD/YYYY",
            "naive_values_assumed_utc": True,
        }
    )

    # ----------------------------------------------------------------- tickets
    tickets: list[dict[str, Any]] = []
    ticket_sources = {
        "support_inbox": read_json("tickets/support_inbox_export.json"),
        "public_inbox": read_json("inbox/public_tickets.json"),
    }
    ticket_email_changes = {"support_inbox": 0, "public_inbox": 0}
    attachments_total = 0
    attachments_empty_base64 = 0
    body_groups: dict[str, list[str]] = {}
    for source, records in ticket_sources.items():
        for record in records:
            sender = normalise_email(record["from"])
            if sender != record["from"]:
                ticket_email_changes[source] += 1
            received_at, date_kind = parse_utc(record["received_at"])
            attachments = record.get("attachments") or []
            attachments_total += len(attachments)
            attachments_empty_base64 += sum(
                1 for attachment in attachments if not attachment.get("content_base64")
            )
            has_image = int(
                any(
                    str(attachment.get("content_type", "")).startswith("image/")
                    for attachment in attachments
                )
            )
            body_key = re.sub(r"\s+", " ", str(record["body"]).strip().lower())
            body_groups.setdefault(body_key, []).append(record["ticket_id"])
            tickets.append(
                {
                    "ticket_id": record["ticket_id"],
                    "source": source,
                    "received_at": received_at,
                    "channel": record["channel"],
                    "from_email": sender,
                    "to_email": normalise_email(record["to"]),
                    "subject": record["subject"],
                    "body": record["body"],
                    "attachment_count": len(attachments),
                    "has_image_attachment": has_image,
                    "attachments_json": json.dumps(attachments, ensure_ascii=False),
                }
            )

    duplicate_body_groups = [
        ids for ids in body_groups.values() if len(ids) > 1
    ]

    orders_by_id = {row["order_id"]: row for row in orders_clean}
    customers_by_email = {row["email"]: row for row in customers}
    ticket_ref_stats = {
        "tickets": len(tickets),
        "tickets_with_no_order_reference": 0,
        "tickets_with_nonexistent_order_reference": 0,
        "nonexistent_order_references": [],
        "tickets_with_multiple_valid_orders": 0,
        "sender_emails_not_on_file": 0,
        "owner_mismatches": [],
    }
    for ticket in tickets:
        text = f"{ticket['subject']}\n{ticket['body']}"
        refs = extract_order_refs(text)
        valid_refs = [ref for ref in refs if ref in orders_by_id]
        invalid_refs = [ref for ref in refs if ref not in orders_by_id]
        if not refs:
            ticket_ref_stats["tickets_with_no_order_reference"] += 1
        if invalid_refs:
            ticket_ref_stats["tickets_with_nonexistent_order_reference"] += 1
            ticket_ref_stats["nonexistent_order_references"].append(
                {"ticket_id": ticket["ticket_id"], "refs": invalid_refs}
            )
        if len(valid_refs) > 1:
            ticket_ref_stats["tickets_with_multiple_valid_orders"] += 1
        sender = customers_by_email.get(ticket["from_email"])
        if sender is None:
            ticket_ref_stats["sender_emails_not_on_file"] += 1
        for ref in valid_refs:
            if sender is not None and orders_by_id[ref]["customer_id"] != sender["customer_id"]:
                ticket_ref_stats["owner_mismatches"].append(
                    {
                        "ticket_id": ticket["ticket_id"],
                        "order_id": ref,
                        "ticket_customer_id": sender["customer_id"],
                        "order_customer_id": orders_by_id[ref]["customer_id"],
                    }
                )

    ticket_email_total = sum(ticket_email_changes.values())
    issues["emails"] = {
        "customer_emails_needing_normalisation": customer_emails_changed,
        "ticket_emails_needing_normalisation": {
            **ticket_email_changes,
            "total": ticket_email_total,
        },
        "normalisation": "strip leading/trailing whitespace and lowercase",
    }
    issues["tickets"] = {
        "total": len(tickets),
        "duplicate_body_clusters": len(duplicate_body_groups),
        "duplicate_body_ticket_id_groups": duplicate_body_groups,
        "attachments_total": attachments_total,
        "attachments_with_empty_base64": attachments_empty_base64,
    }
    issues["ticket_order_references"] = ticket_ref_stats
    fixes.append(
        {
            "fix": "normalise_emails_strip_lower",
            "customer_emails_changed": customer_emails_changed,
            "ticket_emails_changed": ticket_email_total,
        }
    )

    # ----------------------------------------------------- referential integrity
    order_ids = set(orders_by_id)
    customer_ids = {row["customer_id"] for row in customers}
    issues["referential_integrity"] = {
        "order_items_with_unknown_order": sum(
            1 for row in order_items if row["order_id"] not in order_ids
        ),
        "shipments_with_unknown_order": sum(
            1 for row in shipments if row["order_id"] not in order_ids
        ),
        "refunds_with_unknown_order": sum(
            1 for row in refunds if row["order_id"] not in order_ids
        ),
        "orders_with_unknown_customer": sum(
            1 for row in orders_clean if row["customer_id"] not in customer_ids
        ),
    }

    # ---------------------------------------------------------- eval labels/docs
    eval_labels: list[dict[str, Any]] = []
    for record in read_json("tickets/eval_seed_labelled.json"):
        eval_labels.append(
            {
                "ticket_id": record["ticket_id"],
                "expected_outcome": record["expected_outcome"],
                "expected_human_approval_required": int(
                    record["expected_human_approval_required"]
                ),
                "expected_approver_role": record.get("expected_approver_role"),
                "expected_amount_usd": record.get("expected_amount_usd"),
                "expected_reason_codes": json.dumps(
                    record.get("expected_reason_codes", [])
                ),
                "note": record.get("note"),
            }
        )

    docs = parse_policy_docs()
    issues["policies"] = {
        "documents": len(docs),
        "names_and_versions": [
            {"name": doc["name"], "version": doc["version"]} for doc in docs
        ],
    }
    fixes.append(
        {
            "fix": "copy_policy_name_and_version",
            "documents_copied": len(docs),
        }
    )

    # ------------------------------------------------------------------ SQLite
    if DB_PATH.exists():
        DB_PATH.unlink()
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.executescript(
            """
            CREATE TABLE customers (
                customer_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                country TEXT NOT NULL,
                created_at TEXT NOT NULL,
                loyalty_tier TEXT NOT NULL
            );
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                order_date TEXT NOT NULL,
                status TEXT NOT NULL,
                order_total REAL NOT NULL,
                shipping_fee REAL NOT NULL,
                currency TEXT NOT NULL,
                payment_method TEXT NOT NULL
            );
            CREATE TABLE order_items (
                order_id TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                sku TEXT NOT NULL,
                product_name TEXT NOT NULL,
                qty INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                PRIMARY KEY (order_id, line_no)
            );
            CREATE TABLE refunds (
                refund_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                approved_by TEXT NOT NULL
            );
            CREATE TABLE shipments (
                shipment_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                carrier TEXT NOT NULL,
                tracking_no TEXT NOT NULL,
                status TEXT NOT NULL,
                shipped_at TEXT NOT NULL,
                delivered_at TEXT,
                last_event_at TEXT,
                timestamp_anomaly INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE tickets (
                ticket_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                received_at TEXT NOT NULL,
                channel TEXT NOT NULL,
                from_email TEXT NOT NULL,
                to_email TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                attachment_count INTEGER NOT NULL,
                has_image_attachment INTEGER NOT NULL,
                attachments_json TEXT NOT NULL
            );
            CREATE TABLE docs (
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                source_file TEXT NOT NULL,
                PRIMARY KEY (name, version)
            );
            CREATE TABLE eval_labels (
                ticket_id TEXT PRIMARY KEY,
                expected_outcome TEXT NOT NULL,
                expected_human_approval_required INTEGER NOT NULL,
                expected_approver_role TEXT,
                expected_amount_usd REAL,
                expected_reason_codes TEXT NOT NULL,
                note TEXT
            );
            """
        )

        def insert_many(table: str, columns: list[str], rows: Iterable[dict[str, Any]]) -> int:
            placeholders = ", ".join("?" for _ in columns)
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
            values = [tuple(row[column] for column in columns) for row in rows]
            connection.executemany(sql, values)
            return len(values)

        row_counts = {
            "customers": insert_many(
                "customers",
                [
                    "customer_id",
                    "name",
                    "email",
                    "country",
                    "created_at",
                    "loyalty_tier",
                ],
                customers,
            ),
            "orders": insert_many(
                "orders",
                [
                    "order_id",
                    "customer_id",
                    "order_date",
                    "status",
                    "order_total",
                    "shipping_fee",
                    "currency",
                    "payment_method",
                ],
                orders_clean,
            ),
            "order_items": insert_many(
                "order_items",
                ["order_id", "line_no", "sku", "product_name", "qty", "unit_price"],
                order_items,
            ),
            "refunds": insert_many(
                "refunds",
                [
                    "refund_id",
                    "order_id",
                    "line_no",
                    "amount",
                    "status",
                    "requested_at",
                    "approved_by",
                ],
                refunds,
            ),
            "shipments": insert_many(
                "shipments",
                [
                    "shipment_id",
                    "order_id",
                    "carrier",
                    "tracking_no",
                    "status",
                    "shipped_at",
                    "delivered_at",
                    "last_event_at",
                    "timestamp_anomaly",
                ],
                shipments,
            ),
            "tickets": insert_many(
                "tickets",
                [
                    "ticket_id",
                    "source",
                    "received_at",
                    "channel",
                    "from_email",
                    "to_email",
                    "subject",
                    "body",
                    "attachment_count",
                    "has_image_attachment",
                    "attachments_json",
                ],
                tickets,
            ),
            "docs": insert_many("docs", ["name", "version", "source_file"], docs),
            "eval_labels": insert_many(
                "eval_labels",
                [
                    "ticket_id",
                    "expected_outcome",
                    "expected_human_approval_required",
                    "expected_approver_role",
                    "expected_amount_usd",
                    "expected_reason_codes",
                    "note",
                ],
                eval_labels,
            ),
        }
        connection.commit()
    finally:
        connection.close()

    fixes.append(
        {
            "fix": "write_clean_sqlite_tables",
            "database": str(DB_PATH.relative_to(ROOT)).replace("\\", "/"),
            "tables": sorted(row_counts),
        }
    )

    report = {
        "generated_at": utc_now_iso(),
        "source": "dataroom",
        "read_only_source": True,
        "database": str(DB_PATH.relative_to(ROOT)).replace("\\", "/"),
        "row_counts": row_counts,
        "issues_found": issues,
        "fixes_applied": fixes,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
