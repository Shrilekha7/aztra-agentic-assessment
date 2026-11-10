"""Read-only access to the cleaned Larkfield data store."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DB_PATH = BUILD / "larkfield.db"
RUNTIME_PATH = BUILD / "runtime.db"


def ensure_prepare() -> None:
    if DB_PATH.exists():
        return
    from src import prepare

    prepare.build()


def normalise_email(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _connect() -> sqlite3.Connection:
    ensure_prepare()
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _connect() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def _row(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


def find_customer(email: str) -> dict[str, Any] | None:
    return _row("SELECT * FROM customers WHERE email = ?", (normalise_email(email),))


def get_customer(customer_id: str) -> dict[str, Any] | None:
    return _row("SELECT * FROM customers WHERE customer_id = ?", (customer_id,))


def get_order(order_id: str) -> dict[str, Any] | None:
    return _row("SELECT * FROM orders WHERE order_id = ?", (order_id,))


def get_orders_for_customer(customer_id: str) -> list[dict[str, Any]]:
    return _rows(
        "SELECT * FROM orders WHERE customer_id = ? ORDER BY order_date, order_id",
        (customer_id,),
    )


def get_order_items(order_id: str) -> list[dict[str, Any]]:
    return _rows(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY line_no",
        (order_id,),
    )


def get_shipment(order_id: str) -> dict[str, Any] | None:
    return _row("SELECT * FROM shipments WHERE order_id = ? LIMIT 1", (order_id,))


def get_refunds(order_id: str) -> list[dict[str, Any]]:
    return _rows(
        "SELECT * FROM refunds WHERE order_id = ? ORDER BY line_no, refund_id",
        (order_id,),
    )


def product_names() -> list[str]:
    rows = _rows("SELECT DISTINCT product_name FROM order_items ORDER BY product_name")
    return [row["product_name"] for row in rows]


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
