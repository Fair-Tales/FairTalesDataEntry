"""Pure reference-agreement metrics over frozen references and cached predictions.

Reference eligibility is a property of the snapshot. Metadata additionally
excludes fields whose ISBN enrichment could not be measured due to lookup failure.
All returned objects are JSON serializable. None denotes an undefined metric,
never a perfect score for two empty sets.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from collections import Counter, defaultdict

from text_content.alias_metrics import AliasMetrics

from .alias_metrics import aggregate_alias_foundation, score_alias_foundation
from .normalization import (UNCERTAINTY, character_similarity, edit_distance,
                            normalize_name, normalize_text, token_counts, tokenize)
from .statistics import bootstrap_books

ELIGIBLE = {"observed", "verified"}
UNRUN = {"pending", "dispatched", "budget_stopped", "outcome_unknown", "unknown_outcome"}


def apply_overlay(manifest, overlay):
    """Return a copy with reviewed reference changes, before opening predictions.

    Overlay schema: version, snapshot_id, reviewed=true, changes=[{book_id,
    page_id (for page fields), field, value, status, provenance}]. Fields are text,
    contains_story, characters, or metadata.<field>. Originals are never mutated.
    """
    if not overlay.get("version") or overlay.get("reviewed") is not True:
        raise ValueError("Adjudication requires a version and reviewed=true")
    if not manifest.get("snapshot_id") or overlay.get("snapshot_id") != manifest.get("snapshot_id"):
        raise ValueError("Adjudication snapshot_id does not match the frozen snapshot")
    revised = deepcopy(manifest)
    books = {book.get("id"): book for book in revised.get("books", [])}
    for change in overlay.get("changes", []):
        book = books.get(change.get("book_id"))
        if book is None:
            raise ValueError("Adjudication refers to an unknown book")
        field = change.get("field", "")
        status = change.get("status", "verified")
        if status not in ELIGIBLE | {"missing", "ambiguous", "not_annotated"}:
            raise ValueError("Invalid adjudication eligibility status")
        if "value" not in change or not change.get("provenance"):
            raise ValueError("Adjudication requires value and review provenance")
        if field in {"text", "contains_story"}:
            pages = {page.get("id"): page for page in book.get("pages", [])}
            page = pages.get(change.get("page_id"))
            if page is None:
                raise ValueError("Adjudication refers to an unknown page")
            target = page.setdefault("reference", {})
        elif field.startswith("metadata."):
            target = book.setdefault("reference", {}).setdefault("metadata", {})
            field = field.removeprefix("metadata.")
            if field not in {"title", "authors", "illustrators", "publisher", "published_year"}:
                raise ValueError("Unknown metadata adjudication field")
        elif field == "characters":
            target = book.setdefault("reference", {})
        else:
            raise ValueError("Unknown adjudication field")
        record = {**target.get(field, {}), "value": change.get("value"), "status": status,
                  "provenance": change["provenance"], "adjudication_version": overlay["version"]}
        if "complete" in change:
            record["complete"] = bool(change["complete"])
        if field == "text":
            record["kind"] = "wordless" if not normalize_text(change.get("value")) else "text"
        target[field] = record
    revised["adjudication"] = {"version": overlay["version"], "snapshot_id": overlay["snapshot_id"]}
    return revised


def apply_equivalences(protocol, document):
    """Validate an explicitly reviewed, versioned equivalence table."""
    if not document.get("version") or document.get("reviewed") is not True:
        raise ValueError("Equivalences require a version and reviewed=true")
    table = document.get("equivalences", {})
    if not isinstance(table, dict) or any(not isinstance(mapping, dict) for mapping in table.values()):
        raise ValueError("Equivalences must map field names to string mappings")
    if any(not isinstance(key, str) or not isinstance(value, str)
           for mapping in table.values() for key, value in mapping.items()):
        raise ValueError("Equivalence names must be strings")
    return {**protocol, "reviewed_equivalences": table, "equivalence_version": document["version"]}


def _reference(reference):
    return reference if isinstance(reference, dict) else {"value": reference, "status": "observed"}


def _prediction(prediction, key="value"):
    if prediction is None:
        return None, "pending"
    if isinstance(prediction, dict):
        return prediction.get(key, prediction.get("value")), prediction.get("status", "completed")
    return prediction, "completed"


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _prf(matched, reference, predicted):
    return {"precision": _ratio(matched, predicted), "recall": _ratio(matched, reference),
            "f1": _ratio(2 * matched, reference + predicted),
            "matched": matched, "reference": reference, "predicted": predicted}


def _base(reference, status):
    return {"eligible": reference.get("status") in ELIGIBLE,
            "reference_status": reference.get("status", "missing"),
            "provenance": reference.get("provenance"), "prediction_status": status,
            "completed": status == "completed", "failed": status == "failed",
            "unrun": status in UNRUN}


def score_ocr(reference, prediction):
    """Page-local token multiset overlap, ordered CER/WER and wordless agreement.

    Failed and unrun eligible transcriptions recover zero tokens, so intended
    denominators remain visible. Unrun work additionally marks the run incomplete.
    Wordless pages have their own denominator; their token F1 is always None.
    Uncertainty markers consume denominator tokens and can never match each other.
    """
    reference = _reference(reference)
    actual, status = _prediction(prediction, "text")
    if status == "completed" and not isinstance(actual, str):
        status = "failed"
    base = _base(reference, status)
    result = {**base, "scored": False, "subgroup": "excluded", "precision": None,
              "recall": None, "f1": None, "cer": None, "wer": None,
              "matched_tokens": 0, "reference_tokens": 0, "predicted_tokens": 0,
              "missing_tokens": {}, "excess_tokens": {}, "uncertainty_tokens": 0,
              "character_errors": 0, "reference_characters": 0, "word_errors": 0}
    if not base["eligible"]:
        result["exclusion"] = reference.get("status", "missing")
        return result
    expected = reference.get("value")
    if not isinstance(expected, str):
        raise ValueError("Eligible OCR reference must contain a string value")
    actual = actual if status == "completed" else ""
    expected_normal = normalize_text(expected)
    actual_normal = normalize_text(actual)
    if not expected_normal:
        result["subgroup"] = ("agreed_wordless" if not actual_normal else "disputed_wordless") \
            if status == "completed" else ("wordless_unrun" if status in UNRUN else "wordless_failed")
        result["wordless_reference"] = True
        result["predicted_tokens"] = len(tokenize(actual))
        result["invented_tokens"] = len(tokenize(actual)) if status == "completed" else None
        result["invented_characters"] = len(actual_normal) if status == "completed" else None
        result["verified_wordless"] = reference.get("status") == "verified"
        return result
    gold_counts, pred_counts = token_counts(expected), token_counts(actual)
    overlap = gold_counts & pred_counts
    overlap.pop(UNCERTAINTY, None)
    matched = sum(overlap.values())
    gold_total, pred_total = sum(gold_counts.values()), sum(pred_counts.values())
    if not gold_total:
        result["exclusion"] = "no_lexical_reference_tokens"
        return result
    # Even identical uncertainty placeholders must create an ordered error.
    ordered_gold = ["<reference-uncertain>" if item == UNCERTAINTY else item for item in tokenize(expected)]
    ordered_pred = ["<prediction-uncertain>" if item == UNCERTAINTY else item for item in tokenize(actual)]
    char_gold = expected_normal.replace(UNCERTAINTY, "\ue000")
    char_pred = actual_normal.replace(UNCERTAINTY, "\ue001")
    char_errors = edit_distance(char_gold, char_pred)
    word_errors = edit_distance(ordered_gold, ordered_pred)
    scores = _prf(matched, gold_total, pred_total)
    result.update({"scored": True, "subgroup": "text_recovery", "precision": scores["precision"],
                   "recall": scores["recall"], "f1": scores["f1"], "matched_tokens": matched,
                   "reference_tokens": gold_total, "predicted_tokens": pred_total,
                   "cer": char_errors / len(char_gold), "wer": word_errors / gold_total,
                   "missing_tokens": dict(gold_counts - overlap), "excess_tokens": dict(pred_counts - overlap),
                   "uncertainty_tokens": gold_counts.get(UNCERTAINTY, 0) + pred_counts.get(UNCERTAINTY, 0),
                   "character_errors": char_errors, "reference_characters": len(char_gold),
                   "word_errors": word_errors})
    return result


def _story_metrics(counts):
    tp, fp, fn, tn = (counts.get(key, 0) for key in ("tp", "fp", "fn", "tn"))
    failed_true, failed_false = (counts.get(key, 0) for key in ("failed_true", "failed_false"))
    valid = tp + fp + fn + tn
    total = valid + failed_true + failed_false
    return {**counts, "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
            "f1": _ratio(2 * tp, 2 * tp + fp + fn), "accuracy": _ratio(tp + tn, valid),
            "coverage": _ratio(valid, total), "eligible_pages": total,
            "end_to_end_recall": _ratio(tp, tp + fn + failed_true),
            "end_to_end_accuracy": _ratio(tp + tn, total)}


def score_story(reference, prediction):
    """Imported-label agreement; the 2x3 table retains failed and unrun flags."""
    reference = _reference(reference)
    actual, status = _prediction(prediction, "contains_story")
    if status == "completed" and not isinstance(actual, bool):
        status = "failed"
    result = _base(reference, status)
    counts = {key: 0 for key in ("tp", "fp", "fn", "tn", "failed_true", "failed_false", "unrun_true", "unrun_false")}
    if result["eligible"]:
        expected = reference.get("value")
        if not isinstance(expected, bool):
            raise ValueError("Eligible story reference must contain a boolean")
        if status == "completed":
            key = ("tp" if actual else "fn") if expected else ("fp" if actual else "tn")
            counts[key] = 1
        else:
            counts["failed_true" if expected else "failed_false"] = 1
            if status in UNRUN:
                counts["unrun_true" if expected else "unrun_false"] = 1
    return {**result, **_story_metrics(counts)}


def _equivalent(value, equivalences, *, character=False):
    normal = normalize_name(value, strip_article=character)
    table = {normalize_name(key, strip_article=character): normalize_name(target, strip_article=character)
             for key, target in (equivalences or {}).items()}
    return table.get(normal, normal)


def _credit_names(value):
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    names = []
    for item in values:
        if isinstance(item, dict):
            name = item.get("name") or " ".join(str(item.get(key) or "") for key in ("forename", "surname")).strip()
        else:
            name = str(item)
        if normalize_name(name) not in {"", "unknown", "none", "n a"}:
            names.append(name)
    return names


def score_metadata(field, reference, prediction, equivalences=None):
    """Strict exact agreement, with fuzzy/reviewed agreement reported separately.

    Incomplete author/illustrator lists measure known-credit recall only; extra
    predictions are not treated as false positives or perfect complete-set matches.
    """
    reference = _reference(reference)
    actual, status = _prediction(prediction)
    base = _base(reference, status)
    if status == "lookup_failed":
        base.update(eligible=False, exclusion="lookup_failed" if base["eligible"]
                    else reference.get("status", "missing"))
    result = {**base, "field": field, "exact": None, "reviewed_equivalent": None,
              "similarity": None, "reference_value": reference.get("value"),
              "prediction_value": actual, "complete_reference": reference.get("complete", False)}
    if not base["eligible"]:
        return result
    expected = reference.get("value")
    if field in {"authors", "author", "illustrators", "illustrator"}:
        gold = {normalize_name(name) for name in _credit_names(expected)}
        pred = {normalize_name(name) for name in _credit_names(actual)} if status == "completed" else set()
        gold_reviewed = {_equivalent(name, equivalences) for name in gold}
        pred_reviewed = {_equivalent(name, equivalences) for name in pred}
        complete = reference.get("complete", False)
        result.update({"exact": gold == pred if complete else None,
                       "known_reference_agreement": gold <= pred if gold else None,
                       "known_credit_recall": _ratio(len(gold & pred), len(gold)),
                       "credit_precision": _ratio(len(gold & pred), len(pred)) if complete else None,
                       "reviewed_equivalent": (gold_reviewed == pred_reviewed if complete else gold_reviewed <= pred_reviewed)
                       if gold else None,
                       "matched_credits": len(gold & pred), "reference_credits": len(gold),
                       "predicted_credits": len(pred), "unverified_extra_credits": sorted(pred - gold) if not complete else [],
                       "missing_credits": sorted(gold - pred)})
        return result
    if field in {"published", "published_year"}:
        try:
            expected_year = int(expected)
        except (TypeError, ValueError) as exc:
            raise ValueError("Eligible publication year must be an integer") from exc
        if isinstance(expected, bool) or str(expected_year) != str(expected).strip() or expected_year < 0:
            raise ValueError("Eligible publication year must be a nonnegative integer")
        try:
            actual_year = int(actual) if status == "completed" and not isinstance(actual, bool) else None
            if actual_year is not None and str(actual_year) != str(actual).strip():
                actual_year = None
        except (TypeError, ValueError):
            actual_year = None
        result.update({"exact": actual_year == expected_year,
                       "year_difference": actual_year - expected_year if actual_year is not None else None})
        return result
    gold = normalize_name(str(expected or ""))
    pred = normalize_name(str(actual or "")) if status == "completed" else ""
    result.update({"exact": bool(gold) and gold == pred,
                   "reviewed_equivalent": bool(gold) and _equivalent(gold, equivalences) == _equivalent(pred, equivalences),
                   "similarity": character_similarity(gold, pred) if gold else None})
    return result


def _entities(value, equivalences=None):
    entities = []
    for i, item in enumerate(value or []):
        item = item if isinstance(item, dict) else {"name": str(item)}
        aliases = item.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        raw = [item.get("name", "")] + [alias.get("name", "") if isinstance(alias, dict) else alias for alias in aliases]
        normalized = {_equivalent(name, equivalences, character=True) for name in raw if isinstance(name, str) and name.strip()}
        explicit_aliases = {_equivalent(name, equivalences, character=True) for name in raw[1:]
                            if isinstance(name, str) and name.strip()}
        entities.append({"id": str(item.get("id", i)), "name": item.get("name", ""),
                         "variants": sorted(normalized), "raw_variants": raw,
                         "explicit_aliases": sorted(explicit_aliases),
                         "visual_only": bool(item.get("visual_only", False))})
    return entities


def _maximum_matching(edges, forbidden=None):
    matched = {}

    def visit(ref_id, seen):
        for pred_id in edges.get(ref_id, []):
            if (ref_id, pred_id) == forbidden or pred_id in seen:
                continue
            seen.add(pred_id)
            if pred_id not in matched or visit(matched[pred_id], seen):
                matched[pred_id] = ref_id
                return True
        return False

    for ref_id in edges:
        visit(ref_id, set())
    return sorted((ref_id, pred_id) for pred_id, ref_id in matched.items())


def match_characters(reference, prediction, equivalences=None):
    """One-to-one exact variant matching, with ambiguity exposed as explicit bounds.

    Only edges present in every maximum matching receive conservative credit.
    Normalization collisions remain separate entities and require review.
    """
    reference = _reference(reference)
    actual, status = _prediction(prediction)
    base = _base(reference, status)
    refs = _entities(reference.get("value", []), equivalences) if base["eligible"] else []
    preds = _entities(actual, equivalences) if status == "completed" and isinstance(actual, list) else []
    edges = {r: [p for p, pred in enumerate(preds) if set(ref["variants"]) & set(pred["variants"])]
             for r, ref in enumerate(refs)}
    maximum = _maximum_matching(edges)
    forced = [(r, p) for r, p in maximum if len(_maximum_matching(edges, (r, p))) < len(maximum)]
    possible = [(r, p) for r, options in edges.items() for p in options]
    collisions = []
    for side, entities in (("reference", refs), ("prediction", preds)):
        variants = defaultdict(list)
        for i, entity in enumerate(entities):
            for variant in entity["variants"]:
                variants[variant].append(i)
        collisions.extend({"side": side, "variant": variant, "entities": indices}
                          for variant, indices in variants.items() if len(indices) > 1)
    return {**base, "reference": refs, "prediction": preds, "matches": forced,
            "representative_maximum_matching": maximum, "candidate_matches": possible,
            "ambiguous": len(forced) != len(maximum), "normalization_collisions": collisions,
            "matched_lower": len(forced), "matched_upper": len(maximum),
            "complete_reference": reference.get("complete", False),
            "unmatched_reference": [i for i in range(len(refs)) if not any(r == i for r, _ in forced)],
            "unmatched_prediction": [i for i in range(len(preds)) if not any(p == i for _, p in forced)]}


def score_characters(matches):
    """Entity and correctly attached variant metrics, with ambiguity bounds."""
    refs, preds = matches.get("reference", []), matches.get("prediction", [])
    lower = sum(len(set(refs[r]["variants"]) & set(preds[p]["variants"])) for r, p in matches.get("matches", []))
    # An intentionally conservative upper bound; no arbitrary ambiguous pairing
    # receives exact alias credit. It may be unattainable and is labelled as such.
    upper = sum(max([len(set(ref["variants"]) & set(preds[p]["variants"]))
                     for r, p in matches.get("candidate_matches", []) if r == i] or [0])
                for i, ref in enumerate(refs))
    ref_variants = sum(len(ref["variants"]) for ref in refs)
    pred_variants = sum(len(pred["variants"]) for pred in preds)
    upper = min(upper, ref_variants, pred_variants)
    known_aliases = sum(len(ref.get("explicit_aliases", [])) for ref in refs)
    recovered_aliases = sum(len(set(refs[r].get("explicit_aliases", [])) & set(preds[p]["variants"]))
                            for r, p in matches.get("matches", []))
    return {**matches, "entities": _prf(matches.get("matched_lower", 0), len(refs), len(preds)),
            "entities_upper": _prf(matches.get("matched_upper", 0), len(refs), len(preds)),
            "variants": _prf(lower, ref_variants, pred_variants),
            "variants_upper_bound": _prf(upper, ref_variants, pred_variants),
            "alias_recall": {"label": AliasMetrics.recall_label, "matched": recovered_aliases,
                             "reference": known_aliases, "recall": _ratio(recovered_aliases, known_aliases)},
            "reference_alias_precision_diagnostic": {"label": AliasMetrics.precision_label,
                                                     "value": _ratio(lower, pred_variants),
                                                     "legacy_metric": "variants.precision",
                                                     "reason": AliasMetrics.precision_reason},
            "visual_only_reference": sum(ref["visual_only"] for ref in refs),
            "visual_only_matched": sum(refs[r]["visual_only"] for r, _ in matches.get("matches", [])),
            "precision_qualification": "Agreement with possibly incomplete character/alias annotations; extras require review."
            if not matches.get("complete_reference") else None}


def _ocr_totals(pages):
    scored = [page for page in pages if page.get("scored")]
    matched = sum(page["matched_tokens"] for page in scored)
    reference = sum(page["reference_tokens"] for page in scored)
    predicted = sum(page["predicted_tokens"] for page in scored)
    return {**_prf(matched, reference, predicted), "pages": len(scored),
            "cer": _ratio(sum(page["character_errors"] for page in scored), sum(page["reference_characters"] for page in scored)),
            "wer": _ratio(sum(page["word_errors"] for page in scored), reference)}


def _wordless(pages):
    wordless = [page for page in pages if page.get("wordless_reference")]
    counts = Counter(page.get("subgroup") for page in wordless)
    agreed, disputed = counts["agreed_wordless"], counts["disputed_wordless"]
    return {"intended_pages": len(wordless), "completed_pages": agreed + disputed,
            "agreed_wordless": agreed, "disputed_wordless": disputed,
            "failed_pages": counts["wordless_failed"], "unrun_pages": counts["wordless_unrun"],
            "agreement_rate": _ratio(agreed, agreed + disputed),
            "end_to_end_agreement_rate": _ratio(agreed, len(wordless)),
            "coverage": _ratio(agreed + disputed, len(wordless)),
            "invented_tokens": sum(page.get("invented_tokens") or 0 for page in wordless),
            "verified_pages": sum(page.get("verified_wordless", False) for page in wordless),
            "qualification": "Imported wordless agreement; disputed pages require adjudication before calling text hallucinated."}


def score_book(book, prediction=None, protocol=None):
    """Assemble pure book-level metrics from the public manifest/artifact schema."""
    prediction = prediction or {}
    protocol = protocol or {}
    equivalents = protocol.get("reviewed_equivalences", {})
    pages = []
    for page in book.get("pages", []):
        reference = page.get("reference", {})
        actual = prediction.get("pages", {}).get(page.get("id"))
        pages.append({"id": page.get("id"), "page_number": page.get("page_number"),
                      "ocr": score_ocr(reference.get("text", {"status": "missing"}), actual),
                      "story": score_story(reference.get("contains_story", {"status": "missing"}), actual)})
    reference = book.get("reference", {})
    metadata_prediction = prediction.get("metadata", {})
    metadata_value = metadata_prediction.get("value", {}) or {}
    metadata_status = metadata_prediction.get("status", "pending")
    failed_fields = set(metadata_value.get("lookup_failed_fields") or [])
    metadata = {field: score_metadata(field, record, {"value": metadata_value.get(field),
                                      "status": "lookup_failed" if metadata_status == "completed" and field in failed_fields else metadata_status},
                                      equivalents.get(field))
                for field, record in reference.get("metadata", {}).items()}
    for field, record in metadata.items():
        record["prediction_source"] = metadata_value.get("sources", {}).get(field)
    character_matches = match_characters(reference.get("characters", {"status": "missing"}),
                                         prediction.get("characters"), equivalents.get("characters"))
    ref_hash = hashlib.sha256(json.dumps({"pages": [{"id": page.get("id"), "reference": page.get("reference")}
                                                  for page in book.get("pages", [])], "reference": reference},
                                       sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"id": book.get("id"), "title": book.get("title"), "reference_hash": ref_hash,
            "pages": pages, "ocr": _ocr_totals([page["ocr"] for page in pages]),
            "wordless": _wordless([page["ocr"] for page in pages]), "metadata": metadata,
            "metadata_diagnostics": {"status": metadata_status, "located": metadata_value.get("located"),
                                     "isbn": metadata_value.get("isbn"), "error": metadata_prediction.get("error"),
                                     "isbn_lookup": metadata_value.get("isbn_lookup"),
                                     "lookup_failed_fields": sorted(failed_fields)},
            "characters": score_characters(character_matches),
            "alias_foundation": score_alias_foundation(book, prediction),
            "themes": {"status": reference.get("themes", {}).get("status", "not_annotated"), "scored": False}}


def aggregate(scores, protocol=None):
    """Book-macro primary, token-micro secondary, intended coverage and exclusions."""
    protocol = protocol or {}
    books = list(scores)
    pages = [page for book in books for page in book.get("pages", [])]
    ocr = [page["ocr"] for page in pages]
    book_values = [book["ocr"]["f1"] for book in books if book["ocr"].get("f1") is not None]
    story_counts = Counter()
    for page in pages:
        story_counts.update({key: page["story"].get(key, 0) for key in
                             ("tp", "fp", "fn", "tn", "failed_true", "failed_false", "unrun_true", "unrun_false")})
    metadata = {}
    for field in sorted({field for book in books for field in book.get("metadata", {})}):
        records = [book["metadata"][field] for book in books if field in book.get("metadata", {})]
        eligible = [record for record in records if record["eligible"]]
        exact = [record for record in eligible if record.get("exact") is not None]
        known = [record for record in eligible if record.get("known_reference_agreement") is not None]
        metadata[field] = {"eligible_books": len(eligible), "strict_denominator": len(exact),
                           "lookup_failed_exclusions": sum(record.get("exclusion") == "lookup_failed" for record in records),
                           "strict_exact_agreement": _ratio(sum(record["exact"] for record in exact), len(exact)),
                           "known_credit_denominator": len(known),
                           "known_credit_agreement": _ratio(sum(record["known_reference_agreement"] for record in known), len(known)),
                           "completed_books": sum(record["completed"] for record in eligible),
                           "failed_books": sum(record["failed"] for record in eligible),
                           "unrun_books": sum(record["unrun"] for record in eligible)}
    characters = {}
    for metric in ("entities", "entities_upper", "variants", "variants_upper_bound"):
        records = [book["characters"][metric] for book in books if book["characters"]["eligible"]]
        characters[metric] = _prf(sum(record["matched"] for record in records), sum(record["reference"] for record in records),
                                  sum(record["predicted"] for record in records))
    characters.update({"eligible_books": sum(book["characters"]["eligible"] for book in books),
                       "completed_books": sum(book["characters"]["eligible"] and book["characters"]["completed"] for book in books),
                       "failed_books": sum(book["characters"]["eligible"] and book["characters"]["failed"] for book in books),
                       "unrun_books": sum(book["characters"]["eligible"] and book["characters"]["unrun"] for book in books),
                       "ambiguous_books": [book["id"] for book in books if book["characters"]["ambiguous"]],
                       "incomplete_annotation_books": sum(not book["characters"]["complete_reference"] for book in books if book["characters"]["eligible"]),
                       "visual_only_reference": sum(book["characters"]["visual_only_reference"] for book in books),
                       "visual_only_matched": sum(book["characters"]["visual_only_matched"] for book in books)})
    alias_records = [book["characters"]["alias_recall"] for book in books if book["characters"]["eligible"]]
    alias_reference = sum(record["reference"] for record in alias_records)
    alias_matched = sum(record["matched"] for record in alias_records)
    alias_values = [record["recall"] for record in alias_records if record["recall"] is not None]
    characters["alias_recall"] = {"label": AliasMetrics.recall_label, "matched": alias_matched,
                                   "reference": alias_reference, "recall": _ratio(alias_matched, alias_reference),
                                   "book_macro_recall": _ratio(sum(alias_values), len(alias_values)),
                                   "eligible_books": len(alias_values)}
    characters["reference_alias_precision_diagnostic"] = {
        "label": AliasMetrics.precision_label, "value": characters["variants"]["precision"],
        "legacy_metric": "variants.precision", "reason": AliasMetrics.precision_reason}
    sampling_records = [{"id": book["id"], "value": book["ocr"].get("f1"),
                         "role": book.get("role", "descriptive"), "stratum": book.get("stratum", "all"),
                         "inclusion_probability": book.get("inclusion_probability", 1.0)}
                        for book in books]
    probability = [record for record in sampling_records if record["role"] in {"probability", "random"}]
    verified = [page for page in ocr if page["reference_status"] == "verified"]
    verified_book_values = [_ocr_totals([page["ocr"] for page in book["pages"] if page["ocr"]["reference_status"] == "verified"])["f1"] for book in books]
    verified_book_values = [value for value in verified_book_values if value is not None]
    incomplete_stages = sum(page["ocr"]["unrun"] for page in pages) + sum(record["unrun"] for book in books for record in book["metadata"].values()) + sum(book["characters"]["unrun"] for book in books)
    lookup_failed_books = [book["id"] for book in books
                           if (book.get("metadata_diagnostics", {}).get("isbn_lookup") or {}).get("status") == "failed"
                           or any(record.get("prediction_status") == "lookup_failed"
                                  for record in book.get("metadata", {}).values())]
    return {"label": "pilot reference-agreement", "protocol_version": protocol.get("version", "pilot-metrics-v2"),
            "scorer_version": "pilot-metrics-v2+alias-foundation-v1",
            "books": len(books), "pages": len(pages), "incomplete": incomplete_stages > 0,
            "ocr": {"book_macro_f1": _ratio(sum(book_values), len(book_values)), "book_denominator": len(book_values),
                    "token_micro": _ocr_totals(ocr), "intended_text_pages": sum(page["scored"] for page in ocr),
                    "completed_text_pages": sum(page["scored"] and page["completed"] for page in ocr),
                    "failed_text_pages": sum(page["scored"] and page["failed"] for page in ocr),
                    "unrun_text_pages": sum(page["scored"] and page["unrun"] for page in ocr),
                    "exclusions": dict(Counter(page.get("exclusion") for page in ocr if page.get("exclusion"))),
                    "reference_status_counts": dict(Counter(page["reference_status"] for page in ocr)),
                    "provenance_counts": dict(Counter(json.dumps(page.get("provenance"), sort_keys=True) for page in ocr)),
                    "verified_reference": {"book_macro_f1": _ratio(sum(verified_book_values), len(verified_book_values)),
                                           "book_denominator": len(verified_book_values), "token_micro": _ocr_totals(verified)}},
            "wordless": {**_wordless(ocr), "books": [book["id"] for book in books if book["wordless"]["intended_pages"]]},
            "story": _story_metrics(dict(story_counts)), "metadata": metadata, "characters": characters,
            "alias_foundation": aggregate_alias_foundation(books),
            "metadata_lookup_failures": {"affected_books": len(lookup_failed_books),
                                         "book_ids": lookup_failed_books,
                                         "excluded_fields": sum(record.get("exclusion") == "lookup_failed"
                                                                for book in books for record in book.get("metadata", {}).values())},
            "probability_sample": bootstrap_books(probability, repeats=protocol.get("bootstrap_repeats", 2000),
                                                    seed=protocol.get("bootstrap_seed", 20261104)) if probability else None,
            "challenge": {"books": [record["id"] for record in sampling_records if record["role"] == "challenge"],
                          "book_macro_f1": _ratio(sum(record["value"] for record in sampling_records if record["role"] == "challenge" and record["value"] is not None),
                                                  sum(record["role"] == "challenge" and record["value"] is not None for record in sampling_records))},
            "qualification": "Imported OCR, cleaning and metadata include AI-derived values. Agreement is not independent accuracy. Failed and unrun text recover zero content; unrun work makes this report incomplete."}
