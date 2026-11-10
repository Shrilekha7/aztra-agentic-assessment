"""Per-model token prices used to compute measured request cost."""

from __future__ import annotations

SOURCE_URL = "https://console.groq.com/docs/models"
CHECKED_DATE = "2026-11-10"

PRICING: dict[str, dict[str, float | str]] = {
    "qwen/qwen3.8-27b": {
        "input_per_million_usd": 0.80,
        "output_per_million_usd": 4.00,
        "source_url": SOURCE_URL,
        "checked_date": CHECKED_DATE,
    },
    "llama-3.1-8b-instant": {
        "input_per_million_usd": 0.05,
        "output_per_million_usd": 0.08,
        "source_url": SOURCE_URL,
        "checked_date": CHECKED_DATE,
    },
    "llama-3.3-70b-versatile": {
        "input_per_million_usd": 0.59,
        "output_per_million_usd": 0.79,
        "source_url": SOURCE_URL,
        "checked_date": CHECKED_DATE,
    },
    "llama-3.1-70b-versatile": {
        "input_per_million_usd": 0.59,
        "output_per_million_usd": 0.79,
        "source_url": SOURCE_URL,
        "checked_date": CHECKED_DATE,
    },
}


def price_for(model: str) -> dict[str, float | str]:
    return PRICING.get(model, PRICING["llama-3.1-8b-instant"])


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = price_for(model)
    return round(
        (input_tokens / 1_000_000) * float(price["input_per_million_usd"])
        + (output_tokens / 1_000_000) * float(price["output_per_million_usd"]),
        8,
    )
