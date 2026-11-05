"""Additive, offline alias diagnostics; never an identity/attribution truth score.

The grounding normalizer deliberately does not call the mention normalizer.
Its historical possessive stripping is permitted only for M1 existence checks.
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import combinations, product

from text_content.alias_metrics import AliasMetrics

from .normalization import UNCERTAINTY, normalize_text, tokenize

FOUNDATION_VERSION = "alias-foundation-metrics-v1"
GROUNDING_VERSION = "page-token-grounding-permissive-determiners-v1"
HISTORICAL_DELETION_VERSION = "historical-punctuation-deletion-determiners-v1"
HISTORICAL_SEPARATOR_VERSION = "historical-punctuation-separation-determiners-v1"
ARTICLE_DIAGNOSTIC_VERSION = "article-only-pattern-overlap-v1"
_GROUNDING_DETERMINERS = frozenset("the a an my your his her its our their".split())
_HISTORICAL_DETERMINER_RE = re.compile(r"^(?:the|a|an|my|your|his|her|its|our|their)\s+")


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def grounding_tokens(surface, *, strip_determiner=False):
    """M1 only: preserve named genitives, optionally remove one determiner."""
    tokens = tokenize(surface if isinstance(surface, str) else "")
    if strip_determiner and tokens and tokens[0] in _GROUNDING_DETERMINERS:
        tokens = tokens[1:]
    return tokens if tokens and UNCERTAINTY not in tokens else []


def _hits(tokens, pages):
    if not tokens:
        return []
    size = len(tokens)
    return [{"page_id": page_id, "token_start": index, "token_end": index + size}
            for page_id, words in pages
            for index in range(len(words) - size + 1)
            if words[index:index + size] == tokens]


def grounding_support(surface, pages):
    """Boundary-safe, page-local full/permissive support; pages are (id, text)."""
    tokenized = [(str(page_id), tokenize(text)) for page_id, text in pages]
    exact = _hits(grounding_tokens(surface), tokenized)
    if exact:
        return {"tier": "exact", "hits": exact}
    permissive = _hits(grounding_tokens(surface, strip_determiner=True), tokenized)
    return {"tier": "determiner_stripped" if permissive else "ungrounded", "hits": permissive}


def _raw_alias(alias):
    if isinstance(alias, str):
        return alias
    if isinstance(alias, dict):
        for key in ("raw_surface", "surface", "name"):
            value = alias.get(key)
            if isinstance(value, str):
                return value
    return None


def _entities(stage):
    values = stage.get("value", []) if isinstance(stage, dict) else stage
    entities = []
    for index, value in enumerate(values if isinstance(values, list) else []):
        value = value if isinstance(value, dict) else {"name": str(value)}
        aliases = value.get("aliases") or []
        aliases = [aliases] if isinstance(aliases, str) else aliases
        aliases = aliases if isinstance(aliases, list) else []
        records = [{**(alias if isinstance(alias, dict) else {}), "name": raw}
                   for alias in aliases if (raw := _raw_alias(alias)) is not None]
        reference = value.get("reference")
        reference_id = reference if isinstance(reference, str) else getattr(reference, "path", None)
        entity_id = str(value.get("entity_id") or reference_id or value.get("id", index))
        entities.append({**value, "id": entity_id, "entity_id": entity_id,
                         "name": value.get("name") if isinstance(value.get("name"), str) else "",
                         "aliases": records, "raw_aliases": [record["name"] for record in records]})
    return entities


def _pages(book, prediction):
    predicted = prediction.get("pages") or {}
    archival = [(str(page_id), page.get("text") or "") for page_id, page in predicted.items()
                if isinstance(page, dict) and page.get("status") == "completed"]
    character_input = [(page_id, text) for page_id, text in archival
                       if (predicted.get(page_id) or {}).get("contains_story") is True]
    reference = []
    for page in book.get("pages", []):
        record = (page.get("reference") or {}).get("text") or {}
        if record.get("status") in {"observed", "verified"} and isinstance(record.get("value"), str):
            reference.append((str(page.get("id")), record["value"]))
    intended = {str(page.get("id")) for page in book.get("pages", [])} | {str(key) for key in predicted}
    completed = {page_id for page_id, _ in archival}
    coverage = "completed" if intended and intended == completed else "failed_or_partial" if intended else "unavailable"
    return archival, character_input, reference, {"intended_pages": len(intended), "completed_pages": len(completed),
                                                "missing_or_failed_pages": len(intended - completed), "stratum": coverage}


def _validated_span(span, archival):
    """Verify exact raw offsets and whole-token boundaries in completed text."""
    if not isinstance(span, dict):
        return False
    text = dict(archival).get(str(span.get("page_id")))
    start, end, surface = span.get("start"), span.get("end"), span.get("surface")
    if (not isinstance(text, str) or type(start) is not int or type(end) is not int
            or not isinstance(surface, str) or not 0 <= start < end <= len(text)
            or text[start:end] != surface or not grounding_tokens(surface)):
        return False
    return tokenize(text[:start]) + tokenize(surface) + tokenize(text[end:]) == tokenize(text)


def validate_editorial_allowance(surface, record, entity_id, archival):
    """Validate typed lexical construction support, never semantic identity truth.

    ``editorial_evidence`` has ``construction_type`` and ``entity_ids``. A
    parenthetical_disambiguator supplies ``head`` and ``modifier`` source spans,
    each with the target entity ID, and ``identity_evidence`` records with
    relation=same_entity, entity_ids and an exact ``source`` span. An elided_list
    supplies ordered ``components`` spans (each with one member entity ID) and
    ``membership_evidence`` records with relation=member_of, member_entity_id,
    group_entity_id and ``source``. A span is {page_id,start,end,surface} with
    optional entity_ids; offsets address completed archival text. Every emitted
    lexical token must be accounted for by the head/modifier or list components.
    Unsupported classes, absent source spans and unverified assertions earn no E.
    """
    evidence = record.get("editorial_evidence") if isinstance(record, dict) else None
    if not isinstance(evidence, dict) or evidence.get("entity_ids") != [entity_id]:
        return {"validated": False, "reason": "missing_typed_entity_evidence"}
    kind = evidence.get("construction_type")
    if kind == "parenthetical_disambiguator":
        parsed = re.fullmatch(r"\s*(.+?)\s*\(([^()]+)\)\s*", surface)
        head, modifier = evidence.get("head"), evidence.get("modifier")
        if not parsed or not all(_validated_span(span, archival) for span in (head, modifier)):
            return {"validated": False, "reason": "invalid_parenthetical_source_spans"}
        if (head.get("entity_ids") != [entity_id] or modifier.get("entity_ids") != [entity_id]
                or grounding_tokens(parsed.group(1)) != grounding_tokens(head.get("surface"))
                or grounding_tokens(parsed.group(2)) != grounding_tokens(modifier.get("surface"))):
            return {"validated": False, "reason": "parenthetical_entity_or_token_mismatch"}
        relations = evidence.get("identity_evidence") or []
        relations = relations if isinstance(relations, list) else []
        valid = any(isinstance(relation, dict) and relation.get("relation") == "same_entity"
                    and relation.get("entity_ids") == [entity_id]
                    and _validated_span(relation.get("source"), archival) for relation in relations)
    elif kind == "elided_list":
        components = evidence.get("components") or []
        components = components if isinstance(components, list) else []
        if len(components) < 2 or not all(_validated_span(span, archival) for span in components):
            return {"validated": False, "reason": "invalid_list_source_spans"}
        if grounding_tokens(surface) != [token for span in components for token in grounding_tokens(span.get("surface"))]:
            return {"validated": False, "reason": "list_has_unaccounted_tokens"}
        relations = evidence.get("membership_evidence") or []
        relations = relations if isinstance(relations, list) else []
        valid = all(isinstance(span.get("entity_ids"), list) and len(span["entity_ids"]) == 1
                    and any(isinstance(relation, dict) and relation.get("relation") == "member_of"
                            and relation.get("member_entity_id") == span["entity_ids"][0]
                            and relation.get("group_entity_id") == entity_id
                            and _validated_span(relation.get("source"), archival) for relation in relations)
                    for span in components)
    else:
        return {"validated": False, "reason": "unsupported_construction_type"}
    return {"validated": bool(valid), "construction_type": kind,
            "reason": "validated_source_construction" if valid else "missing_identity_or_membership_evidence"}


def _grounding_ledger(entities, field, archival, character_input, reference):
    ledger = []
    for entity in entities:
        seen = set()
        records = entity["aliases"] if field == "aliases" else entity.get("editorial_labels", []) if field == "editorial_labels" else [{"name": entity["name"]}]
        for record in records if isinstance(records, list) else []:
            surface = _raw_alias(record)
            if surface is None:
                continue
            exact_key = normalize_text(surface)
            if exact_key in seen:
                continue
            seen.add(exact_key)
            support = grounding_support(surface, archival)
            input_support = grounding_support(surface, character_input)
            reference_support = grounding_support(surface, reference)
            editorial = validate_editorial_allowance(surface, record, entity["id"], archival)
            allowed = support["tier"] == "ungrounded" and editorial["validated"]
            if input_support["tier"] != "ungrounded":
                confound = "present_in_character_input"
            elif support["tier"] != "ungrounded":
                confound = "archival_text_excluded_from_character_input"
            elif reference_support["tier"] != "ungrounded":
                confound = "cached_reference_text_only_ocr_gap"
            elif allowed:
                confound = "validated_editorial_allowance"
            else:
                confound = "unlocated_with_available_evidence"
            ledger.append({"entity_id": entity["id"], "raw_surface": surface, **support,
                           "character_input_support": input_support, "reference_support": reference_support,
                           "ocr_gap_diagnostic": confound, "editorial_allowance": allowed,
                           "editorial_validation": editorial})
    return ledger


def _grounding_counts(ledger):
    counts = Counter(entry["tier"] for entry in ledger)
    denominator = len(ledger)
    grounded = counts["exact"] + counts["determiner_stripped"]
    editorial = sum(entry.get("editorial_allowance", False) for entry in ledger)
    return {"aliases": denominator, "exact": counts["exact"], "determiner_stripped": counts["determiner_stripped"],
            "grounded": grounded, "ungrounded": counts["ungrounded"],
            "exact_rate": _ratio(counts["exact"], denominator), "lexical_grounding": _ratio(grounded, denominator),
            "editorial_allowances": editorial, "editorial_rate": _ratio(editorial, denominator),
            "explained_support": _ratio(grounded + editorial, denominator), "unexplained_rate": _ratio(counts["ungrounded"] - editorial, denominator),
            "ocr_gap_diagnostics": dict(Counter(entry["ocr_gap_diagnostic"] for entry in ledger))}


def _audit_key(surface, version):
    if version in {ARTICLE_DIAGNOSTIC_VERSION, HISTORICAL_SEPARATOR_VERSION}:
        words = tokenize(surface)
        removable = {"a", "an", "the"} if version == ARTICLE_DIAGNOSTIC_VERSION else _GROUNDING_DETERMINERS
        remove = bool(words) and words[0] in removable and (version == ARTICLE_DIAGNOSTIC_VERSION or len(words) > 1)
        return tuple(words[1:] if remove else words)
    text = surface.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = _HISTORICAL_DETERMINER_RE.sub("", " ".join(text.split()), count=1)
    return tuple(text.split())


def _overlap(left, right):
    if not left or not right:
        return None
    if left == right:
        return "equal"
    if set(left) < set(right) or set(right) < set(left):
        return "strict_subset"
    return None


def _structural_audit(entities, version, occurrences):
    variants = [[(raw, _audit_key(raw, version)) for raw in [entity["name"], *entity["raw_aliases"]]] for entity in entities]
    within = []
    per_character = []
    for entity, rows in zip(entities, variants):
        pairs = [{"entity_id": entity["id"], "left": left, "right": right, "kind": kind}
                 for (left, left_key), (right, right_key) in combinations(rows, 2)
                 if (kind := _overlap(left_key, right_key))]
        within.extend(pairs)
        per_character.append({"entity_id": entity["id"], "pairs": len(pairs),
                              "equal_pairs": sum(pair["kind"] == "equal" for pair in pairs),
                              "subset_pairs": sum(pair["kind"] == "strict_subset" for pair in pairs),
                              "affected": bool(pairs)})
    flags = []
    record_pairs = 0
    for left_index, right_index in combinations(range(len(entities)), 2):
        pair_flags = []
        ids = [entities[left_index]["id"], entities[right_index]["id"]]
        for (left, left_key), (right, right_key) in product(variants[left_index], variants[right_index]):
            kind = _overlap(left_key, right_key)
            if kind:
                spans = [{"page_id": occurrence.get("page_id"), "start": occurrence.get("start"), "end": occurrence.get("end")}
                         for occurrence in occurrences if all(
                             any(entity_id in hit.get("candidate_entity_ids", []) and
                                 _audit_key(raw, ARTICLE_DIAGNOSTIC_VERSION) == _audit_key(hit.get("surface", ""), ARTICLE_DIAGNOSTIC_VERSION)
                                 for hit in occurrence.get("nested_hits", []))
                             for entity_id, raw in zip(ids, (left, right)))]
                pair_flags.append({"entity_ids": ids, "left": left, "right": right, "kind": kind,
                                   "status": "unresolved", "handled": True, "candidate_spans": spans,
                                   "span_available": bool(spans)})
        flags.extend(pair_flags)
        record_pairs += bool(pair_flags)
    equality = [row for row in per_character if row["equal_pairs"]]
    subset = [row for row in per_character if row["subset_pairs"]]
    flagged_spans = {(span["page_id"], span["start"], span["end"]) for flag in flags for span in flag["candidate_spans"]}
    return {"version": version,
            "M2": {"characters": len(entities), "affected_characters": sum(row["affected"] for row in per_character),
                   "character_incidence": _ratio(sum(row["affected"] for row in per_character), len(entities)),
                   "affected_book": bool(within), "pairs": len(within),
                   "equal_pairs": sum(row["equal_pairs"] for row in per_character), "equal_characters": len(equality),
                   "equal_book": bool(equality), "subset_pairs": sum(row["subset_pairs"] for row in per_character),
                   "subset_characters": len(subset), "subset_book": bool(subset),
                   "per_character": per_character, "flags": within},
            "M3": {"surface_pairs": len(flags), "record_pairs": record_pairs, "affected_book": bool(flags),
                   "handled_flags": len(flags), "unresolved_flags": len(flags), "unhandled_flags": 0,
                   "candidate_occurrences": len(occurrences), "unresolved_flagged_occurrences": len(flagged_spans),
                   "span_unavailable_flags": sum(not flag["span_available"] for flag in flags), "flags": flags}}


def score_alias_foundation(book, prediction):
    """Score every output book, including empty-reference and failed books."""
    from alias_pipeline import CONVENTION_VERSION, dictionary_diagnostics

    stage = prediction.get("characters") or {}
    entities = _entities(stage)
    archival, character_input, reference, ocr_coverage = _pages(book, prediction)
    aliases = _grounding_ledger(entities, "aliases", archival, character_input, reference)
    names = _grounding_ledger(entities, "name", archival, character_input, reference)
    editorial_labels = _grounding_ledger(entities, "editorial_labels", archival, character_input, reference)
    diagnostic = dictionary_diagnostics(entities, [{"page_id": page_id, "text": text} for page_id, text in character_input], book_id=str(book.get("id", "")))
    audits = {version: _structural_audit(entities, version, diagnostic.get("occurrences", []))
              for version in (HISTORICAL_DELETION_VERSION, HISTORICAL_SEPARATOR_VERSION, ARTICLE_DIAGNOSTIC_VERSION)}
    return {"version": FOUNDATION_VERSION, "convention_version": CONVENTION_VERSION,
            "conventions": AliasMetrics.conventions, "label": AliasMetrics.report_label,
            "output_coverage": {"character_status": stage.get("status", "pending") if isinstance(stage, dict) else "completed",
                                "emitted_characters": len(entities), "emitted_aliases": len(aliases),
                                "empty_alias_output": not aliases, "ocr": ocr_coverage},
            "M1": {"label": AliasMetrics.grounding_label, "version": GROUNDING_VERSION,
                   "qualification": AliasMetrics.grounding_qualification, **_grounding_counts(aliases), "entries": aliases,
                   "character_names": _grounding_counts(names), "character_name_entries": names,
                   "input_support_qualification": AliasMetrics.input_support_qualification,
                   "editorial_validation": "typed_source_construction_v1", "editorial_qualification": AliasMetrics.editorial_qualification,
                   "separately_emitted_editorial_labels": len(editorial_labels), "editorial_label_entries": editorial_labels,
                   "editorial_label_support": _grounding_counts(editorial_labels)},
            "M2": {"label": AliasMetrics.redundancy_label, "qualification": AliasMetrics.redundancy_qualification,
                   "version": ARTICLE_DIAGNOSTIC_VERSION, **audits[ARTICLE_DIAGNOSTIC_VERSION]["M2"]},
            "M3": {"label": AliasMetrics.collision_label, "qualification": AliasMetrics.collision_qualification,
                   "version": ARTICLE_DIAGNOSTIC_VERSION, **audits[ARTICLE_DIAGNOSTIC_VERSION]["M3"]},
            "historical_structural_audits": {version: audit for version, audit in audits.items() if version != ARTICLE_DIAGNOSTIC_VERSION},
            "M4": {"label": AliasMetrics.mention_label, "qualification": AliasMetrics.mention_qualification,
                   **diagnostic, "correct_attribution": None, "wrong_attribution": None,
                   "false_merge_rate": None, "false_split_rate": None,
                   "false_merges": {"eligible_pairs": 0, "errors": None, "rate": None},
                   "false_splits": {"eligible_pairs": 0, "errors": None, "rate": None},
                   "independent_labels_available": False}}


def _aggregate_m1(records):
    count_keys = ("aliases", "exact", "determiner_stripped", "grounded", "ungrounded", "editorial_allowances")
    totals = {key: sum(record.get(key, 0) for record in records) for key in count_keys}
    values = [record["lexical_grounding"] for record in records if record.get("lexical_grounding") is not None]
    confounds = Counter()
    for record in records:
        confounds.update(record.get("ocr_gap_diagnostics", {}))
    return {**totals, "lexical_grounding": _ratio(totals["grounded"], totals["aliases"]),
            "exact_rate": _ratio(totals["exact"], totals["aliases"]),
            "editorial_rate": _ratio(totals["editorial_allowances"], totals["aliases"]),
            "explained_support": _ratio(totals["grounded"] + totals["editorial_allowances"], totals["aliases"]),
            "unexplained_rate": _ratio(totals["ungrounded"] - totals["editorial_allowances"], totals["aliases"]),
            "book_macro_grounding": _ratio(sum(values), len(values)), "defined_books": len(values),
            "undefined_books": len(records) - len(values), "ocr_gap_diagnostics": dict(confounds)}


def _aggregate_structure(records, metric, statuses):
    keys = (("characters", "affected_characters", "pairs", "equal_pairs", "equal_characters", "subset_pairs", "subset_characters")
            if metric == "M2" else ("surface_pairs", "record_pairs", "handled_flags", "unresolved_flags", "unhandled_flags", "span_unavailable_flags", "candidate_occurrences", "unresolved_flagged_occurrences"))
    totals = {key: sum(record.get(key, 0) for record in records) for key in keys}
    completed = [record for record, status in zip(records, statuses) if status == "completed"]
    affected = sum(record.get("affected_book", False) for record in completed)
    result = {**totals, "books": len(records), "book_denominator": len(completed),
              "incomplete_books": len(records) - len(completed), "affected_books": affected,
              "affected_output_books": sum(record.get("affected_book", False) for record in records),
              "book_incidence": _ratio(affected, len(completed)),
              "coverage_qualification": AliasMetrics.structural_coverage_qualification}
    if metric == "M2":
        numerator = sum(record.get("affected_characters", 0) for record in completed)
        denominator = sum(record.get("characters", 0) for record in completed)
        result.update({"character_incidence": _ratio(numerator, denominator),
                       "character_incidence_numerator": numerator, "character_denominator": denominator,
                       "equal_books": sum(record.get("equal_book", False) for record in records),
                       "subset_books": sum(record.get("subset_book", False) for record in records)})
    return result


def aggregate_alias_foundation(scores):
    """Aggregate counts and book-macro grounding without dropping undefined books."""
    records = [score["alias_foundation"] for score in scores if "alias_foundation" in score]
    statuses = [record["output_coverage"]["character_status"] for record in records]
    counts = Counter(statuses)
    m1 = _aggregate_m1([record["M1"] for record in records])
    strata = sorted({record["output_coverage"]["ocr"]["stratum"] for record in records})
    m4 = {mode: {key: sum(record["M4"].get(mode, {}).get(key, 0) for record in records)
                 for key in ("universe", "found", "single_candidate", "ambiguous", "missed")}
          for mode in ("optional_articles", "exact")}
    first = records[0] if records else {}
    return {"version": FOUNDATION_VERSION, "convention_version": first.get("convention_version"),
            "conventions": AliasMetrics.conventions, "label": AliasMetrics.report_label,
            "coverage": {"books": len(records), "character_status_counts": dict(counts),
                         "empty_alias_books": sum(record["output_coverage"]["empty_alias_output"] for record in records),
                         "failed_or_partial_character_books": sum(count for status, count in counts.items() if status not in {"completed", "pending", "budget_stopped"})},
            "M1": {"label": AliasMetrics.grounding_label, "version": GROUNDING_VERSION, **m1,
                   "qualification": AliasMetrics.grounding_qualification,
                   "by_ocr_coverage": {stratum: _aggregate_m1([record["M1"] for record in records if record["output_coverage"]["ocr"]["stratum"] == stratum]) for stratum in strata},
                   "character_names": _aggregate_m1([record["M1"]["character_names"] for record in records]),
                   "editorial_validation": "typed_source_construction_v1", "editorial_qualification": AliasMetrics.editorial_qualification,
                   "separately_emitted_editorial_labels": sum(record["M1"]["separately_emitted_editorial_labels"] for record in records),
                   "editorial_label_support": _aggregate_m1([record["M1"]["editorial_label_support"] for record in records])},
            "M2": {"label": AliasMetrics.redundancy_label, "version": ARTICLE_DIAGNOSTIC_VERSION,
                   "qualification": AliasMetrics.redundancy_qualification, **_aggregate_structure([record["M2"] for record in records], "M2", statuses)},
            "M3": {"label": AliasMetrics.collision_label, "version": ARTICLE_DIAGNOSTIC_VERSION,
                   "qualification": AliasMetrics.collision_qualification, **_aggregate_structure([record["M3"] for record in records], "M3", statuses)},
            "historical_structural_audits": {version: {metric: _aggregate_structure([record["historical_structural_audits"][version][metric] for record in records], metric, statuses)
                                                       for metric in ("M2", "M3")}
                                            for version in (HISTORICAL_DELETION_VERSION, HISTORICAL_SEPARATOR_VERSION)},
            "M4": {"label": AliasMetrics.mention_label, "qualification": AliasMetrics.mention_qualification, **m4,
                   "matcher_version": first.get("M4", {}).get("matcher_version"),
                   "correct_attribution": None, "wrong_attribution": None,
                   "false_merge_rate": None, "false_split_rate": None,
                   "false_merges": {"eligible_pairs": 0, "errors": None, "rate": None},
                   "false_splits": {"eligible_pairs": 0, "errors": None, "rate": None},
                   "independent_labels_available": False}}
