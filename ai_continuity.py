"""Text-only neighbour-continuity judge for picture-book pages (reusable).

This is a **Streamlit-free**, dependency-light module (standard library only;
the Anthropic client is *passed in*, never imported here) so it can be reused by
BOTH:

* the standalone pilot importer ``scripts/import_pilot_data.py`` (which is
  deliberately decoupled from the Streamlit ``data_structures`` package), and
* the live Streamlit app's ``utilities.py`` (later work — a validator flag),
  which must not import anything Streamlit-coupled from a CLI script.

What it does
------------
Given ONLY the text of the page BEFORE an unknown page and the page AFTER it, it
judges whether the story reads continuously across the gap (so the middle page is
almost certainly a genuinely WORDLESS illustration) or whether text appears to be
MISSING on the middle page. In the importer this lets an image-only story page
that is flanked on both sides by text-layer pages SKIP a vision-OCR call when the
narrative flows straight through — validated at ~81% of such OCR calls avoided
with ~zero data loss on the pilot corpus (idea2 experiment).

The tuned prompt (:data:`CONTINUITY_PROMPT`) and reply schema
(:data:`CONTINUITY_SCHEMA`) are ported VERBATIM from that validated experiment;
do not tweak them without re-validating.

Public API
----------
* :func:`check_narrative_continuity` — one Claude call; returns a normalised
  verdict dict and **degrades safely** (any error/unparseable reply yields a
  verdict that forces OCR — never a false skip).
* :func:`should_skip_ocr` — pure, I/O-free decision helper
  (``flows_continuously AND NOT text_appears_missing``).

Note: the small structured-output / JSON-salvage / bool-parse helpers below are
intentionally re-implemented here (rather than imported from the importer or the
Streamlit ``utilities``) to keep this module standalone and app-agnostic; it must
depend on nothing beyond the Anthropic client handed to it.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Tuned prompt + schema — ported VERBATIM from the validated idea2 experiment.
# ---------------------------------------------------------------------------

#: Keep JSON string values single-line so a stray literal newline can't break
#: parsing (matches ``import_pilot_data._JSON_NEWLINE_RULE``).
_JSON_NEWLINE_RULE = (
    "Inside the JSON string values, encode any line break as \\n; never emit a "
    "literal newline inside a quoted string."
)

#: JSON schema for the continuity reply (structured outputs). Kept identical to
#: the validated experiment's ``JUDGE_SCHEMA`` (including ``expected_middle``, so
#: the model reasons exactly as validated) even though only four keys are
#: surfaced by :func:`check_narrative_continuity`.
CONTINUITY_SCHEMA = {
    "type": "object",
    "properties": {
        "flows_continuously": {"type": "boolean"},
        "text_appears_missing": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "expected_middle": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": [
        "flows_continuously",
        "text_appears_missing",
        "confidence",
        "expected_middle",
        "reason",
    ],
    "additionalProperties": False,
}

#: The tuned continuity judge prompt (verbatim from idea2 ``JUDGE_PROMPT``).
CONTINUITY_PROMPT = (
    "You are analysing a children's picture book to decide whether a SINGLE page that sits "
    "BETWEEN two known pages is a genuinely WORDLESS illustration, or whether it contains "
    "printed story text that is currently missing from our data.\n\n"
    "You are given ONLY the text of the page BEFORE it (PREV) and the page AFTER it (NEXT). "
    "You CANNOT see the middle page. Judge purely from whether the narrative reads continuously "
    "from PREV straight into NEXT.\n\n"
    "How picture books work (all NORMAL, and all still count as 'flows continuously'):\n"
    "- Page turns mid-sentence: PREV may stop mid-clause and NEXT complete it.\n"
    "- Wordless full-bleed illustrations between two text pages — extremely common. The art carries "
    "a beat of the story with no words; PREV and NEXT still join up around it.\n"
    "- Scene/time jumps, a new character or setting appearing, and repeated refrains across pages.\n"
    "- Rhyme: PREV's last line often rhymes with NEXT's line, confirming nothing sits between them.\n\n"
    "Signs that TEXT IS MISSING on the middle page (narrative does NOT join up):\n"
    "- PREV ends a complete thought and NEXT begins one that needs a bridge that isn't there: an "
    "action, reply, or event is clearly skipped.\n"
    "- NEXT refers to something (a pronoun, 'the X', a reply, a consequence) never set up by PREV.\n"
    "- A question in PREV whose answer plainly belongs before NEXT; a call with its response gone; "
    "a rhyme/refrain pattern that skips a beat.\n"
    "- A hard narrative discontinuity that a wordless picture alone could not smooth over.\n\n"
    "Decide:\n"
    "- flows_continuously: true if PREV reads naturally into NEXT (allowing a purely wordless picture "
    "between them); false if there is a narrative gap.\n"
    "- text_appears_missing: true if you think the middle page most likely carried story text that is "
    "needed for the narrative to make sense.\n"
    "- confidence: how sure you are of flows_continuously. Use 'high' ONLY when the join is clearly "
    "seamless (e.g. sentence continues, rhyme closes, obvious wordless beat) or clearly broken.\n"
    "- expected_middle: one short phrase — what, if anything, you'd expect the middle page to say "
    "(or 'wordless illustration').\n"
    "- reason: one sentence.\n\n"
    "Respond with ONLY a JSON object.\n"
    + _JSON_NEWLINE_RULE
)


# ---------------------------------------------------------------------------
# Pure decision helper (no I/O — unit-testable).
# ---------------------------------------------------------------------------

def should_skip_ocr(verdict: object) -> bool:
    """True if OCR can be skipped for the flanked image-only page.

    The rule is ``flows_continuously AND NOT text_appears_missing``: the story
    reads straight through, so the middle page is a genuine wordless spread. Any
    non-dict / missing / falsey ``flows_continuously`` (including the safe verdict
    a failed judge returns) yields ``False`` — i.e. OCR. Never skips on doubt.
    """
    if not isinstance(verdict, dict):
        return False
    return bool(verdict.get("flows_continuously")) and not bool(
        verdict.get("text_appears_missing")
    )


# ---------------------------------------------------------------------------
# Anthropic call + safe degradation.
# ---------------------------------------------------------------------------

def _safe_verdict(reason: str) -> dict:
    """A verdict that forces OCR (used on any judge error/unparseable reply)."""
    return {
        "flows_continuously": False,
        "text_appears_missing": True,
        "confidence": "low",
        "reason": reason,
    }


def _as_bool(value: object, default: bool) -> bool:
    """Parse a judge boolean, tolerating the strings 'true'/'false' etc.

    ``bool("false")`` is ``True`` in Python, so a stringified reply would silently
    invert the flag. Defaults are chosen so that ambiguity NEVER produces a skip.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "1"):
            return True
        if v in ("false", "no", "0"):
            return False
    return default


def _extract_json_object(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model reply, or None (fallback path)."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = brace.group(0) if brace else None
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate, strict=False)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _strip_cache_control(blocks: list) -> list:
    """Return content blocks with any ``cache_control`` key removed (SDK fallback)."""
    out: list = []
    for b in blocks:
        if isinstance(b, dict) and "cache_control" in b:
            b = {k: v for k, v in b.items() if k != "cache_control"}
        out.append(b)
    return out


def _supports_structured_outputs(client) -> bool:
    """True if this SDK's ``messages.create`` accepts ``output_config``."""
    try:
        sig = inspect.signature(client.messages.create)
    except (TypeError, ValueError):
        return False
    return "output_config" in sig.parameters


def _continuity_call(client, *, model: str, content_blocks: list,
                     on_usage=None) -> Optional[dict]:
    """Make the Claude call and return the parsed JSON dict (or None).

    Prefers Anthropic structured outputs (``output_config`` json_schema) for a
    guaranteed-valid reply; retries plain and salvages JSON manually on an older
    SDK. Prompt caching (``cache_control`` on the leading static block) is
    harmless below the model minimum. Any API error PROPAGATES to the caller,
    which converts it into the safe OCR-forcing verdict.
    """
    messages = [{"role": "user", "content": content_blocks}]
    use_structured = _supports_structured_outputs(client)
    try:
        if use_structured:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                messages=messages,
                output_config={
                    "format": {"type": "json_schema", "schema": CONTINUITY_SCHEMA}
                },
            )
        else:
            response = client.messages.create(
                model=model, max_tokens=512, messages=messages
            )
    except TypeError:
        # Older SDK: output_config and/or cache_control not accepted — retry plain.
        response = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": _strip_cache_control(content_blocks)}],
        )
    if on_usage is not None:
        on_usage(getattr(response, "usage", None))
    raw = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    try:
        parsed = json.loads(raw, strict=False)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return _extract_json_object(raw)


def check_narrative_continuity(
    client, prev_text: str, next_text: str, *, model: str, on_usage=None
) -> dict:
    """Judge whether the story flows across an unknown page from PREV to NEXT.

    ``client`` is any ``anthropic.Anthropic`` (passed in; never constructed here).
    ``prev_text`` / ``next_text`` are the TRUE text-layer texts of the pages
    either side of the unknown page (do NOT feed OCR'd neighbour text — the judge
    was validated on text-layer neighbours only). ``model`` should be a Sonnet
    model (the validated judge used ``claude-sonnet-4-6``).

    Returns ``{"flows_continuously": bool, "text_appears_missing": bool,
    "confidence": str, "reason": str}``. On ANY error (API failure, no usable
    JSON) it returns a safe verdict (``flows_continuously=False,
    text_appears_missing=True``) so :func:`should_skip_ocr` yields ``False`` and
    the caller OCRs — a judge failure never causes a silent skip.

    ``on_usage`` (optional) is a callback invoked with the call's
    ``response.usage`` (or ``None``) so a caller can meter token cost without this
    module taking any pricing/accounting dependency (kept Streamlit-free and
    dependency-light).
    """
    context = (
        "PREV PAGE TEXT:\n"
        + (prev_text or "(empty)")
        + "\n\nNEXT PAGE TEXT:\n"
        + (next_text or "(empty)")
    )
    content = [
        {"type": "text", "text": CONTINUITY_PROMPT, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": context},
    ]
    try:
        data = _continuity_call(
            client, model=model, content_blocks=content, on_usage=on_usage
        )
    except Exception as exc:  # noqa: BLE001 - network/parse; degrade to OCR-forcing verdict
        return _safe_verdict(f"continuity judge call failed: {type(exc).__name__}")
    if not isinstance(data, dict):
        return _safe_verdict("continuity judge returned no usable JSON")
    return {
        "flows_continuously": _as_bool(data.get("flows_continuously"), False),
        "text_appears_missing": _as_bool(data.get("text_appears_missing"), True),
        "confidence": str(data.get("confidence") or "low").strip(),
        "reason": str(data.get("reason") or "").strip(),
    }
