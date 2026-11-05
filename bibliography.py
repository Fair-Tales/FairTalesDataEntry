"""Lossless, offline bibliographic evidence and versioned C4/C6 projections.

Legacy publisher/year values never acquire event scope by being imported.  The
printed imprint and the publishing house are distinct even within one event.
All extracted events and credits survive analytical projection.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import unicodedata

METADATA_SCHEMA_VERSION = "bibliography-v2"
PUBLICATION_POLICY_VERSION = "publication-events-v1"
EXPORT_VERSION = "bibliography-export-v2"
TARGET_FIELDS = ("publisher", "imprint", "publishing_house", "year", "territory")
PERSISTED_FIELDS = (
    "metadata_schema_version", "publication_policy_version", "photographed_edition",
    "first_publication", "publication_events", "legacy_publication", "credits",
    "metadata_conflicts", "metadata_conventions", "imprint", "publishing_house",
    "published_year_scope", "published_year_territory",
    "metadata_evidence_payloads", "metadata_history",
    "metadata_reconciliation", "metadata_page_numbers", "metadata_prompt_manifest",
    "metadata_extraction_status",
)


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def empty_target(*, first=False):
    result = {field: None for field in TARGET_FIELDS}
    result.update(publisher_authority_id=None, evidence=[])
    result.update({field + "_status": "unresolved"
                   for field in ("publisher", "imprint", "publishing_house", "year")})
    if first:
        result["scope"] = "unknown"
    return result


def _json_default(value):
    if hasattr(value, "path"):
        return {"reference_path": value.path}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def record_hash(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                    default=_json_default).encode()).hexdigest()


def legacy_publication(record, *, record_id=None):
    """Preserve values, source references, timestamps and source hash verbatim."""
    return {
        "publisher": deepcopy(record.get("publisher")),
        "published_year": deepcopy(record.get("published_year", record.get("published"))),
        "source": deepcopy(record.get("sources", record.get("source"))),
        "record_id": record_id,
        "source_ids": deepcopy({key: record[key] for key in
                                ("entered_by", "source_id", "source_ids") if key in record}),
        "timestamps": deepcopy({key: record[key] for key in
                                ("datetime_created", "last_updated") if key in record}),
        "original_record_hash": record_hash(record),
        "migration_status": "needs_review",
    }


def _evidence(event, *, field=None):
    records = deepcopy(event.get("evidence") or [])
    if not records and event.get("quote"):
        records = [{key: event.get(key) for key in
                    ("page_number", "source_page_id", "image_hash", "quote", "provenance", "plain_print")}]
    for item in records:
        item["field"] = field or item.get("field") or "publication_event"
        item["provenance"] = item.get("provenance") or "legacy_unverified"
        item["source_page_id"] = item.get("source_page_id") or item.get("page_number")
    return records


def event_supported(event):
    return any(item.get("quote") and item.get("source_page_id") is not None
               and ((item.get("provenance") == "photo" and item.get("image_hash"))
                    or (item.get("provenance") == "frozen_lookup"
                        and event.get("event_identity_verified") is True))
               for item in _evidence(event))


def _event_target(event, *, first=False):
    target = empty_target(first=first)
    role = event.get("publisher_role", "unknown")
    imprint = event.get("imprint")
    house = event.get("publishing_house")
    if role == "imprint":
        imprint = imprint or event.get("publisher")
    elif role in {"publisher", "parent"}:
        house = house or event.get("publisher")
    # A publisher statement with an unknown role does not identify an imprint.
    target.update(imprint=imprint, publisher=imprint, publishing_house=house,
                  year=event.get("year") if type(event.get("year")) is int else None,
                  territory=event.get("territory"),
                  publisher_authority_id=event.get("publisher_authority_id"))
    for field in ("publisher", "imprint", "publishing_house", "year"):
        if target.get(field) is not None:
            target[field + "_status"] = "evidenced"
            target["evidence"].extend(_evidence(event, field=field))
    if first:
        target["scope"] = "work_first" if event.get("scope") in {"original", "work_first"} else "territorial_first"
    return target


def _select_event(events, *, first=False):
    """Select a whole event: never assemble a publisher and date across events."""
    if not events:
        return empty_target(first=first), []
    targets = [_event_target(event, first=first) for event in events]
    fields = TARGET_FIELDS + (("scope",) if first else ())
    signatures = {tuple(normalized(target.get(field)) for field in fields) for target in targets}
    if len(signatures) == 1:
        target = targets[0]
        target["evidence"] = [item for candidate in targets for item in candidate["evidence"]]
        return target, []
    # Compatible incomplete statements may corroborate one complete event, but
    # two incomplete events cannot be stitched into a new publication claim.
    complete = [target for target in targets if all(
        all(other.get(field) is None or normalized(other.get(field)) == normalized(target.get(field))
            for field in fields) for other in targets)]
    if complete:
        return complete[0], []
    target = empty_target(first=first)
    for field in ("publisher", "imprint", "publishing_house", "year"):
        if len({normalized(candidate.get(field)) for candidate in targets}) > 1:
            target[field + "_status"] = "conflicting"
    return target, [{"field": "first_publication" if first else "photographed_edition",
                     "candidates": deepcopy(events), "reason": "conflicting_events_at_selected_scope"}]


def publication_projection(events, *, legacy=None):
    events = deepcopy(events or [])
    supported = [event for event in events if isinstance(event, dict) and event_supported(event)]
    edition = [event for event in supported if event.get("scope") == "edition"]
    if not edition:
        edition = [event for event in supported if event.get("scope") == "title_imprint"
                   and event.get("corroborated") is True]
    originals = [event for event in supported if event.get("scope") in {"original", "work_first"}]
    territorial = [event for event in supported if event.get("scope") == "territorial_first"
                   and event.get("territory")]
    photographed, edition_conflicts = _select_event(edition)
    first, first_conflicts = _select_event(originals or territorial, first=True)
    return {
        "metadata_schema_version": METADATA_SCHEMA_VERSION,
        "publication_policy_version": PUBLICATION_POLICY_VERSION,
        "photographed_edition": photographed, "first_publication": first,
        "publication_events": events,
        "legacy_publication": deepcopy(legacy) if legacy is not None else {
            "publisher": None, "published_year": None, "source": None,
            "migration_status": "needs_review"},
        "metadata_conflicts": edition_conflicts + first_conflicts,
        "imprint": photographed["imprint"], "publishing_house": photographed["publishing_house"],
        "publisher": photographed["imprint"], "published_year": first["year"],
        "published_year_scope": first["scope"], "published_year_territory": first["territory"],
    }


def adapt_legacy_metadata(record):
    """Symmetric baseline/candidate adapter; raw legacy facts stay unverified."""
    if record.get("metadata_schema_version") == METADATA_SCHEMA_VERSION:
        result = deepcopy(record)
        for name, first_target in (('photographed_edition', False), ('first_publication', True)):
            if not isinstance(result.get(name), dict):
                if name in result:
                    result.setdefault('metadata_invalid_targets', {})[name] = deepcopy(result[name])
                result[name] = empty_target(first=first_target)
        edition = result.get('photographed_edition') or empty_target()
        first = result.get('first_publication') or empty_target(first=True)
        result['publisher'] = (edition.get('imprint', edition.get('publisher'))
                               if edition.get('imprint_status', edition.get('publisher_status')) == 'evidenced' else None)
        result['published_year'] = first.get('year') if first.get('year_status') == 'evidenced' else None
        result['published_year_scope'] = first.get('scope', 'unknown')
        result['published_year_territory'] = first.get('territory')
        return result
    result = deepcopy(record)
    result.update(publication_projection(record.get("publication_events", []),
                                         legacy=legacy_publication(record)))
    return result


def valid_isbn(raw):
    value = re.sub(r"[\s-]", "", str(raw or "")).upper()
    if len(value) == 13 and value.isdigit():
        return value if sum(int(n) * (1 if i % 2 == 0 else 3)
                            for i, n in enumerate(value)) % 10 == 0 else None
    if len(value) == 10 and value[:9].isdigit() and (value[-1].isdigit() or value[-1] == "X"):
        return value if sum((10 - i) * (10 if n == "X" else int(n))
                            for i, n in enumerate(value)) % 11 == 0 else None
    return None


def reconcile_metadata(payloads, *, legacy=None):
    """Project retained joint responses; stronger cross-page evidence wins.

    Candidate value/evidence records remain available for offline convention
    changes. Conflicting top-ranked titles abstain rather than first-value wins.
    """
    from conventions import convention_metadata, project_authors

    payloads = [payload for payload in payloads if isinstance(payload, dict)]
    events, credits, titles, isbns, conflicts = [], [], [], [], []
    for payload in payloads:
        events.extend(deepcopy(payload.get("publisher_statements", payload.get("publication_events", []))))
        credits.extend(deepcopy(payload.get("credits") or []))
        isbns.extend(deepcopy(payload.get("isbns") or []))
        conflicts.extend(deepcopy(payload.get("conflicts") or []))
        title = payload.get("title")
        if isinstance(title, dict) and isinstance(title.get("value"), str) and title.get("value").strip():
            titles.append(deepcopy(title))
        for conflict in payload.get("conflicts") or []:
            if conflict.get("field") == "title":
                for candidate in conflict.get("candidates") or []:
                    if isinstance(candidate.get("value"), str):
                        titles.append({"value": candidate["value"], "evidence": [candidate]})
    result = publication_projection(events, legacy=legacy)
    grouped = {}
    for candidate in titles:
        key = normalized(candidate.get("value"))
        grouped.setdefault(key, {"value": candidate.get("value"), "evidence": []})["evidence"].extend(candidate.get("evidence") or [])
    ranked = []
    for candidate in grouped.values():
        evidence = [item for item in candidate["evidence"] if item.get("quote")
                    and normalized(candidate["value"]) in normalized(item.get("quote"))]
        pages = {item.get("page_number", item.get("source_page_id")) for item in evidence}
        rank = (len(pages) >= 2, any(item.get("plain_print") is True for item in evidence), bool(evidence))
        ranked.append((rank, candidate))
    strongest = max((rank for rank, _ in ranked), default=None)
    best = [candidate for rank, candidate in ranked if rank == strongest and any(rank)]
    result["title"] = best[0]["value"] if len(best) == 1 else None
    if len(best) > 1:
        conflicts.append({"field": "title", "candidates": best, "reason": "unresolved_visible_title_conflict"})
    result["title_candidates"] = list(grouped.values())
    unique_credits = {}
    for credit in credits:
        if not isinstance(credit, dict) or not credit.get("name"):
            continue
        key = (normalized(credit.get("name")), credit.get("role", "unspecified"))
        if key not in unique_credits:
            unique_credits[key] = {**credit, "evidence": []}
        unique_credits[key]["evidence"].extend(_evidence(credit, field="credits"))
    result["credits"] = list(unique_credits.values())
    supported_credits = [credit for credit in result["credits"] if any(
        normalized(credit.get("name")) in normalized(item.get("quote"))
        for item in credit.get("evidence", []))]
    # Different people are unioned unless the extractor explicitly identified
    # competing readings. Resolve only those candidate sets by visible evidence;
    # unresolved candidates remain archived and absent from the authors view.
    excluded = set()
    for conflict in conflicts:
        if conflict.get('field') not in {'credits', 'authors', 'illustrators'}:
            continue
        names = set()
        for candidate in conflict.get('candidates') or []:
            value = candidate.get('value')
            if isinstance(value, dict):
                value = value.get('name')
            if isinstance(value, str):
                names.add(normalized(value))
        contenders = [credit for credit in supported_credits if normalized(credit.get('name')) in names]
        ranked_credits = []
        for credit in contenders:
            evidence = credit.get('evidence') or []
            pages = {item.get('source_page_id', item.get('page_number')) for item in evidence}
            strong = len(pages) >= 2 or any(item.get('plain_print') is True for item in evidence)
            ranked_credits.append((strong, credit))
        strongest_credit = max((rank for rank, _ in ranked_credits), default=False)
        winners = {normalized(credit.get('name')) for rank, credit in ranked_credits
                   if rank == strongest_credit}
        if len(winners) == 1:
            chosen = next(iter(winners))
            excluded.update(names - {chosen})
            conflict['resolution'] = 'evidence_ranked'
            conflict['selected_normalized_name'] = chosen
        else:
            excluded.update(names)
            conflict['resolution'] = 'unresolved'
    projected_credits = [credit for credit in supported_credits if normalized(credit.get('name')) not in excluded]
    result["authors"] = project_authors(projected_credits)
    result["illustrators"] = [credit["name"] for credit in projected_credits if credit.get("role") == "illustrator"]
    result["isbns"] = isbns
    valid = [valid_isbn(item.get("raw")) for item in isbns if isinstance(item, dict)]
    result["isbn"] = next((item for item in valid if item), None)
    result["metadata_conflicts"].extend(conflicts)
    result["metadata_conventions"] = convention_metadata()
    result["metadata_evidence_payloads"] = deepcopy(payloads)
    result["sources"] = {field: {"source": "photo_joint", "policy_version": PUBLICATION_POLICY_VERSION}
                         for field in ("title", "authors", "illustrators", "publisher", "published_year")
                         if result.get(field)}
    if result.get('title') and best:
        result['sources']['title']['page_number'] = (best[0].get('evidence') or [{}])[0].get('page_number')
    for field, target in (('publisher', 'photographed_edition'), ('published_year', 'first_publication')):
        if field in result['sources']:
            evidence = result.get(target, {}).get('evidence') or [{}]
            result['sources'][field]['page_number'] = evidence[0].get('page_number')
    return result


def fallback_page_candidates(page_results, *, exclude=()):
    """At most two predicted-text pages: cue count, then actual page number."""
    ranked = []
    for page in page_results:
        number = page.get("page_number")
        if type(number) is not int or number in exclude:
            continue
        text = str(page.get("text") or "")
        cues = len(re.findall(r"\b(?:illustrated|pictures|retold|written|author|copyright|published|edition|isbn|title)\b", text, re.I))
        if cues:
            ranked.append((-cues, number, page))
    return [item[2] for item in sorted(ranked, key=lambda item: item[:2])[:2]]


def ocr_evidence_payload(pages, metadata):
    """Reuse exact OCR credit cues and corroborate already retained candidates."""
    result = {"credits": [], "publisher_statements": [], "isbns": [], "conflicts": []}
    candidates = metadata.get("title_candidates") or []
    title_evidence = []
    for page in pages:
        text, number = str(page.get("text") or ""), page.get("page_number")
        for candidate in candidates:
            if normalized(candidate.get("value")) in normalized(text):
                title_evidence.append({"value": candidate.get("value"), "evidence": [
                    {"page_number": number, "quote": text, "provenance": "photo"}]})
        for line in text.splitlines():
            match = re.search(r"\b(?P<role>Retold and Illustrated|Written|Illustrated|Pictures|Retold|Text)\s+by\s+(?P<names>[^.;©]+)", line, re.I)
            if not match:
                continue
            role_text = match.group("role").casefold()
            roles = ["reteller", "illustrator"] if role_text == "retold and illustrated" else [
                "illustrator" if role_text in {"illustrated", "pictures"} else
                "reteller" if role_text == "retold" else "author"]
            for name in re.split(r"\s+and\s+|\s*&\s*", match.group("names")):
                for role in roles:
                    result["credits"].append({"name": name.strip(), "role": role,
                        "page_number": number, "quote": line, "plain_print": True, "provenance": "photo"})
    return [result] + [{"title": candidate} for candidate in title_evidence]


def export_publication_columns(record):
    """Explicitly version export meanings; retain original columns untouched."""
    adapted = adapt_legacy_metadata(record)
    columns = {"publication_export_version": EXPORT_VERSION,
               "legacy_publisher": deepcopy(record.get('publisher')),
               "legacy_published_year": record.get('published_year', record.get('published')),
               "publisher": adapted.get('publisher'),
               "published_year": adapted.get('published_year')}
    for target in ("photographed_edition", "first_publication"):
        for field in TARGET_FIELDS + ("scope",):
            columns[f"{target}.{field}"] = adapted.get(target, {}).get(field)
    columns["published_year_scope"] = adapted.get("published_year_scope")
    columns["published_year_territory"] = adapted.get("published_year_territory")
    return columns
