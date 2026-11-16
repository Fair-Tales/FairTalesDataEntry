"""Evaluation-only metrics and character rules over app-owned lexical helpers."""

from __future__ import annotations

import re
from collections import Counter

# Keep the harness import path compatible; the application owns these rules.
from text_normalization import (  # noqa: F401
    NORMALIZATION_VERSION, UNCERTAINTY, normalize_name, normalize_text, tokenize,
)

CHARACTER_NORMALIZATION_VERSION = "character-normalization-v1"
_POSSESSOR_TOKEN_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*'?", re.UNICODE)
_POSSESSIVES = {'my', 'your', 'his', 'her', 'its', 'our', 'their', 'whose'}


def token_counts(value: str | None) -> Counter:
    return Counter(tokenize(value))


def normalize_character_name(value: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Ratified character tokens and ordered possessors, before punctuation loss.

    Separate from legacy normalize_name: only a/an/the preceding another token
    may be removed; a final possessive apostrophe survives in the signature.
    """
    text = normalize_text(value)
    possessors = tuple(word for word in _POSSESSOR_TOKEN_RE.findall(text)
                       if word.endswith(("'s", "'")) or word in _POSSESSIVES)
    tokens = tokenize(text)
    if len(tokens) > 1 and tokens[0] in {'a', 'an', 'the'}:
        tokens = tokens[1:]
    return tuple(tokens), possessors


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


def edit_operations(reference, prediction) -> dict[str, int]:
    """Exact Levenshtein counts; ties from the start: match, substitute, delete, insert.

    Myers' bit-vector recurrence packs each DP row's adjacent cost differences
    into two integers. Reversed sequences give suffix costs for forward traceback.
    Python integer operations avoid the quadratic Python cell loop; retained rows
    use two bits per cell (plus integer overhead), not a matrix of Python ints.
    Tokens must be hashable. Normalization is deliberately left to the caller.
    """
    n, m = len(reference), len(prediction)
    counts = dict(substitutions=0, deletions=0, insertions=0)
    if reference == prediction:
        return counts
    if not n or not m:
        return dict(substitutions=0, deletions=n, insertions=m)
    mask = (1 << m) - 1
    matches = {}
    for j, token in enumerate(reversed(prediction)):
        matches[token] = matches.get(token, 0) | (1 << j)
    positive, negative = mask, 0
    rows = [(positive, negative)]
    for token in reversed(reference):
        equal = matches.get(token, 0)
        vertical = equal | negative
        horizontal = (((equal & positive) + positive) ^ positive) | equal
        plus = negative | ~(horizontal | positive)
        minus = positive & horizontal
        plus = (plus << 1) | 1
        minus <<= 1
        positive = (minus | ~(vertical | plus)) & mask
        negative = plus & vertical
        rows.append((positive, negative))

    def cost(i, j):
        positive, negative = rows[n - i]
        prefix = (1 << (m - j)) - 1
        return n - i + (positive & prefix).bit_count() - (negative & prefix).bit_count()

    i = j = 0
    remaining = cost(0, 0)
    while i < n and j < m:
        if reference[i] == prediction[j]:
            i += 1
            j += 1
            continue
        if cost(i + 1, j + 1) == remaining - 1:
            counts['substitutions'] += 1
            i += 1
            j += 1
        elif cost(i + 1, j) == remaining - 1:
            counts['deletions'] += 1
            i += 1
        else:
            counts['insertions'] += 1
            j += 1
        remaining -= 1
    counts['deletions'] += n - i
    counts['insertions'] += m - j
    return counts
