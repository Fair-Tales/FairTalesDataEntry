"""App-owned lexical rules shared with evaluation; no service or harness imports.

These rules also determine stored alias-canonical-v1 values. Changes require an
explicit canonical-rule version decision, not just a scorer version bump.
"""
from __future__ import annotations

import re
import unicodedata

NORMALIZATION_VERSION = "pilot-normalization-v1"
UNCERTAINTY = "[?]"
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "＇": "'"})
_UNCERTAIN_RE = re.compile(r"\[\s*(?:\?+|illegible|unreadable|unclear)\s*\]", re.I)
_TOKEN_RE = re.compile(r"\[\?\]|[^\W_]+(?:'[^\W_]+)*", re.UNICODE)
_ARTICLE_RE = re.compile(r"^(?:the|an|a|my|your|his|her|its|our|their)\s+", re.I)


def normalize_text(value: str | None) -> str:
    """NFKC, case-fold, canonical apostrophes, collapsed whitespace.

    Diacritics and numbers are retained. Punctuation is removed only by the
    tokenizer; ordered CER retains it. Explicit uncertainty is never discarded.
    """
    text = unicodedata.normalize("NFKC", value or "").translate(_APOSTROPHES)
    return " ".join(_UNCERTAIN_RE.sub(UNCERTAINTY, text).casefold().split())


def tokenize(value: str | None) -> list[str]:
    """Punctuation-insensitive Unicode words, retaining internal apostrophes."""
    return _TOKEN_RE.findall(normalize_text(value))


def normalize_name(value: str | None, *, strip_article: bool = False) -> str:
    """Strict normalized word sequence, optionally applying the app's one-article rule.

    The article rule mirrors utilities.strip_leading_article symmetrically while
    keeping offline metric imports independent from Streamlit and cloud clients.
    """
    text = normalize_text(value)
    if strip_article:
        text = _ARTICLE_RE.sub("", text, count=1) or text
    return " ".join(tokenize(text))
