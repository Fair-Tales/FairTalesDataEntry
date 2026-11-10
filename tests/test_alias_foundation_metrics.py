"""Offline output-consistency diagnostics and frozen dev20 census regressions."""

from copy import deepcopy

import pytest

from conftest import load_dev20_predictions
from alias_pipeline import dictionary_diagnostics
from scripts.extraction_eval.alias_metrics import (
    HISTORICAL_DELETION_VERSION,
    HISTORICAL_SEPARATOR_VERSION,
    aggregate_alias_foundation,
    grounding_support,
    grounding_tokens,
    score_alias_foundation,
)
from scripts.extraction_eval.reporting import write_reports
from scripts.extraction_eval.scoring import aggregate, match_characters, score_book, score_characters


def prediction(characters, pages=None, status="completed"):
    return {"characters": {"status": status, "value": characters},
            "pages": pages if pages is not None else {
                "p1": {"status": "completed", "text": "the little whale", "contains_story": True}}}


def foundation(characters, pages=None, **kwargs):
    return score_alias_foundation({"id": "book"}, prediction(characters, pages, **kwargs))


def aggregate_foundations(*records):
    return aggregate_alias_foundation([{"alias_foundation": record} for record in records])


@pytest.mark.parametrize("surface,text,tier", [
    ("CAFÉ’S mum", "Café's mum waved.", "exact"),
    ("the swan", "The swans saw one swan.", "determiner_stripped"),
    ("the swan", "the swans", "ungrounded"),
    ("us", "just a bus", "ungrounded"),
    ("their mum", "his mum", "determiner_stripped"),
    ("Marlon's mum", "Billy's mum", "ungrounded"),
    ("[?]", "[?]", "ungrounded"),
    ("", "", "ungrounded"),
    ("the", "a rabbit", "ungrounded"),
])
def test_m1_grounding_boundaries_and_independent_permissive_rule(surface, text, tier):
    assert grounding_support(surface, [("page", text)])["tier"] == tier


def test_m1_does_not_cross_page_boundaries_or_remove_more_than_one_determiner():
    assert grounding_support("little whale", [("p1", "little"), ("p2", "whale")])["tier"] == "ungrounded"
    assert grounding_tokens("his the mum", strip_determiner=True) == ["the", "mum"]
    assert grounding_tokens("Marlon’s mum", strip_determiner=True) == ["marlon's", "mum"]


def test_possessive_evidence_is_preserved_by_mentions_but_m1_is_permissive():
    pages = [{"page_id": "p1", "text": "her mum waved"}]
    result = dictionary_diagnostics([{"id": "mum", "name": "Mother", "aliases": ["his mum"]}], pages)
    assert result["optional_articles"]["found"] == 0
    assert grounding_support("his mum", [("p1", pages[0]["text"])])["tier"] == "determiner_stripped"
    assert grounding_support("her mum", [("p1", pages[0]["text"])])["tier"] == "exact"


def test_m1_explicit_emitted_alias_ledger_deduplicates_per_entity_and_retains_failures():
    result = foundation([
        {"id": "a", "name": "the little whale", "aliases": ["little whale", "LITTLE  WHALE", "invented"]},
        {"id": "b", "name": "Other whale", "aliases": ["little whale"]},
    ])
    assert result["M1"]["aliases"] == 3
    assert result["M1"]["exact"] == 2
    assert result["M1"]["ungrounded"] == 1
    assert result["M1"]["character_names"]["aliases"] == 2
    assert [entry["raw_surface"] for entry in result["M1"]["entries"]] == ["little whale", "invented", "little whale"]


def test_m1_empty_output_is_undefined_and_failure_coverage_stays_visible():
    empty = foundation([])
    failed = foundation([], status="failed")
    partial = foundation([{"name": "Whale", "aliases": ["little whale", "unlocated"]}], status="partial")
    assert empty["M1"]["lexical_grounding"] is None
    assert failed["M1"]["lexical_grounding"] is None
    assert partial["M1"]["aliases"] == 2
    summary = aggregate_foundations(empty, failed, partial)
    assert summary["M1"]["defined_books"] == 1
    assert summary["M1"]["undefined_books"] == 2
    assert summary["M1"]["lexical_grounding"] == .5
    assert summary["M1"]["book_macro_grounding"] == .5
    assert summary["coverage"]["empty_alias_books"] == 2
    assert summary["coverage"]["failed_or_partial_character_books"] == 2


def test_m1_archival_projection_and_cached_reference_ocr_gaps_are_separate():
    book = {"id": "book", "pages": [
        {"id": "p1", "reference": {"text": {"status": "verified", "value": "missing mouse"}}},
        {"id": "p2", "reference": {"text": {"status": "observed", "value": "other"}}},
    ]}
    actual = prediction([{"name": "animal", "aliases": ["missing mouse", "excluded fox", "unknown"]}], {
        "p1": {"status": "failed", "text": "missing mouse"},
        "p2": {"status": "completed", "text": "excluded fox", "contains_story": False},
    })
    result = score_alias_foundation(book, actual)
    assert result["M1"]["grounded"] == 1
    assert result["M1"]["ungrounded"] == 2
    assert result["M1"]["ocr_gap_diagnostics"] == {
        "cached_reference_text_only_ocr_gap": 1,
        "archival_text_excluded_from_character_input": 1,
        "unlocated_with_available_evidence": 1,
    }
    assert result["output_coverage"]["ocr"]["stratum"] == "failed_or_partial"
    assert result["M4"]["optional_articles"]["universe"] == 0
    assert aggregate_foundations(result)["M1"]["by_ocr_coverage"]["failed_or_partial"]["aliases"] == 3


def test_m1_macro_and_micro_use_different_denominators():
    first = foundation([{"name": "Whale", "aliases": ["little whale"]}])
    second = foundation([{"name": "Other", "aliases": ["absent1", "absent2", "absent3"]}])
    result = aggregate_foundations(first, second)
    assert result["M1"]["lexical_grounding"] == .25
    assert result["M1"]["book_macro_grounding"] == .5


def test_m2_counts_affected_characters_and_books_once_without_deleting_variants():
    actual = prediction([
        {"name": "the whale", "aliases": ["whale", "little whale"]},
        {"name": "Rabbit", "aliases": []},
    ])
    original = deepcopy(actual)
    result = score_alias_foundation({"id": "book"}, actual)
    assert actual == original
    assert result["M2"]["characters"] == 2
    assert result["M2"]["affected_characters"] == 1
    assert result["M2"]["pairs"] == 3
    assert result["M2"]["character_incidence"] == .5
    clean = foundation([{"name": "Rabbit", "aliases": []}])
    summary = aggregate_foundations(result, clean)
    assert summary["M2"]["book_incidence"] == .5
    assert summary["M2"]["character_incidence"] == pytest.approx(1 / 3)


def test_m3_shared_surfaces_remain_unresolved_with_span_availability():
    result = foundation([
        {"id": "one", "name": "the whale", "aliases": ["unseen fish"]},
        {"id": "two", "name": "little whale", "aliases": ["fish"]},
    ])
    assert result["M3"]["affected_book"]
    assert result["M3"]["unresolved_flags"] == result["M3"]["surface_pairs"]
    assert result["M3"]["unhandled_flags"] == 0
    assert result["M3"]["span_unavailable_flags"] == 1
    assert result["M3"]["unresolved_flagged_occurrences"] == 1
    assert all(flag["status"] == "unresolved" for flag in result["M3"]["flags"])
    assert result["M4"]["optional_articles"]["ambiguous"] == 1
    assert result["M4"]["correct_attribution"] is None
    assert result["M4"]["wrong_attribution"] is None
    assert result["M4"]["false_merge_rate"] is None
    assert result["M4"]["false_split_rate"] is None
    assert "dictionary-uniqueness" in result["M4"]["label"]


def test_m3_span_unavailable_flag_is_not_a_verified_zero_error():
    result = foundation([{"name": "fox"}, {"name": "little fox"}])
    assert result["M3"]["surface_pairs"] == 1
    assert result["M3"]["span_unavailable_flags"] == 1
    assert result["M3"]["flags"][0]["status"] == "unresolved"


def test_m4_patterns_for_same_identity_do_not_inflate_occurrence_counts():
    result = foundation([{"name": "whale", "aliases": ["the whale", "little whale", "little whale"]}])
    assert result["M4"]["optional_articles"]["found"] == 1
    assert result["M4"]["optional_articles"]["single_candidate"] == 1
    assert result["M4"]["optional_articles"]["ambiguous"] == 0


def test_rejected_alias_stays_in_emitted_m1_ledger_but_not_mention_patterns():
    result = foundation([{"id": "a", "name": "Narrator", "aliases": [
        {"name": "little whale", "entry_status": "rejected", "canonical_form": "Narrator"}]}])
    assert result["M1"]["aliases"] == 1
    assert result["M1"]["grounded"] == 1
    assert result["M4"]["optional_articles"]["found"] == 0


def test_structural_failure_does_not_inflate_book_or_character_denominators():
    failed = foundation([], status="failed")
    partial = foundation([{"name": "whale", "aliases": ["the whale"]}], status="partial")
    completed = foundation([{"name": "whale", "aliases": ["the whale"]}, {"name": "little whale"}])
    summary = aggregate_foundations(failed, partial, completed)
    assert summary["M2"]["books"] == 3
    assert summary["M2"]["book_denominator"] == 1
    assert summary["M2"]["incomplete_books"] == 2
    assert summary["M2"]["book_incidence"] == 1
    assert summary["M2"]["characters"] == 3  # includes partial emitted ledger
    assert summary["M2"]["character_denominator"] == 2
    assert summary["M2"]["character_incidence"] == .5
    assert summary["M3"]["book_incidence"] == 1
    assert aggregate_foundations(failed)["M3"]["book_incidence"] is None


def source_span(text, surface, *, page_id="p1", entity_ids=None):
    start = text.find(surface)
    assert start >= 0
    span = {"page_id": page_id, "start": start, "end": start + len(surface), "surface": surface}
    if entity_ids is not None:
        span["entity_ids"] = entity_ids
    return span


def parenthetical_alias(text="the little whale swam", head="whale", modifier="little"):
    return {"name": f"{head} ({modifier})", "editorial_evidence": {
        "construction_type": "parenthetical_disambiguator", "entity_ids": ["whale"],
        "head": source_span(text, head, entity_ids=["whale"]),
        "modifier": source_span(text, modifier, entity_ids=["whale"]),
        "identity_evidence": [{"relation": "same_entity", "entity_ids": ["whale"],
                               "source": source_span(text, text)}],
    }}


def test_editorial_parenthetical_requires_complete_evidence_and_stays_outside_lexical_support():
    text = "the little whale swam"
    alias = parenthetical_alias(text)
    result = foundation([{"id": "whale", "name": "Whale", "aliases": [alias]}], {
        "p1": {"status": "completed", "text": text, "contains_story": True}})
    assert result["M1"]["aliases"] == 1
    assert result["M1"]["grounded"] == 0
    assert result["M1"]["editorial_allowances"] == 1
    assert result["M1"]["lexical_grounding"] == 0
    assert result["M1"]["explained_support"] == 1
    assert result["M1"]["unexplained_rate"] == 0
    assert result["M1"]["entries"][0]["ocr_gap_diagnostic"] == "validated_editorial_allowance"
    alias["editorial_evidence"]["identity_evidence"] = []
    assert foundation([{"id": "whale", "name": "Whale", "aliases": [alias]}])["M1"]["editorial_allowances"] == 0


def test_editorial_allowance_does_not_credit_embedded_tokens_or_wrong_referent():
    text = "the quiet bus"
    alias = parenthetical_alias(text, head="us", modifier="quiet")
    pages = {"p1": {"status": "completed", "text": text, "contains_story": True}}
    assert foundation([{"id": "whale", "name": "Bus", "aliases": [alias]}], pages)["M1"]["editorial_allowances"] == 0
    alias = parenthetical_alias()
    alias["editorial_evidence"]["modifier"]["entity_ids"] = ["another-whale"]
    assert foundation([{"id": "whale", "name": "Whale", "aliases": [alias]}])["M1"]["editorial_allowances"] == 0


def test_grounded_alias_never_also_receives_editorial_credit():
    text = "the little whale"
    alias = parenthetical_alias(text, head="little", modifier="whale")
    result = foundation([{"id": "whale", "name": "Whale", "aliases": [alias]}])
    assert result["M1"]["exact"] == 1
    assert result["M1"]["editorial_allowances"] == 0


def test_editorial_elided_list_requires_all_components_and_memberships():
    text = "Alfie and Zara joined the children"
    alias = {"name": "Alfie ... Zara", "editorial_evidence": {
        "construction_type": "elided_list", "entity_ids": ["children"],
        "components": [source_span(text, "Alfie", entity_ids=["alfie"]),
                       source_span(text, "Zara", entity_ids=["zara"])],
        "membership_evidence": [{"relation": "member_of", "member_entity_id": member,
                                 "group_entity_id": "children", "source": source_span(text, text)}
                                for member in ("alfie", "zara")],
    }}
    pages = {"p1": {"status": "completed", "text": text, "contains_story": True}}
    entity = {"id": "children", "name": "Children", "aliases": [alias]}
    assert foundation([entity], pages)["M1"]["editorial_allowances"] == 1
    alias["editorial_evidence"]["membership_evidence"].pop()
    assert foundation([entity], pages)["M1"]["editorial_allowances"] == 0
    alias["name"] = "Alfie, extra, Zara"
    assert foundation([entity], pages)["M1"]["editorial_allowances"] == 0


def test_separately_emitted_editorial_label_never_enters_alias_denominator():
    alias = parenthetical_alias("the little whale")
    result = foundation([{"id": "whale", "name": "Whale", "aliases": [], "editorial_labels": [alias]}])
    assert result["M1"]["aliases"] == 0
    assert result["M1"]["lexical_grounding"] is None
    assert result["M1"]["editorial_allowances"] == 0
    assert result["M1"]["separately_emitted_editorial_labels"] == 1
    assert result["M1"]["editorial_label_support"]["editorial_allowances"] == 1


def test_explicit_alias_recall_is_headline_and_precision_retains_legacy_value():
    reference = {"status": "observed", "value": [{"name": "Mother", "aliases": ["Mum"]}]}
    actual = [{"name": "Mum", "aliases": ["Mother", "the parent"]}]
    result = score_characters(match_characters(reference, actual))
    assert result["alias_recall"]["recall"] == 1
    assert result["variants"]["precision"] == pytest.approx(2 / 3)
    diagnostic = result["reference_alias_precision_diagnostic"]
    assert diagnostic["value"] == result["variants"]["precision"]
    assert "diagnostic only" in diagnostic["label"]
    assert "48%" in diagnostic["reason"]
    assert "cannot" in diagnostic["reason"]


def test_reports_expose_additive_version_conventions_and_metric_qualifications(tmp_path):
    book = {"id": "book", "reference": {"characters": {"status": "observed", "value": [
        {"name": "Whale", "aliases": ["little whale"]}]}}}
    actual = prediction([{"name": "Whale", "aliases": ["little whale"]}])
    result = write_reports({"books": [book]}, {"books": {"book": actual}}, tmp_path)
    assert result["protocol_version"] == "pilot-metrics-v2"
    assert result["scorer_version"] == "pilot-metrics-v3+alias-foundation-v1+bibliography-v2"
    assert result["alias_foundation"]["convention_version"]
    for name in ("C1", "C2", "C6"):
        assert result["alias_foundation"]["conventions"][name]["status"] == "provisional"
    for name in ("C3", "C4"):
        assert result["alias_foundation"]["conventions"][name]["status"] == "accepted"
    assert result["characters"]["alias_recall"]["recall"] == 1
    assert result["book_scores"][0]["alias_grounding"]["lexical_grounding"] == 1
    report = (tmp_path / "inspection.html").read_text()
    assert "dictionary-uniqueness diagnostic" in report
    assert "Alias recall is the headline alias metric" in report
    assert "alias_foundation" in (tmp_path / "books.csv").read_text().splitlines()[0]


def test_dev20_grounding_structural_and_dictionary_regressions():
    predictions = load_dev20_predictions()
    records = [score_alias_foundation({"id": book_id}, actual) for book_id, actual in predictions["books"].items()]
    result = aggregate_foundations(*records)
    assert (result["M1"]["aliases"], result["M1"]["exact"], result["M1"]["determiner_stripped"], result["M1"]["ungrounded"]) == (112, 95, 9, 8)
    assert result["M1"]["book_macro_grounding"] == pytest.approx(.8806372549019608)
    assert (result["M1"]["defined_books"], result["M1"]["undefined_books"]) == (17, 3)
    old = result["historical_structural_audits"][HISTORICAL_DELETION_VERSION]
    assert (old["M2"]["equal_pairs"], old["M2"]["subset_pairs"], old["M2"]["pairs"]) == (9, 44, 53)
    assert (old["M2"]["affected_characters"], old["M2"]["affected_books"], old["M2"]["characters"]) == (37, 13, 105)
    assert (old["M3"]["surface_pairs"], old["M3"]["record_pairs"], old["M3"]["affected_books"]) == (12, 9, 2)
    separators = result["historical_structural_audits"][HISTORICAL_SEPARATOR_VERSION]["M3"]
    assert (separators["surface_pairs"], separators["record_pairs"], separators["affected_books"]) == (14, 10, 2)
    optional = result["M4"]["optional_articles"]
    assert (optional["universe"], optional["found"], optional["single_candidate"], optional["ambiguous"]) == (916, 916, 894, 22)
    exact = result["M4"]["exact"]
    assert (exact["found"], exact["missed"]) == (811, 105)


def test_empty_aggregate_keeps_undefined_metrics():
    result = aggregate([])
    assert result["alias_foundation"]["M1"]["lexical_grounding"] is None
    assert result["characters"]["alias_recall"]["recall"] is None
