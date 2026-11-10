"""Persistent runs and ledger store for the service."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
RUNTIME_PATH = BUILD / "runtime.db"


def _connect() -> sqlite3.Connection:
    BUILD.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(RUNTIME_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            received_at TEXT NOT NULL,
            response_json TEXT NOT NULL,
            extraction_json TEXT,
            status TEXT NOT NULL,
            decision_decision TEXT,
            decision_approver TEXT,
            decision_decided_at TEXT,
            amount_usd REAL,
            latency_ms REAL,
            fingerprint TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ledger (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            ticket_id TEXT NOT NULL,
            order_id TEXT,
            line_no INTEGER,
            action TEXT NOT NULL,
            amount_usd REAL,
            status TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            idempotency_key TEXT,
            fingerprint TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ledger_idempotency
            ON ledger(idempotency_key);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ledger_refund_line
            ON ledger(order_id, line_no)
            WHERE action = 'REFUND' AND status IN ('PROPOSED', 'APPROVED');
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(ledger)")}
    if "fingerprint" not in columns:
        connection.execute("ALTER TABLE ledger ADD COLUMN fingerprint TEXT")
    return connection


def init() -> None:
    with _connect():
        pass


def _run_view(row: sqlite3.Row) -> dict[str, Any]:
    response = json.loads(row["response_json"])
    decision = None
    if row["decision_decision"]:
        decision = {
            "decision": row["decision_decision"],
            "approver": row["decision_approver"],
            "decided_at": row["decision_decided_at"],
        }
    extraction: dict[str, Any] = {}
    try:
        extraction = json.loads(row["extraction_json"] or "{}")
    except (TypeError, ValueError):
        extraction = {}
    response.update(
        {
            "status": row["status"],
            "decision": decision,
            "amount_usd": row["amount_usd"],
            "latency_ms": row["latency_ms"],
            "fallback_reason": extraction.get("fallback_reason"),
        }
    )
    return response


def create_run(
    *,
    run_id: str,
    ticket_id: str,
    received_at: str,
    response: dict[str, Any],
    extraction: dict[str, Any],
    status: str,
    amount_usd: float | None,
    latency_ms: float,
    fingerprint: str,
    created_at: str,
) -> dict[str, Any]:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO runs (
                run_id, ticket_id, received_at, response_json, extraction_json,
                status, amount_usd, latency_ms, fingerprint, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                ticket_id,
                received_at,
                json.dumps(response, ensure_ascii=False),
                json.dumps(extraction, ensure_ascii=False),
                status,
                amount_usd,
                latency_ms,
                fingerprint,
                created_at,
            ),
        )
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return _run_view(row)


def get_run(run_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return _run_view(row) if row else None


def existing_refund(order_id: str | None, line_no: int | None) -> dict[str, Any] | None:
    if order_id is None or line_no is None:
        return None
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM ledger
            WHERE order_id = ? AND line_no = ? AND action = 'REFUND'
              AND status IN ('PROPOSED', 'APPROVED')
            LIMIT 1
            """,
            (order_id, line_no),
        ).fetchone()
    return dict(row) if row else None


def existing_refund_for_fingerprint(fingerprint: str | None) -> dict[str, Any] | None:
    if not fingerprint:
        return None
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM ledger
            WHERE fingerprint = ? AND action = 'REFUND'
              AND status IN ('PROPOSED', 'APPROVED')
            LIMIT 1
            """,
            (fingerprint,),
        ).fetchone()
    return dict(row) if row else None


def record_ledger(
    *,
    run_id: str,
    ticket_id: str,
    action: str,
    status: str,
    amount_usd: float | None,
    recorded_at: str,
    fingerprint: str,
    refund_lines: list[dict[str, Any]],
) -> int:
    inserted = 0
    with _connect() as connection:
        if action == "REFUND":
            if refund_lines:
                for line in refund_lines:
                    key = f"{fingerprint}|{line['order_id']}|{line['line_no']}|REFUND"
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO ledger (
                            run_id, ticket_id, order_id, line_no, action,
                            amount_usd, status, recorded_at, idempotency_key, fingerprint
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            ticket_id,
                            line["order_id"],
                            line["line_no"],
                            action,
                            line["amount_usd"],
                            status,
                            recorded_at,
                            key,
                            fingerprint,
                        ),
                    )
                    inserted += cursor.rowcount
            else:
                key = f"{fingerprint}|REFUND"
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO ledger (
                        run_id, ticket_id, order_id, line_no, action,
                        amount_usd, status, recorded_at, idempotency_key, fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        ticket_id,
                        None,
                        None,
                        action,
                        amount_usd,
                        status,
                        recorded_at,
                        key,
                        fingerprint,
                    ),
                )
                inserted += cursor.rowcount
        else:
            cursor = connection.execute(
                """
                INSERT INTO ledger (
                    run_id, ticket_id, order_id, line_no, action,
                    amount_usd, status, recorded_at, idempotency_key, fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    run_id,
                    ticket_id,
                    None,
                    None,
                    action,
                    amount_usd,
                    status,
                    recorded_at,
                    fingerprint,
                ),
            )
            inserted += cursor.rowcount
    return inserted


def ledger_entries() -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT run_id, ticket_id, action, amount_usd, status, recorded_at
            FROM ledger ORDER BY entry_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def decide_run(run_id: str, decision: str, approver: str, decided_at: str) -> tuple[str, dict[str, Any] | None]:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return "missing", None

        response = json.loads(row["response_json"])
        if response.get("outcome") != "PEND":
            return "conflict", _run_view(row)
        if row["status"] in ("APPROVED", "REJECTED"):
            return "unchanged", _run_view(row)
        if row["status"] != "AWAITING_APPROVAL":
            return "conflict", _run_view(row)

        new_status = "APPROVED" if decision == "approve" else "REJECTED"
        connection.execute(
            """
            UPDATE ledger SET status = ? WHERE run_id = ? AND action = 'REFUND'
            """,
            (new_status, run_id),
        )
        response["human_approval_required"] = False
        connection.execute(
            """
            UPDATE runs
            SET status = ?, decision_decision = ?, decision_approver = ?,
                decision_decided_at = ?, response_json = ?
            WHERE run_id = ?
            """,
            (
                new_status,
                decision,
                approver,
                decided_at,
                json.dumps(response, ensure_ascii=False),
                run_id,
            ),
        )
        updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return "updated", _run_view(updated)
