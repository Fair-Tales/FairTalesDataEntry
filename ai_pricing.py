"""Provider usage normalization and token pricing (Streamlit-free, shared).

This is the SINGLE source of truth for API pricing and per-call cost
across BOTH pipelines (#129):

* the live Streamlit app (``utilities.record_api_usage`` -> Firestore), and
* the standalone pilot importer (``scripts/import_pilot_data.py`` ->
  end-of-run cost report).

Like ``s3_constants`` / ``ai_continuity`` it imports NOTHING Streamlit-coupled
(standard library only) so the importer can use it without a Streamlit runtime.

Prices are US dollars per million tokens. Unknown costs are None, never free;
aggregates retain known subtotals and count calls whose cost is incomplete.
This table does not select models or enable dispatch: the app and importer call
Anthropic, while the evaluation gateway dispatches either provider explicitly.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table — US dollars per MILLION tokens ($/MTok), standard processing.
#
# Rates below last confirmed 2026-11-07 against the linked official sources.
# Record current prices and confirmation dates; reverify any announced change
# instead of scheduling a future price increase from an old announcement.
# ---------------------------------------------------------------------------

#: One million tokens — the unit the price constants below are quoted in.
_TOKENS_PER_MTOK = 1_000_000

#: Independent per-model rates. Input EXCLUDES cache reads and writes.
#: None means no applicable verified rate for that bucket; it is never free.
PRICING_PER_MTOK = {
    # Anthropic standard rates, including 5-minute cache writes:
    # https://platform.claude.com/docs/en/about-claude/pricing
    # Sonnet 5: $2 / $10 is permanent standard pricing (confirmed 2026-11-07).
    # https://www.anthropic.com/news/claude-sonnet-5
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": .2, "cache_write": 2.5},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": .3, "cache_write": 3.75},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": .5, "cache_write": 6.25},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": .1, "cache_write": 1.25},
    # OpenAI frontier models: standard short-context rates (up to 272K input),
    # without regional surcharges. Longer prompts remain counted but unpriced.
    # All four cached-input rates are published:
    # https://developers.openai.com/api/docs/pricing
    # Write rates depend on 30m/12h retention, absent from our normalized usage;
    # leave writes unpriced rather than assuming either duration's tariff.
    "gpt-6-astra": {"input": 10.0, "output": 50.0, "cache_read": 1.0, "cache_write": None},
    "gpt-6.1-sol": {"input": 2.0, "output": 10.0, "cache_read": .1, "cache_write": None},
    "gpt-6.1-terra": {"input": 1.0, "output": 5.0, "cache_read": .05, "cache_write": None},
    "gpt-6-luna": {"input": .1, "output": .5, "cache_read": .01, "cache_write": None},
    # Deprecated: retained only for historical accounting, not model candidates.
    # https://developers.openai.com/api/docs/models/gpt-4.1
    # https://developers.openai.com/api/docs/models/gpt-4.1-mini
    "gpt-4.1": {"input": 2.0, "output": 8.0, "cache_read": .5, "cache_write": None},
    "gpt-4.1-mini": {"input": .4, "output": 1.6, "cache_read": .1, "cache_write": None},
}
# Only documented snapshots; do not strip arbitrary model suffixes to guess rates.
for _model in ("gpt-4.1", "gpt-4.1-mini"):
    PRICING_PER_MTOK[f"{_model}-2025-04-14"] = PRICING_PER_MTOK[_model].copy()


def usage_field(value, field, default=None):
    """Read SDK objects and their JSON/dict equivalents through one boundary."""
    return value.get(field, default) if isinstance(value, Mapping) else getattr(value, field, default)


def usage_provider(model="", provider=None):
    """Canonical provider; legacy callers default to Anthropic, GPT IDs to OpenAI."""
    provider = provider or ("openai" if model.startswith("gpt-") else "anthropic")
    return {"anthropic-messages": "anthropic", "openai-responses": "openai",
            "openai-chat-completions": "openai"}.get(provider, provider)


def _token_count(value):
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        raise ValueError("Usage token counts must be non-negative integers or unknown")
    return value


def normalize_usage(usage, provider="anthropic"):
    """Convert native usage to disjoint input/output/cache buckets.

    Missing required counts stay None. Anthropic's optional null/absent cache
    fields mean no cache use. OpenAI cache details must report an explicit count;
    cached input is a subset of its input total. Output already includes any
    reasoning tokens, so those are never added again. Malformed counts raise
    ValueError for the caller to log (or retain an evaluation reservation).
    """
    unknown = dict.fromkeys(("input", "output", "cache_read", "cache_write"))
    if usage is None:
        return unknown
    if not isinstance(usage, Mapping) and not hasattr(usage, "__dict__"):
        raise ValueError("Malformed usage object")
    provider = usage_provider(provider=provider)
    if provider == "anthropic":
        return {
            "input": _token_count(usage_field(usage, "input_tokens")),
            "output": _token_count(usage_field(usage, "output_tokens")),
            "cache_read": _token_count(usage_field(usage, "cache_read_input_tokens")) or 0,
            "cache_write": _token_count(usage_field(usage, "cache_creation_input_tokens")) or 0,
        }
    if provider != "openai":
        return unknown
    chat = usage_field(usage, "prompt_tokens") is not None
    input_tokens = _token_count(usage_field(usage, "prompt_tokens" if chat else "input_tokens"))
    output = _token_count(usage_field(usage, "completion_tokens" if chat else "output_tokens"))
    details = usage_field(usage, "prompt_tokens_details" if chat else "input_tokens_details")
    cache_read = _token_count(usage_field(details, "cached_tokens"))
    # Preserve explicit cache-write usage as its own bucket, even when its
    # model/duration has no applicable rate in the table.
    cache_write = _token_count(usage_field(details, "cache_write_tokens", 0))
    ordinary = None
    if input_tokens is not None and cache_read is not None and cache_write is not None:
        ordinary = input_tokens - cache_read - cache_write
        if ordinary < 0:
            raise ValueError("Cached input exceeds total input tokens")
    return {"input": ordinary, "output": output,
            "cache_read": cache_read, "cache_write": cache_write}


def usage_cost(model, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    """Price normalized, disjoint token buckets; return None for an unknown cost."""
    prices = PRICING_PER_MTOK.get(model)
    if prices is None:
        logger.warning(
            "ai_pricing: unknown model id %r; usage is unpriced "
            "(add it to PRICING_PER_MTOK)", model,
        )
        return None
    cost = 0.0
    for field, count in zip(("input", "output", "cache_read", "cache_write"),
                            (input_tokens, output_tokens, cache_read, cache_write)):
        if _token_count(count) is None:
            return None
        if count:
            rate = prices.get(field)
            if rate is None:
                logger.warning("ai_pricing: unpriced %s tokens for %s", field, model)
                return None
            cost += count * (rate / _TOKENS_PER_MTOK)
    if model in ("gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"):
        if input_tokens + cache_read + cache_write > 272_000:
            logger.warning("ai_pricing: unpriced long-context usage for %s", model)
            return None
    return cost


def usage_metrics(usage, model, provider=None):
    """Return provider and ledger metrics; omit unknown cost/token increments.

    unpriced_calls includes every call with unknown cost; unknown_usage_calls
    identifies the subset with missing token counts. Numeric costs are known
    subtotals when either counter is nonzero.
    """
    provider = usage_provider(model, provider)
    counts = normalize_usage(usage, provider)
    metrics = _token_metrics(model, counts)
    tools = usage_field(usage, "server_tool_use")
    tool_counts = tools if isinstance(tools, Mapping) else vars(tools) if tools is not None else {}
    long_cache = usage_field(usage_field(usage, "cache_creation"), "ephemeral_1h_input_tokens", 0)
    # Existing importer lookups use server-side search. Token rates alone do
    # not establish their full charge, nor the price of a one-hour cache write.
    if any(tool_counts.values()) or long_cache:
        metrics.pop("cost_usd", None)
        metrics["unpriced_calls"] = 1
        logger.warning("ai_pricing: unpriced tool use or cache duration for %s", model)
    return provider, metrics


def _token_metrics(model, counts):
    """Build one call's metrics from normalized counts, preserving unknowns."""
    cost = usage_cost(model, *counts.values())
    metrics = {"calls": 1}
    for field, count in counts.items():
        if count is not None:
            metrics[f"{field}_tokens"] = count
    if cost is None:
        metrics["unpriced_calls"] = 1
    else:
        metrics["cost_usd"] = cost
    if any(count is None for count in counts.values()):
        metrics["unknown_usage_calls"] = 1
        logger.warning("ai_pricing: unknown usage for model=%s", model)
    return metrics


class UsageAccumulator:
    """Tally known tokens/cost and accounting gaps by ``(operation, model)``.

    Used by the importer to sum spend across a whole run. ``record`` takes a raw
    SDK ``response.usage`` object (or ``None``, recorded as unknown usage);
    ``add`` takes explicit normalized token counts for callers that
    already have them. Cost is computed once, here, via :func:`usage_cost` so the
    pricing table is never duplicated (#129).
    """

    def __init__(self):
        # (operation, model) -> mutable totals dict.
        self._entries: dict = {}

    def record(self, operation, model, usage, *, provider=None):
        """Count missing usage; log malformed usage without breaking the run."""
        try:
            provider, metrics = usage_metrics(usage, model, provider)
            self._add_metrics(operation, model, provider, metrics)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("UsageAccumulator: could not record usage for %s: %s", model, exc)

    def add(self, operation, model, input_tokens=0, output_tokens=0,
            cache_read=0, cache_write=0, *, provider=None):
        """Add disjoint token counts, already normalized by the caller."""
        try:
            counts = dict(zip(("input", "output", "cache_read", "cache_write"),
                              map(_token_count, (input_tokens, output_tokens, cache_read, cache_write))))
            metrics = _token_metrics(model, counts)
            self._add_metrics(operation, model, usage_provider(model, provider), metrics)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("UsageAccumulator: could not record usage for %s: %s", model, exc)

    def _add_metrics(self, operation, model, provider, metrics):
        entry = self._entries.setdefault(
            (operation, model),
            {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
             "cost": 0.0, "calls": 0, "provider": provider,
             "unpriced_calls": 0, "unknown_usage_calls": 0},
        )
        for field in ("input", "output", "cache_read", "cache_write"):
            entry[field] += metrics.get(f"{field}_tokens", 0)
        entry["cost"] += metrics.get("cost_usd", 0)
        for field in ("unpriced_calls", "unknown_usage_calls"):
            entry[field] += metrics.get(field, 0)
        entry["calls"] += 1

    @property
    def total_cost(self):
        """Known USD subtotal; incomplete when total_unpriced_calls is nonzero."""
        return sum(e["cost"] for e in self._entries.values())

    @property
    def total_unpriced_calls(self):
        return sum(e["unpriced_calls"] for e in self._entries.values())

    @property
    def total_unknown_usage_calls(self):
        return sum(e["unknown_usage_calls"] for e in self._entries.values())

    @property
    def total_calls(self):
        """Total number of recorded API calls."""
        return sum(e["calls"] for e in self._entries.values())

    def rows(self):
        """Yield ``(operation, model, totals_dict)`` sorted by descending cost."""
        for (operation, model), entry in sorted(
            self._entries.items(), key=lambda kv: kv[1]["cost"], reverse=True
        ):
            yield operation, model, entry

    def by_operation(self):
        """Return ``{operation: totals_dict}`` aggregated across models."""
        out: dict = {}
        for (operation, _model), entry in self._entries.items():
            agg = out.setdefault(
                operation,
                {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                 "cost": 0.0, "calls": 0, "unpriced_calls": 0, "unknown_usage_calls": 0},
            )
            for key in agg:
                agg[key] += entry[key]
        return out


def format_cost_report(accumulator, *, books=None, title="API COST"):
    """Render a plain-text token + USD cost report for an importer run.

    Breaks the spend down by ``(operation, model)`` with a grand total, and — when
    ``books`` (the number of books processed) is given and non-zero — an average
    ``$/book``. Returns the report as a string so the caller can print it in both
    the end-of-run TOTALS block and the circuit-breaker abort path.
    """
    lines = []
    lines.append("=" * 78)
    lines.append(title)
    lines.append("=" * 78)
    if accumulator.total_calls == 0:
        lines.append("  (no billable AI calls recorded)")
        return "\n".join(lines)

    if accumulator.total_unpriced_calls:
        lines.append(
            f"  INCOMPLETE: {accumulator.total_unpriced_calls} unpriced call(s), "
            f"{accumulator.total_unknown_usage_calls} with unknown usage. "
            "Costs and tokens below are known subtotals."
        )

    header = (
        f"  {'operation':<14} {'model':<20} {'calls':>6} "
        f"{'in':>10} {'out':>8} {'cache_r':>9} {'cache_w':>8} {'cost $':>10}"
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for operation, model, e in accumulator.rows():
        lines.append(
            f"  {operation:<14} {model:<20} {e['calls']:>6} "
            f"{e['input']:>10,} {e['output']:>8,} {e['cache_read']:>9,} "
            f"{e['cache_write']:>8,} {e['cost']:>10.4f}"
            + (" (incomplete)" if e["unpriced_calls"] else "")
        )
    lines.append("  " + "-" * (len(header) - 2))
    lines.append(
        f"  {'TOTAL':<14} {'':<20} {accumulator.total_calls:>6} "
        f"{'':>10} {'':>8} {'':>9} {'':>8} {accumulator.total_cost:>10.4f}"
    )
    if accumulator.total_unpriced_calls:
        lines.append(f"  known subtotal : ${accumulator.total_cost:.2f} (incomplete)")
    elif books:
        lines.append(f"  grand total : ${accumulator.total_cost:.2f} "
                     f"over {books} book(s) = ${accumulator.total_cost / books:.4f}/book")
    else:
        lines.append(f"  grand total : ${accumulator.total_cost:.2f}")
    return "\n".join(lines)


#: Alias so callers can spell the operation tags consistently. These are the
#: four AI operations the importer tracks (kept here as documentation).
IMPORT_OPERATIONS = ("ocr", "clean_judge", "continuity", "lookup")
