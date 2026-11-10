"""Run the ordered evaluation cases against a live service and write a baseline."""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "cases.json"
RESULTS_DIR = ROOT / "evals" / "results"


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    last: Exception | None = None
    for _ in range(3):
        try:
            return client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            last = exc
    assert last is not None
    raise last


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        return abs(float(actual) - expected) < 0.005
    return actual == expected


def _run_case(
    client: httpx.Client,
    case: dict[str, Any],
    previous: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expected = case["expected"]
    result: dict[str, Any] = {
        "case_id": case["case_id"],
        "ticket_id": case.get("ticket_id"),
        "description": case.get("description", ""),
        "expected": expected,
        "assertions": [],
    }

    response = _request(client, "POST", "/tickets", json=case["ticket"])
    if response.status_code != 200:
        result.update(
            {
                "passed": False,
                "error": f"POST /tickets returned {response.status_code}: {response.text[:500]}",
                "primary_match": False,
                "reason_codes_match": None,
                "amount_match": None,
            }
        )
        return result

    body = response.json()
    run = _request(client, "GET", f"/runs/{body['run_id']}").json()
    previous[case["case_id"]] = {"run_id": body["run_id"], "response": body, "run": run}

    actual = {
        "outcome": body["outcome"],
        "human_approval_required": body["human_approval_required"],
        "approver_role": body["approver_role"],
        "reason_codes": body["reason_codes"],
        "amount_usd": run.get("amount_usd"),
        "status": run.get("status"),
        "fallback_reason": run.get("fallback_reason"),
        "cost_usd": float(body.get("trace", {}).get("cost_usd", 0.0)),
        "latency_ms": float(run.get("latency_ms") or 0.0),
    }
    result["actual"] = actual
    result["trace"] = body.get("trace", {})

    primary_match = (
        actual["outcome"] == expected["outcome"]
        and actual["human_approval_required"] == expected["human_approval_required"]
        and actual["approver_role"] == expected.get("approver_role")
    )
    result["primary_match"] = primary_match

    if "reason_codes" in expected:
        result["reason_codes_match"] = set(actual["reason_codes"]) == set(expected["reason_codes"])
    else:
        result["reason_codes_match"] = None

    if "amount_usd" in expected:
        result["amount_match"] = _matches(actual["amount_usd"], expected["amount_usd"])
    else:
        result["amount_match"] = None

    if "assert_no_second_refund" in case:
        previous_case = case["assert_no_second_refund"]["previous_case_id"]
        previous_run_id = previous[previous_case]["run_id"]
        ledger = _request(client, "GET", "/ledger").json()
        previous_refunds = [
            row
            for row in ledger
            if row["run_id"] == previous_run_id and row["action"] == "REFUND"
        ]
        duplicate_refunds = [
            row for row in ledger if row["run_id"] == body["run_id"] and row["action"] == "REFUND"
        ]
        total_refunds = len(previous_refunds) + len(duplicate_refunds)
        result["assertions"].append(
            {
                "name": "original_refund_still_present",
                "passed": bool(previous_refunds),
                "detail": f"{len(previous_refunds)} refund row(s) for {previous_case}",
            }
        )
        result["assertions"].append(
            {
                "name": "exactly_one_refund_row_for_line",
                "passed": total_refunds == 1,
                "detail": (
                    f"{total_refunds} refund row(s) across original and duplicate runs "
                    f"(original={len(previous_refunds)}, duplicate={len(duplicate_refunds)})"
                ),
            }
        )

    if case.get("workflow") == "double_approve":
        first = _request(
            client,
            "POST",
            f"/runs/{body['run_id']}/decision",
            json={"decision": "approve", "approver": "eval-support-lead"},
        )
        second = _request(
            client,
            "POST",
            f"/runs/{body['run_id']}/decision",
            json={"decision": "approve", "approver": "eval-support-lead"},
        )
        run_after = _request(client, "GET", f"/runs/{body['run_id']}").json()
        ledger = _request(client, "GET", "/ledger").json()
        approved_rows = [
            row
            for row in ledger
            if row["run_id"] == body["run_id"]
            and row["action"] == "REFUND"
            and row["status"] == "APPROVED"
        ]
        result["assertions"].extend(
            [
                {
                    "name": "first_approve_200",
                    "passed": first.status_code == 200 and first.json().get("status") == "APPROVED",
                    "detail": f"status={first.status_code}",
                },
                {
                    "name": "second_approve_200_unchanged",
                    "passed": second.status_code == 200,
                    "detail": f"status={second.status_code}",
                },
                {
                    "name": "run_approved_and_human_cleared",
                    "passed": run_after.get("status") == "APPROVED"
                    and run_after.get("human_approval_required") is False,
                    "detail": f"status={run_after.get('status')}, human={run_after.get('human_approval_required')}",
                },
                {
                    "name": "exactly_one_approved_ledger_row",
                    "passed": len(approved_rows) == 1,
                    "detail": f"{len(approved_rows)} approved row(s)",
                },
            ]
        )

    result["passed"] = bool(primary_match and all(item["passed"] for item in result["assertions"]))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    results: list[dict[str, Any]] = []
    previous: dict[str, dict[str, Any]] = {}
    costs: list[float] = []
    latencies: list[float] = []

    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        health = _request(client, "GET", "/health")
        if health.status_code != 200:
            raise SystemExit(f"health check failed: {health.status_code}")
        for case in cases:
            try:
                result = _run_case(client, case, previous)
            except Exception as exc:
                result = {
                    "case_id": case.get("case_id"),
                    "ticket_id": case.get("ticket_id"),
                    "description": case.get("description", ""),
                    "expected": case.get("expected"),
                    "passed": False,
                    "primary_match": False,
                    "reason_codes_match": None,
                    "amount_match": None,
                    "assertions": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append(result)
            actual = result.get("actual")
            if actual:
                costs.append(float(actual["cost_usd"]))
                latencies.append(float(actual["latency_ms"]))

    passed = sum(1 for result in results if result["passed"])
    reason_scored = [r for r in results if r.get("reason_codes_match") is not None]
    amount_scored = [r for r in results if r.get("amount_match") is not None]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "cases_total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "reason_codes_scored": len(reason_scored),
        "reason_codes_matches": sum(1 for r in reason_scored if r["reason_codes_match"]),
        "amount_scored": len(amount_scored),
        "amount_matches": sum(1 for r in amount_scored if r["amount_match"]),
        "median_cost_usd": round(statistics.median(costs), 8) if costs else None,
        "median_latency_ms": round(statistics.median(latencies), 3) if latencies else None,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = RESULTS_DIR / f"results_{timestamp}.json"
    output_path.write_text(
        json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(summary, indent=2))
    failures = [result for result in results if not result["passed"]]
    print(f"\nfailing cases: {len(failures)}")
    for failure in failures:
        print(
            f"- {failure['case_id']}: expected={failure.get('expected')} "
            f"actual={failure.get('actual')} error={failure.get('error')} "
            f"assertions={failure.get('assertions')}"
        )
    print(f"\nresult file: {output_path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
