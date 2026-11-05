"""Build the already-approved dev20 overlay and monitoring cohort, offline only.

Reads a local immutable snapshot/run; creates new evidence artifacts only. Does
not inspect holdout data, call inference, or alter historical runs/reports.
"""

from __future__ import annotations

from text_content.evaluation import EvaluationErrors, EvaluationText

import argparse
from pathlib import Path

from conventions import CONVENTION_VERSION
from .adjudication import apply_v2_overlay
from .exposure import DEV20
from .normalization import tokenize
from .store import atomic_json, digest, read_json

RECOVERIES = {
    "how_the_grinch_stole_christmas": (46, 2),
    "it's_christmas": (30, 1),
    "norman_the_slug_with_the_silly_shell": (24, 4),
    "santa_is_coming_to_devon": (24, 28),
    "the_book_with_no_pictures": (33, 4),
    "the_runaway_pea": (20, 8),
    "wiggling_worms_at_work": (29, 33),
}
BLANK_HASH = "2799b9e051da36c4de2cd83c47f506b2744ac5a6d8b9ca3cecc861c5e1d6ccd9"


def build_evidence(manifest, predictions):
    books = {book.get("id"): book for book in manifest.get("books", [])}
    if not set(books).issubset(DEV20) or not set(RECOVERIES).issubset(books):
        raise ValueError(
            EvaluationErrors.adjudication_import_is_restricted_to_the_frozen_exposed_dev20
        )
    if manifest.get("snapshot_id") != predictions.get("snapshot_id"):
        raise ValueError(EvaluationErrors.snapshot_prediction_identity_mismatch)
    records = []
    for book_id, (number, expected_tokens) in RECOVERIES.items():
        matches = [
            page
            for page in books[book_id].get("pages", [])
            if page.get("page_number") == number
        ]
        if len(matches) != 1:
            raise ValueError(
                EvaluationErrors.expected_adjudicated_page_is_absent_or_ambiguous
            )
        page = matches[0]
        actual = (
            predictions.get("books", {})
            .get(book_id, {})
            .get("pages", {})
            .get(page.get("id"), {})
        )
        text = actual.get("text")
        original = page.get("reference", {}).get("text", {})
        if (
            actual.get("status") != "completed"
            or not isinstance(text, str)
            or len(tokenize(text)) != expected_tokens
            or original.get("value") != ""
        ):
            raise ValueError(
                EvaluationErrors.adjudicated_cached_recovery_no_longer_matches_its_accepted_token
            )
        evidence = {
            "image_sha256": (page.get("image") or {}).get("sha256"),
            "adjudication_source": "planning/wordless_adjudication_2026-11-05.md",
            "method": "photographic_review_reported_in_supplied_adjudication",
            "cached_response_hash": digest(actual),
            "accepted_tokens": expected_tokens,
            "region_kind": "illustration_lettering"
            if book_id in {"how_the_grinch_stole_christmas", "it's_christmas"}
            else "narrative",
        }
        if book_id == "santa_is_coming_to_devon":
            evidence["quotation_discrepancy"] = (
                'The supplied shortened table omits "Everybody, PULL!"; the full cached accepted 28-token output is retained under the supplied 80-token verdict.'
            )
        records.append(
            {
                "book_id": book_id,
                "page_id": page.get("id"),
                "field": "text",
                "original_value_hash": digest(original),
                "verdict": "reference_wrong",
                "status": "verified",
                "corrected_value": text,
                "evidence": evidence,
                "reviewer": "Identity unavailable in supplied adjudication report",
                "reason": "Printed text omitted from pilot transcription",
            }
        )
    book = books["the_book_with_no_pictures"]
    for number in (6, 24):
        page = next(
            (
                page
                for page in book.get("pages", [])
                if page.get("page_number") == number
            ),
            None,
        )
        if page is None or (page.get("image") or {}).get("sha256") != BLANK_HASH:
            raise ValueError(EvaluationErrors.verified_blank_image_identity_mismatch)
        original = page.get("reference", {}).get("contains_story", {})
        if original.get("value") is not True:
            raise ValueError(EvaluationErrors.original_blank_page_story_flag_mismatch)
        records.append(
            {
                "book_id": book.get("id"),
                "page_id": page.get("id"),
                "field": "contains_story",
                "original_value_hash": digest(original),
                "verdict": "reference_wrong",
                "status": "verified",
                "corrected_value": False,
                "reviewer": "Chris",
                "reason": "Genuinely blank page received the imported all-pages story default",
                "evidence": {
                    "image_sha256": BLANK_HASH,
                    "page_type": "blank",
                    "adjudication_source": "planning/pipeline_improvement_design_2026-11-05.md#7",
                    "method": "reported_chris_photographic_review"
                    if number == 6
                    else "identical_image_hash_transfer_from_page_6",
                    "independent_image_count": 1,
                    "transferred_from": None
                    if number == 6
                    else "the_book_with_no_pictures_6",
                },
            }
        )
    overlay = {
        "schema_version": "adjudication-v2",
        "convention_version": CONVENTION_VERSION,
        "snapshot_id": manifest.get("snapshot_id"),
        "records": records,
    }
    apply_v2_overlay(manifest, overlay, convention_version=CONVENTION_VERSION)
    monitoring = []
    for book_id, book in books.items():
        for page in book.get("pages", []):
            reference = page.get("reference", {}).get("text", {})
            actual = (
                predictions.get("books", {})
                .get(book_id, {})
                .get("pages", {})
                .get(page.get("id"), {})
            )
            if (
                reference.get("status") in {"observed", "verified"}
                and reference.get("value") == ""
                and actual.get("status") == "completed"
                and not actual.get("text")
            ):
                monitoring.append(
                    {
                        "book_id": book_id,
                        "page_id": page.get("id"),
                        "image_sha256": (page.get("image") or {}).get("sha256"),
                        "baseline_response_hash": digest(actual),
                        "baseline_response": actual,
                        "reference_hash": digest(reference),
                        "verified_blank": book_id == "the_book_with_no_pictures"
                        and page.get("page_number") in {6, 24},
                    }
                )
    if len(monitoring) != 101:
        raise ValueError(
            EvaluationErrors.frozen_empty_empty_monitoring_cohort_must_contain_101_pages
        )
    cohort = {
        "version": "empty-reference-monitoring-v1",
        "snapshot_id": manifest.get("snapshot_id"),
        "records": monitoring,
        "original_empty_reference_pages": 108,
        "verified_recovery_pages": 7,
        "confirmed_hallucinations": 0,
        "independent_verified_blank_images": 1,
        "qualification": EvaluationText.monitoring_cohort,
    }
    return overlay, cohort


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    overlay, cohort = build_evidence(
        read_json(args.snapshot / "manifest.json"),
        read_json(args.run / "predictions.json"),
    )
    for name, value in (
        ("adjudications.json", overlay),
        ("empty_reference_cohort.json", cohort),
    ):
        path = args.out / name
        if path.exists() and read_json(path) != value:
            raise ValueError(
                EvaluationErrors.evidence_artifacts_are_append_only_choose_a_new_output
            )
        atomic_json(path, value)


if __name__ == "__main__":
    main()
