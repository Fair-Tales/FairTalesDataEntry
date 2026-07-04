from google.cloud import firestore
import streamlit as st
from google.api_core.exceptions import GoogleAPIError
from google.cloud.firestore_v1 import FieldFilter
from google.oauth2 import service_account
import pandas as pd
import anthropic
import base64
import difflib
import json
import logging
import re
import urllib.request
import urllib.error
import bcrypt
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

from ai_pricing import usage_cost

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data-extraction model + resolution (issue #135).
#
# The DATA-EXTRACTION vision/OCR calls (page OCR, title/copyright/collection
# metadata, #52 character detection) run on Claude Sonnet 5 rather than the
# ``claude-sonnet-4-6`` used elsewhere. Sonnet 5 accepts a larger 2576px image
# long edge (vs 1568px on Sonnet 4.6), so dense children's-book text is captured
# at higher effective resolution for better OCR (Chris, 2026-07-03). The cheap
# routing/QC calls (Haiku title/cover-page detection, rotation-angle +
# crop-quality checks) deliberately KEEP their own models/resolution — only the
# extraction calls opt in here.
#
# COST NOTE (corrected 2026-07-04): the higher edge is NOT free. A ~2576px page
# image costs roughly 3x the input tokens of the ~1568px standard tier
# (empirically ~5,300 vs ~1,568 input tokens for a full page) — the earlier
# "SAME token rate" claim here was wrong (#135 comment). The extra resolution is
# a deliberate accuracy-for-cost trade for OCR of dense text; it is kept because
# dropping to 1568px measurably hurt OCR. ``EXTRACTION_MAX_EDGE`` is a single,
# documented knob so the trade can be re-tuned in one place, and per-call token
# usage is now logged (see ``_log_usage``) so a resolution/model cost regression
# like this is visible instead of silent.
#
# Model id verified 2026-07-03 against the Anthropic models docs
# (platform.claude.com/docs/en/about-claude/models/all-models): Claude Sonnet 5
# is generally available with API id ``claude-sonnet-5``.
EXTRACTION_MODEL = 'claude-sonnet-5'

#: Long-edge pixel cap for EXTRACTION images (#135). Higher than the ~1568px
#: default used by the cheap routing/QC vision calls so dense text is sent at
#: higher resolution. ``downscale_for_vision`` still JPEG re-encodes below
#: Claude's 10MB per-image byte cap (#134), so raising the edge cannot
#: reintroduce the oversized-image rejection. Single documented knob for the
#: resolution/cost trade (see the cost note above).
EXTRACTION_MAX_EDGE = 2576

#: ``max_tokens`` for the page-OCR extraction reply. Dense picture-book pages can
#: carry a lot of text and the Sonnet 5 tokenizer runs larger, so this is set
#: comfortably above the 1024 default to avoid truncating a long page mid-JSON
#: (which would otherwise surface as a generic parse failure). See the
#: ``stop_reason == "max_tokens"`` truncation logging in ``vision_text``.
EXTRACTION_OCR_MAX_TOKENS = 2048

#: Long-edge pixel cap for the page-TYPE / cover CLASSIFICATION vision calls
#: (``locate_key_pages`` / ``locate_cover_pages``). These only need to tell a
#: cover/title/copyright page apart from an interior page — they do NOT OCR the
#: text — so a much smaller image is plenty and sends far fewer image tokens
#: across a whole-book multi-image request (#135 cost right-sizing).
LOCATE_MAX_EDGE = 784


# ---------------------------------------------------------------------------
# Global, admin-editable AI-pipeline settings.
#
# The cost/quality-relevant Claude parameters above are the DEFAULTS. An admin
# can override any of them GLOBALLY, without a code deploy, via a single plain
# Firestore config document (collection ``settings``, doc ``ai_pipeline``) edited
# from the admin AI-settings page. The doc is handled as a RAW DICT — the same
# deliberate exception the ``User`` entity uses (#90) — rather than forced
# through the ``DataStructureBase``/``Field`` write-through pattern, because it is
# a single singleton config record, not a domain entity.
#
# Safety / backward-compatibility contract:
#   * ``get_ai_settings()`` starts from ``AI_SETTINGS_DEFAULTS`` (today's
#     constants) and overlays only the stored keys that VALIDATE, so an
#     absent/empty/partial/corrupt doc behaves exactly as the hardcoded app did.
#   * Every stored value is validated on read (models against a GA allow-list;
#     resolutions/tokens against sane bounds) and an invalid value falls back to
#     the default rather than being trusted (guarded lookup, #91).
#   * The one intentional default change from the old constants is
#     ``extraction_max_edge`` = 2000 (down from the ``EXTRACTION_MAX_EDGE`` 2576),
#     an approved cost reduction; the constant is kept as-is for the higher-res
#     opt-in and as documentation of the previous value.
# ---------------------------------------------------------------------------

AI_SETTINGS_COLLECTION = 'settings'
AI_SETTINGS_DOCUMENT = 'ai_pipeline'

#: Collection holding daily Claude API usage/cost rollups (one doc per UTC day,
#: id ``YYYY-MM-DD``). Written cheaply with atomic ``firestore.Increment`` on
#: every AI call (see ``record_api_usage``) and surfaced read-only in the admin
#: AI-settings page. See ``record_api_usage`` for the document schema.
AI_USAGE_COLLECTION = 'api_usage'

#: How many recent daily usage docs the admin summary reads back.
AI_USAGE_SUMMARY_DAYS = 30

#: Allow-list of the real, generally-available Claude model ids that may be
#: selected for any pipeline call. A stored/selected model NOT in this tuple is
#: rejected and the call falls back to the default model (#91 guarded lookup).
#: Verified against the Anthropic models docs (2026-07): Opus 4.8, Sonnet 5,
#: Sonnet 4.6 and Haiku 4.5 are the current GA ids.
AI_MODEL_ALLOWLIST = (
    'claude-opus-4-8',
    'claude-sonnet-5',
    'claude-sonnet-4-6',
    'claude-haiku-4-5',
)

#: Sane bounds for the vision image long-edge (px) and extraction reply tokens.
#: A stored value outside these bounds is treated as invalid and falls back to
#: the default. The upper edge stays under what ``downscale_for_vision`` will
#: JPEG-shrink below Claude's per-image byte cap (#134), so it can never
#: reintroduce an oversized-image rejection.
AI_EDGE_MIN = 512
AI_EDGE_MAX = 4096
AI_TOKENS_MIN = 256
AI_TOKENS_MAX = 8192

#: Effective defaults for every tunable key. Each mirrors today's hardcoded
#: constant so an absent config doc is a no-op — EXCEPT ``extraction_max_edge``
#: (approved 2576 -> 2000 cost reduction).
AI_SETTINGS_DEFAULTS = {
    # Models (validated against AI_MODEL_ALLOWLIST).
    'extraction_model': EXTRACTION_MODEL,             # page OCR
    'metadata_model': EXTRACTION_MODEL,               # title/copyright/collection
    'character_detection_model': EXTRACTION_MODEL,    # #52 character detection
    'locate_model': 'claude-haiku-4-5',               # locate key/cover pages
    'rotation_model': 'claude-sonnet-4-6',            # get_rotation_angle
    'crop_quality_model': 'claude-haiku-4-5',         # check_crop_quality
    'theme_model': 'claude-sonnet-4-6',               # suggest_themes
    # Resolutions / tokens (validated against the bounds above).
    'extraction_max_edge': 2000,                      # approved: down from 2576
    'extraction_max_tokens': EXTRACTION_OCR_MAX_TOKENS,  # 2048
    'locate_max_edge': LOCATE_MAX_EDGE,               # 784
    # Feature toggles (validated as bool).
    'enable_rotation_correction': True,               # gate get_rotation_angle
    'enable_crop_quality_gate': True,                 # gate check_crop_quality
}

#: The tunable keys grouped by validation type.
AI_SETTINGS_MODEL_KEYS = (
    'extraction_model', 'metadata_model', 'character_detection_model',
    'locate_model', 'rotation_model', 'crop_quality_model', 'theme_model',
)
_AI_SETTINGS_EDGE_KEYS = ('extraction_max_edge', 'locate_max_edge')
_AI_SETTINGS_TOKEN_KEYS = ('extraction_max_tokens',)
_AI_SETTINGS_BOOL_KEYS = ('enable_rotation_correction', 'enable_crop_quality_gate')

#: TTL for the cached read of the config doc. Short so an admin's save is picked
#: up promptly by other sessions even without an explicit cache clear; the save
#: path clears the cache immediately for the saving admin.
_AI_SETTINGS_TTL_SECONDS = 60


def _coerce_int(value):
    """Best-effort int coercion for a stored numeric setting, or ``None``.

    Accepts ints and numeric strings; rejects bools (``True``/``False`` are ints
    in Python but are never valid resolutions/tokens) and anything unparseable.
    """
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=_AI_SETTINGS_TTL_SECONDS, show_spinner=False)
def _read_ai_settings_doc():
    """Read the RAW stored AI-pipeline settings dict from Firestore (cached).

    Returns ``{}`` when the doc is absent, empty, oddly-shaped, or the read
    fails — every such case degrades to "use the defaults" rather than raising,
    so the pipeline keeps working. A read error is logged, not swallowed (#127).
    Validation of the individual values happens in ``get_ai_settings`` so the
    cached raw payload stays a faithful copy of what is stored.
    """
    try:
        doc = FirestoreWrapper(auth=False).get_by_reference(
            AI_SETTINGS_COLLECTION, AI_SETTINGS_DOCUMENT
        )
    except GoogleAPIError as exc:
        logger.warning("get_ai_settings: could not read %s/%s: %s",
                       AI_SETTINGS_COLLECTION, AI_SETTINGS_DOCUMENT, exc)
        return {}
    if not doc.exists:
        return {}
    data = doc.to_dict()
    return data if isinstance(data, dict) else {}


def get_ai_settings():
    """Return the effective, validated AI-pipeline settings as a plain dict.

    Starts from ``AI_SETTINGS_DEFAULTS`` and overlays each stored key ONLY when
    its value validates (model in the GA allow-list; resolution/token within
    bounds; toggle is a real bool). An invalid or missing stored value keeps the
    default (guarded, #91), so an empty/absent/corrupt config doc reproduces the
    previous hardcoded behaviour exactly (bar the approved 2000px edge default).

    Cheap to call repeatedly: the Firestore read is cached (``_read_ai_settings_doc``)
    and only the light validation runs per call. Call ``clear_ai_settings_cache``
    after an admin save so the new values are read immediately.
    """
    stored = _read_ai_settings_doc()
    settings = dict(AI_SETTINGS_DEFAULTS)
    if not isinstance(stored, dict):
        return settings
    for key in AI_SETTINGS_DEFAULTS:
        if key not in stored:
            continue
        value = stored[key]
        if key in AI_SETTINGS_MODEL_KEYS:
            if isinstance(value, str) and value in AI_MODEL_ALLOWLIST:
                settings[key] = value
        elif key in _AI_SETTINGS_EDGE_KEYS:
            number = _coerce_int(value)
            if number is not None and AI_EDGE_MIN <= number <= AI_EDGE_MAX:
                settings[key] = number
        elif key in _AI_SETTINGS_TOKEN_KEYS:
            number = _coerce_int(value)
            if number is not None and AI_TOKENS_MIN <= number <= AI_TOKENS_MAX:
                settings[key] = number
        elif key in _AI_SETTINGS_BOOL_KEYS:
            if isinstance(value, bool):
                settings[key] = value
    return settings


def clear_ai_settings_cache():
    """Invalidate the cached AI-settings read so the next ``get_ai_settings``
    re-reads Firestore. Called right after an admin saves new values."""
    _read_ai_settings_doc.clear()


def save_ai_settings(values):
    """Persist admin-edited AI settings to the config doc, then clear the cache.

    ``values`` is filtered to the known tunable keys and re-validated (via
    ``get_ai_settings``'s rules) so nothing out of range or off the model
    allow-list is ever written. Writes with ``merge=True`` so a partial update
    never drops unrelated keys. Raises ``GoogleAPIError`` on a write failure so
    the caller can surface it (per the error-handling convention).
    """
    clean = {}
    for key in AI_SETTINGS_DEFAULTS:
        if key not in values:
            continue
        value = values[key]
        if key in AI_SETTINGS_MODEL_KEYS:
            if isinstance(value, str) and value in AI_MODEL_ALLOWLIST:
                clean[key] = value
        elif key in _AI_SETTINGS_EDGE_KEYS:
            number = _coerce_int(value)
            if number is not None and AI_EDGE_MIN <= number <= AI_EDGE_MAX:
                clean[key] = number
        elif key in _AI_SETTINGS_TOKEN_KEYS:
            number = _coerce_int(value)
            if number is not None and AI_TOKENS_MIN <= number <= AI_TOKENS_MAX:
                clean[key] = number
        elif key in _AI_SETTINGS_BOOL_KEYS:
            clean[key] = bool(value)
    FirestoreWrapper(auth=False).set_document(
        AI_SETTINGS_COLLECTION, AI_SETTINGS_DOCUMENT, clean, merge=True
    )
    clear_ai_settings_cache()


# ---------------------------------------------------------------------------
# Shared connection + AI helpers (issue #129).
#
# Single source of truth for the three patterns that were previously hand-rolled
# across many pages/modules: the S3 filesystem, the Anthropic client, and the
# image -> vision-call -> JSON boilerplate. Construct these ONLY via these
# accessors — never inline an ``s3fs.S3FileSystem(...)`` /
# ``anthropic.Anthropic(...)`` / vision request in a page or data structure.
# (scripts/data_cleanup.py is the one exception: it runs OUTSIDE Streamlit, has
# no ``st.secrets``, and keeps its own construction.)
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def get_s3_filesystem():
    """Build the app's authenticated s3fs filesystem from the AWS secrets (#129).

    Cached so every Streamlit surface that touches S3 shares one configured
    filesystem instead of constructing ``s3fs.S3FileSystem`` inline.
    """
    import s3fs

    return s3fs.S3FileSystem(
        anon=False,
        key=st.secrets["AWS_ACCESS_KEY_ID"],
        secret=st.secrets["AWS_SECRET_ACCESS_KEY"],
    )


def get_anthropic_client():
    """Return an ``anthropic.Anthropic`` client, or ``None`` when no API key is
    configured (#129).

    Centralises the ``'ANTHROPIC_API_KEY' in st.secrets`` guard: callers do
    ``client = get_anthropic_client(); if client is None: <show no-API-key UI>``.
    """
    if 'ANTHROPIC_API_KEY' not in st.secrets:
        return None
    return anthropic.Anthropic(api_key=st.secrets['ANTHROPIC_API_KEY'])


def strip_json_fence(raw):
    """Strip a leading ```` ``` ```` / ```` ```json ```` markdown fence from a
    model reply and return the inner payload, ready for ``json.loads`` (#129)."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def first_text_block(response):
    """Return the text of the FIRST ``text`` content block in a Claude response,
    or ``None`` when the reply carried no text block.

    Selecting the first *text* block (rather than the fragile
    ``response.content[0].text``) is robust to a leading non-text block — e.g. a
    ``thinking`` block on a model that emits one — which would otherwise raise
    ``AttributeError`` and break extraction. Defensive hardening: on the real
    OCR/extraction calls today Sonnet 5 does not emit a leading thinking block,
    but this removes the latent failure mode entirely (audit item 1).
    """
    return next(
        (block.text for block in response.content
         if getattr(block, "type", None) == "text"),
        None,
    )


def _log_usage(response, model, label=""):
    """Log the token usage of a Claude ``response`` so per-call cost is visible.

    Emitted at INFO on the module logger (visible in the Streamlit Cloud logs).
    This is the telemetry that would have surfaced the #135 image-token
    regression instead of it going unnoticed (audit item 7): it records the
    input / output / cache token counts, the model, a short flow ``label`` and
    the ``stop_reason`` for every shared vision / JSON call. Fully guarded — a
    missing/oddly-shaped ``usage`` object never breaks the call.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    logger.info(
        "ai_usage flow=%s model=%s input=%s output=%s cache_read=%s "
        "cache_write=%s stop=%s",
        label or "-", model,
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
        getattr(usage, "cache_read_input_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(response, "stop_reason", None),
    )
    # Persist the cost/token rollup for the admin usage dashboard (best-effort:
    # a tracking failure must never break the user's actual request).
    record_api_usage(response, model, label)


def _usage_token_counts(usage):
    """Read the four token counts off an SDK ``usage`` object (all guarded).

    Returns ``(input, output, cache_read, cache_write)`` as ints; any missing
    attribute counts as 0 so an SDK usage-shape change can't crash accounting.
    """
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
        int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )


def _usage_flow_key(label):
    """Sanitise a flow label into a Firestore-safe map key.

    Firestore treats ``.`` as a field-path separator inside a map key, so it is
    replaced (defensive — today's labels have none). Empty labels become
    ``"unlabelled"`` so every call is attributed to some flow.
    """
    key = (label or "").strip() or "unlabelled"
    return key.replace(".", "_")


def record_api_usage(response, model, label=""):
    """Accumulate one Claude call's tokens + USD cost into the daily usage doc.

    Cost is computed from the shared ``ai_pricing`` table (#129 — never duplicate
    the pricing recipe). The per-day document (``api_usage/YYYY-MM-DD``) is updated
    with atomic ``firestore.Increment`` so this is a single cheap write with NO
    read-modify-write, safe under concurrency. The schema is::

        api_usage/2026-07-04:
          date:                    "2026-07-04"
          total_calls, total_cost_usd,
          total_input_tokens, total_output_tokens,
          total_cache_read_tokens, total_cache_write_tokens
          by_model: { "<model>": { calls, cost_usd, input_tokens,
                                   output_tokens, cache_read_tokens,
                                   cache_write_tokens } }
          by_flow:  { "<label>": { ...same shape... } }

    Fully guarded per the error-handling convention: a missing usage is a no-op,
    and any Firestore/other failure is LOGGED (never raised) so usage tracking can
    never break a user's real request.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    try:
        input_tokens, output_tokens, cache_read, cache_write = _usage_token_counts(usage)
        cost = usage_cost(model, input_tokens, output_tokens, cache_read, cache_write)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        def _bucket():
            # Fresh Increment sentinels per bucket (a sentinel is single-use).
            return {
                "calls": firestore.Increment(1),
                "cost_usd": firestore.Increment(cost),
                "input_tokens": firestore.Increment(input_tokens),
                "output_tokens": firestore.Increment(output_tokens),
                "cache_read_tokens": firestore.Increment(cache_read),
                "cache_write_tokens": firestore.Increment(cache_write),
            }

        data = {
            "date": day,
            "total_calls": firestore.Increment(1),
            "total_cost_usd": firestore.Increment(cost),
            "total_input_tokens": firestore.Increment(input_tokens),
            "total_output_tokens": firestore.Increment(output_tokens),
            "total_cache_read_tokens": firestore.Increment(cache_read),
            "total_cache_write_tokens": firestore.Increment(cache_write),
            "by_model": {model: _bucket()},
            "by_flow": {_usage_flow_key(label): _bucket()},
        }
        # set(merge=True) creates the day doc on first write and applies the
        # Increment transforms to nested maps without a prior read.
        FirestoreWrapper(auth=False).set_document(
            AI_USAGE_COLLECTION, day, data, merge=True
        )
    except (GoogleAPIError, ValueError, TypeError) as exc:
        logger.warning("record_api_usage: could not record usage (flow=%s "
                       "model=%s): %s", label or "-", model, exc)


_USAGE_METRIC_KEYS = (
    "calls", "cost_usd", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens",
)


def _empty_usage_bucket():
    """Zeroed usage metrics dict (one per model / flow / total)."""
    return {key: 0 for key in _USAGE_METRIC_KEYS}


def _add_usage_breakdown(target, source):
    """Merge a stored ``{name: metrics}`` breakdown map into ``target`` in place."""
    if not isinstance(source, dict):
        return
    for name, metrics in source.items():
        if not isinstance(metrics, dict):
            continue
        bucket = target.setdefault(name, _empty_usage_bucket())
        for key in _USAGE_METRIC_KEYS:
            bucket[key] += metrics.get(key, 0) or 0


def _day_usage_totals(doc):
    """Extract the flat per-day totals from a stored usage doc as a metrics dict."""
    return {
        "calls": doc.get("total_calls", 0) or 0,
        "cost_usd": doc.get("total_cost_usd", 0) or 0,
        "input_tokens": doc.get("total_input_tokens", 0) or 0,
        "output_tokens": doc.get("total_output_tokens", 0) or 0,
        "cache_read_tokens": doc.get("total_cache_read_tokens", 0) or 0,
        "cache_write_tokens": doc.get("total_cache_write_tokens", 0) or 0,
    }


def get_api_usage_summary(days=AI_USAGE_SUMMARY_DAYS):
    """Read the recent daily API-usage docs and aggregate them for the admin view.

    Reads the last ``days`` daily docs (``api_usage/YYYY-MM-DD``) by explicit id —
    no query/index needed — and returns a read-only summary::

        {
          "today":       <metrics dict or None>,   # today's flat totals
          "window_days": days,
          "window":      {"totals": <metrics>, "by_model": {...}, "by_flow": {...}},
          "daily":       [ {"date": ..., <metrics>}, ... ]  # newest first
        }

    Guarded (#127): a read failure logs and yields empty totals rather than
    raising, so the admin page degrades gracefully.
    """
    window_totals = _empty_usage_bucket()
    by_model: dict = {}
    by_flow: dict = {}
    daily: list = []
    today_metrics = None
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    wrapper = FirestoreWrapper(auth=False)
    for offset in range(days):
        day = (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            snapshot = wrapper.get_by_reference(AI_USAGE_COLLECTION, day)
        except GoogleAPIError as exc:
            logger.warning("get_api_usage_summary: could not read %s/%s: %s",
                           AI_USAGE_COLLECTION, day, exc)
            continue
        if not snapshot.exists:
            continue
        doc = snapshot.to_dict()
        if not isinstance(doc, dict):
            continue
        totals = _day_usage_totals(doc)
        for key in _USAGE_METRIC_KEYS:
            window_totals[key] += totals[key]
        _add_usage_breakdown(by_model, doc.get("by_model"))
        _add_usage_breakdown(by_flow, doc.get("by_flow"))
        daily.append(dict(date=day, **totals))
        if day == today:
            today_metrics = totals

    return {
        "today": today_metrics,
        "window_days": days,
        "window": {"totals": window_totals, "by_model": by_model, "by_flow": by_flow},
        "daily": daily,
    }


def build_vision_content(images, prompt, *, downscale=True, max_edge=1568):
    """Build the ``[image block, ..., text]`` content list for a vision request.

    Each item in ``images`` (raw image byte strings) becomes a base64 JPEG image
    block, optionally downscaled for Claude's vision sweet spot, followed by the
    text ``prompt``. Centralises the downscale -> base64 -> content-block
    boilerplate duplicated across the vision callers (#129).

    ``max_edge`` is the longest-edge pixel cap applied when ``downscale`` is set;
    it defaults to the ~1568px sweet spot but the DATA-EXTRACTION callers pass the
    higher ``EXTRACTION_MAX_EDGE`` (2576px) for better OCR of dense text (#135).
    """
    from image_processing import downscale_for_vision

    content = []
    for image_bytes in images:
        data = (
            downscale_for_vision(image_bytes, max_edge=max_edge)
            if downscale else image_bytes
        )
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(data).decode('utf-8'),
            },
        })
    content.append({"type": "text", "text": prompt})
    return content


def vision_text(client, images, prompt, *, model="claude-sonnet-4-6",
                max_tokens=1024, downscale=True, max_edge=1568, label=""):
    """Send ``images`` + ``prompt`` to a Claude vision model and return the raw
    reply text (stripped), or ``None`` when the response carried no text block.

    Anthropic API errors propagate to the caller (#127): callers that want a
    resilient default on a transient failure catch ``anthropic.AnthropicError``
    themselves and log it.

    ``max_edge`` threads the longest-edge downscale cap through to
    ``build_vision_content`` so extraction callers can request higher-resolution
    OCR images (#135). The first *text* block is selected robustly via
    ``first_text_block`` (audit item 1), token usage is logged (audit item 7),
    and a ``max_tokens`` truncation is logged distinctly so a truncated reply is
    diagnosable rather than surfacing only as a downstream JSON parse failure.
    """
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": build_vision_content(
                images, prompt, downscale=downscale, max_edge=max_edge
            ),
        }],
    )
    _log_usage(response, model, label)
    if getattr(response, "stop_reason", None) == "max_tokens":
        logger.warning(
            "vision_text: reply truncated at max_tokens=%s (model=%s, flow=%s); "
            "output may be incomplete and unparseable",
            max_tokens, model, label or "-",
        )
    text = first_text_block(response)
    return text.strip() if text is not None else None


def vision_json(client, images, prompt, *, model="claude-sonnet-4-6",
                max_tokens=1024, downscale=True, max_edge=1568, label=""):
    """Send ``images`` + ``prompt`` to a Claude vision model and parse the JSON
    reply, returning ``(data_or_None, raw_text)`` (#129).

    ``data`` is the parsed object (typically a dict), or ``None`` when the reply
    carried no text block or could not be parsed as JSON. ``raw_text`` is the raw
    model response (``""`` when there was none) so callers can retain it for
    audit even on a parse failure. Anthropic API errors propagate to the caller;
    JSON-decode failures are logged here, not silently swallowed (#127).

    ``max_edge`` threads the downscale resolution through to ``vision_text`` so
    DATA-EXTRACTION callers can OCR at the higher ``EXTRACTION_MAX_EDGE`` (#135).
    """
    raw = vision_text(
        client, images, prompt, model=model, max_tokens=max_tokens,
        downscale=downscale, max_edge=max_edge, label=label,
    )
    if raw is None:
        return None, ""
    try:
        return json.loads(strip_json_fence(raw)), raw
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("vision_json: could not parse model reply as JSON: %s", exc)
        return None, raw


def is_authenticated():
    if 'authentication_status' not in st.session_state:
        st.session_state['authentication_status'] = False

    return st.session_state['authentication_status']


def check_authentication_status():
    if 'authentication_status' not in st.session_state:
        st.session_state['authentication_status'] = False

    if not is_authenticated():
        st.switch_page("./pages/login.py")

    # Consume the remember-me restore flag (#111) on any normally-reached
    # authenticated page, so it cannot go stale and wrongly redirect a later,
    # deliberate visit to the login/sign-out page. The login page itself does not
    # call this function, so it still sees the flag and redirects a freshly
    # restored user home.
    st.session_state.pop('_remember_restored', None)


_MAX_HISTORY = 10


def navigate_to(page_path):
    """Navigate to a page, pushing the current page onto the back-history stack."""
    current = st.session_state.get('_current_page', None)
    if current:
        history = st.session_state.get('_page_history', [])
        history.append(current)
        st.session_state['_page_history'] = history[-_MAX_HISTORY:]
    st.switch_page(page_path)


def go_back(fallback="./pages/user_home.py"):
    """Navigate to the previous page in the history stack."""
    history = st.session_state.get('_page_history', [])
    if history:
        previous = history.pop()
        st.session_state['_page_history'] = history
        st.switch_page(previous)
    else:
        st.switch_page(fallback)


def clear_page_history():
    """Reset the back-history stack (used at root pages and on logout)."""
    st.session_state['_page_history'] = []


def clear_entity_form_state(prefix):
    """Drop any persisted widget state for a per-entity ``to_form()`` form.

    Entity form widgets are keyed ``<entity>_form_<field>_<document_id>`` (see
    the "Widget key naming" note in CLAUDE.md). A brand-new, unregistered entity
    has an empty/placeholder ``document_id``, so two consecutive new entities of
    the same type would share keys and Streamlit would re-show the first
    entity's values (ignoring the ``value=``/``index=`` seeding) for the second.

    Call this at each "start a new X" choke point with the entity's key prefix
    (e.g. ``"book_form_"``) so the next form re-seeds cleanly.
    """
    for key in [k for k in st.session_state if k.startswith(prefix)]:
        st.session_state.pop(key, None)


# Belt-and-braces hide of Streamlit's *default* multipage navigation (#116).
#
# The app suppresses the auto page list with ``st.navigation(pages,
# position="hidden")`` in Home.py, but on a cold load / reconnect the frontend
# can momentarily render the default ``pages/``-directory nav (the full list of
# would-be-hidden internal pages) into the ``stSidebarNav`` container before the
# server's "hidden" config lands — the intermittent flash reported on the login
# screen (#116). This static CSS is part of the served page markup, so the
# container is forced hidden as soon as the stylesheet is parsed, regardless of
# render order. Our intended sidebar links use ``st.sidebar.page_link(...)``,
# which render into the sidebar *user-content* area (NOT ``stSidebarNav``), so
# this never hides the real navigation.
_HIDE_DEFAULT_NAV_CSS = """
    <style>
    [data-testid="stSidebarNav"] { display: none !important; }
    </style>
"""

# Fair Tales brand yellow, sampled as the dominant opaque colour of
# resources/logo_temp.png (RGB 253,201,25 / #FDC919). Reused as a soft, low-alpha
# tint for the home/login header bar and the navigation sidebar so the whole app
# stays on-brand from a single source of truth.
LOGO_YELLOW_RGB = (253, 201, 25)
_LOGO_PATH = "resources/logo_temp.png"


def _yellow_rgba(alpha):
    """Return the brand yellow as a CSS ``rgba(...)`` string at the given alpha."""
    r, g, b = LOGO_YELLOW_RGB
    return f"rgba({r}, {g}, {b}, {alpha})"


# Soft yellow tint on the whole navigation sidebar (kept light so the dark
# page-link text stays legible).
_BRAND_SIDEBAR_CSS = f"""
    <style>
    [data-testid="stSidebar"] {{
        background-color: {_yellow_rgba(0.18)};
    }}
    </style>
"""


@st.cache_data(show_spinner=False)
def _logo_data_uri():
    """Return the app logo as a base64 ``data:`` URI for embedding in inline HTML.

    Cached per server process so the file is read and encoded once rather than on
    every page render.
    """
    with open(_LOGO_PATH, "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_header_bar():
    """Render the Fair Tales header bar: logo + app title on a soft yellow tint.

    Used ONLY on the landing/home page and the login page (not app-wide). The logo
    is embedded as a base64 ``data:`` URI so it renders inside the inline HTML.
    Title text is sourced from ``text_content`` (Instructions.app_title).
    """
    from text_content import Instructions

    header_html = f"""
        <div style="display: flex; align-items: center; gap: 1.25rem;
                    background: {_yellow_rgba(0.30)};
                    padding: 1rem 1.5rem; border-radius: 0.75rem;
                    margin-bottom: 1.5rem;">
            <img src="{_logo_data_uri()}" alt="Fair Tales logo"
                 style="height: 5rem; width: auto; flex: 0 0 auto;" />
            <span style="font-size: 2.1rem; font-weight: 700; line-height: 1.15;">
                {Instructions.app_title}
            </span>
        </div>
    """
    st.markdown(header_html, unsafe_allow_html=True)


def page_layout(current_page=None):
    st.set_page_config(
        initial_sidebar_state="collapsed",
        layout="wide"
    )
    # Force-hide the default multipage nav to defeat the intermittent flash (#116)
    # before any sidebar content is rendered, and apply the soft yellow brand tint
    # to the navigation sidebar.
    st.markdown(_HIDE_DEFAULT_NAV_CSS, unsafe_allow_html=True)
    st.markdown(_BRAND_SIDEBAR_CSS, unsafe_allow_html=True)
    if current_page:
        st.session_state['_current_page'] = current_page

    # App-wide Fair Tales logo at the top of the navigation sidebar, rendered large
    # so it is clearly legible in the nav bar (st.logo's own sizes were too small).
    # width is an explicit pixel integer per the st.image convention.
    st.sidebar.image(_LOGO_PATH, width=200)

    # Sidebar labels live in text_content (Nav); the Ko-fi URL in text_content
    # (Donate). Imported lazily to match this module's text_content usage and
    # avoid any import-time coupling.
    from text_content import Nav, Donate

    if not is_authenticated():
        # Logged-out sidebar (#137): ONLY Login and Donate — no Home / Books We
        # Need / Settings / Report. Donate links straight to Ko-fi (external URL,
        # opens a new tab) so a visitor can donate without an account; it does not
        # route through the donate.py page (which requires auth).
        st.sidebar.page_link("pages/login.py", label=Nav.login)
        st.sidebar.page_link(Donate.url, label=Nav.donate)
        return

    # Authenticated sidebar. The auth item reads 'Sign out' (not 'Login') and
    # points at login.py, which renders the sign-out view when authenticated (#138).
    st.sidebar.page_link("pages/login.py", label=Nav.sign_out)
    st.sidebar.page_link("pages/landing.py", label=Nav.home)
    st.sidebar.page_link("pages/priority_books.py", label=Nav.books_we_need)
    st.sidebar.page_link("pages/account_settings.py", label=Nav.settings)
    # Donate is a direct external Ko-fi link everywhere (#137); the donate.py page
    # is retained but no longer linked from the sidebar.
    st.sidebar.page_link(Donate.url, label=Nav.donate)
    st.sidebar.page_link("pages/report_feedback.py", label=Nav.report)
    # Team members and admins can reach data validation straight from the sidebar
    # (#47/#83); admin-only tools (the Admin page) stay hidden from team members.
    # 'Reconstruct orphaned books' has moved off the sidebar to the bottom of the
    # Admin page (#141).
    role = st.session_state.get('role', 'archivist')
    is_admin_user = st.session_state.get('admin', False) or role == 'admin'
    if is_admin_user or role == 'team':
        st.sidebar.page_link("pages/validation.py", label=Nav.data_validation)
    if is_admin_user:
        st.sidebar.page_link("pages/admin.py", label=Nav.admin)

    # Unobtrusive current-user caption (#140) so a user notices a wrong account.
    # Rendered after the links and before Back so it never disrupts the layout.
    username = st.session_state.get('username', '')
    if username:
        st.sidebar.caption(Nav.signed_in_as.format(username=username))

    history = st.session_state.get('_page_history', [])
    # Hide Back during the guided book sub-entry flow (add author/illustrator/
    # publisher): returning to add_book.py would just re-forward here. Use Cancel.
    if history and not st.session_state.get('adding_book_entries', False):
        if st.sidebar.button(Nav.back, key="sidebar_back_button"):
            go_back()



def get_user(username):
    db = FirestoreWrapper().connect_user(auth=False)
    users_ref = db.collection("users")
    query_ref = users_ref.where(filter=firestore.FieldFilter("username", "==", username))
    docs = query_ref.get()
    if len(docs) == 1:
        return docs[0]
    else:
        return None
    
# ---------------------------------------------------------------------------
# Role tiers (issue #83).
#
# Every user has one of three permission tiers, stored as a ``role`` string on
# their Firestore user document:
#   'archivist' (default) — view results; enter single books (manual + photo);
#                           edit ONLY books they uploaded (entered_by == them).
#   'team'                — everything an archivist can do, PLUS edit books
#                           uploaded by others and access the validation
#                           workflow (the validation workflow itself is #47;
#                           this change only gates access to that page).
#   'admin'               — everything above, PLUS delete users/books, export /
#                           download data, and the admin page.
#
# BACK-COMPAT: older user documents predate the ``role`` field. A legacy user
# with ``admin: true`` and no ``role`` resolves to 'admin'; a user with neither
# resolves to 'archivist'. This is resolved at read time (``resolve_role``), so
# NO data migration is required.
#
# NOTE: there is no in-app role-management UI yet — admins set a user's ``role``
# directly on the Firestore user document for now. A management UI is tracked by
# #47 / #69 and is out of scope here.
ROLE_ARCHIVIST = 'archivist'
ROLE_TEAM = 'team'
ROLE_ADMIN = 'admin'
VALID_ROLES = (ROLE_ARCHIVIST, ROLE_TEAM, ROLE_ADMIN)

#: Reserved system "user" that OWNS AI-generated books (#131). A book whose
#: ``entered_by`` is databot is editable by ANY role — AI-reconstructed books
#: are not locked to the single person who triggered their creation, so whoever
#: is free can pick one up to finish/correct. Not a real login account; it is a
#: stable owner identity that AI-creation flows stamp onto the books they produce
#: (book_reconstruction now; #123's automated pipeline later).
DATABOT_USERNAME = 'databot'


def resolve_role(user_dict):
    """Resolve a user's effective role from their raw user dict (back-compat).

    A valid stored ``role`` wins; otherwise a legacy ``admin: true`` flag maps
    to 'admin'; otherwise the default 'archivist'. Every lookup is guarded with
    a ``.get`` default so a missing field never raises.
    """
    role = user_dict.get('role')
    if role in VALID_ROLES:
        return role
    if user_dict.get('admin', False):
        return ROLE_ADMIN
    return ROLE_ARCHIVIST


def get_role(username):
    """Return the effective role string for ``username`` (back-compat aware).

    Falls back to 'archivist' when the user document cannot be found.
    """
    user = get_user(username)
    if user is None:
        return ROLE_ARCHIVIST
    return resolve_role(user.to_dict())


def get_admin(username):
    """Back-compat shim: True when ``username`` resolves to the admin role."""
    return get_role(username) == ROLE_ADMIN


def is_admin():
    """True when the current session's role is admin (guarded session read).

    Gates admin-only actions: deleting users/books, exporting/downloading data,
    and the admin page.
    """
    return st.session_state.get('role', ROLE_ARCHIVIST) == ROLE_ADMIN


def is_team_or_above():
    """True when the current session's role is team member or admin.

    Gates team-and-above actions: editing books uploaded by others and reaching
    the validation page (the validation workflow itself is #47).
    """
    return st.session_state.get('role', ROLE_ARCHIVIST) in (ROLE_TEAM, ROLE_ADMIN)


def databot_entered_by():
    """The ``entered_by`` value identifying the databot system user (#131).

    Returns the SAME representation real books use for ``entered_by`` — a
    ``users``-collection ``DocumentReference`` (here pointing at
    ``users/databot``) — so databot is treated exactly like a normal owner by
    Firestore equality queries and by ref-path comparisons (e.g. validation's
    ``_current_ref_name`` / the "entered by" caption).

    Representation choice: we ALWAYS return ``username_to_doc_ref(DATABOT_USERNAME)``
    rather than conditionally falling back to the plain string ``"databot"`` when
    no ``users/databot`` document exists. ``username_to_doc_ref`` only builds a
    reference (it does not require the document to exist), and Firestore reference
    equality is path-based, so a reference to a not-yet-created ``users/databot``
    doc still matches consistently. This keeps a single, stable representation
    (a doc ref, matching real books) used both when STAMPING databot onto a book
    (book_reconstruction) and when QUERYING databot-owned books (review_my_books),
    avoids an extra existence read on every call, and — crucially — does not
    silently switch representation (string vs ref) if a databot user doc is later
    created, which would split databot books into two non-matching owner values.
    The codebase still tolerates a plain-string ``entered_by`` (see
    ``pages/validation.py``) for legacy/single-DB records, so nothing breaks if a
    string ``"databot"`` is ever encountered.
    """
    return st.session_state['firestore'].username_to_doc_ref(DATABOT_USERNAME)


def authenticate_user(username, password):
    """Authenticate a user by username and password.

    Returns one of three string statuses:
    - "ok"              — credentials valid and account confirmed.
    - "not_confirmed"   — credentials valid but account not yet confirmed.
    - "bad_credentials" — username not found or password incorrect.

    Security note: the password is always checked before the confirmation flag
    is inspected.  This prevents an attacker from inferring account existence
    via the confirmation state using a wrong password.
    """
    user = get_user(username)
    if user is None:
        return "bad_credentials"

    user_dict = user.to_dict()

    password_ok = bcrypt.checkpw(
        password=password.encode('utf8'),
        hashed_password=user_dict['password'].encode('utf8')
    )
    if not password_ok:
        return "bad_credentials"

    if not user_dict.get('is_confirmed', False):
        return "not_confirmed"

    return "ok"


def hash_password(password):
    hashed_password = bcrypt.hashpw(
        password.encode('utf8'), bcrypt.gensalt()
    ).decode('utf8')
    return hashed_password


def send_confirmation_email(send_to, username, confirmation_token, name):

    smtpserver = smtplib.SMTP_SSL('smtp.gmail.com', 465)
    smtpserver.ehlo()
    smtpserver.login(st.secrets["email_address"], st.secrets["gmail_app_password"])

    subject = "Please confirm your account registration"
    body = """
        Dear %s, 
        
        Thank you for registering for an account on our data entry tool.
        Please click the link below to confirm your registration.
        
        If you did not register, please reply to this email to let us know
        and we will delete your email address.
        
        Thanks,
        The Fair Tales team
        
    """ % name
    confirmation_link = f"{st.secrets['app_url']}confirm?token={confirmation_token}&user={username}"
    body += confirmation_link
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = st.secrets["email_address"]
    msg['To'] = send_to

    smtpserver.send_message(msg)
    smtpserver.close()


def send_password_reset_email(send_to, username, reset_token, name):
    """Email a self-service password reset link.

    Mirrors ``send_confirmation_email``'s SMTP path and the ``app_url`` secret
    pattern, but points the recipient at the public ``reset_password`` page with
    ``token`` and ``user`` query params.  The email copy lives in the
    ``text_content`` module (``PasswordReset``).
    """
    from text_content import PasswordReset

    smtpserver = smtplib.SMTP_SSL('smtp.gmail.com', 465)
    smtpserver.ehlo()
    smtpserver.login(st.secrets["email_address"], st.secrets["gmail_app_password"])

    body = PasswordReset.email_body % name
    reset_link = f"{st.secrets['app_url']}reset_password?token={reset_token}&user={username}"
    body += reset_link
    msg = MIMEText(body)
    msg['Subject'] = PasswordReset.email_subject
    msg['From'] = st.secrets["email_address"]
    msg['To'] = send_to

    smtpserver.send_message(msg)
    smtpserver.close()


def author_entry_to_name(entry):
    """
    Helper method converts an author/illustrator entry from the Firestore
    database to a readable display name.

    Illustrators are now stored as a single ``name`` field (#156), while authors
    (and legacy illustrator records) still store ``forename``/``surname``. Prefer
    a populated ``name`` when present, otherwise fall back to joining
    ``forename``/``surname`` so both shapes render correctly.
    """
    data = entry.to_dict()
    name = (data.get('name') or '').strip()
    if name:
        return name
    return ' '.join([data.get('forename', ''), data.get('surname', '')]).strip()


def extract_isbn(text):
    """Extract ISBN-13 or ISBN-10 from text. Returns string or None.

    Real-world copyright pages hyphenate ISBNs with varied group sizes, so we
    match a run of digits separated by optional hyphens/spaces and validate the
    cleaned length rather than assuming a fixed grouping.
    """
    if not text:
        return None
    isbn13 = re.search(r'97[89][-\s]?(?:\d[-\s]?){9}\d', text)
    if isbn13:
        return re.sub(r'[-\s]', '', isbn13.group())
    isbn10 = re.search(r'\b\d[-\s]?(?:\d[-\s]?){8}[\dX]\b', text)
    if isbn10:
        return re.sub(r'[-\s]', '', isbn10.group())
    return None


_PERSON_GENDER_OPTIONS = ("Woman", "Man", "Non-binary", "Other", "Unknown")


def _parse_person_details(response):
    """Extract a validated ``{'gender'}`` dict from a lookup response, robustly
    (#113/#149).

    A web-search reply may narrate before emitting the JSON, so this walks the
    text blocks (preferring the final one), strips any markdown fence and, when
    the whole block still isn't valid JSON, falls back to extracting the first
    ``{...}`` object. Returns ``None`` when no JSON payload can be recovered.

    The lookup is gender-only now that author/illustrator date of birth has
    been dropped (#149); any ``birth_year`` the model emits is ignored.
    """
    texts = [
        block.text for block in response.content
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    if not texts:
        return None

    data = None
    # Try the final text block first (the model's answer usually comes last),
    # then the whole reply joined, so a stray leading narration can't hide the
    # JSON.
    for candidate in (texts[-1], "\n".join(texts)):
        candidate = strip_json_fence(candidate.strip())
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            match = re.search(r"\{.*?\}", candidate, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except (json.JSONDecodeError, ValueError):
                    data = None
        if isinstance(data, dict):
            break
    if not isinstance(data, dict):
        return None

    gender = data.get("gender", "Unknown")
    if gender not in _PERSON_GENDER_OPTIONS:
        gender = "Unknown"

    return {"gender": gender}


def lookup_person_details(name, role, client, book_title=None):
    """Use Claude + web search to suggest the gender of a named person.

    When known, ``book_title`` is passed to the model as disambiguating context
    ("<role> of the children's book '<title>'") so common names resolve to the
    right person (#113). Date of birth is no longer looked up (#149) — the forms
    only consume gender.

    Returns a dict with 'gender' (str from ``_PERSON_GENDER_OPTIONS``, the same
    set AuthorForm.gender_options offers), or None on any failure. A clean
    "no reliable info found" result is returned as ``{'gender': 'Unknown'}``
    rather than a confident wrong guess.
    """
    from text_content import AIPrompts

    context = ""
    if book_title and book_title.strip():
        context = AIPrompts.person_lookup_book_context.format(title=book_title.strip())
    prompt = AIPrompts.person_lookup.format(name=name, role=role, context=context)

    tools = [{"type": "web_search_20260209", "name": "web_search"}]
    try:
        messages = [{"role": "user", "content": prompt}]
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            tools=tools,
            messages=messages,
        )
        # The server-side web-search loop can stop with ``pause_turn`` before it
        # has produced the final answer; re-send so it resumes rather than
        # returning an empty/partial reply (#113).
        continuations = 0
        while getattr(response, "stop_reason", None) == "pause_turn" and continuations < 3:
            messages.append({"role": "assistant", "content": response.content})
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                tools=tools,
                messages=messages,
            )
            continuations += 1

        return _parse_person_details(response)
    except (anthropic.AnthropicError, json.JSONDecodeError,
            ValueError, TypeError) as exc:
        # Narrowed from a broad ``except`` (#127): API failures and malformed /
        # unparseable replies degrade to "no suggestion", but are logged rather
        # than silently swallowed so transient issues are diagnosable.
        logger.warning("lookup_person_details failed for %r (%s): %s", name, role, exc)
        return None


def _claude_json(client, prompt, max_tokens=1024, label="character_detection",
                 model=None):
    """Send a text prompt to a Claude model and parse the JSON response.

    Reuses the JSON-fence-stripping convention used by the existing Claude
    helpers. This is the #52 character-detection path (its only caller is
    ``detect_book_characters``); ``model`` defaults to ``EXTRACTION_MODEL`` but
    the caller passes the admin-configured ``character_detection_model`` so the
    model can be re-tuned globally without a deploy.
    Raises json.JSONDecodeError if the model does not return valid JSON, or an
    anthropic error if the API call fails — the caller is expected to surface
    these to the user.

    The first *text* block is selected robustly via ``first_text_block`` (audit
    item 1) so a leading non-text block can't break parsing, token usage is
    logged (audit item 7), and a ``max_tokens`` truncation is logged distinctly.
    """
    model = model or EXTRACTION_MODEL
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    _log_usage(response, model, label)
    if getattr(response, "stop_reason", None) == "max_tokens":
        logger.warning(
            "_claude_json: reply truncated at max_tokens=%s (model=%s, flow=%s); "
            "output may be incomplete and unparseable",
            max_tokens, model, label,
        )
    text = first_text_block(response)
    if text is None:
        raise json.JSONDecodeError("no text block in model reply", "", 0)
    return json.loads(strip_json_fence(text))


def detect_book_characters(pages, client, progress_callback=None):
    """Two-pass character + alias detection across a book's story pages (#52).

    Args:
        pages: iterable of (page_number, page_text) for story pages with text.
        client: an anthropic.Anthropic client.
        progress_callback: optional callable(done, total) for UI progress.

    Returns a list of character suggestion dicts, each with keys:
        name (str), gender (one of CharacterForm.gender_options),
        human (bool), plural (bool), protagonist (bool), aliases (list[str]).

    A whole picture book's story text is only ~1-2K tokens, so this now sends
    ALL of the page texts in ONE Claude call that both extracts the character
    references and consolidates them across pages (so e.g. "the boy", "Tom" and
    "Tommy" collapse into a single character with the others as aliases),
    replacing the previous N-per-page + 1-consolidation call pattern (audit item
    5). The reviewed output shape is unchanged. Nothing is written to the
    database — the caller presents the result for the user to review, correct and
    confirm.
    """
    from text_content import AIPrompts

    pages = list(pages)
    # Single call over the whole book — report a coarse 0 -> 1 so existing
    # progress UIs still update.
    if progress_callback is not None:
        progress_callback(0, 1)

    pages_json = json.dumps(
        [{"page": page_number, "text": page_text}
         for page_number, page_text in pages],
        ensure_ascii=False,
    )
    result = _claude_json(
        client,
        AIPrompts.character_detection.format(pages_json=pages_json),
        max_tokens=2048,
        model=get_ai_settings()['character_detection_model'],
    )
    raw_characters = result.get("characters", []) if isinstance(result, dict) else []

    valid_genders = ["Female", "Male", "Non-specific", "Transgender"]
    suggestions = []
    for character in raw_characters:
        if not isinstance(character, dict):
            continue
        name = str(character.get("name", "")).strip()
        if not name:
            continue
        gender = character.get("gender", "Non-specific")
        if gender not in valid_genders:
            gender = "Non-specific"
        seen = set()
        aliases = []
        for alias in character.get("aliases", []) or []:
            alias = str(alias).strip()
            if alias and alias.lower() != name.lower() and alias.lower() not in seen:
                seen.add(alias.lower())
                aliases.append(alias)
        suggestions.append({
            "name": name,
            "gender": gender,
            "human": bool(character.get("human", True)),
            "plural": bool(character.get("plural", False)),
            "protagonist": bool(character.get("protagonist", False)),
            "aliases": aliases,
        })

    if progress_callback is not None:
        progress_callback(1, 1)
    return suggestions


def lookup_isbn(isbn):
    """
    Look up book metadata via the Google Books API (free, no auth required).
    Returns dict with keys title, authors, publisher, published_date,
    or None on any failure.
    """
    if not isbn:
        return None
    url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}&maxResults=1"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get('totalItems', 0) == 0:
            return None
        info = data['items'][0]['volumeInfo']
        return {
            'title': info.get('title', ''),
            'authors': info.get('authors', []),
            'publisher': info.get('publisher', ''),
            'published_date': info.get('publishedDate', ''),
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
            ValueError, KeyError, IndexError) as exc:
        # Narrowed from a broad ``except`` (#127): a network/timeout failure or an
        # unexpected response shape degrades to "no metadata", but is logged.
        logger.warning("lookup_isbn failed for %r: %s", isbn, exc)
        return None


def extract_book_metadata(image_bytes, client):
    """Extract bibliographic metadata from a title-page image using Claude vision.

    Sends the image to Claude using the same model/integration as
    ``extract_page_info`` and ``lookup_person_details`` and parses the JSON reply.
    Returns a dict with keys:
      - 'title'          (str)
      - 'authors'        (list[str])
      - 'illustrators'   (list[str])
      - 'publisher'      (str or None)
      - 'published_year' (int or None)
      - 'raw'            (the raw model response text, kept for audit/debugging)

    The raw response is always included whenever a reply is received, so the caller
    can store it even when individual fields cannot be parsed. Anthropic API errors
    are deliberately allowed to propagate so the caller can surface them to the user
    (per ``book_edit_home.py``'s pattern); only response-parsing problems are handled
    here, by returning empty fields alongside the raw text.
    """
    from text_content import AIPrompts

    ai_settings = get_ai_settings()
    result, raw_text = vision_json(
        client, [image_bytes], AIPrompts.book_metadata_extraction, max_tokens=1024,
        model=ai_settings['metadata_model'], max_edge=ai_settings['extraction_max_edge'],
    )
    empty = {
        'title': "",
        'authors': [],
        'illustrators': [],
        'publisher': None,
        'published_year': None,
        'raw': raw_text,
    }
    if not isinstance(result, dict):
        return empty

    def _as_name_list(value):
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []

    publisher = result.get('publisher')
    if not (isinstance(publisher, str) and publisher.strip()):
        publisher = None
    else:
        publisher = publisher.strip()

    year = result.get('published_year')
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    if year is not None and not (1900 <= year <= datetime.now(timezone.utc).year):
        year = None

    return {
        'title': (result.get('title') or "").strip(),
        'authors': _as_name_list(result.get('authors')),
        'illustrators': _as_name_list(result.get('illustrators')),
        'publisher': publisher,
        'published_year': year,
        'raw': raw_text,
    }


def extract_books_from_photos(images, client):
    """Extract the visible book titles + authors from one or more photos (#75).

    ``images`` is a list of raw image byte strings (one per uploaded photo); all
    of them are sent to Claude vision in a single request — using the same model
    and JSON-fence-stripping convention as ``extract_book_metadata`` — so books
    spread across several photos are read together and de-duplicated by the model.

    Returns a list of ``{'title': str, 'author': str}`` dicts (``author`` may be
    an empty string). Anthropic API errors are deliberately allowed to propagate
    so the caller can surface them (per ``book_edit_home.py``'s pattern); only
    response-parsing problems are handled here, by returning an empty list.
    """
    from text_content import AIPrompts

    ai_settings = get_ai_settings()
    result, _raw = vision_json(
        client, images, AIPrompts.collection_books_extraction, max_tokens=2048,
        model=ai_settings['metadata_model'], max_edge=ai_settings['extraction_max_edge'],
    )

    raw_books = result.get('books', []) if isinstance(result, dict) else []
    books = []
    for entry in raw_books:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get('title') or "").strip()
        author = str(entry.get('author') or "").strip()
        if title:
            books.append({'title': title, 'author': author})
    return books


def locate_key_pages(pages, client):
    """Locate the title-page and copyright-page positions in a set of book photos.

    Pass 1 of the photo-first two-pass flow (#109): a single cheap Claude Haiku
    call is sent ALL page images at once and asked which page is the title page
    and which is the copyright / imprint page (the latter's position varies —
    sometimes just after the title page, sometimes at the back of the book).

    Args:
        pages: ordered list of (name, image_bytes) tuples.
        client: an anthropic.Anthropic client.

    Returns a dict {'title_page': int|None, 'copyright_page': int|None} whose
    values are 1-based positions into ``pages`` (matching the "Page N" labels sent
    to the model), or None when a page could not be identified or the reply could
    not be parsed. Anthropic API errors propagate to the caller.
    """
    from text_content import AIPrompts
    from image_processing import downscale_for_vision

    ai_settings = get_ai_settings()
    locate_model = ai_settings['locate_model']
    locate_max_edge = ai_settings['locate_max_edge']

    pages = list(pages)
    content = []
    for index, (_name, image_bytes) in enumerate(pages):
        content.append({"type": "text", "text": f"Page {index + 1}:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                # Page-type classification does not need OCR resolution — send a
                # smaller image so a whole-book multi-image request stays cheap
                # (#135 cost right-sizing).
                "data": base64.standard_b64encode(
                    downscale_for_vision(image_bytes, max_edge=locate_max_edge)
                ).decode('utf-8'),
            },
        })
    content.append({"type": "text", "text": AIPrompts.locate_key_pages})

    response = client.messages.create(
        model=locate_model,
        max_tokens=128,
        messages=[{"role": "user", "content": content}],
    )
    _log_usage(response, locate_model, "locate_key_pages")

    none_result = {'title_page': None, 'copyright_page': None}
    raw = first_text_block(response)
    if raw is None:
        return none_result
    raw = raw.strip()
    try:
        result = json.loads(strip_json_fence(raw))
    except (json.JSONDecodeError, ValueError):
        return none_result

    def _as_page(value):
        try:
            page = int(value)
        except (TypeError, ValueError):
            return None
        return page if 1 <= page <= len(pages) else None

    return {
        'title_page': _as_page(result.get('title_page')),
        'copyright_page': _as_page(result.get('copyright_page')),
    }


def extract_copyright_metadata(image_bytes, client):
    """Extract publisher, first-published year and ISBN from a copyright-page image.

    Pass 2 (copyright page) of the photo-first two-pass flow (#109): Claude Sonnet
    reads the single located copyright / imprint page for the details that the
    title page usually omits. Returns a dict with keys:
      - 'publisher'      (str or None)
      - 'published_year' (int or None)
      - 'isbn'           (str or None — normalised digits via extract_isbn)
      - 'raw'            (the raw model response text, kept for audit/debugging)

    Mirrors ``extract_book_metadata``: the raw text is always retained, parsing
    problems yield empty fields, and Anthropic API errors propagate to the caller.
    """
    from text_content import AIPrompts

    ai_settings = get_ai_settings()
    result, raw_text = vision_json(
        client, [image_bytes], AIPrompts.copyright_page_extraction, max_tokens=512,
        model=ai_settings['metadata_model'], max_edge=ai_settings['extraction_max_edge'],
    )
    empty = {'publisher': None, 'published_year': None, 'isbn': None, 'raw': raw_text}
    if not isinstance(result, dict):
        return empty

    publisher = result.get('publisher')
    if not (isinstance(publisher, str) and publisher.strip()):
        publisher = None
    else:
        publisher = publisher.strip()

    year = result.get('first_published_year')
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    if year is not None and not (1900 <= year <= datetime.now(timezone.utc).year):
        year = None

    # Reuse the upload pipeline's ISBN parser so hyphenation and stray characters
    # are normalised identically (and ISBN-10/13 both validated).
    raw_isbn = result.get('isbn')
    isbn = extract_isbn(str(raw_isbn)) if raw_isbn else None

    return {'publisher': publisher, 'published_year': year, 'isbn': isbn, 'raw': raw_text}


def extract_photo_first_metadata(pages, client, title_page_hint=None,
                                 progress_callback=None):
    """Two-pass, cost-aware metadata extraction for the photo-first flow
    (#109; completes #63 and makes the #103 form pre-fill reachable).

    Pass 1 (locate): one cheap Claude Haiku call over ALL page images finds the
    title-page and copyright/imprint-page positions (``locate_key_pages``).
    Pass 2 (extract): Claude Sonnet reads ONLY those one or two pages — the title
    page via ``extract_book_metadata`` and the copyright page via
    ``extract_copyright_metadata`` — and any ISBN found is fed into the Google
    Books lookup (``lookup_isbn``), the most reliable metadata source.

    Cost profile: one Haiku call-set + up to two Sonnet calls per book.

    Args:
        pages: ordered list of (name, image_bytes) tuples.
        client: an anthropic.Anthropic client.
        title_page_hint: optional 1-based position the user designated as the
            title page; used in preference to the located title page.
        progress_callback: optional callable(done, total) for UI progress.

    Returns the merged title-page metadata dict (``title``, ``authors``,
    ``illustrators``, ``publisher``, ``published_year``, ``raw``) — with
    publisher / year / authors back-filled from the copyright page and Google
    Books where the title page was silent — plus:
      - 'isbn'          (str or None)
      - 'isbn_metadata' (Google Books dict or None) for the Add-Book form pre-fill
      - 'located'       (the locate-pass result dict)

    Anthropic API errors propagate to the caller (mirrors ``extract_book_metadata``).
    """
    pages = list(pages)
    total_steps = 3
    done = 0

    def _step():
        nonlocal done
        done += 1
        if progress_callback is not None:
            progress_callback(min(done, total_steps), total_steps)

    # Pass 1 — locate the two pages of interest.
    located = locate_key_pages(pages, client)
    _step()

    title_pos = title_page_hint or located.get('title_page')
    copyright_pos = located.get('copyright_page')

    def _bytes_at(position):
        if isinstance(position, int) and 1 <= position <= len(pages):
            return pages[position - 1][1]
        return None

    # Pass 2a — title page (fall back to the first photo if nothing was located).
    title_bytes = _bytes_at(title_pos)
    if title_bytes is None and pages:
        title_bytes = pages[0][1]
    metadata = extract_book_metadata(title_bytes, client)
    _step()

    # Pass 2b — copyright page, only when a distinct one was located.
    copyright_meta = {'publisher': None, 'published_year': None, 'isbn': None, 'raw': None}
    copyright_bytes = _bytes_at(copyright_pos)
    if copyright_bytes is not None and copyright_pos != title_pos:
        copyright_meta = extract_copyright_metadata(copyright_bytes, client)
    _step()

    # Back-fill publisher / year from the copyright page where the title page was
    # silent (these usually live on the copyright page, not the title page).
    if not metadata.get('publisher') and copyright_meta.get('publisher'):
        metadata['publisher'] = copyright_meta['publisher']
    if metadata.get('published_year') is None and copyright_meta.get('published_year') is not None:
        metadata['published_year'] = copyright_meta['published_year']

    # ISBN → Google Books (most reliable source). Use the copyright-page ISBN.
    isbn = copyright_meta.get('isbn')
    isbn_metadata = lookup_isbn(isbn) if isbn else None

    # Google Books back-fills only where vision was silent. Vision stays primary
    # for the printed title and for illustrators (which the API rarely returns).
    if isbn_metadata:
        if not metadata.get('title') and isbn_metadata.get('title'):
            metadata['title'] = isbn_metadata['title']
        if not metadata.get('authors') and isbn_metadata.get('authors'):
            metadata['authors'] = list(isbn_metadata['authors'])
        if not metadata.get('publisher') and isbn_metadata.get('publisher'):
            metadata['publisher'] = isbn_metadata['publisher']

    metadata['isbn'] = isbn
    metadata['isbn_metadata'] = isbn_metadata
    metadata['located'] = located
    return metadata


def locate_cover_pages(pages, client):
    """Stage-2 fallback for batch splitting (#84): find the cover/title page that
    starts each book in a sequential multi-book photo batch.

    Used only when no black separator frames were detected (see
    ``split_photo_batch``). A single cheap Claude Haiku call is sent ALL page
    images and asked which page numbers begin a new book (front cover / title
    page). Returns a sorted list of distinct 1-based positions into ``pages`` (an
    empty list when none could be identified or the reply could not be parsed).
    Anthropic API errors propagate to the caller.
    """
    from text_content import AIPrompts
    from image_processing import downscale_for_vision

    ai_settings = get_ai_settings()
    locate_model = ai_settings['locate_model']
    locate_max_edge = ai_settings['locate_max_edge']

    pages = list(pages)
    if not pages:
        return []

    content = []
    for index, (_name, image_bytes) in enumerate(pages):
        content.append({"type": "text", "text": f"Page {index + 1}:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                # Cover/title classification does not need OCR resolution (#135).
                "data": base64.standard_b64encode(
                    downscale_for_vision(image_bytes, max_edge=locate_max_edge)
                ).decode('utf-8'),
            },
        })
    content.append({"type": "text", "text": AIPrompts.locate_cover_pages})

    response = client.messages.create(
        model=locate_model,
        max_tokens=256,
        messages=[{"role": "user", "content": content}],
    )
    _log_usage(response, locate_model, "locate_cover_pages")

    raw = first_text_block(response)
    if raw is None:
        return []
    raw = raw.strip()
    try:
        result = json.loads(strip_json_fence(raw))
    except (json.JSONDecodeError, ValueError):
        return []

    covers = result.get('cover_pages') if isinstance(result, dict) else None
    if not isinstance(covers, list):
        return []
    seen = set()
    positions = []
    for value in covers:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= page <= len(pages) and page not in seen:
            seen.add(page)
            positions.append(page)
    return sorted(positions)


def split_photo_batch(pages, client, black_threshold=10.0):
    """Split one sequential photo batch covering MULTIPLE books into per-book
    groups (#84). Assumes the photos are in capture order.

    Two-stage algorithm (everything assumes sequential photo order):
      Stage 1 (primary): black separator frames. Between books the user covers
        the lens and takes a fully black photo; ``image_processing.is_black_frame``
        flags those. Each black frame is a book boundary and is DISCARDED (never
        stored as a page). If ANY black separators are found, the batch is split
        on them and Stage 2 is not run.
      Stage 2 (fallback): when NO black separators are present, a single Claude
        Haiku pass (``locate_cover_pages``) finds the cover/title page that starts
        each book, and the batch is split immediately before each detected cover.

    If neither stage yields more than one book the whole batch is treated as a
    single book.

    Args:
        pages: ordered list of (name, image_bytes) tuples.
        client: an ``anthropic.Anthropic`` client, or None. Used only by the
            Stage-2 fallback; when None (no API key) Stage 1 still runs and the
            batch falls back to a single book if no separators are found.
        black_threshold: mean-brightness threshold passed to ``is_black_frame``.

    Returns a dict::

        {'groups': list[list[(name, bytes)]],   # one inner list per detected book
         'method': 'black_frame' | 'cover_page' | 'single'}

    Empty groups (e.g. two adjacent separators, or a leading separator) are
    dropped.
    """
    from image_processing import is_black_frame

    pages = list(pages)
    if not pages:
        return {'groups': [], 'method': 'single'}

    # Stage 1 — split on black separator frames, discarding the separators.
    groups = []
    current = []
    found_separator = False
    for name, image_bytes in pages:
        if is_black_frame(image_bytes, mean_threshold=black_threshold):
            found_separator = True
            if current:
                groups.append(current)
                current = []
            continue
        current.append((name, image_bytes))
    if current:
        groups.append(current)

    if found_separator:
        groups = [group for group in groups if group]
        return {'groups': groups, 'method': 'black_frame'}

    # Stage 2 — no separators: fall back to cover/title-page detection. Needs the
    # AI client; without it we can't classify covers, so treat as a single book.
    if client is None:
        return {'groups': [pages], 'method': 'single'}

    covers = locate_cover_pages(pages, client)
    boundaries = sorted({c for c in covers if 1 <= c <= len(pages)})
    # The first group always starts at page 1, even if the first detected cover
    # is later in the batch (the leading pages belong to the first book).
    if not boundaries or boundaries[0] != 1:
        boundaries = [1] + boundaries
    # A single boundary means one book — nothing meaningful was split.
    if len(boundaries) <= 1:
        return {'groups': [pages], 'method': 'single'}

    groups = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] - 1 if i + 1 < len(boundaries) else len(pages)
        group = pages[start - 1:end]
        if group:
            groups.append(group)
    return {'groups': groups, 'method': 'cover_page'}


def fuzzy_match_name(name, options, cutoff=0.8):
    """Return the closest matching key in ``options`` for ``name``, or None.

    Case-insensitive fuzzy match using the standard-library ``difflib``. ``cutoff``
    is the minimum similarity ratio (0-1). Used to reconcile names extracted from a
    title page against the existing author/illustrator/publisher session lookup
    dicts before creating a new record.
    """
    if not name or not options:
        return None
    name_l = name.strip().lower()
    lower_to_original = {}
    for opt in options:
        lower_to_original.setdefault(opt.lower(), opt)
    matches = difflib.get_close_matches(
        name_l, list(lower_to_original.keys()), n=1, cutoff=cutoff
    )
    if not matches:
        return None
    return lower_to_original[matches[0]]


def split_name(full_name):
    """Split a full name into ``(forename, surname)``.

    The final whitespace-separated token is treated as the surname and the rest as
    the forename(s). A single-token name is returned as the forename with an empty
    surname. Used to seed the new-author/illustrator sub-forms from an extracted
    name that did not fuzzy-match an existing record.
    """
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


class FirestoreWrapper:
    """
    Wrapper class to handle interacting with
    Firestore database (searching, querying, entering new data).
    """

    def __init__(self, auth=True):
        self.auth = auth
        self.firestore_key = json.loads(st.secrets["firestore_key"])

    def _connect(self, auth=None):
        auth = self.auth if auth is None else auth
        if is_authenticated() or not auth:
            creds = service_account.Credentials.from_service_account_info(self.firestore_key)
            return firestore.Client(credentials=creds, project="sawdataentry")
        else:
            return None

    # connect_book and connect_user are kept as separate methods in anticipation
    # of issue #48, which will split the single Firestore database into two:
    # one for book/content data and one for user credentials. When that work is
    # done, each method will connect to its own named database. For now both
    # route to the same default database.
    def connect_book(self, auth=None):
        return self._connect(auth)

    def connect_user(self, auth=None):
        return self._connect(auth)

    def single_field_search(self, collection, field, contains_string):
        """ Search for string withing field. """
        db = self.connect_book()

        results = (
            db.collection(collection)
                .where(filter=FieldFilter(field, ">=", contains_string))
                .where(filter=FieldFilter(field, "<=", contains_string + 'z'))
                .stream()
        )

        results_dict = list(map(lambda x: x.to_dict(), results))
        return pd.DataFrame(results_dict)

    def get_by_field(self, collection, field, match):
        """ Get exact match in field"""
        db = self.connect_book()
        results = db.collection(collection).where(
            filter=FieldFilter(field, "==", match)
        ).stream()
        # return doc_ref.get()

        results_dict = list(map(lambda x: x.to_dict(), results))
        return pd.DataFrame(results_dict)

    def get_by_reference(self, collection, document_ref):
        db = self.connect_book()
        doc_ref = db.collection(collection).document(document_ref)
        return doc_ref.get()

    def get_all_documents_stream(self, collection):
        db = self.connect_book()
        return db.collection(collection).stream()

    def query_stream(self, collection, field, op, value):
        """Stream documents from ``collection`` matching a single field filter.

        Unlike ``get_by_field`` (which returns a DataFrame of values), this
        yields the raw document snapshots so callers can access ``.id`` and
        ``.reference`` — needed for deletion and reference look-ups.
        """
        db = self.connect_book()
        return (
            db.collection(collection)
            .where(filter=FieldFilter(field, op, value))
            .stream()
        )

    def delete_document(self, collection, doc_id):
        db = self.connect_book()
        db.collection(collection).document(doc_id).delete()

    def username_to_doc_ref(self, username):
        return self.connect_user().collection('users').document(username)

    def document_exists(self, collection, doc_id):
        db = self.connect_book()
        doc = db.collection(collection).document(doc_id).get()
        return doc.exists

    def update_field(self, collection, document, field, value):
        db = self.connect_book()
        doc_ref = db.collection(collection).document(document)
        doc_ref.update({field: value})

    def set_document(self, collection, doc_id, data, merge=True):
        """Write ``data`` to ``collection/doc_id`` at a fixed document id.

        Used for singleton config records (e.g. the ``settings/ai_pipeline``
        AI-parameters doc) that are handled as a raw dict rather than through the
        ``DataStructureBase``/``Field`` write-through pattern. ``merge=True``
        updates the given keys without dropping unrelated ones.
        """
        db = self.connect_book()
        db.collection(collection).document(doc_id).set(data, merge=merge)

    def add_document(self, collection, data):
        """Append a new document with a Firestore-generated id.

        Unlike ``save_to_db``/``set`` (which write to a deterministic,
        content-derived ``document_id``), this uses Firestore's ``add()`` to
        create an auto-id document. It is used for append-only records that have
        no natural key — currently the ``edit_log`` audit collection (issue #47,
        Part B). Returns the new ``DocumentReference``.
        """
        db = self.connect_book()
        _timestamp, doc_ref = db.collection(collection).add(data)
        return doc_ref


# ---------------------------------------------------------------------------
# Cached lookup-dict loaders (issue #53 — reduce Firestore read traffic).
#
# Previously Home.initialise() streamed the whole of every lookup collection
# (authors, publishers, illustrators, books, characters) on *every* session
# init. These functions move that work behind Streamlit's cache so the data is
# fetched once and shared across sessions/reruns instead of re-read each load.
#
# Why @st.cache_resource and NOT @st.cache_data:
#   The dict *values* are Firestore ``DocumentReference`` objects bound to a
#   live ``firestore.Client``. ``@st.cache_data`` pickles its return value, and
#   a client-bound DocumentReference is explicitly unpicklable
#   ("Pickling client objects is explicitly not supported"), so cache_data
#   would raise at runtime. ``@st.cache_resource`` stores the object by
#   reference without serialising, which both works and keeps the underlying
#   client alive for as long as the refs are cached. The cached dict is
#   shallow-copied into session_state by the caller so in-session mutations
#   (adding a freshly registered author/book/etc.) never poison the shared
#   cache.
#
# FRESHNESS / INVALIDATION — IMPORTANT:
#   The TTL is a safety net only. Whenever a write *adds* an entry to one of
#   these collections (the FormConfirmation.confirm_new_* methods and
#   Character.register), the caller MUST call the matching ``load_*_dict.clear()``
#   so the next session re-reads from Firestore. The current session continues
#   to see its own newly added entry because the entry is also written into the
#   session_state copy in place (unchanged existing behaviour). This preserves
#   the write-through freshness guarantee while removing the per-session full
#   re-read.
_LOOKUP_CACHE_TTL_SECONDS = 600  # 10 minutes; bounds staleness from external edits.


@st.cache_resource(ttl=_LOOKUP_CACHE_TTL_SECONDS, show_spinner=False)
def load_author_dict():
    firestore_wrapper = FirestoreWrapper(auth=False)
    return {
        author_entry_to_name(author): author.reference
        for author in firestore_wrapper.get_all_documents_stream(collection='authors')
    }


@st.cache_resource(ttl=_LOOKUP_CACHE_TTL_SECONDS, show_spinner=False)
def load_publisher_dict():
    firestore_wrapper = FirestoreWrapper(auth=False)
    return {
        publisher.to_dict()['name'].replace('_', ' '): publisher.reference
        for publisher in firestore_wrapper.get_all_documents_stream(collection='publishers')
    }


@st.cache_resource(ttl=_LOOKUP_CACHE_TTL_SECONDS, show_spinner=False)
def load_illustrator_dict():
    firestore_wrapper = FirestoreWrapper(auth=False)
    return {
        author_entry_to_name(illustrator): illustrator.reference
        for illustrator in firestore_wrapper.get_all_documents_stream(collection='illustrators')
    }


@st.cache_resource(ttl=_LOOKUP_CACHE_TTL_SECONDS, show_spinner=False)
def load_book_dict():
    firestore_wrapper = FirestoreWrapper(auth=False)
    return {
        book.to_dict()['title']: book.reference
        for book in firestore_wrapper.get_all_documents_stream(collection='books')
    }


@st.cache_resource(ttl=_LOOKUP_CACHE_TTL_SECONDS, show_spinner=False)
def load_character_dict():
    firestore_wrapper = FirestoreWrapper(auth=False)
    return {
        character.to_dict()['name']: character.reference
        for character in firestore_wrapper.get_all_documents_stream(collection='characters')
    }


# TODO: check that required fields (e.g. book title) are not blank
# TODO: fix warnings in table display (arrows?)
class FormConfirmation:
    """
    Class with helper methods to handle form confirmation and routing
    based on form type.
    """

    forms = {
        'new_book': 'confirm_new_book',
        'new_author': 'confirm_new_author',
        'new_illustrator': 'confirm_new_illustrator',
        'new_publisher': 'confirm_new_publisher',
        'new_character': 'confirm_new_character'
    }

    @classmethod
    def display_confirmation(cls, data):

        # Compact, borderless key/value summary in a constrained-width column,
        # rather than a full-width bordered table.
        summary_col, _ = st.columns([2, 1])
        for field, value in data.items():
            label = field.replace('_', ' ').capitalize()
            display_value = "" if value is None else value
            summary_col.markdown(f"**{label}:** {display_value}")
        col1, col2 = st.columns(2)
        confirm_button = col1.button("Confirm", key="confirm_display_confirm_button")
        edit_button = col2.button("Edit", key="confirm_display_edit_button")

        return confirm_button, edit_button

    @classmethod
    def confirm_new_book(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_book'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            if st.session_state['current_book'].author is None:
                navigate_to("./pages/add_author.py")

            else:
                st.session_state['current_book'].register()
                st.session_state['book_dict'][
                    st.session_state['current_book'].title
                ] = st.session_state['current_book'].get_ref()
                # Invalidate the shared cache so other/new sessions re-read the
                # newly registered book (this session already sees it via the
                # in-place session_state update above).
                load_book_dict.clear()
                st.session_state.pop('isbn_metadata', None)

                if st.session_state.current_book.photos_uploaded:
                    navigate_to("./pages/enter_text.py")
                else:
                    navigate_to("./pages/page_photo_upload.py")

        if edit_button:
            st.switch_page("./pages/add_book.py")

    @classmethod
    def confirm_new_author(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_author'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            st.session_state['current_author'].register()
            st.session_state['author_dict'][
                st.session_state['current_author'].name
            ] = st.session_state['current_author'].get_ref()
            # Invalidate shared cache so new/other sessions re-read this author.
            load_author_dict.clear()

            st.session_state['current_book'].author = (
                st.session_state['current_author'].name
            )
            st.switch_page("./pages/add_book.py")

        if edit_button:
            st.switch_page("./pages/add_author.py")

    @classmethod
    def confirm_new_illustrator(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_illustrator'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            st.session_state['current_illustrator'].register()
            st.session_state['illustrator_dict'][
                st.session_state['current_illustrator'].name
            ] = st.session_state['current_illustrator'].get_ref()
            # Invalidate shared cache so new/other sessions re-read this illustrator.
            load_illustrator_dict.clear()

            st.session_state['current_book'].illustrator = (
                st.session_state['current_illustrator'].name
            )
            st.switch_page("./pages/add_book.py")

        if edit_button:
            st.switch_page("./pages/add_illustrator.py")

    @classmethod
    def confirm_new_publisher(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_publisher'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            st.session_state['current_publisher'].register()
            st.session_state['publisher_dict'][
                st.session_state['current_publisher'].name
            ] = st.session_state['current_publisher'].get_ref()
            # Invalidate shared cache so new/other sessions re-read this publisher.
            load_publisher_dict.clear()

            st.session_state['current_book'].publisher = (
                st.session_state['current_publisher'].name
            )
            st.switch_page("./pages/add_book.py")

        if edit_button:
            st.switch_page("./pages/add_publisher.py")

    @classmethod
    def confirm_new_character(cls):
        confirm_button, edit_button = cls.display_confirmation('character_details')

        if confirm_button:
            navigate_to("./pages/book_data_entry.py")

        if edit_button:
            st.switch_page("./pages/add_character.py")


@st.dialog("Are you sure?")
def confirm_submit():
    st.write(
        """
        Are you sure you want to submit this book? You will not be able to edit it again after submission,
        so please only submit once you are confident that everything is correct and complete.
        """
    )
    if st.button("Confirm", key="confirm_submit_confirm_button"):
        st.session_state.current_book.entry_status = 'completed'
        st.session_state.current_book.datetime_submitted = datetime.now(timezone.utc)
        clear_page_history()
        st.switch_page("./pages/user_home.py")
    if st.button("Cancel", key="confirm_submit_cancel_button"):
        st.rerun()
