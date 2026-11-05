"""Offline metric contracts and fixture reports; no cloud/application imports."""

import json
import socket
from copy import deepcopy

import pytest

from scripts.extraction_eval.normalization import normalize_name, tokenize
from scripts.extraction_eval.reporting import write_reports
from scripts.extraction_eval.scoring import (aggregate, apply_equivalences, apply_overlay,
                                              match_characters, score_book, score_characters,
                                              score_metadata, score_ocr, score_story)
from scripts.extraction_eval.statistics import bootstrap_books, paired_comparison, replicate_variation


def ref(value, **kwargs):
    return {"value": value, "status": "observed", "provenance": "fixture", **kwargs}


def test_multiset_reordered_spread_and_ordered_errors():
    score = score_ocr(ref("Very, very small fox. Big RED dog!"), "big red dog very very small fox")
    assert score["f1"] == 1
    assert score["wer"] > 0
    assert score["cer"] > 0


def test_repeated_words_numbers_and_diacritics_are_retained():
    assert tokenize("CAFÉ’s １２ cats — café's") == ["café's", "12", "cats", "café's"]
    score = score_ocr(ref("very very 2 cafés"), "very 2 cafes")
    assert score["matched_tokens"] == 2
    assert score["reference_tokens"] == 4
    assert score["f1"] == pytest.approx(4 / 7)


@pytest.mark.parametrize("prediction", ["[?]", "[illegible]", ""])
def test_uncertainty_never_disappears_into_perfect_match(prediction):
    score = score_ocr(ref("[?]"), prediction)
    assert score["f1"] == 0
    assert score["wer"] == 1
    assert score["cer"] > 0


def test_empty_wordless_is_agreement_never_perfect_f1():
    agreed = score_ocr(ref("", kind="wordless"), "  ")
    disputed = score_ocr(ref("", kind="wordless"), "Once upon a time")
    assert agreed["subgroup"] == "agreed_wordless"
    assert agreed["f1"] is None
    assert not agreed["scored"]
    assert disputed["subgroup"] == "disputed_wordless"
    assert disputed["invented_tokens"] == 4
    assert disputed["f1"] is None


@pytest.mark.parametrize("status,subgroup", [("failed", "wordless_failed"), ("budget_stopped", "wordless_unrun"), ("pending", "wordless_unrun")])
def test_wordless_failures_are_not_agreement(status, subgroup):
    result = score_ocr(ref(""), {"status": status, "text": ""})
    assert result["subgroup"] == subgroup
    assert result["f1"] is None


@pytest.mark.parametrize("status", ["missing", "ambiguous", "not_annotated"])
def test_reference_eligibility_independent_of_prediction(status):
    record = ref("Once upon a time", status=status)
    before = deepcopy(record)
    for prediction in ["Once upon a time", "", {"status": "failed"}]:
        score = score_ocr(record, prediction)
        assert not score["eligible"] and not score["scored"]
        assert score["exclusion"] == status
    assert record == before


def test_failure_and_unrun_keep_nonempty_reference_in_denominator():
    for status in ("failed", "pending", "budget_stopped"):
        result = score_ocr(ref("three simple words"), {"status": status})
        assert result["reference_tokens"] == 3
        assert result["f1"] == 0
        assert result["wer"] == 1
        assert result["unrun"] == (status != "failed")


def test_error_rates_can_exceed_one():
    result = score_ocr(ref("a"), "many more extra invented words")
    assert result["wer"] > 1
    assert result["cer"] > 1


def test_story_confusion_failure_coverage_and_end_to_end_metrics():
    assert score_story(ref(True), True)["tp"] == 1
    assert score_story(ref(False), True)["fp"] == 1
    result = score_story(ref(True), {"status": "failed"})
    assert result["failed_true"] == 1
    assert result["coverage"] == 0
    assert result["end_to_end_recall"] == 0
    assert result["recall"] is None
    assert score_story(ref(False), None)["unrun_false"] == 1
    assert score_story(ref(True), "true")["failed_true"] == 1


def test_metadata_incomplete_credit_lists_do_not_penalize_additions():
    score = score_metadata("authors", ref(["Ada Smith"], complete=False), ["Ada Smith", "Ben Jones"])
    assert score["known_reference_agreement"] is True
    assert score["known_credit_recall"] == 1
    assert score["credit_precision"] is None
    assert score["exact"] is None
    assert score["unverified_extra_credits"] == ["ben jones"]
    complete = score_metadata("authors", ref(["Ada Smith"], complete=True), ["Ada Smith", "Ben Jones"])
    assert complete["exact"] is False
    assert complete["credit_precision"] == .5


def test_metadata_strict_equivalences_and_year_semantics():
    assert score_metadata("authors", ref(["Ada Smith"], complete=True), ["A Smith"])["exact"] is False
    score = score_metadata("publisher", ref("Puffin"), "Penguin", {"Puffin": "Penguin"})
    assert score["exact"] is False
    assert score["reviewed_equivalent"] is True
    assert 0 < score["similarity"] < 1
    assert score_metadata("published_year", ref(1999), 2000)["exact"] is False
    assert score_metadata("published_year", ref(1999), 2000)["year_difference"] == 1
    assert score_metadata("published_year", ref(-1, status="missing"), 2000)["exact"] is None


def test_metadata_lookup_failure_excludes_only_affected_eligible_fields(tmp_path):
    book = {"id": "lookup-failed", "pages": [], "reference": {"metadata": {
        "title": ref("Vision title"), "authors": ref(["Ada Smith"], complete=True),
        "publisher": ref(None, status="missing"), "published_year": ref(1999),
        "illustrators": ref(["Ben Jones"], complete=True)}}}
    prediction = {"metadata": {"status": "completed", "value": {
        "title": "Vision title", "authors": [], "publisher": None,
        "isbn_lookup": {"status": "failed", "reason": "http_error", "http_status": 429},
        "lookup_failed_fields": ["authors", "publisher"]}}}
    score = score_book(book, prediction)
    assert score["metadata"]["title"]["exact"] is True
    authors = score["metadata"]["authors"]
    assert authors["prediction_status"] == "lookup_failed"
    assert authors["exclusion"] == "lookup_failed"
    assert authors["eligible"] is False and authors["exact"] is None
    assert score["metadata"]["publisher"]["exclusion"] == "missing"
    assert score["metadata"]["published_year"]["exact"] is False
    assert score["metadata"]["illustrators"]["exact"] is False
    summary = write_reports({"books": [book]}, {"books": {book["id"]: prediction}}, tmp_path)
    assert summary["metadata"]["authors"]["strict_denominator"] == 0
    assert summary["metadata"]["authors"]["failed_books"] == 0
    assert summary["metadata"]["authors"]["lookup_failed_exclusions"] == 1
    assert summary["metadata"]["publisher"]["lookup_failed_exclusions"] == 0
    assert summary["metadata_lookup_failures"] == {
        "affected_books": 1, "book_ids": [book["id"]], "excluded_fields": 1}
    document = (tmp_path / "inspection.html").read_text()
    assert "metadata_lookup_failures" in document and "excluded_fields" in document


def test_metadata_lookup_failure_remains_visible_when_vision_filled_every_field():
    book = {"id": "filled", "reference": {"metadata": {"title": ref("Vision title")}}}
    prediction = {"metadata": {"status": "completed", "value": {
        "title": "Vision title", "isbn_lookup": {"status": "failed"}, "lookup_failed_fields": []}}}
    summary = aggregate([score_book(book, prediction)])
    assert summary["metadata"]["title"]["strict_denominator"] == 1
    assert summary["metadata_lookup_failures"]["affected_books"] == 1
    assert summary["metadata_lookup_failures"]["excluded_fields"] == 0


@pytest.mark.parametrize('raw_response,excluded', [(None, 1), ({"totalItems": 0}, 0)])
def test_offline_report_classifies_legacy_isbn_cache_without_mutating_run(tmp_path, monkeypatch, raw_response, excluded):
    from scripts.extraction_eval.store import atomic_json, digest

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("Network access"))
    run = tmp_path / "old-run"
    path = run / "lookups" / f'{digest("123")}.json'
    atomic_json(path, {"isbn": "123", "value": None, "raw_response": raw_response})
    before_cache = path.read_bytes()
    book = {"id": "legacy", "reference": {"metadata": {"title": ref("Missing title")}}}
    predictions = {"_run_dir": str(run), "books": {"legacy": {
        "metadata": {"status": "completed", "value": {"isbn": "123", "title": None}}}}}
    before_predictions = deepcopy(predictions)
    summary = write_reports({"books": [book]}, predictions, tmp_path / "report")
    assert summary["metadata_lookup_failures"]["excluded_fields"] == excluded
    assert summary["metadata_lookup_failures"]["affected_books"] == excluded
    assert summary["metadata"]["title"]["strict_denominator"] == 1 - excluded
    assert predictions == before_predictions
    assert path.read_bytes() == before_cache


def test_character_alias_as_primary_and_symmetric_article_stripping():
    expected = ref([{"name": "The narrator", "aliases": ["I"]}])
    result = score_characters(match_characters(expected, [{"name": "I", "aliases": ["narrator"]}]))
    assert result["entities"]["f1"] == 1
    assert result["variants"]["f1"] == 1
    assert normalize_name("Tom’s mum", strip_article=True) == "tom's mum"
    assert normalize_name("her mum", strip_article=True) == "mum"


def test_character_duplicates_one_to_one_and_ambiguity_bounds():
    result = score_characters(match_characters(ref([{"name": "Rabbit"}]), [{"name": "Rabbit"}, {"name": "the Rabbit"}]))
    assert result["matched_upper"] == 1
    assert result["matched_lower"] == 0
    assert result["entities_upper"]["precision"] == .5
    assert result["ambiguous"]
    assert result["normalization_collisions"]


def test_alias_attachment_wrong_entity_cannot_receive_unconditional_credit():
    expected = ref([{"name": "Tom", "aliases": ["brother"]}, {"name": "Sue", "aliases": ["sister"]}])
    actual = [{"name": "Tom", "aliases": ["sister"]}, {"name": "Sue", "aliases": ["brother"]}]
    result = score_characters(match_characters(expected, actual))
    assert result["variants"]["f1"] < 1
    assert result["ambiguous"]


def test_empty_character_reference_not_annotated_is_not_verified_absence():
    result = score_characters(match_characters(ref([], status="not_annotated"), []))
    assert not result["eligible"]
    assert result["entities"]["f1"] is None


def fixture_manifest():
    def page(identifier, text, story=True):
        return {"id": identifier, "page_number": int(identifier[-1]), "reference": {
            "text": ref(text, status="observed" if story else "not_annotated"), "contains_story": ref(story)}}
    return {"snapshot_id": "fixture-v1", "books": [
        {"id": "a", "title": "A book", "pages": [page("a1", "one two"), page("a2", ""), page("a3", ""), page("a4", "", False)],
         "reference": {"metadata": {"title": ref("A book")}, "characters": ref([], status="not_annotated")}},
        {"id": "b", "title": "B book", "pages": [page("b1", "three")],
         "reference": {"metadata": {}, "characters": ref([], status="not_annotated")}}]}


def fixture_predictions():
    return {"run_id": "fixture-run", "books": {
        "a": {"pages": {"a1": {"status": "completed", "text": "one two", "contains_story": True},
                         "a2": {"status": "completed", "text": "", "contains_story": True},
                         "a3": {"status": "completed", "text": "invented", "contains_story": False},
                         "a4": {"status": "completed", "text": "Copyright", "contains_story": False}},
              "metadata": {"status": "completed", "value": {"title": "A book"}}, "characters": {"status": "completed", "value": []}},
        "b": {"pages": {"b1": {"status": "budget_stopped"}}, "characters": {"status": "budget_stopped"}}}}


def test_book_macro_micro_and_wordless_denominators_reconcile():
    manifest, predictions = fixture_manifest(), fixture_predictions()
    scores = [score_book(book, predictions["books"].get(book["id"])) for book in manifest["books"]]
    summary = aggregate(scores)
    assert summary["ocr"]["book_macro_f1"] == .5
    assert summary["ocr"]["token_micro"]["f1"] == .8
    assert summary["ocr"]["book_denominator"] == 2
    assert summary["ocr"]["intended_text_pages"] == 2
    assert summary["ocr"]["unrun_text_pages"] == 1
    assert summary["ocr"]["exclusions"] == {"not_annotated": 1}
    assert summary["wordless"]["intended_pages"] == 2
    assert summary["wordless"]["agreed_wordless"] == 1
    assert summary["wordless"]["disputed_wordless"] == 1
    assert summary["wordless"]["agreement_rate"] == .5
    assert summary["incomplete"]


def test_wordless_intended_denominator_includes_failures_and_unrun():
    manifest, predictions = fixture_manifest(), fixture_predictions()
    predictions["books"]["a"]["pages"]["a2"] = {"status": "failed"}
    predictions["books"]["a"]["pages"]["a3"] = {"status": "budget_stopped"}
    summary = aggregate([score_book(manifest["books"][0], predictions["books"]["a"])])
    assert summary["wordless"]["intended_pages"] == 2
    assert summary["wordless"]["failed_pages"] == 1
    assert summary["wordless"]["unrun_pages"] == 1
    assert summary["wordless"]["agreement_rate"] is None
    assert summary["wordless"]["end_to_end_agreement_rate"] == 0


def test_offline_reports_use_selection_and_escape_html(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Scoring attempted a network call")
    monkeypatch.setattr(socket, "socket", no_network)
    manifest, predictions = fixture_manifest(), fixture_predictions()
    predictions["books"]["a"]["pages"]["a3"]["text"] = '<script>alert("bad")</script>'
    selection = {"book_ids": ["a"], "selected": [{"id": "a", "role": "explicit", "inclusion_probability": None}]}
    summary = write_reports(manifest, predictions, tmp_path, selection=selection)
    assert summary["books"] == 1
    assert summary["book_scores"][0]["id"] == "a"
    assert summary["probability_sample"] is None
    for name in ("summary.json", "books.csv", "pages.csv", "calls.csv", "inspection.html"):
        assert (tmp_path / name).is_file()
    report = (tmp_path / "inspection.html").read_text()
    assert '<script>alert("bad")</script>' not in report
    assert "&lt;script&gt;" in report
    assert "http://" not in report and "https://" not in report
    assert json.loads((tmp_path / "summary.json").read_text())["wordless"]["intended_pages"] == 2
    assert write_reports(manifest, predictions, tmp_path, selection=selection) == summary


def test_reviewed_overlay_is_versioned_reference_only_and_immutable():
    manifest = fixture_manifest()
    overlay = {"version": "review-1", "snapshot_id": "fixture-v1", "reviewed": True, "changes": [
        {"book_id": "a", "page_id": "a3", "field": "text", "value": "Printed text", "provenance": "independent reviewer"}]}
    revised = apply_overlay(manifest, overlay)
    assert revised["books"][0]["pages"][2]["reference"]["text"]["status"] == "verified"
    assert manifest["books"][0]["pages"][2]["reference"]["text"]["value"] == ""
    with pytest.raises(ValueError, match="snapshot_id"):
        apply_overlay(manifest, {**overlay, "snapshot_id": "different"})
    with pytest.raises(ValueError, match="reviewed"):
        apply_overlay(manifest, {**overlay, "reviewed": False})


def test_reviewed_equivalences_require_explicit_review():
    assert apply_equivalences({}, {"version": "v1", "reviewed": True, "equivalences": {"characters": {"Teddy": "Ted"}}})["equivalence_version"] == "v1"
    with pytest.raises(ValueError, match="reviewed"):
        apply_equivalences({}, {"version": "v1", "equivalences": {}})


def test_cluster_bootstrap_weighting_and_challenge_separation():
    records = [{"id": "a", "value": 1, "role": "probability", "stratum": "small", "inclusion_probability": .5},
               {"id": "b", "value": 0, "role": "probability", "stratum": "large", "inclusion_probability": .25},
               {"id": "c", "value": 0, "role": "challenge", "inclusion_probability": None}]
    result = bootstrap_books(records, repeats=100)
    assert result["estimate"] == pytest.approx(1 / 3)
    assert result["books"] == 2
    assert result == bootstrap_books(records, repeats=100)
    assert result["resampling_unit"] == "book"


def test_paired_comparison_identity_and_explicit_selection():
    baseline = [{"id": "a", "value": .5, "reference_hash": "abc", "role": "explicit", "inclusion_probability": None}]
    candidate = [{**baseline[0], "value": .7}]
    result = paired_comparison(baseline, candidate, repeats=100)
    assert result["paired_books"] == 1
    assert result["descriptive_paired_difference"] == pytest.approx(.2)
    assert result["interval_95"] is None
    with pytest.raises(ValueError, match="reference"):
        paired_comparison(baseline, [{**candidate[0], "reference_hash": "different"}])
    with pytest.raises(ValueError, match="identical book"):
        paired_comparison(baseline, [])


def test_replicate_variation_keeps_all_responses_and_story_flips():
    rows = [{"id": "a", "replicate_id": "r1", "reference_hash": "abc", "value": .5, "story_flags": {"p1": True}},
            {"id": "a", "replicate_id": "r2", "reference_hash": "abc", "value": 1, "story_flags": {"p1": False}}]
    result = replicate_variation(rows)[0]
    assert result["mean"] == .75
    assert result["replicates"] == 2
    assert result["story_flip_pages"] == ["p1"]
    assert result["sample_standard_deviation"] > 0
    with pytest.raises(ValueError, match="unique"):
        replicate_variation(rows + [rows[0]])
