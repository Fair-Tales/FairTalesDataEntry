"""Frozen dictionary retrieval, lossless entries, and source-span contracts."""
import json
from collections import Counter
from pathlib import Path

import pytest

from alias_pipeline import (build_alias_index, dictionary_diagnostics, match_page,
                            mention_pattern_tokens, tokens_with_raw_offsets)
from scripts.extraction_eval.normalization import tokenize


def test_dev20_frozen_dictionary_regression():
    fixture = Path(__file__).parent / "fixtures" / "alias_dev20_predictions.json"
    predictions = json.loads(fixture.read_text())
    optional, exact = Counter(), Counter()
    characters = aliases = pages_count = 0
    ambiguous_books = {}
    for book_id, book in predictions["books"].items():
        cast = book["characters"]["value"]
        characters += len(cast)
        aliases += sum(len(character["aliases"]) for character in cast)
        pages = [dict(page, page_id=page_id) for page_id, page in book["pages"].items()
                 if page.get("contains_story") and page.get("status") == "completed"]
        pages_count += len(pages)
        result = dictionary_diagnostics(cast, pages, book_id=book_id)
        optional.update(result["optional_articles"])
        exact.update(result["exact"])
        if result["optional_articles"]["ambiguous"]:
            ambiguous_books[book_id] = result["optional_articles"]["ambiguous"]
        for page in pages:
            assert [token["token"] for token in tokens_with_raw_offsets(page["text"])] == tokenize(page["text"])
        page_text = {page["page_id"]: page["text"] for page in pages}
        for occurrence in result["occurrences"]:
            assert occurrence["surface"] == page_text[occurrence["page_id"]][occurrence["start"]:occurrence["end"]]
            assert occurrence["resolved_entity_id"] is None
    assert (characters, aliases, pages_count) == (105, 112, 579)
    assert dict(optional) == dict(universe=916, found=916, single_candidate=894, ambiguous=22, missed=0)
    assert dict(exact) == dict(universe=916, found=811, single_candidate=807, ambiguous=4, missed=105)
    assert ambiguous_books == {"santa_is_coming_to_devon": 18, "the_enormous_crocodile": 4}


@pytest.mark.parametrize("surface,expected", [
    ("THE Little Whale", ("little", "whale")), ("a whale", ("whale",)),
    ("an owl", ("owl",)), ("the", ()), ("his mum", ("his", "mum")),
    ("Marlon’s mum", ("marlon's", "mum")), ("one little girl", ("one", "little", "girl")),
    ("all the birds", ("all", "the", "birds")), ("no whale", ("no", "whale")),
    ("the littlest penguin", ("littlest", "penguin")),
])
def test_mention_normalization_preserves_owner_modifiers_and_number(surface, expected):
    assert mention_pattern_tokens(surface) == expected


def test_duplicate_patterns_do_not_inflate_occurrences_and_no_raw_entry_is_deleted():
    characters = [{"id": "c", "name": "the whale", "aliases": ["whale", "The whale", "the whale"]}]
    index = build_alias_index(characters)
    assert [entry["name"] for entry in index["entries"]] == ["the whale", "whale", "The whale", "the whale"]
    broad = [p for p in index["patterns"] if p["tier"] == "optional_articles"]
    assert len(broad) == 1
    assert len(broad[0]["entry_ids"]) == 4
    result = dictionary_diagnostics(characters, [{"text": "The whale whale."}])
    assert result["optional_articles"]["found"] == 2  # adjacent occurrences stay separate
    assert all(len(o["candidate_entity_ids"]) == 1 for o in result["occurrences"])


def test_nested_and_transitive_overlap_unions_candidates_before_resolution():
    characters = [{"id": "a", "name": "small whale"}, {"id": "b", "name": "whale swims"},
                  {"id": "c", "name": "swims away"}, {"id": "d", "name": "whale"}]
    result = dictionary_diagnostics(characters, [{"id": "p", "text": "A small whale swims away."}])
    assert result["optional_articles"]["ambiguous"] == 1
    occurrence, = result["occurrences"]
    assert occurrence["surface"] == "A small whale swims away"
    assert occurrence["candidate_entity_ids"] == ["a", "b", "c", "d"]
    assert len(occurrence["nested_hits"]) == 4
    assert occurrence["status"] == "unresolved"


def test_possessive_surface_cannot_match_a_different_owner():
    index = build_alias_index([{"name": "his mum", "aliases": ["Marlon's mum"]}])
    assert not match_page(index, "her mum and Billy's mum waved.")
    assert len(match_page(index, "His mum and Marlon’s mum waved.")) == 2


def test_no_cross_page_or_embedded_word_or_plural_match():
    characters = [{"name": "little whale", "aliases": ["us", "swan"]}]
    result = dictionary_diagnostics(characters, [{"text": "little"}, {"text": "whale swans bus"}])
    assert result["optional_articles"]["found"] == 0


@pytest.mark.parametrize("text", ["Straße STRASSE", "Cafe\u0301’s CAFÉ's", "ﬁsh １２", "[ unreadable ] whale", "가 fox", "\u09c7\u09be fox"])
def test_unicode_offset_mapping_matches_frozen_tokenizer(text):
    tokens = tokens_with_raw_offsets(text)
    assert [token["token"] for token in tokens] == tokenize(text)
    assert all(token["start"] < token["end"] <= len(text) for token in tokens)
    for token in tokens:
        assert tokenize(text[token["start"]:token["end"]]) == [token["token"]]


def test_manual_canonical_does_not_invent_patterns_and_inactive_entries_are_archived():
    characters = [{"id": "c", "name": "Display", "name_is_label": True, "aliases": [
        {"name": "the whale", "canonical_form": "invented", "canonical_source": "manual",
         "canonical_override": True},
        {"name": "the fish", "entry_status": "superseded", "supersedes": "old"},
        {"name": "the bird", "entry_status": "rejected"}]}]
    index = build_alias_index(characters)
    assert len(index["entries"]) == 4
    assert not match_page(index, "Display invented fish bird")
    assert len(match_page(index, "whale")) == 1


def test_corrective_attachment_and_proper_name_article_policy():
    characters = [{"id": "original", "name": "Label", "name_is_label": True, "aliases": [
        {"name": "The Who", "article_policy": "preserve", "effective_entity_id": "corrected"}]}]
    index = build_alias_index(characters, diagnostic=False)
    assert not match_page(index, "Who")
    hit, = match_page(index, "The Who")
    assert hit["candidate_entity_ids"] == ["corrected"]


def test_prior_character_names_stay_matchable_after_display_rename():
    index = build_alias_index([{"entity_id": "characters/old", "name": "New name",
                               "name_history": ["Old name"], "aliases": []}])
    assert [entry["name"] for entry in index["entries"]] == ["New name", "Old name"]
    assert match_page(index, "Old name")[0]["candidate_entity_ids"] == ["characters/old"]


def test_retired_character_archive_does_not_supply_active_patterns():
    index = build_alias_index([{"name": "fox", "aliases": ["the fox"], "entry_status": "rejected"}])
    assert len(index["entries"]) == 2
    assert not index["patterns"]
