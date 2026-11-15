"""Frozen dev-only evidence, reversible conventions and lossless adapters."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from character_evidence import IncompleteCharacterResponse, validate_character_evidence
from conventions import (DEFAULT_CONVENTIONS, convention_metadata, project_authors,
                         project_regions, project_story)
from text_content.pipeline_prompts import (load_example_pack, prompt_for,
                                          prompt_request_manifest)


# The legacy source file matches dev20-run/run.json's recorded SHA-256
# b4f278964e017ae8e1592e3954302679f48ae916e27c1dc5c9a1d150218f14a6:
# git show 9277ef723d46d22930ea09ed756208233ead74ae:text_content/ai_prompts.py.
# Literal hashes derived from that verified source; request hashes are also
# checked directly against the preserved ledger below (including all 20 books).
LEGACY_PROMPT_HASHES = {
    "character_detection": "61c356bb44df9484872e87943bf0a76e0649aaf848b974bfd8f6b322dee83b3a",
    "page_extraction": "b0476eb036f98511635e90df317384c786de066bc4fec1d616168d2a571ae763",
    "locate_key_pages": "d4192e8bf22bd6c462e7b1ef92216d3224b1512ec9401fa2076af6a661cc3d7e",
    "book_metadata_extraction": "4ac1a67fd86644e256bdc7593d5b3baf10bcbbcce27bf26725195abe6b2819d6",
    "copyright_page_extraction": "1398ae638c53599c4aad5df8fa73cdfa2b67c9e4632bb9a4c4c85e24a0b8fd50",
}

# Captured before A4; pin both existing variants and the application's default.
EXISTING_PROMPT_HASHES = {
    "rules-only": {
        "character_detection": "651b7d5d85c8c57cdf5bc8d4670de8fab2cc75b158b8e096e5ad04df78c4b021",
        "page_extraction": "0d0287d61f0c1426ee9cc1f18ef94a6a32c842f8a88268ff73d3a02c5ef4b9d5",
        "story_classification": "c10ef168da457ad744ad1c52f892572cc561a257a09d48f8391f808b6336edc3",
        "locate_key_pages": "a16a1ecb5d35cb88ed1c8361924252f5786cdbfc4aeaf98bfb132a80fb4ce9b7",
        "joint_metadata_extraction": "54c98c7f72fe1356f1e25d6b0be080f12dcab3791f8ae255a0ddc048253c1687",
    },
    "rules-plus-icl": {
        "character_detection": "596a3eb529adad7ff8c77f89f27848d36bf00f930676343fd401cceac0ea72a5",
        "page_extraction": "78084ce367c9a678fba8475ca6e3c07a34cca495c141067951c77a63d18edc61",
        "story_classification": "e6f08277c325e176eda19ba5e4e13a85591064afd4cb9aa872e535971dddb3b1",
        "locate_key_pages": "a16a1ecb5d35cb88ed1c8361924252f5786cdbfc4aeaf98bfb132a80fb4ce9b7",
        "joint_metadata_extraction": "b28b59d3a301baa6a6d2e758fd19a461fccecfa4224f7459281ec58a21af91ee",
    },
}


@pytest.mark.parametrize("variant", [None, "", "unknown"])
def test_prompt_variant_must_be_known(variant):
    with pytest.raises(ValueError, match="Unknown prompt variant"):
        prompt_for("page_extraction", variant)
    with pytest.raises(ValueError, match="Unknown prompt variant"):
        prompt_request_manifest("page_extraction", variant)


@pytest.mark.parametrize("stage,expected", LEGACY_PROMPT_HASHES.items())
def test_legacy_prompt_hash_matches_verified_baseline_source(stage, expected):
    prompt = prompt_for(stage, "legacy-v1")
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == expected
    manifest = prompt_request_manifest(stage, "legacy-v1")
    assert manifest["prompt_version"] == "legacy-v1"
    assert manifest["rendered_prompt_sha256"] == expected
    assert manifest["example_pack_utf8"] == ""
    assert manifest["examples"] == []


@pytest.mark.parametrize("stage", ["story_classification", "joint_metadata_extraction"])
def test_legacy_does_not_invent_prompts_for_later_stages(stage):
    with pytest.raises(ValueError, match="did not exist in legacy-v1"):
        prompt_for(stage, "legacy-v1")


@pytest.mark.parametrize("variant,hashes", EXISTING_PROMPT_HASHES.items())
def test_existing_prompt_variants_and_live_defaults_are_unchanged(variant, hashes):
    from text_content.ai_prompts import AIPrompts

    for stage, expected in hashes.items():
        assert hashlib.sha256(prompt_for(stage, variant).encode()).hexdigest() == expected
        if variant == "rules-plus-icl":
            assert prompt_for(stage) == prompt_for(stage, variant) == getattr(AIPrompts, stage)
    assert AIPrompts.book_metadata_extraction == prompt_for("joint_metadata_extraction")
    assert AIPrompts.copyright_page_extraction == prompt_for("joint_metadata_extraction")


def test_legacy_rendered_prompts_match_preserved_request_hashes():
    import sqlite3
    from collections import Counter
    from scripts.extraction_eval.evidence import evidence_dir

    evidence = evidence_dir()
    ledger = evidence / "spend.sqlite"
    record = evidence / "dev20-run/run.json"
    if not ledger.exists() or not record.exists():
        pytest.skip("Preserved dev20 evidence is absent")
    recorded = json.loads(record.read_text())
    assert recorded["contract"]["sources"]["text_content/ai_prompts.py"] == (
        "b4f278964e017ae8e1592e3954302679f48ae916e27c1dc5c9a1d150218f14a6")
    selected = set(recorded["contract"]["selection"]["book_ids"])
    templates = {stage: prompt_for(stage, "legacy-v1") for stage in LEGACY_PROMPT_HASHES}
    counts = Counter()
    # Never create WAL/SHM files or write into the preserved evidence folder.
    connection = sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        for (raw,) in connection.execute("SELECT request FROM calls"):
            request = json.loads(raw)
            assert request["context"]["book_id"] in selected
            content = request["request"]["messages"][0]["content"]
            text = (content if isinstance(content, str) else
                    [block["text"] for block in content if block["type"] == "text"][-1])
            for stage, template in templates.items():
                if not text.startswith(template[:80]):
                    continue
                if stage == "character_detection":
                    prefix = template.format(pages_json="")
                    assert text.startswith(prefix)
                    rendered = template.format(pages_json=text[len(prefix):])
                else:
                    rendered = template
                assert hashlib.sha256(rendered.encode()).hexdigest() == hashlib.sha256(text.encode()).hexdigest()
                counts[stage] += 1
    finally:
        connection.close()
    # Unique requests, including three cached page failures, not replay attempts.
    assert counts == {"page_extraction": 750, "character_detection": 20,
                      "locate_key_pages": 20, "book_metadata_extraction": 20,
                      "copyright_page_extraction": 19}


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


def test_committed_examples_are_iterate_books():
    root = Path(__file__).resolve().parents[1]
    selections = json.loads((root / "scripts/extraction_eval/selections.json").read_text())
    pack = json.loads((root / "text_content/pipeline_examples.json").read_text())
    assert all(row["book_id"] in selections["iterate55_ids"] for row in pack["examples"])


def test_app_prompt_import_does_not_grow_harness_coupling():
    result = subprocess.run([sys.executable, "-c", """
import json
from pathlib import Path
import sys

# The live app must not depend on selections, even when that file is broken.
original = Path.open
def checked_open(path, *args, **kwargs):
    assert path.name != 'selections.json', 'App import read evaluation selections'
    return original(path, *args, **kwargs)
Path.open = checked_open
import text_content.ai_prompts
print(json.dumps(sorted(name for name in sys.modules
                        if name.startswith('scripts.extraction_eval.'))))
"""], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
    assert set(json.loads(result.stdout)) <= {
        "scripts.extraction_eval.tuning", "scripts.extraction_eval.store",
        "scripts.extraction_eval.openai_adapter", "scripts.extraction_eval.exposure",
    }


@pytest.mark.parametrize("key", ["held_out_book_ids", "held_out_groups"])
def test_pack_refuses_heldout_books_and_duplicate_groups(key):
    example = load_example_pack()["examples"][0]
    forbidden = example["book_id" if key == "held_out_book_ids" else "holdout_group"]
    with pytest.raises(ValueError, match="leakage"):
        load_example_pack(**{key: [forbidden]})


def test_pack_rejects_changed_source_and_nonverbatim_quotes(monkeypatch, tmp_path):
    import text_content.pipeline_prompts as prompts
    pack = load_example_pack()
    monkeypatch.setattr(prompts, "__file__", str(tmp_path / "pipeline_prompts.py"))
    path = tmp_path / "pipeline_examples.json"
    pack["examples"][0]["quotes"].append("not a printed quotation")
    path.write_text(json.dumps(pack))
    with pytest.raises(ValueError, match="verbatim"):
        prompts.load_example_pack()
    pack["examples"][0]["quotes"].pop()
    pack["examples"][0]["source_text"] += " changed"
    path.write_text(json.dumps(pack))
    with pytest.raises(ValueError, match="hash"):
        prompts.load_example_pack()


@pytest.mark.parametrize("source_id", ["check", "protected", "held-out", "unknown"])
@pytest.mark.parametrize("cohort", ["tune", "rehearsal", "proof"])
def test_render_refuses_non_iterate_example_before_content_decode(
        monkeypatch, tmp_path, source_id, cohort, cohort_loader_factory):
    import text_content.pipeline_prompts as prompts
    from scripts.extraction_eval.json_sources import JsonSources
    from scripts.extraction_eval.tuning import TuningRefused

    loader = cohort_loader_factory(iterate=("iterate",), holdout=("held-out",), cohort=cohort)
    monkeypatch.setattr(prompts, "__file__", str(tmp_path / "pipeline_prompts.py"))
    (tmp_path / "pipeline_examples.json").write_text(json.dumps({
        "development_book_ids": [source_id],
        "development_holdout_groups": ["FORBIDDEN CONTENT"],
        "examples": [{"book_id": source_id, "source_text": "FORBIDDEN CONTENT"}],
    }))
    original_decode = JsonSources.decode
    content_reads = []

    def checked_decode(source, span):
        start, end = span
        if b"FORBIDDEN CONTENT" in source._data[start:end]:
            content_reads.append(span)
        return original_decode(source, span)

    monkeypatch.setattr(JsonSources, "decode", checked_decode)
    with loader.activate():
        with pytest.raises(TuningRefused):
            prompts.prompt_for("character_detection")
    assert content_reads == []
    assert loader.state["refused_attempts"][-1]["source_ids"] == [source_id]
    assert loader.state["refused_attempts"][-1]["kind"] == "prompt_example"


@pytest.mark.parametrize("variant", ["legacy-v1", "rules-only"])
def test_non_icl_manifest_never_opens_examples(monkeypatch, variant):
    import text_content.pipeline_prompts as prompts

    def forbidden_pack_read():
        pytest.fail("Non-ICL prompts must not read example content")

    monkeypatch.setattr(prompts, "load_example_pack", forbidden_pack_read)
    assert prompts.prompt_request_manifest("character_detection", variant)["examples"] == []


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
