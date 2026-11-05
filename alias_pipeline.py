"""Lossless alias indexing and page-local lexical candidate retrieval.

This module has no services or application imports. Candidates are dictionary
evidence, never resolved identities. In particular a unique candidate is not an
attribution verdict. Grounding's permissive possessive rule lives elsewhere.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher

from scripts.extraction_eval.normalization import tokenize

ALIAS_SCHEMA_VERSION = "alias-mentions-v2"
MATCHER_VERSION = "article-only-overlap-components-v1"
CONVENTION_VERSION = "pilot-conventions-2026-11-05-v1"
ARTICLES = frozenset({"a", "an", "the"})
INACTIVE_STATUSES = frozenset({"rejected", "superseded", "withdrawn"})
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "＇": "'"})
_TOKENS = re.compile(r"\[\?\]|[^\W_]+(?:'[^\W_]+)*", re.UNICODE)
_UNCERTAIN = re.compile(r"\[\s*(?:\?+|illegible|unreadable|unclear)\s*\]", re.I)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def mention_pattern_tokens(surface, *, optional_articles=True):
    """Remove at most one *article*, preserving every owner and modifier.

    This is deliberately independent of M1's permissive grounding normalizer.
    An article alone must never produce an empty wildcard.
    """
    tokens = tuple(tokenize(surface if isinstance(surface, str) else ""))
    if optional_articles and tokens and tokens[0] in ARTICLES:
        return tokens[1:]
    return tokens


def tokens_with_raw_offsets(text):
    """Return normalized tokens with reversible half-open source intervals.

    Normalize Unicode combining clusters as units: NFKC may combine several
    source characters and casefold may expand one (e.g. ß). Both keep the full
    original interval. Token offsets always address the original Python string.
    """
    clusters = []
    for i, char in enumerate(text):
        # Hangul Jamo compose across codepoints despite combining class zero.
        jamo = 0x1100 <= ord(char) <= 0x11ff
        if clusters and (unicodedata.combining(char) or
                         (jamo and 0x1100 <= ord(text[i - 1]) <= 0x11ff)):
            clusters[-1][1] = i + 1
        else:
            clusters.append([i, i + 1])
    chars, offsets = [], []
    for start, end in clusters:
        normalized = unicodedata.normalize("NFKC", text[start:end]).translate(
            _APOSTROPHES).casefold()
        chars.extend(normalized)
        offsets.extend([(start, end)] * len(normalized))
    normalized = "".join(chars)
    complete = unicodedata.normalize("NFKC", text).translate(_APOSTROPHES).casefold()
    if normalized != complete:
        # Some scripts compose across zero-combining-class boundaries (e.g.
        # Bengali vowel signs). Align only in that uncommon case so the public
        # tokenizer is exactly whole-string NFKC, with source intervals intact.
        aligned = []
        for kind, left, right, target_left, target_right in SequenceMatcher(
                None, normalized, complete, autojunk=False).get_opcodes():
            if kind == "equal":
                aligned.extend(offsets[left:right])
            elif target_right > target_left:
                source = offsets[left:right] or offsets[max(0, left - 1):left + 1]
                span = (min(item[0] for item in source), max(item[1] for item in source))
                aligned.extend([span] * (target_right - target_left))
        normalized, offsets = complete, aligned
    # Preserve the uncertainty marker exactly as the frozen tokenizer does.
    parts, mapped, previous = [], [], 0
    for match in _UNCERTAIN.finditer(normalized):
        parts.extend(normalized[previous:match.start()])
        mapped.extend(offsets[previous:match.start()])
        parts.extend("[?]")
        span = (offsets[match.start()][0], offsets[match.end() - 1][1])
        mapped.extend([span] * 3)
        previous = match.end()
    parts.extend(normalized[previous:])
    mapped.extend(offsets[previous:])
    normalized = "".join(parts)
    return [{"token": match.group(), "start": mapped[match.start()][0],
             "end": mapped[match.end() - 1][1]}
            for match in _TOKENS.finditer(normalized)]


def _reference_id(value):
    if isinstance(value, str):
        return value
    return getattr(value, "path", None) or getattr(value, "id", None)


def build_alias_index(characters, *, book_id="", diagnostic=False):
    """Build identity -> archived entries -> compiled patterns without deletion.

    Legacy array positions seed deterministic book-scoped candidate IDs when no
    stored identity exists. Canonical overrides never become lexical patterns.
    Operational expansion respects article_policy=preserve; unknown eligibility
    is retained as a constraint, so even one dictionary candidate stays unresolved.
    The frozen broad diagnostic intentionally precedes those eligibility checks.
    """
    from alias_schema import effective_canonical

    identities, entries, compiled = [], [], {}
    for index, character in enumerate(characters):
        entity_id = (character.get("entity_id") or
                     _reference_id(character.get("reference")) or
                     character.get("id") or f"{book_id}:character:{index}")
        identity = {"entity_id": entity_id, "book_id": book_id,
                    "name": character.get("name", ""),
                    "entity_kind": character.get("entity_kind", "unclassified"),
                    "reference": _reference_id(character.get("reference")),
                    "identity_evidence": character.get("identity_evidence", []),
                    "mentions": character.get("mentions", []),
                    "editorial_labels": character.get("editorial_labels", []),
                    "unresolved_mentions": character.get("unresolved_mentions", []),
                    "member_of": character.get("member_of", []),
                    "former_ids": character.get("former_ids", []),
                    "unresolved_identity_alternatives": character.get(
                        "unresolved_identity_alternatives", [])}
        identities.append(identity)
        surfaces = []
        if character.get("name"):
            surfaces.append({"name": character["name"], "source": "character_name",
                             "entry_status": ("editorial_label" if character.get(
                                 "name_is_label") else "unverified"),
                             "article_policy": character.get("article_policy", "unknown")})
        for prior_name in character.get("name_history", []) or []:
            if isinstance(prior_name, str):
                surfaces.append({"name": prior_name, "source": "recorded_character_name",
                                 "entry_status": "unverified"})
        for alias in character.get("aliases", []) or []:
            if isinstance(alias, str):
                surfaces.append({"name": alias, "source": "recorded_alias"})
            elif isinstance(alias, dict):
                surfaces.append({**alias, "name": alias.get(
                    "name", alias.get("surface", alias.get("raw_surface", "")))})
        for ordinal, record in enumerate(surfaces):
            raw = record.get("name", "")
            if not isinstance(raw, str):
                raw = ""
            entry_id = record.get("storage_id") or record.get("id") or (
                f"{entity_id}:entry:{ordinal}:{_digest(raw)[:16]}")
            status = record.get("entry_status") or "unverified"
            entry = {**record, **effective_canonical(record), "recorded_entry": dict(record),
                     "entry_id": entry_id,
                     "name": raw, "entity_id": entity_id, "entry_status": status}
            entries.append(entry)
            if (status in INACTIVE_STATUSES | {"editorial_label"} or
                    character.get("entry_status") in INACTIVE_STATUSES):
                continue
            candidate = (record.get("effective_entity_id") or
                         _reference_id(record.get("effective_character")) or entity_id)
            article_policy = record.get("article_policy", "unknown")
            constraints = {"article_policy": article_policy,
                           "owner_tokens": [token for token in tokenize(raw)
                                            if token.endswith("'s") or token in {
                                                "my", "your", "his", "her", "its", "our", "their"}]}
            # Both exact and broad patterns retain pointers to every raw entry.
            for tier in ("exact", "optional_articles"):
                allow = tier == "optional_articles" and (
                    diagnostic or article_policy != "preserve")
                tokens = mention_pattern_tokens(raw, optional_articles=allow)
                if not tokens or "[?]" in tokens:
                    continue
                key = (tier, tokens, json.dumps(constraints, sort_keys=True), allow)
                pattern = compiled.setdefault(key, {
                    "pattern_id": _digest(key), "tokens": list(tokens), "tier": tier,
                    "optional_articles": allow, "constraints": constraints,
                    "entry_ids": [], "candidate_entity_ids": []})
                if entry_id not in pattern["entry_ids"]:
                    pattern["entry_ids"].append(entry_id)
                if candidate not in pattern["candidate_entity_ids"]:
                    pattern["candidate_entity_ids"].append(candidate)
    return {"schema_version": ALIAS_SCHEMA_VERSION, "matcher_version": MATCHER_VERSION,
            "convention_version": CONVENTION_VERSION, "book_id": book_id,
            "input_hash": _digest(characters), "identities": identities,
            "entries": entries, "patterns": list(compiled.values())}


def match_page(index, text, *, page_id="", tier="optional_articles"):
    """Return raw hits; duplicate source spans are collapsed by occurrence_ledger."""
    mapped = tokens_with_raw_offsets(text)
    words = [token["token"] for token in mapped]
    hits = []
    for pattern in index["patterns"]:
        if pattern["tier"] != tier:
            continue
        tokens = pattern["tokens"]
        size = len(tokens)
        for start in range(len(words) - size + 1):
            if words[start:start + size] != tokens:
                continue
            end = start + size
            left = start - 1 if (pattern["optional_articles"] and start and
                                 words[start - 1] in ARTICLES) else start
            raw_start, raw_end = mapped[left]["start"], mapped[end - 1]["end"]
            hits.append({"page_id": page_id, "token_start": left, "token_end": end,
                         "start": raw_start, "end": raw_end, "surface": text[raw_start:raw_end],
                         "pattern_ids": [pattern["pattern_id"]],
                         "entry_ids": list(pattern["entry_ids"]),
                         "candidate_entity_ids": list(pattern["candidate_entity_ids"]),
                         "tier": tier, "constraints": pattern["constraints"]})
    return hits


def occurrence_ledger(hits, text, *, book_id="", page_id=""):
    """Union strictly overlapping spans transitively, retaining nested evidence.

    Adjacent spans are independent occurrences. Candidate sets are unioned before
    any resolution; a longest-span winner must never hide a nested collision.
    """
    components = []
    for hit in sorted(hits, key=lambda item: (item["token_start"], item["token_end"])):
        if components and hit["token_start"] < components[-1]["token_end"]:
            component = components[-1]
            component["token_end"] = max(component["token_end"], hit["token_end"])
            component["end"] = max(component["end"], hit["end"])
            component["nested_hits"].append(hit)
        else:
            components.append({"book_id": book_id, "page_id": page_id,
                               "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                               "token_start": hit["token_start"], "token_end": hit["token_end"],
                               "start": hit["start"], "end": hit["end"], "nested_hits": [hit]})
    for component in components:
        for field in ("entry_ids", "pattern_ids", "candidate_entity_ids"):
            component[field] = sorted({value for hit in component["nested_hits"] for value in hit[field]})
        component.update(surface=text[component["start"]:component["end"]],
                         status="unresolved", resolved_entity_id=None,
                         resolution_evidence=[], speech_relations=[], coreference_links=[],
                         language_spans=[])
    return components


def dictionary_diagnostics(characters, pages, *, book_id=""):
    """Compare raw optional-article and exact hits on the same fixed universe U.

    U contains the optional-article overlap components, not independently labelled
    mentions. This reproduces the dev20 measurement without declaring accuracy.
    """
    index = build_alias_index(characters, book_id=book_id, diagnostic=True)
    occurrences, exact_occurrences = [], []
    counts = {tier: {"universe": 0, "found": 0, "single_candidate": 0,
                     "ambiguous": 0, "missed": 0}
              for tier in ("optional_articles", "exact")}
    for page in pages:
        text = page.get("text") or ""
        page_id = page.get("page_id", page.get("id", ""))
        broad = match_page(index, text, page_id=page_id)
        exact = match_page(index, text, page_id=page_id, tier="exact")
        universe = occurrence_ledger(broad, text, book_id=book_id, page_id=page_id)
        occurrences.extend(universe)
        for component in universe:
            candidates = {entity for hit in exact
                          if (hit["token_start"] >= component["token_start"] and
                              hit["token_end"] <= component["token_end"])
                          for entity in hit["candidate_entity_ids"]}
            exact_hits = [hit for hit in exact if (
                hit["token_start"] >= component["token_start"] and
                hit["token_end"] <= component["token_end"])]
            exact_occurrences.append({
                **component, "candidate_entity_ids": sorted(candidates), "nested_hits": exact_hits,
                **{field: sorted({value for hit in exact_hits for value in hit[field]})
                   for field in ("entry_ids", "pattern_ids")}})
            for tier, number in (("optional_articles", len(component["candidate_entity_ids"])),
                                 ("exact", len(candidates))):
                counts[tier]["universe"] += 1
                counts[tier]["found"] += int(number > 0)
                counts[tier]["missed"] += int(number == 0)
                counts[tier]["single_candidate"] += int(number == 1)
                counts[tier]["ambiguous"] += int(number > 1)
    return {"schema_version": ALIAS_SCHEMA_VERSION, "matcher_version": MATCHER_VERSION,
            "convention_version": CONVENTION_VERSION, "diagnostic_only": True,
            **counts, "occurrences": occurrences, "exact_occurrences": exact_occurrences}
