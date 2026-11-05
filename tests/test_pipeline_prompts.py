"""Frozen dev-only evidence, reversible conventions and lossless adapters."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from character_evidence import IncompleteCharacterResponse, validate_character_evidence
from conventions import (DEFAULT_CONVENTIONS, convention_metadata, project_authors,
                         project_regions, project_story)
from text_content.pipeline_prompts import (load_example_pack, prompt_for,
                                          prompt_request_manifest)


def test_frozen_pack_quotes_hashes_and_adjudication_boundaries():
    pack = load_example_pack()
    assert len(pack["examples"]) == 26
    assert {row["stage"] for row in pack["examples"]} == {
        "character_detection", "page_extraction", "story_classification", "joint_metadata_extraction"}
    for row in pack["examples"]:
        assert row["book_id"] in pack["development_book_ids"]
        assert all(quote in row["source_text"] for quote in row["quotes"])
        assert hashlib.sha256(row["source_text"].encode()).hexdigest() == row["source_text_sha256"]
        assert len(row["source_image_sha256"]) == 64
        assert row["gold_story_label"] is False
        assert row["gold_identity"] is False
        assert row["gold_transcription"] == (row["stage"] == "page_extraction")
    assert "the_gruffalo's_child" not in pack["development_book_ids"]


@pytest.mark.parametrize("key", ["held_out_book_ids", "held_out_groups"])
def test_pack_refuses_heldout_books_and_duplicate_groups(key):
    example = load_example_pack()["examples"][0]
    forbidden = example["book_id" if key == "held_out_book_ids" else "holdout_group"]
    with pytest.raises(ValueError, match="leakage"):
        load_example_pack(**{key: [forbidden]})


def test_pack_rejects_changed_source_and_nonverbatim_quotes(monkeypatch):
    import text_content.pipeline_prompts as prompts
    pack = load_example_pack()

    class FakePath:
        def with_name(self, name):
            return self

        def read_text(self):
            return json.dumps(pack)

    monkeypatch.setattr(prompts, "Path", lambda _: FakePath())
    pack["examples"][0]["quotes"].append("not a printed quotation")
    with pytest.raises(ValueError, match="verbatim"):
        prompts.load_example_pack()
    pack["examples"][0]["quotes"].pop()
    pack["examples"][0]["source_text"] += " changed"
    with pytest.raises(ValueError, match="hash"):
        prompts.load_example_pack()


@pytest.mark.parametrize("stage", ["character_detection", "page_extraction", "story_classification",
                                  "joint_metadata_extraction", "locate_key_pages"])
def test_rules_and_icl_variants_have_exact_request_bytes(stage):
    rules = prompt_for(stage, "rules-only")
    icl = prompt_for(stage, "rules-plus-icl")
    assert "EXAMPLE " not in rules
    if stage != "locate_key_pages":
        assert "EXAMPLE " in icl
    if stage == "character_detection":
        icl = icl.format(pages_json='[{"page": 1, "text": "TEST"}]')
    manifest = prompt_request_manifest(stage, rendered_prompt=icl)
    assert manifest["rendered_prompt_utf8"] == icl
    assert manifest["rendered_prompt_sha256"] == hashlib.sha256(icl.encode()).hexdigest()
    assert manifest["rendered_prompt_byte_length"] == len(icl.encode())
    assert manifest["example_pack_sha256"] == hashlib.sha256(
        manifest["example_pack_utf8"].encode()).hexdigest()
    assert prompt_request_manifest(stage, "rules-only")["examples"] == []


def test_convention_status_and_coverage_rule_are_explicit():
    assert all(DEFAULT_CONVENTIONS[key]["status"] == "provisional" for key in ["C1", "C2", "C6", "C7"])
    assert all(DEFAULT_CONVENTIONS[key]["status"] == "accepted" for key in ["C3", "C4"])
    copy = convention_metadata()
    copy["conventions"]["C1"]["status"] = "changed"
    assert DEFAULT_CONVENTIONS["C1"]["status"] == "provisional"
    rules = prompt_for("character_detection", "rules-only")
    assert "embedded_short_form" in rules
    assert "Category" in rules or "category" in rules
    assert "Possessives are never" in rules
    assert "collective" in rules and "candidate" in rules


def test_c1_projection_reversible_and_manual_text_is_preserved():
    regions = [{"kind": "narrative", "text": "Speech."},
               {"kind": "illustration_lettering", "text": "SIGN"},
               {"kind": "paratext", "text": "Blurb."}]
    text = "Speech.\n\nSIGN\n\nBlurb."
    original = deepcopy(regions)
    with_sign = project_regions(text, regions)
    without = project_regions(text, regions, include_illustration_lettering=False)
    assert with_sign["analysis_text"] == "Speech.\n\nSIGN"
    assert without["analysis_text"] == "Speech."
    assert with_sign["analysis_projection_version"] != without["analysis_projection_version"]
    assert regions == original
    assert project_regions("A student's correction", regions)["analysis_text"] == "A student's correction"
    assert project_regions("legacy", None)["projection_status"] == "legacy_unclassified"


def test_c2_and_c6_reproject_without_inference_or_evidence_loss():
    assert project_story("wordless_narrative") is True
    assert project_story("informational") is True
    assert project_story("glossary_index") is False
    assert project_story("uncertain") is None
    assert project_story("supplemental_activity") is False
    assert project_story("supplemental_activity", include_supplemental=True) is True
    credits = [{"name": "Writer", "role": "author"}, {"name": "Reteller", "role": "reteller"},
               {"name": "Editor", "role": "editor"}, {"name": "Designer", "role": "designer"}]
    original = deepcopy(credits)
    assert project_authors(credits) == ["Writer", "Reteller"]
    assert project_authors(credits, include_retellers=False) == ["Writer"]
    assert credits == original


def test_evidence_flags_wrong_pages_unsupported_surface_and_ambiguity_without_deletion():
    response = {"characters": [{"id": "mother", "name": "Display label", "name_is_label": True,
        "mentions": [{"surface": "his mother", "page": 1, "quote": "his\n mother spoke"}],
        "aliases": [{"surface": "mother", "page": 1, "quote": "his mother spoke",
                     "surface_scope": "embedded_short_form", "full_surface": "his mother",
                     "candidate_entity_ids": ["mother", "other"]},
                    {"surface": "fruit", "page": 8, "quote": "the fruit"},
                    {"surface": "pig", "page": 1, "quote": "the pigs"}],
        "identity_evidence": [], "member_of": ["family"]}],
        "unresolved_mentions": [{"surface": "mother", "page": 1,
            "quote": "his mother spoke", "candidate_entity_ids": ["mother", "other"]}]}
    original = deepcopy(response)
    validated = validate_character_evidence(response, [(1, "his mother spoke to the pigs")])
    assert response == original
    assert validated["characters"][0]["aliases"] == original["characters"][0]["aliases"]
    assert validated["unresolved_mentions"] == original["unresolved_mentions"]
    issues = validated["evidence_validation"]["issues"]
    assert {row["status"] for row in issues} == {"wrong_page", "unsupported_surface", "unresolved_attachment"}
    assert not any(row["path"].endswith(".name") for row in issues)
    assert not any(row["path"].endswith("mentions[0]") and row["status"] != "unresolved_attachment"
                   for row in issues)


def test_character_adapter_keeps_all_evidence_and_empty_cast_unresolved(monkeypatch):
    import utilities
    response = {"characters": [{"name": "his mother", "aliases": [{"surface": "mother", "page": 1,
        "quote": "his mother spoke", "surface_scope": "embedded_short_form", "full_surface": "his mother"}],
        "mentions": [{"surface": "his mother", "page": 1, "quote": "his mother spoke"}],
        "language_relations": [{"relation": "speech", "page": 1, "quote": "his mother spoke"}]}],
        "unresolved_mentions": [{"surface": "someone", "page": 2, "quote": "someone"}]}
    monkeypatch.setattr(utilities, "_claude_json", lambda *a, **kw: response)
    result = utilities.detect_book_characters([(1, "his mother spoke")], object(), model="offline",
                                             prompt_variant="rules-only")
    assert result[0]["aliases"] == ["mother"]
    assert result[0]["raw_alias_entries"] == response["characters"][0]["aliases"]
    assert result[0]["language_relations"] == response["characters"][0]["language_relations"]
    assert result.raw_response["unresolved_mentions"] == response["unresolved_mentions"]
    assert result.raw_response["prompt_manifest"]["prompt_variant"] == "rules-only"
    response["characters"] = []
    empty = utilities.detect_book_characters([(1, "text")], object(), model="offline")
    assert empty == [] and empty.raw_response["unresolved_mentions"]


@pytest.mark.parametrize("response", [None, [], {}, {"characters": None},
    {"characters": ["not an entity"]}, {"characters": [{"name": None}]},
    {"characters": [{"name": ""}]}, {"characters": [], "unresolved_mentions": None}])
def test_malformed_character_output_fails_instead_of_empty_success(monkeypatch, response):
    import utilities
    monkeypatch.setattr(utilities, "_claude_json", lambda *a, **kw: response)
    with pytest.raises(IncompleteCharacterResponse) as caught:
        utilities.detect_book_characters([(1, "text")], object(), model="offline")
    assert caught.value.failure_kind == "invalid_character_structure"
    assert caught.value.raw_response == response


def test_precomputed_empty_success_retains_unresolved_without_retry():
    from alias_schema import RecordedSuggestions
    from utilities import usable_precomputed_suggestions
    raw = {"characters": [], "unresolved_mentions": [{"surface": "someone"}]}
    prepared = usable_precomputed_suggestions(
        {"book_id": "book", "suggestions": RecordedSuggestions([], raw_response=raw)}, "book")
    assert prepared == [] and prepared.raw_response == raw
    assert usable_precomputed_suggestions({"book_id": "book", "suggestions": []}, "book") is None


def test_manual_character_detection_uses_versioned_regions_and_retains_student_edits(monkeypatch):
    from contextlib import nullcontext
    import streamlit as st
    from test_book_characters_atomic import _AttrDict, _load_enter_text_defs
    ns = _load_enter_text_defs(monkeypatch)
    regions = {'regions': [{'kind': 'narrative', 'text': 'Story'},
                           {'kind': 'paratext', 'text': 'Blurb'}]}
    state = _AttrDict(book_pages_dict={
        1: SimpleNamespace(text='Story\n\nBlurb', contains_story=True, extraction_evidence=regions),
        2: SimpleNamespace(text='Student corrected text', contains_story=True, extraction_evidence=regions),
    })
    monkeypatch.setattr(st, 'session_state', state)
    monkeypatch.setattr(st, 'empty', lambda: SimpleNamespace(write=lambda text: None))
    monkeypatch.setattr(st, 'progress', lambda value: SimpleNamespace(progress=lambda value: None))
    monkeypatch.setattr(st, 'spinner', lambda text: nullcontext())
    ns['get_anthropic_client'] = lambda: object()
    inputs = []
    ns['detect_book_characters'] = lambda pages, *a, **kw: inputs.append(pages) or []
    ns['_filter_existing_characters'] = lambda suggestions: (suggestions, [])
    assert ns['run_character_detection']() is True
    assert inputs == [[(1, 'Story'), (2, 'Student corrected text')]]
    assert state['_detected_character_input_projection'][1]['projection_status'] == 'legacy_unclassified'


@pytest.mark.parametrize("stop,text,kind", [
    ("max_tokens", '{"characters": []}', "truncated_response"),
    ("end_turn", '{"characters": [{"name": "one"}, {', "salvaged_partial_response"),
])
def test_character_partial_response_fails_with_retained_evidence(monkeypatch, stop, text, kind):
    import utilities
    response = SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))
    monkeypatch.setattr(utilities, "_log_usage", lambda *a, **kw: None)
    with pytest.raises(IncompleteCharacterResponse) as caught:
        utilities._claude_json(client, "offline", model="offline")
    assert caught.value.raw_text == text
    assert caught.value.raw_response is not None
    assert caught.value.failure_kind == kind


def test_malformed_evidence_is_flagged_without_losing_character():
    from character_evidence import validate_character_evidence
    from conventions import project_authors, project_story
    raw = {'characters': [{'name': 'Fox', 'aliases': ['fox'], 'mentions': [
        {'surface': 'fox', 'page': [], 'quote': 'fox', 'decision': {}}]}]}
    result = validate_character_evidence(raw, [(1, 'fox')])
    assert result['characters'][0]['aliases'] == ['fox']
    assert any(issue['status'] == 'wrong_page' for issue in result['evidence_validation']['issues'])
    assert project_authors([{'name': 'Uncertain', 'role': []}]) == []
    assert project_story({}) is None
