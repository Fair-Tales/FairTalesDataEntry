"""Versioned, deterministic normalization; no application or network imports."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

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


def token_counts(value: str | None) -> Counter:
    return Counter(tokenize(value))


def normalize_name(value: str | None, *, strip_article: bool = False) -> str:
    """Strict normalized word sequence, optionally applying the app's one-article rule.

    The article rule mirrors utilities.strip_leading_article symmetrically while
    keeping offline metric imports independent from Streamlit and cloud clients.
    """
    text = normalize_text(value)
    if strip_article:
        text = _ARTICLE_RE.sub("", text, count=1) or text
    return " ".join(tokenize(text))


def edit_distance(reference, prediction) -> int:
    """Levenshtein distance on strings or token sequences, in linear memory."""
    if len(reference) < len(prediction):
        reference, prediction = prediction, reference
    previous = list(range(len(prediction) + 1))
    for i, expected in enumerate(reference, 1):
        current = [i]
        for j, actual in enumerate(prediction, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (expected != actual)))
        previous = current
    return previous[-1]


def character_similarity(reference: str, prediction: str) -> float:
    denominator = max(len(reference), len(prediction))
    return 1 - edit_distance(reference, prediction) / denominator if denominator else 1.0
