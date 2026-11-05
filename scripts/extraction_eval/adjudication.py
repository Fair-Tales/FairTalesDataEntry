"""Hash-checked v2 adjudication overlays; original snapshot records are immutable."""

from __future__ import annotations

from text_content.evaluation import EvaluationErrors

from copy import deepcopy

from .normalization import normalize_text
from .store import digest

ADJUDICATION_VERSION = "adjudication-v2"
VERDICTS = {
    "reference_wrong",
    "reference_confirmed",
    "prediction_wrong",
    "both_wrong",
    "ambiguous",
    "convention_pending",
}


def apply_v2_overlay(manifest, overlay, *, convention_version=None):
    if convention_version is None:
        from conventions import CONVENTION_VERSION

        convention_version = CONVENTION_VERSION
    if overlay.get("schema_version") != ADJUDICATION_VERSION:
        raise ValueError(EvaluationErrors.unknown_adjudication_schema_version)
    if overlay.get("snapshot_id") != manifest.get("snapshot_id") or not manifest.get(
        "snapshot_id"
    ):
        raise ValueError(
            EvaluationErrors.adjudication_snapshot_id_does_not_match_the_frozen_snapshot
        )
    if not overlay.get("convention_version") or (
        convention_version is not None
        and convention_version != overlay.get("convention_version")
    ):
        raise ValueError(EvaluationErrors.adjudication_convention_version_mismatch)
    records = overlay.get("records")
    if not isinstance(records, list):
        raise ValueError(EvaluationErrors.adjudication_requires_records)
    revised = deepcopy(manifest)
    books = {book.get("id"): book for book in revised.get("books", [])}
    seen = set()
    history = list(revised.get("correction_history", []))
    for change in records:
        identity = (
            change.get("book_id"),
            change.get("page_id"),
            change.get("field"),
            change.get("entity_id"),
        )
        if identity in seen:
            raise ValueError(
                EvaluationErrors.conflicting_or_duplicate_adjudication_records
            )
        seen.add(identity)
        verdict, status = change.get("verdict"), change.get("status")
        if verdict not in VERDICTS or status not in {
            "verified",
            "ambiguous",
            "convention_pending",
        }:
            raise ValueError(EvaluationErrors.invalid_adjudication_verdict_status)
        if (
            verdict in {"reference_wrong", "both_wrong", "reference_confirmed"}
            and status != "verified"
        ):
            raise ValueError(
                EvaluationErrors.effective_reference_corrections_require_verified_status
            )
        evidence = change.get("evidence", {})
        if (
            not change.get("reviewer")
            or not change.get("reason")
            or not evidence.get("adjudication_source")
            or not evidence.get("method")
        ):
            raise ValueError(
                EvaluationErrors.adjudication_requires_reviewer_reason_and_evidence_provenance
            )
        book = books.get(change.get("book_id"))
        if book is None:
            raise ValueError(EvaluationErrors.adjudication_refers_to_unknown_book)
        field = change.get("field", "")
        page = None
        if field in {"text", "contains_story"}:
            page = next(
                (
                    row
                    for row in book.get("pages", [])
                    if row.get("id") == change.get("page_id")
                ),
                None,
            )
            if page is None:
                raise ValueError(EvaluationErrors.adjudication_refers_to_unknown_page)
            original_hash = (page.get("image") or {}).get("sha256")
            if not original_hash or original_hash != evidence.get("image_sha256"):
                raise ValueError(EvaluationErrors.adjudication_image_hash_mismatch)
            target = page.setdefault("reference", {})
        elif field == "characters" or field == "aliases":
            target = book.setdefault("reference", {})
            field = "characters"
        elif field.startswith("metadata."):
            target = book.setdefault("reference", {}).setdefault("metadata", {})
            field = field.removeprefix("metadata.")
            if field not in {
                "title",
                "authors",
                "illustrators",
                "publisher",
                "published_year",
                "photographed_edition",
                "first_publication",
            }:
                raise ValueError(EvaluationErrors.unknown_metadata_adjudication_field)
        else:
            raise ValueError(EvaluationErrors.unknown_adjudication_field)
        original = deepcopy(target.get(field, {}))
        if not change.get("original_value_hash") or digest(original) != change.get(
            "original_value_hash"
        ):
            raise ValueError(
                EvaluationErrors.adjudication_original_value_provenance_hash_mismatch
            )
        record = deepcopy(original)
        correction = change.get("corrected_value")
        if verdict in {"reference_wrong", "both_wrong"}:
            if "corrected_value" not in change:
                raise ValueError(
                    EvaluationErrors.resolved_adjudication_requires_corrected_value
                )
            if change.get("field") == "aliases":
                if (
                    not isinstance(correction, str)
                    or not correction.strip()
                    or not change.get("entity_id")
                ):
                    raise ValueError(
                        EvaluationErrors.alias_correction_requires_an_entity_id_and_surface
                    )
                entities = deepcopy(record.get("value", []))
                matches = [
                    entity
                    for entity in entities
                    if entity.get("id") == change.get("entity_id")
                ]
                if len(matches) != 1:
                    raise ValueError(
                        EvaluationErrors.alias_correction_requires_a_stable_unique_reference_entity_id
                    )
                aliases = matches[0].setdefault("aliases", [])
                if correction not in aliases:
                    aliases.append(correction)
                correction = entities
            if field == "text" and not isinstance(correction, str):
                raise ValueError(EvaluationErrors.text_correction_must_be_a_string)
            if field == "contains_story" and type(correction) is not bool:
                raise ValueError(EvaluationErrors.story_correction_must_be_boolean)
            if field in {"title", "publisher"} and not isinstance(correction, str):
                raise ValueError(
                    EvaluationErrors.bibliographic_string_correction_must_be_a_string
                )
            if field == "published_year" and (
                type(correction) is not int or correction <= 0
            ):
                raise ValueError(
                    EvaluationErrors.publication_year_correction_must_be_a_positive_integer
                )
            if field in {"authors", "illustrators"} and (
                not isinstance(correction, list)
                or any(not isinstance(item, (str, dict)) for item in correction)
            ):
                raise ValueError(
                    EvaluationErrors.credit_correction_must_be_a_list_of_names
                )
            if field == "characters" and (
                not isinstance(correction, list)
                or any(not isinstance(item, dict) for item in correction)
            ):
                raise ValueError(
                    EvaluationErrors.character_correction_must_be_entity_records
                )
            if field in {
                "photographed_edition",
                "first_publication",
            } and not isinstance(correction, dict):
                raise ValueError(
                    EvaluationErrors.publication_correction_must_retain_scoped_target
                )
            record.update(value=correction, status="verified")
            if field == "text":
                record["kind"] = "text" if normalize_text(correction) else "wordless"
            # One corrected alias never makes the rest of the reference complete.
            if field == "characters":
                record["complete"] = original.get("complete", False)
            target[field] = record
        elif verdict == "reference_confirmed":
            if "corrected_value" in change and correction != original.get("value"):
                raise ValueError(
                    EvaluationErrors.reference_confirmed_verdict_cannot_change_the_original_value
                )
            record["status"] = "verified"
            target[field] = record
        elif verdict in {"ambiguous", "convention_pending"}:
            record["status"] = "ambiguous"
            target[field] = record
        # prediction_wrong supplies no automatic score and never edits reference values.
        if verdict != "prediction_wrong":
            record.update(
                adjudication_version=ADJUDICATION_VERSION,
                adjudication_verdict=verdict,
                adjudication_evidence=deepcopy(evidence),
                original_reference=original,
                provenance={
                    "original": original.get("provenance"),
                    "adjudication": evidence,
                },
            )
        history.append(
            {
                **deepcopy(change),
                "overlay_hash": digest(overlay),
                "original_record": original,
            }
        )
    revised["correction_history"] = history
    revised["adjudication"] = {
        "version": ADJUDICATION_VERSION,
        "snapshot_id": manifest.get("snapshot_id"),
        "convention_version": overlay.get("convention_version"),
        "overlay_hash": digest(overlay),
    }
    return revised
