"""Token usage totals and optional, user-configured cost estimates.

Providers report token counts but generally not prices, and prices change, so
APEX CORE ships no price table. If you add ``pricing`` to a provider in
``config.toml``, an estimate is shown; otherwise only token counts are.
"""

from __future__ import annotations

from apexcore.config import Pricing
from apexcore.providers.base import Usage


def estimate_cost(usage: Usage, pricing: Pricing | None) -> float | None:
    if pricing is None or pricing.input_per_million is None or pricing.output_per_million is None:
        return None
    return (
        usage.input_tokens * pricing.input_per_million
        + usage.output_tokens * pricing.output_per_million
    ) / 1_000_000


def format_usage(usage: Usage, pricing: Pricing | None = None, *, reported: bool = True) -> str:
    if not reported and usage.total_tokens == 0:
        return "Tokens: not reported by provider"
    text = f"Tokens: {usage.input_tokens:,} in / {usage.output_tokens:,} out"
    cost = estimate_cost(usage, pricing)
    if cost is not None and pricing is not None:
        text += f" (est. {cost:.4f} {pricing.currency}, from your configured pricing)"
    return text
