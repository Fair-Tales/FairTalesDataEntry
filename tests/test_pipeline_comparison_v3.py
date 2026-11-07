"""Offline field-aware comparison, scoped bibliography and exposure contracts."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from conventions import CONVENTION_VERSION
from scripts.extraction_eval.adjudication import apply_v2_overlay
from scripts.extraction_eval.comparison import (
    METRIC_REGISTRY,
    book_comparison_record,
    compare_metrics,
    comparison_protocol,
    count,
)
from scripts.extraction_eval.exposure import EXPOSED_IDS
from scripts.extraction_eval.publication_metrics import score_publication
from scripts.extraction_eval.sampling import select_books, select_holdout_books
from scripts.extraction_eval.scoring import score_book
from scripts.extraction_eval.store import digest


def reference(value):
    return {
        "value": value,
        "status": "observed",
        "provenance": "student",
        "complete": False,
    }


def fixture():
    book = {
        "id": "fixture",
        "title": "Fixture",
        "pages": [
            {
                "id": "p1",
                "page_number": 1,
                "image": {"sha256": "photo"},
                "reference": {
                    "text": reference("Rabbit met bunny."),
                    "contains_story": reference(True),
                },
            }
        ],
        "reference": {
            "characters": reference(
                [{"id": "rabbit", "name": "Rabbit", "aliases": ["bunny"]}]
            ),
            "metadata": {"title": reference("Fixture")},
        },
    }
    prediction = {
        "pages": {
            "p1": {
                "status": "completed",
                "text": "Rabbit met bunny.",
                "contains_story": True,
            }
        },
        "characters": {
            "status": "completed",
            "value": [{"name": "Rabbit", "aliases": []}],
        },
        "metadata": {"status": "completed", "value": {"title": "Fixture"}},
    }
    manifest = {"snapshot_id": "fixture", "books": [book]}
    return manifest, book, prediction


def row(book, prediction, *, manifest=None):
    return book_comparison_record(
        score_book(book, prediction),
        protocol_identity=comparison_protocol({}, manifest or {}),
    )


def test_character_only_candidate_is_visible_and_ocr_cannot_substitute():
    manifest, book, before = fixture()
    after = deepcopy(before)
    after["characters"]["value"][0]["aliases"] = ["bunny"]
    result = compare_metrics([row(book, before)], [row(book, after)], repeats=25)
    assert result["metrics"]["ocr_f1"]["improvement"] == 0
    assert result["metrics"]["alias_recall"]["improvement"] == 1
    assert result["metrics"]["alias_recall"]["point_gate_passed"] is True
    assert result["metrics"]["M1_groundedness"]["status"] == "undefined"
    assert set(result["metrics"]) == set(METRIC_REGISTRY)
    assert all("precision" not in endpoint for endpoint in result["metrics"])
    reverse = compare_metrics([row(book, after)], [row(book, before)], repeats=25)
    assert reverse["metrics"]["alias_recall"]["point_gate_passed"] is False


def test_ratio_comparison_uses_counts_not_mean_of_book_ratios():
    _, book, prediction = fixture()
    a = row(book, prediction)
    a.update(
        id="a",
        role="probability",
        inclusion_probability=0.5,
        stratum="one",
        holdout_group="a",
    )
    b = deepcopy(a)
    b.update(id="b", holdout_group="b")
    a["metrics"]["alias_recall"] = count(0, 1)
    b["metrics"]["alias_recall"] = count(100, 100)
    after = deepcopy([a, b])
    after[0]["metrics"]["alias_recall"] = count(1, 1)
    score = compare_metrics([a, b], after, repeats=100)["metrics"]["alias_recall"]
    assert score["baseline"] == pytest.approx(100 / 101)
    assert score["improvement"] == pytest.approx(1 / 101)
    assert score["interval_95"] is not None
    assert score["finite_population_interval_95"] is not None


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "convention_version",
        "matcher_version",
        "normalization_version",
        "overlay_hash",
    ],
)
def test_comparison_rejects_mixed_protocols(field):
    _, book, prediction = fixture()
    before = row(book, prediction)
    after = deepcopy(before)
    after["protocol_identity"][field] = "different"
    with pytest.raises(ValueError, match="protocol"):
        compare_metrics([before], [after], repeats=2)


def test_comparison_never_silently_drops_undefined_or_failed_metrics():
    _, book, prediction = fixture()
    before = row(book, prediction)
    failed = deepcopy(prediction)
    failed["pages"]["p1"] = {"status": "failed"}
    failed["characters"] = {"status": "failed"}
    after = row(book, failed)
    result = compare_metrics([before], [after], repeats=2)
    assert result["metrics"]["character_recall"]["candidate"] == 0
    assert result["metrics"]["ocr_recall"]["candidate"] == 0
    assert result["metrics"]["page_output_coverage"]["point_gate_passed"] is False
    assert result["metrics"]["false_merge_rate"]["status"] == "undefined"
    assert result["metrics"]["false_merge_rate"]["point_gate_passed"] is None
    after["metrics"].pop("alias_recall")
    with pytest.raises(ValueError, match="complete named metric registry"):
        compare_metrics([before], [after], repeats=2)


def test_fixed_occurrence_denominator_retained_when_alias_is_dropped():
    _, book, prediction = fixture()
    prediction["characters"]["value"][0]["aliases"] = ["bunny"]
    after = deepcopy(prediction)
    after["characters"]["value"][0]["aliases"] = []
    result = compare_metrics([row(book, prediction)], [row(book, after)], repeats=2)
    metric = result["metrics"]["M4_fixed_retrieval"]
    assert metric["baseline"] == 1
    assert metric["candidate"] == 0.5
    assert metric["per_book"][0]["candidate"]["denominator"] == 2
    after["pages"]["p1"]["text"] = "changed"
    changed = compare_metrics([row(book, prediction)], [row(book, after)], repeats=2)
    assert changed["metrics"]["M4_fixed_retrieval"]["status"] == "undefined"


def correction(manifest, *, field="text", value="Recovered wording"):
    book = manifest["books"][0]
    source = book["pages"][0]["reference"][field]
    return {
        "schema_version": "adjudication-v2",
        "convention_version": CONVENTION_VERSION,
        "snapshot_id": manifest["snapshot_id"],
        "records": [
            {
                "book_id": book["id"],
                "page_id": "p1",
                "field": field,
                "original_value_hash": digest(source),
                "verdict": "reference_wrong",
                "status": "verified",
                "corrected_value": value,
                "reviewer": "reported reviewer",
                "reason": "Omitted printed wording",
                "evidence": {
                    "image_sha256": "photo",
                    "adjudication_source": "supplied report",
                    "method": "reported_review",
                },
            }
        ],
    }


def test_overlay_hashes_immutability_partial_credit_and_symmetry():
    manifest, book, prediction = fixture()
    book["pages"][0]["reference"]["text"] = reference("")
    overlay = correction(manifest)
    corrected = apply_v2_overlay(
        manifest, overlay, convention_version=CONVENTION_VERSION
    )
    assert manifest["books"][0]["pages"][0]["reference"]["text"]["value"] == ""
    before = deepcopy(prediction)
    before["pages"]["p1"]["text"] = "Recovered wording"
    after = deepcopy(prediction)
    after["pages"]["p1"]["text"] = "Recovered"
    revised = corrected["books"][0]
    result = compare_metrics(
        [row(revised, before, manifest=corrected)],
        [row(revised, after, manifest=corrected)],
        repeats=2,
    )
    assert result["metrics"]["ocr_recall"]["candidate"] == 0.5
    assert result["metrics"]["recovery_regression"]["point_gate_passed"] is False
    assert (
        score_book(revised, after)["reference_corrections"]["recovered_correct_tokens"]
        == 1
    )
    assert len(corrected["correction_history"]) == 1
    with pytest.raises(ValueError, match="reference hashes"):
        compare_metrics(
            [row(book, before)], [row(revised, after, manifest=corrected)], repeats=2
        )
    repair = compare_metrics(
        [row(book, before)],
        [row(revised, before, manifest=corrected)],
        repeats=2,
        measurement_repair=True,
    )
    assert repair["kind"] == "fixed_output_measurement_repair"


@pytest.mark.parametrize(
    "mutation", ["original", "image", "convention", "duplicate", "type"]
)
def test_stale_conflicting_or_invalid_overlay_is_rejected(mutation):
    manifest, _, _ = fixture()
    overlay = correction(manifest)
    if mutation == "original":
        overlay["records"][0]["original_value_hash"] = "stale"
    if mutation == "image":
        overlay["records"][0]["evidence"]["image_sha256"] = "stale"
    if mutation == "convention":
        overlay["convention_version"] = "different"
    if mutation == "duplicate":
        overlay["records"].append(deepcopy(overlay["records"][0]))
    if mutation == "type":
        overlay["records"][0]["corrected_value"] = False
    with pytest.raises(ValueError):
        apply_v2_overlay(manifest, overlay, convention_version=CONVENTION_VERSION)


def test_corrected_alias_does_not_make_reference_complete():
    manifest, book, _ = fixture()
    overlay = correction(manifest)
    change = overlay["records"][0]
    change.update(
        field="aliases",
        entity_id="rabbit",
        corrected_value="young rabbit",
        original_value_hash=digest(book["reference"]["characters"]),
    )
    revised = apply_v2_overlay(manifest, overlay)
    reference_characters = revised["books"][0]["reference"]["characters"]
    assert reference_characters["complete"] is False
    assert reference_characters["value"][0]["aliases"] == ["bunny", "young rabbit"]


def publication(
    publisher="Printed Imprint", year=2008, scope="work_first", territory=None
):
    return {
        "publisher": publisher,
        "imprint": publisher,
        "publishing_house": None,
        "year": year,
        "scope": scope,
        "territory": territory,
        "publisher_status": "evidenced",
        "imprint_status": "evidenced",
        "year_status": "evidenced",
        "publishing_house_status": "unresolved",
        "evidence": [
            {
                "quote": "Printed publication statement",
                "source_page_id": "p1",
                "image_hash": "photo",
                "provenance": "photo",
                "field": "publication_event",
            }
        ],
    }


def test_publication_unknown_legacy_is_not_gold_and_scope_is_mandatory():
    assert (
        score_publication({"publisher": reference("Old")}, {"publisher": "New"})[
            "targets"
        ]["photographed_edition"]["publisher"]["reference"]
        == 0
    )
    expected = {
        "first_publication": publication(
            year=1973, scope="territorial_first", territory="UK"
        )
    }
    actual = {"first_publication": publication(year=1973, scope="work_first")}
    scores = score_publication(expected, actual)["targets"]["first_publication"]
    assert scores["year"]["reference"] == 1
    assert scores["year"]["matched"] == 0
    assert scores["year"]["known_fact_conflict"] is True
    failed = score_publication(expected, expected, status="failed")["targets"][
        "first_publication"
    ]
    assert failed["year"]["reference"] == 1 and failed["year"]["matched"] == 0


def test_publisher_literal_agreement_and_reviewed_scoped_equivalence_separate():
    expected = {"photographed_edition": publication("Nosy Crow")}
    actual = {"photographed_edition": publication("Nosy Crow Ltd")}
    protocol = {
        "publication_equivalences": {
            "version": "review-1",
            "reviewed": True,
            "records": [
                {
                    "target": "photographed_edition",
                    "scope": "edition",
                    "territory": None,
                    "names": ["Nosy Crow", "Nosy Crow Ltd"],
                    "evidence": "existing review",
                }
            ],
        }
    }
    scores = score_publication(expected, actual, protocol=protocol)["targets"][
        "photographed_edition"
    ]
    assert scores["publisher"]["matched"] == 0
    assert scores["publisher_equivalent"]["matched"] == 1
    assert scores["imprint"]["matched"] == 0


def inventory_book(name, *, aliases=False, group=None):
    _, book, _ = fixture()
    book["id"] = name
    book["title"] = name
    book["pages"][0]["image"]["sha256"] = name
    if not aliases:
        book["reference"]["characters"]["value"][0]["aliases"] = []
    if group:
        book["holdout_group"] = group
    return book


def test_all_34_exposures_and_linked_groups_quarantined():
    assert len(EXPOSED_IDS) == 34
    assert {"the_gruffalo", "the_gruffalo's_child", "a_letter_to_santa"} <= EXPOSED_IDS
    manifest = {
        "snapshot_id": "fixture",
        "books": [
            inventory_book("the_gruffalo's_child", group="linked"),
            inventory_book("unseen_linked", group="linked"),
            inventory_book("unseen"),
        ],
    }
    result = select_books(manifest, books=["unseen"])
    assert result["held_out_book_ids"] == []
    assert {row["id"] for row in result["held_out_exclusions"]} == {
        "the_gruffalo's_child",
        "unseen_linked",
    }


def test_holdout_two_alias_strata_freezes_weights_and_reference_hashes():
    manifest = {
        "snapshot_id": "fixture",
        "books": [
            inventory_book("a", aliases=True),
            inventory_book("b", aliases=True),
            inventory_book("c"),
            inventory_book("the_gruffalo", aliases=True),
        ],
    }
    result = select_holdout_books(manifest, alias_positive=1, alias_zero=1)
    assert result == select_holdout_books(manifest, alias_positive=1, alias_zero=1)
    assert result["eligible_counts"] == {"alias_positive": 2, "alias_zero": 1}
    assert "the_gruffalo" not in result["book_ids"]
    assert all(row.get("reference_hash") for row in result["selected"])
    assert sorted(row["inclusion_probability"] for row in result["selected"]) == [
        0.5,
        1,
    ]


def test_locked_development_evidence_contains_seven_recoveries_and_blank_transfer():
    root = (
        Path(__file__).resolve().parents[1]
        / "scripts/extraction_eval/evidence/dev20-v1"
    )
    overlay = json.loads((root / "adjudications.json").read_text())
    cohort = json.loads((root / "empty_reference_cohort.json").read_text())
    text_records = [row for row in overlay["records"] if row["field"] == "text"]
    assert len(text_records) == 7
    assert sum(row["evidence"]["accepted_tokens"] for row in text_records) == 80
    santa = next(
        row for row in text_records if row["book_id"] == "santa_is_coming_to_devon"
    )
    assert "Everybody, PULL!" in santa["corrected_value"]
    assert len(cohort["records"]) == 101
    assert sum(row["verified_blank"] for row in cohort["records"]) == 2
    assert cohort["independent_verified_blank_images"] == 1


def test_lookup_failure_stays_in_operational_comparison_denominator():
    _, book, prediction = fixture()
    book["reference"]["metadata"]["authors"] = reference(["Known Author"])
    prediction["metadata"]["value"]["lookup_failed_fields"] = ["authors"]
    score = score_book(book, prediction)
    assert score["metadata"]["authors"]["exclusion"] == "lookup_failed"
    operational = book_comparison_record(
        score, protocol_identity=comparison_protocol({}, {})
    )
    metric = operational["metrics"]["metadata.authors"]
    assert metric["denominator"] == 1 and metric["numerator"] == 0


def test_character_metrics_use_saved_projected_inputs_and_preserve_archival_grounding():
    _, book, prediction = fixture()
    prediction["characters"]["value"][0]["aliases"] = ["bunny"]
    prediction["pages"]["p1"].update(
        text="Rabbit\n\nbunny",
        regions=[
            {"kind": "narrative", "text": "Rabbit"},
            {"kind": "paratext", "text": "bunny"},
        ],
    )
    score = score_book(book, prediction)
    assert score["alias_foundation"]["M1"]["lexical_grounding"] == 1
    assert len(score["alias_foundation"]["M4"]["occurrences"]) == 1
    prediction["characters"]["input_pages"] = [
        {"page_id": "p1", "page_number": 1, "text": "bunny"}
    ]
    changed = score_book(book, prediction)
    assert changed["character_input_hash"] != score["character_input_hash"]
    assert changed["alias_foundation"]["M4"]["occurrences"][0]["surface"] == "bunny"


def test_empty_cohort_monitoring_does_not_call_unreviewed_output_hallucination():
    from scripts.extraction_eval.monitoring import monitor_empty_reference

    manifest, book, prediction = fixture()
    cohort = {
        "snapshot_id": manifest["snapshot_id"],
        "records": [
            {
                "book_id": book["id"],
                "page_id": "p1",
                "image_sha256": "photo",
                "verified_blank": False,
            }
        ],
    }
    result = monitor_empty_reference(
        manifest, {"books": {book["id"]: prediction}}, cohort
    )
    assert result["unreviewed_disagreement_tokens"] == 3
    assert result["confirmed_hallucinated_tokens"] == 0
    cohort["records"][0]["image_sha256"] = "stale"
    with pytest.raises(ValueError, match="identity"):
        monitor_empty_reference(manifest, {"books": {}}, cohort)


def test_cli_comparison_routes_the_same_corrected_manifest_to_both_runs(tmp_path):
    from argparse import Namespace
    from scripts.extraction_eval.cli import _score

    manifest, book, prediction = fixture()
    manifest.update(complete=True, source_consistent=True)
    book["pages"][0]["reference"]["text"] = reference("")
    overlay = correction(manifest)
    before, after = deepcopy(prediction), deepcopy(prediction)
    before["pages"]["p1"]["text"] = "Recovered wording"
    after["pages"]["p1"]["text"] = ""
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text(json.dumps(manifest))
    selection = {
        "book_ids": [book["id"]],
        "selected": [
            {
                "id": book["id"],
                "role": "explicit",
                "stratum": "all",
                "inclusion_probability": None,
            }
        ],
    }
    for name, predicted in (("baseline", before), ("candidate", after)):
        target = tmp_path / name
        target.mkdir()
        (target / "run.json").write_text(
            json.dumps(
                {
                    "run_id": name,
                    "contract": {
                        "snapshot_id": manifest["snapshot_id"],
                        "snapshot_hash": digest(manifest),
                        "selection": selection,
                    },
                }
            )
        )
        (target / "selection.json").write_text(json.dumps(selection))
        (target / "predictions.json").write_text(
            json.dumps(
                {
                    "run_id": name,
                    "snapshot_id": manifest["snapshot_id"],
                    "books": {book["id"]: predicted},
                }
            )
        )
    (tmp_path / "overlay.json").write_text(json.dumps(overlay))
    (tmp_path / "protocol.json").write_text(
        json.dumps({"convention_version": CONVENTION_VERSION})
    )
    args = Namespace(
        original_experiment=True,
        snapshot=snapshot,
        run=tmp_path / "baseline",
        protocol=tmp_path / "protocol.json",
        adjudications=tmp_path / "overlay.json",
        equivalences=None,
        out=tmp_path / "new_reports",
        compare_run=tmp_path / "candidate",
    )
    assert _score(args) == 0
    result = json.loads((args.out / "comparison.json").read_text())
    corrected = result["corrected_to_corrected"]["metrics"]["ocr_recall"]
    assert corrected["baseline"] == 1 and corrected["candidate"] == 0
    assert result["raw_to_raw"]["metrics"]["ocr_recall"]["status"] == "undefined"
    assert (
        result["fixed_baseline_measurement_repair"]["kind"]
        == "fixed_output_measurement_repair"
    )
    assert not (tmp_path / "baseline" / "reports").exists()


def test_dev20_probability_one_inventory_is_descriptive_not_population():
    _, book, prediction = fixture()
    before = row(book, prediction)
    before.update(id="the_runaway_pea", role="probability", inclusion_probability=1)
    result = compare_metrics([before], [deepcopy(before)], repeats=2)
    assert result["sampling"] == "descriptive_only"
    assert result["metrics"]["ocr_recall"]["finite_population_interval_95"] is None
    assert result["metrics"]["ocr_recall"]["interval_95"] == [0, 0]


def test_verified_blank_guard_preserves_legacy_bool_projection_and_rejects_conflicts():
    manifest, book, prediction = fixture()
    book["pages"][0]["reference"]["text"] = reference("")
    overlay = correction(manifest, field="contains_story", value=False)
    overlay["records"][0]["evidence"]["page_type"] = "blank"
    revised = apply_v2_overlay(manifest, overlay)["books"][0]
    prediction["pages"]["p1"].update(text="", contains_story=False)
    assert score_book(revised, prediction)["regressions"]["blank_regression"] == {
        "eligible": 1,
        "errors": 0,
    }
    prediction["pages"]["p1"]["has_text"] = True
    assert (
        score_book(revised, prediction)["regressions"]["blank_regression"]["errors"]
        == 1
    )


def test_reviewed_publisher_equivalence_cannot_admit_unlisted_normalized_forms():
    expected = {"photographed_edition": publication("Nosy Crow")}
    actual = {"photographed_edition": publication("NOSY CROW LTD.")}
    protocol = {
        "publication_equivalences": {
            "version": "review-1",
            "reviewed": True,
            "records": [
                {
                    "target": "photographed_edition",
                    "scope": "edition",
                    "territory": None,
                    "names": ["Nosy Crow", "Nosy Crow Ltd"],
                    "evidence": "existing review",
                }
            ],
        }
    }
    score = score_publication(expected, actual, protocol=protocol)
    assert (
        score["targets"]["photographed_edition"]["publisher_equivalent"]["matched"] == 0
    )
    assert score["equivalence_normalization"] == "literal-reviewed-names-v1"


def test_optional_stage_failures_remain_visible_with_usable_initial_outputs(tmp_path):
    from scripts.extraction_eval.reporting import write_reports

    manifest, book, prediction = fixture()
    after = deepcopy(prediction)
    after["story_context"] = {
        "status": "failed",
        "fallback": True,
        "error_type": "context_request_limit",
    }
    after["pages"]["p1"]["classification_source"] = "provisional_fallback"
    after["metadata_reconciliation"] = {
        "status": "failed",
        "error": "late extraction failed",
    }
    result = compare_metrics([row(book, prediction)], [row(book, after)], repeats=2)
    for name in (
        "context_failures_per_book",
        "context_fallback_rate",
        "metadata_reconciliation_failures_per_book",
    ):
        assert result["metrics"][name]["baseline"] == 0
        assert result["metrics"][name]["candidate"] == 1
        assert result["metrics"][name]["point_gate_passed"] is False
    assert result["metrics"]["metadata_output_coverage"]["candidate"] == 1
    assert result["metrics"]["metadata.title"]["candidate"] == 1
    assert result["metrics"]["context_output_coverage"]["baseline"] is None
    assert result["metrics"]["context_output_coverage"]["candidate"] == 0
    assert (
        result["metrics"]["metadata_reconciliation_output_coverage"]["candidate"] == 0
    )
    assert result["metrics"]["context_fallback_story_agreement"]["candidate"] == 1
    summary = write_reports(manifest, {"books": {book["id"]: after}}, tmp_path)
    assert summary["optional_stages"]["story_context"]["failed"] == 1
    assert summary["optional_stages"]["metadata_reconciliation"]["failed"] == 1
    assert summary["story_context_fallback"]["end_to_end_accuracy"] == 1
    assert (
        "classification_source" in (tmp_path / "pages.csv").read_text().splitlines()[0]
    )
    assert "optional_stages" in (tmp_path / "books.csv").read_text().splitlines()[0]


def test_optional_stages_absent_are_not_requested_and_context_abstentions_are_counted():
    from scripts.extraction_eval.scoring import aggregate

    _, book, prediction = fixture()
    legacy = score_book(book, prediction)
    assert legacy["optional_stages"]["story_context"]["intended"] is False
    assert (
        legacy["optional_stages"]["metadata_reconciliation"]["status"]
        == "not_requested"
    )
    after = deepcopy(prediction)
    after["story_context"] = {
        "status": "completed",
        "fallback": False,
        "pages": [
            {"page_number": 1, "contains_story": None, "decision": "review_required"}
        ],
        "unresolved_pages": 1,
    }
    after["pages"]["p1"].update(
        contains_story=None, classification_source="book_context"
    )
    after["metadata_reconciliation"] = {"status": "pending"}
    result = aggregate([legacy, score_book(book, after)])
    context = result["optional_stages"]["story_context"]
    assert context["all_books"] == 2 and context["intended"] == 1
    assert context["completed"] == 1 and context["unresolved"] == 1
    assert context["unresolved_pages"] == 1 and context["fallback"] == 0
    assert result["optional_stages"]["metadata_reconciliation"]["unrun"] == 1
    assert result["incomplete"] is True
