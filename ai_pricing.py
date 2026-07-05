"""Anthropic token-pricing + usage/cost accounting (Streamlit-free, shared).

This is the SINGLE source of truth for Claude API pricing and per-call cost
across BOTH pipelines (#129):

* the live Streamlit app (``utilities.record_api_usage`` -> Firestore), and
* the standalone pilot importer (``scripts/import_pilot_data.py`` ->
  end-of-run cost report).

Like ``s3_constants`` / ``ai_continuity`` it imports NOTHING Streamlit-coupled
(standard library only) so the importer can use it without a Streamlit runtime.

Pricing is expressed as clearly-commented ``$/MTok`` constants so the numbers
are trivial to update when Anthropic changes them. An unknown model id costs
0.0 and logs a warning rather than crashing the pipeline (error-handling
convention).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table — US dollars per MILLION tokens ($/MTok).
#
# Source: AI_COST_OPTIMIZATION.md (verified 2026-07-04 against the account's
# live pricing). Keep these as plain, commented constants so a price change is a
# one-line edit.
# ---------------------------------------------------------------------------

#: One million tokens — the unit the price constants below are quoted in.
_TOKENS_PER_MTOK = 1_000_000

#: Standard Anthropic prompt-cache multipliers applied to the INPUT price:
#:   * a cache READ token bills at ~0.1x the base input price;
#:   * a cache WRITE (creation) token bills at ~1.25x the base input price.
#: (These are the standard 5-minute-cache multipliers.)
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

#: Per-model input/output price in $/MTok. Cache read/write prices are derived
#: from the input price via the multipliers above.
PRICING_PER_MTOK = {
    # Sonnet 5: $2 / $10 INTRODUCTORY pricing. NOTE: standard pricing rises to
    # $3 / $15 per MTok from ~2026-09-01 — update these two numbers then.
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    # Sonnet 4.6: $3 / $15 per MTok.
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    # Opus 4.8: $5 / $25 per MTok.
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    # Haiku 4.5: $1 / $5 per MTok.
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
}


def usage_cost(model, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    """Return the USD cost of one Claude call from its token counts.

    ``cache_read`` / ``cache_write`` are the ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens`` reported on ``response.usage`` and are billed
    at the standard cache multipliers of the model's INPUT price. An unrecognised
    ``model`` id returns ``0.0`` and logs a warning (never raises) so a new/typo
    model id can never crash the pipeline (error-handling convention, #127).
    """
    prices = PRICING_PER_MTOK.get(model)
    if prices is None:
        logger.warning(
            "ai_pricing: unknown model id %r; counting its cost as $0.00 "
            "(add it to PRICING_PER_MTOK)", model,
        )
        return 0.0
    input_rate = prices["input"] / _TOKENS_PER_MTOK
    output_rate = prices["output"] / _TOKENS_PER_MTOK
    cost = (input_tokens or 0) * input_rate + (output_tokens or 0) * output_rate
    cost += (cache_read or 0) * input_rate * CACHE_READ_MULTIPLIER
    cost += (cache_write or 0) * input_rate * CACHE_WRITE_MULTIPLIER
    return cost


def _usage_tokens(usage):
    """Pull the four token counts off an SDK ``usage`` object (all guarded).

    Returns ``(input, output, cache_read, cache_write)`` as ints, tolerating a
    missing/oddly-shaped ``usage`` (any absent attribute counts as 0) so a change
    in the SDK's usage shape can never crash accounting.
    """
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
        int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )


class UsageAccumulator:
    """Tally Claude token usage + USD cost, grouped by ``(operation, model)``.

    Used by the importer to sum spend across a whole run. ``record`` takes a raw
    SDK ``response.usage`` object (or ``None``, which is a no-op — a missing usage
    must never crash a run); ``add`` takes explicit token counts for callers that
    already have them. Cost is computed once, here, via :func:`usage_cost` so the
    pricing table is never duplicated (#129).
    """

    def __init__(self):
        # (operation, model) -> mutable totals dict.
        self._entries: dict = {}

    def record(self, operation, model, usage):
        """Add one call's usage from an SDK ``response.usage`` (``None`` = skip)."""
        if usage is None:
            return
        input_tokens, output_tokens, cache_read, cache_write = _usage_tokens(usage)
        self.add(operation, model, input_tokens, output_tokens, cache_read, cache_write)

    def add(self, operation, model, input_tokens=0, output_tokens=0,
            cache_read=0, cache_write=0):
        """Add one call's usage from explicit token counts."""
        entry = self._entries.setdefault(
            (operation, model),
            {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
             "cost": 0.0, "calls": 0},
        )
        entry["input"] += int(input_tokens or 0)
        entry["output"] += int(output_tokens or 0)
        entry["cache_read"] += int(cache_read or 0)
        entry["cache_write"] += int(cache_write or 0)
        entry["cost"] += usage_cost(
            model, input_tokens, output_tokens, cache_read, cache_write
        )
        entry["calls"] += 1

    @property
    def total_cost(self):
        """Grand-total USD cost across every recorded call."""
        return sum(e["cost"] for e in self._entries.values())

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
                 "cost": 0.0, "calls": 0},
            )
            for key in ("input", "output", "cache_read", "cache_write", "cost", "calls"):
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
        )
    lines.append("  " + "-" * (len(header) - 2))
    lines.append(
        f"  {'TOTAL':<14} {'':<20} {accumulator.total_calls:>6} "
        f"{'':>10} {'':>8} {'':>9} {'':>8} {accumulator.total_cost:>10.4f}"
    )
    if books:
        lines.append(f"  grand total : ${accumulator.total_cost:.2f} "
                     f"over {books} book(s) = ${accumulator.total_cost / books:.4f}/book")
    else:
        lines.append(f"  grand total : ${accumulator.total_cost:.2f}")
    return "\n".join(lines)


#: Alias so callers can spell the operation tags consistently. These are the
#: four AI operations the importer tracks (kept here as documentation).
IMPORT_OPERATIONS = ("ocr", "clean_judge", "continuity", "lookup")
