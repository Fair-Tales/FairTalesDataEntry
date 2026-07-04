"""Unit tests for the pure mappers/helpers in ``scripts/import_pilot_data.py``.

These exercise only the pure, no-I/O helpers (mapping, id derivation, title
normalisation, page-range logic and the document builders), so they run without
Firestore / S3 / Anthropic / openpyxl / pypdf installed. The live import path is
NOT tested here — it must never write during CI.
"""

import json
import os
import sys
import types
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import import_pilot_data as mod  # noqa: E402

# ``import_pilot_data`` adds the repo root to ``sys.path`` on import, so the
# Streamlit-free continuity module is importable here without network/anthropic.
import ai_continuity  # noqa: E402


# --- title normalisation ----------------------------------------------------

def test_normalise_title_collapses_source_differences():
    # Excel trailing space / case, DB apostrophes, "&" vs "and", PDF filename.
    assert mod.normalise_title("Owl Babies") == "owl babies"
    assert mod.normalise_title("Owl Babies ") == "owl babies"
    assert mod.normalise_title("We're Going on a Bear Hunt") == "we re going on a bear hunt"
    assert mod.normalise_title("Jack & Jill") == mod.normalise_title("Jack and Jill")
    assert mod.normalise_title("The Lighthouse Keeper’s Lunch") == \
        mod.normalise_title("The Lighthouse Keeper's Lunch")
    assert mod.normalise_title(None) == ""


# --- author gender ----------------------------------------------------------

def test_map_author_gender():
    assert mod.map_author_gender("M") == "Man"
    assert mod.map_author_gender("F") == "Woman"
    assert mod.map_author_gender("m") == "Man"
    assert mod.map_author_gender("M/F") == "Unknown"  # multi-author -> Unknown
    assert mod.map_author_gender("") == "Unknown"
    assert mod.map_author_gender(None) == "Unknown"
    # Only ever emits a valid AuthorForm option.
    for code in ["M", "F", "X", "", None, "M/F"]:
        assert mod.map_author_gender(code) in mod.AUTHOR_GENDER_OPTIONS


# --- character gender -------------------------------------------------------

def test_map_character_gender():
    assert mod.map_character_gender("F") == "Female"
    assert mod.map_character_gender("M") == "Male"
    assert mod.map_character_gender("NGS") == "Non-specific"
    assert mod.map_character_gender("ngs") == "Non-specific"
    assert mod.map_character_gender("weird") == "Non-specific"
    for code in ["F", "M", "NGS", "", None, "??"]:
        assert mod.map_character_gender(code) in mod.CHARACTER_GENDER_OPTIONS


# --- human ------------------------------------------------------------------

def test_map_human():
    assert mod.map_human("H") == (True, True)
    assert mod.map_human("NH") == (False, True)
    assert mod.map_human("H ") == (True, True)   # trailing whitespace
    assert mod.map_human("NH ") == (False, True)
    # Stray DB values default to human=True, flagged as unrecognised.
    assert mod.map_human("NO") == (True, False)
    assert mod.map_human("NGS") == (True, False)
    assert mod.map_human(None) == (True, False)


# --- id derivation ----------------------------------------------------------

def test_document_ids_match_data_structures():
    assert mod.book_document_id("Owl Babies") == "owl_babies"
    assert mod.person_document_id("Martin Waddell") == "martin_waddell"
    assert mod.character_document_id("owl_babies", "Sarah") == "owl_babies_sarah"
    assert mod.character_document_id("owl_babies", "Owl Mother") == "owl_babies_owl_mother"
    assert mod.alias_document_id("tabby_mctat", "McTat") == "tabby_mctat_mctat"
    assert mod.page_document_id("owl_babies", 6) == "owl_babies_6"


def test_s3_page_path_uses_raw_title():
    # Matches pages/uploader.py: sawimages/{title}/page_N.jpg (raw title).
    assert mod.s3_page_path("Owl Babies", 3) == "sawimages/Owl Babies/page_3.jpg"
    assert mod.book_photos_url("Owl Babies") == "sawimages/Owl Babies"


# --- name splitting ---------------------------------------------------------

def test_split_name():
    assert mod.split_name("Martin Waddell") == ("Martin", "Waddell")
    assert mod.split_name("Michael Rosen and Helen Oxenbury") == \
        ("Michael Rosen and Helen", "Oxenbury")
    assert mod.split_name("Madonna") == ("Madonna", "")
    assert mod.split_name("") == ("", "")


# --- year + page range ------------------------------------------------------

def test_parse_year():
    assert mod.parse_year(1992) == 1992
    assert mod.parse_year("1989") == 1989
    assert mod.parse_year(1850) is None       # before the form's 1900 floor
    assert mod.parse_year(None) is None
    assert mod.parse_year("n/a") is None


def test_parse_page_range_and_story_page():
    assert mod.parse_page_range(6, 29) == (6, 29)
    assert mod.parse_page_range("6", "29") == (6, 29)
    assert mod.parse_page_range(None, 29) is None
    assert mod.parse_page_range(29, 6) is None  # end before start
    assert mod.is_story_page(6, (6, 29)) is True
    assert mod.is_story_page(29, (6, 29)) is True
    assert mod.is_story_page(5, (6, 29)) is False
    assert mod.is_story_page(30, (6, 29)) is False
    # No range -> every page is a story page.
    assert mod.is_story_page(1, None) is True


# --- document builders ------------------------------------------------------

def _now():
    return datetime(2026, 6, 27, tzinfo=timezone.utc)


def test_build_book_doc_shape():
    now = _now()
    doc = mod.build_book_doc(
        title="Owl Babies",
        author_ref=mod.Ref("authors", "martin_waddell"),
        illustrator_ref=None,
        publisher_ref=None,
        published=1992,
        page_range=(6, 29),
        page_count=34,
        character_refs=[mod.Ref("characters", "owl_babies_sarah")],
        now=now,
    )
    assert doc["title"] == "Owl Babies"
    assert doc["published"] == 1992
    assert doc["validated"] is True
    assert doc["validated_by"] == "pilot_import"
    assert doc["entered_by"] == "pilot_import"
    assert doc["photos_uploaded"] is True
    assert doc["photos_url"] == "sawimages/Owl Babies"
    assert doc["first_content_page"] == 6
    assert doc["last_content_page"] == 29
    assert doc["character_count"] == 1
    assert doc["is_registered"] is True
    # Review fields default to "clean" when not flagged.
    assert doc["needs_review"] is False
    assert doc["review_pages"] == []


def test_build_book_doc_review_flags():
    doc = mod.build_book_doc(
        title="Owl Babies",
        author_ref=None,
        illustrator_ref=None,
        publisher_ref=None,
        published=1992,
        page_range=(6, 29),
        page_count=34,
        character_refs=[],
        now=_now(),
        needs_review=True,
        review_pages=[7, 12],
    )
    assert doc["needs_review"] is True
    assert doc["review_pages"] == [7, 12]


def test_build_character_doc_defaults():
    doc = mod.build_character_doc(
        book_ref=mod.Ref("books", "owl_babies"),
        name="Sarah",
        gender="Female",
        protagonist=True,
        human=False,
        now=_now(),
    )
    assert doc["gender"] == "Female"
    assert doc["human"] is False
    assert doc["protagonist"] is True
    assert doc["plural"] is False          # always False (no source)
    assert doc["ethnicity"] == "Not specified"
    assert doc["disability"] == "Not specified"
    assert doc["entered_by"] == "pilot_import"


def test_build_alias_and_page_docs():
    now = _now()
    alias = mod.build_alias_doc(
        character_ref=mod.Ref("characters", "tabby_mctat_tabby_mctat"),
        book_ref=mod.Ref("books", "tabby_mctat"),
        name="McTat",
        now=now,
    )
    assert alias["name"] == "McTat"
    assert alias["is_registered"] is True

    page = mod.build_page_doc(
        book_ref=mod.Ref("books", "owl_babies"),
        page_number=6,
        contains_story=True,
        text="Once there were three baby owls",
        now=now,
    )
    assert page["page_number"] == 6
    assert page["contains_story"] is True
    assert page["text"].startswith("Once there were")
    # Review fields default to unflagged.
    assert page["needs_review"] is False
    assert page["review_note"] == ""

    flagged = mod.build_page_doc(
        book_ref=mod.Ref("books", "owl_babies"),
        page_number=7,
        contains_story=True,
        text="garbled ~~~ fragment",
        now=now,
        needs_review=True,
        review_note="text is jumbled and out of order",
    )
    assert flagged["needs_review"] is True
    assert flagged["review_note"] == "text is jumbled and out of order"


# --- text cross-check similarity --------------------------------------------

def test_text_similarity():
    # Identical word bags -> 1.0 (order + punctuation + case independent).
    assert mod.text_similarity("The cat sat", "the CAT, sat!") == 1.0
    # Two empty texts count as identical; one empty as disjoint.
    assert mod.text_similarity("", "") == 1.0
    assert mod.text_similarity("cat", "") == 0.0
    assert mod.text_similarity("", "cat") == 0.0
    # Disjoint word sets -> 0.0.
    assert mod.text_similarity("cat dog", "fish bird") == 0.0
    # Partial overlap sits strictly between 0 and 1.
    mid = mod.text_similarity("the cat sat on the mat", "the cat sat on the log")
    assert 0.0 < mid < 1.0
    # A stray junk token barely dents an otherwise-identical long passage.
    base = "twas the night before christmas when all through the house " \
           "not a creature was stirring not even a mouse"
    high = mod.text_similarity(base + " as ij", base)
    assert high > 0.9


# --- blank-text normalisation -----------------------------------------------

def test_normalise_blank_text():
    # Quote/punctuation-only OCR output collapses to a true empty string.
    assert mod.normalise_blank_text('""') == ""
    assert mod.normalise_blank_text('"..."') == ""
    assert mod.normalise_blank_text("   ") == ""
    assert mod.normalise_blank_text("-- .. ") == ""
    assert mod.normalise_blank_text("<br>") == ""       # HTML line-break tag
    assert mod.normalise_blank_text("<br/>\n") == ""
    assert mod.normalise_blank_text("") == ""
    assert mod.normalise_blank_text(None) == ""
    # Any real word is preserved verbatim (including surrounding quotes).
    assert mod.normalise_blank_text("Once upon a time") == "Once upon a time"
    assert mod.normalise_blank_text('"Boo!" said the owl') == '"Boo!" said the owl'
    assert mod.normalise_blank_text("splosh 3 times") == "splosh 3 times"


# --- clean-up divergence guard ----------------------------------------------

def test_clean_kept_guards_against_rewrites():
    original = "Twas the night before Christmas as&ij-\nwhen all through the house"
    # A genuine junk-strip (a few chars removed) is kept.
    good = "Twas the night before Christmas\nwhen all through the house"
    assert mod.clean_kept(original, good) is True
    # A wholesale rewrite is rejected (keep the original).
    rewrite = "It was the evening prior to the Christmas holiday in the dwelling"
    assert mod.clean_kept(original, rewrite) is False
    # An empty/blank "clean" is rejected.
    assert mod.clean_kept(original, "") is False
    assert mod.clean_kept(original, "   ") is False


# --- review derivation ------------------------------------------------------

def test_derive_review():
    # Passes both checks -> not flagged.
    assert mod.derive_review(True, True) == (False, "")
    # Fails both -> high priority.
    assert mod.derive_review(False, False) == (True, "high")
    # Fails exactly one -> normal priority.
    assert mod.derive_review(False, True) == (True, "normal")
    assert mod.derive_review(True, False) == (True, "normal")


def test_derive_review_empty_page_breaking_context_is_high():
    # #73 S7: an empty page (makes_sense=true) that breaks context is the top
    # re-extraction candidate -> HIGH, not normal.
    assert mod.derive_review(True, False, has_text=False) == (True, "high")
    # A page WITH text that merely doesn't fit stays normal.
    assert mod.derive_review(True, False, has_text=True) == (True, "normal")
    # An empty page that DOES fit its context is not flagged.
    assert mod.derive_review(True, True, has_text=False) == (False, "")


# --- page doc carries review_priority ---------------------------------------

def test_build_page_doc_priority_default():
    doc = mod.build_page_doc(
        book_ref=mod.Ref("books", "owl_babies"),
        page_number=6,
        contains_story=True,
        text="Once there were three baby owls",
        now=_now(),
    )
    assert doc["review_priority"] == ""


# --- JSON extraction helper -------------------------------------------------

def test_extract_json_object():
    assert mod._extract_json_object('{"illustrator": "Patrick Benson"}') == \
        {"illustrator": "Patrick Benson"}
    assert mod._extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert mod._extract_json_object("here it is: {\"x\": 2} thanks") == {"x": 2}
    assert mod._extract_json_object("no json here") is None
    assert mod._extract_json_object("") is None
    # #73 M6: a literal newline inside a JSON string value must not defeat the
    # parse (strict=False), so a stray line break is tolerated.
    assert mod._extract_json_object('{"text": "line one\nline two"}') == \
        {"text": "line one\nline two"}


# --- containment metric (#73 S3) --------------------------------------------

def test_text_containment():
    # Full reference captured (plus extra) -> 1.0 (not penalised for excess).
    assert mod.text_containment("the cat sat on the mat", "the cat sat") == 1.0
    # Half of the reference words captured.
    assert mod.text_containment("the cat", "the cat sat dog") == 0.5
    # Disjoint -> 0.0; empty reference -> 1.0 (nothing to capture).
    assert mod.text_containment("cat", "dog fish") == 0.0
    assert mod.text_containment("anything", "") == 1.0
    assert mod.text_containment("", "cat") == 0.0
    # Asymmetric: containment ignores extra extracted text where Jaccard would
    # penalise it.
    long_extract = "the cat sat on the mat and then it ran away quickly"
    assert mod.text_containment(long_extract, "the cat sat") == 1.0
    assert mod.text_similarity(long_extract, "the cat sat") < 1.0


# --- token-subset clean guard (#73 S4) --------------------------------------

def test_clean_kept_token_subset():
    # Pure removal of junk tokens is accepted.
    assert mod.clean_kept("Once upon a time as&ij there", "Once upon a time there") is True
    # Removing whole words is fine (only-remove rule).
    assert mod.clean_kept("Boo said the little owl", "Boo said owl") is True
    # Any ADDED word is rejected.
    assert mod.clean_kept("Boo said owl", "Boo said the owl") is False
    # Any ALTERED word (incl. a spelling/case change) is rejected.
    assert mod.clean_kept("the wonky spelling", "the wonderful spelling") is False
    assert mod.clean_kept("SPLOSH went the water", "Splosh went the water") is False
    # Empty / blank clean rejected.
    assert mod.clean_kept("some text", "") is False
    assert mod.clean_kept("some text", "   ") is False
    # Invented/non-standard words preserved verbatim are kept.
    assert mod.clean_kept("Ker-SPLOOSH!! ~~~", "Ker-SPLOOSH!!") is True


# --- boolean parsing (#73 S6) -----------------------------------------------

def test_as_bool():
    assert mod._as_bool(True) is True
    assert mod._as_bool(False) is False
    # The classic bug: bool("false") is True; _as_bool must return False.
    assert mod._as_bool("false") is False
    assert mod._as_bool("true") is True
    assert mod._as_bool("no") is False
    assert mod._as_bool("yes") is True
    # Unknown -> default.
    assert mod._as_bool(None, default=True) is True
    assert mod._as_bool("maybe", default=False) is False


# --- case-insensitive Unknown guard (#73 S1) --------------------------------

def test_is_unknown():
    for v in ["", "Unknown", "unknown", "UNKNOWN", "n/a", "N/A", "none", "null", None]:
        assert mod.is_unknown(v) is True
    for v in ["Walker Books", "Patrick Benson", "Anonymous"]:
        assert mod.is_unknown(v) is False


# --- neighbour context / boundary flag (#73 M4) -----------------------------

def test_neighbour_context():
    # Real text is returned verbatim.
    assert mod.neighbour_context("hello", is_previous=True, at_book_edge=False) == "hello"
    # Empty at a true book edge reads as first/last page.
    assert "first story page" in mod.neighbour_context("", is_previous=True, at_book_edge=True)
    assert "last story page" in mod.neighbour_context("", is_previous=False, at_book_edge=True)
    # Empty at an INTERIOR neighbour reads as wordless/failed, NOT a boundary.
    interior_prev = mod.neighbour_context("", is_previous=True, at_book_edge=False)
    interior_next = mod.neighbour_context("", is_previous=False, at_book_edge=False)
    assert "no text" in interior_prev and "first story page" not in interior_prev
    assert "no text" in interior_next and "last story page" not in interior_next


# --- multi-author gender recovery (#73 S2) ----------------------------------

def test_parse_author_field_single_author_unchanged():
    p = mod.parse_author_field("Martin Waddell", "M")
    assert p == mod.AuthorParse("Martin Waddell", "M", "", False, "")
    assert mod.map_author_gender(p.gender_code) == "Man"


def test_parse_author_field_two_authors_recovers_first_gender():
    p = mod.parse_author_field("Michael Rosen and Helen Oxenbury", "M/F")
    assert p.author_name == "Michael Rosen"
    assert p.gender_code == "M"
    assert p.second_name == "Helen Oxenbury"
    assert p.needs_review is False
    assert mod.map_author_gender(p.gender_code) == "Man"

    p2 = mod.parse_author_field("Julia Donaldson and Axel Scheffler", "F/M")
    assert p2.author_name == "Julia Donaldson"
    assert mod.map_author_gender(p2.gender_code) == "Woman"


def test_parse_author_field_shared_surname_flags_for_manual_entry():
    # "Janet and Allen Ahlberg" — first part is a bare forename; do NOT import
    # "Janet" as an author, flag for manual entry instead.
    p = mod.parse_author_field("Janet and Allen Ahlberg", "F/M")
    assert p.author_name == ""
    assert p.needs_review is True
    assert p.second_name == "Allen Ahlberg"
    p2 = mod.parse_author_field("Ronda and David Armitage", "F/M")
    assert p2.author_name == "" and p2.needs_review is True


def test_parse_author_field_three_codes_unknown_and_flag():
    p = mod.parse_author_field("A and B and C", "M/F/M")
    assert p.needs_review is True
    assert mod.map_author_gender(p.gender_code) == "Unknown"


# --- provenance + empty-book flags (#73 S8/M3) ------------------------------

def test_build_page_doc_provenance_fields():
    doc = mod.build_page_doc(
        book_ref=mod.Ref("books", "owl_babies"),
        page_number=6,
        contains_story=True,
        text="Once there were three baby owls",
        now=_now(),
        text_source="ocr",
        clean_status="cleaned",
        ocr_model="claude-opus-4-8",
        clean_model="claude-sonnet-4-6",
    )
    assert doc["text_source"] == "ocr"
    assert doc["clean_status"] == "cleaned"
    assert doc["ocr_model"] == "claude-opus-4-8"
    assert doc["clean_model"] == "claude-sonnet-4-6"


def test_build_book_doc_no_photos_for_empty_book():
    # #73 M3: a book with no PDF/pages must NOT claim photos exist.
    doc = mod.build_book_doc(
        title="Seasons",
        author_ref=None,
        illustrator_ref=None,
        publisher_ref=None,
        published=None,
        page_range=None,
        page_count=0,
        character_refs=[],
        now=_now(),
        photos_uploaded=False,
        needs_review=True,
        review_note="no PDF / page images for this book",
    )
    assert doc["photos_uploaded"] is False
    assert doc["photos_url"] == ""
    assert doc["needs_review"] is True
    assert doc["review_note"] == "no PDF / page images for this book"
    # Normal complete books still keep validated=True.
    assert doc["validated"] is True


# --- neighbour-continuity OCR skip (DECISIONS 010) --------------------------

def test_default_ocr_model_is_sonnet_5():
    # The validated cost optimisation switches the default OCR model to Sonnet 5.
    assert mod.DEFAULT_OCR_MODEL == "claude-sonnet-5"
    # The continuity judge defaults to a Sonnet model (as validated).
    assert mod.DEFAULT_CONTINUITY_MODEL == "claude-sonnet-4-6"


def test_flanked_by_text_layer_flanked_edge_and_run():
    # A run of 5 story pages, positions 0..4; only interior pages with text-layer
    # neighbours on BOTH sides qualify for a continuity OCR skip.
    all_text = [True, True, True, True, True]
    # Interior page flanked by text-layer pages on both sides -> candidate.
    assert mod.flanked_by_text_layer(2, 5, all_text) is True
    # Story-range EDGES never qualify (no neighbour on one side).
    assert mod.flanked_by_text_layer(0, 5, all_text) is False
    assert mod.flanked_by_text_layer(4, 5, all_text) is False
    # An image-only NEIGHBOUR disqualifies (covers consecutive image-only runs):
    #   page 2's previous neighbour (pos 1) is image-only.
    run = [True, False, False, False, True]
    assert mod.flanked_by_text_layer(2, 5, run) is False
    # page 1's next neighbour (pos 2) is image-only, and pos 3 too -> no skip.
    assert mod.flanked_by_text_layer(1, 5, run) is False
    assert mod.flanked_by_text_layer(3, 5, run) is False
    # Single-page and two-page ranges have no interior page -> never skip.
    assert mod.flanked_by_text_layer(0, 1, [True]) is False
    assert mod.flanked_by_text_layer(0, 2, [True, True]) is False
    assert mod.flanked_by_text_layer(1, 2, [True, True]) is False


def test_should_skip_ocr_pure_rule():
    # Skip ONLY when the story flows AND no text appears missing.
    assert ai_continuity.should_skip_ocr(
        {"flows_continuously": True, "text_appears_missing": False}
    ) is True
    # A narrative gap -> OCR.
    assert ai_continuity.should_skip_ocr(
        {"flows_continuously": False, "text_appears_missing": True}
    ) is False
    # Flows, but text still looks missing -> OCR (never skip on doubt).
    assert ai_continuity.should_skip_ocr(
        {"flows_continuously": True, "text_appears_missing": True}
    ) is False
    # Malformed / empty verdicts -> OCR.
    assert ai_continuity.should_skip_ocr({}) is False
    assert ai_continuity.should_skip_ocr(None) is False
    assert ai_continuity.should_skip_ocr("nope") is False


class _FakeMessages:
    """Minimal stand-in for ``client.messages`` with an ``output_config`` kwarg
    (so structured outputs are used) that returns a canned reply or raises."""

    def __init__(self, raw=None, exc=None):
        self._raw = raw
        self._exc = exc

    def create(self, *, model, max_tokens, messages, output_config=None):
        if self._exc is not None:
            raise self._exc
        block = types.SimpleNamespace(type="text", text=self._raw)
        return types.SimpleNamespace(content=[block])


class _FakeClient:
    def __init__(self, raw=None, exc=None):
        self.messages = _FakeMessages(raw, exc)


def test_check_narrative_continuity_skip_verdict():
    reply = json.dumps({
        "flows_continuously": True,
        "text_appears_missing": False,
        "confidence": "high",
        "expected_middle": "wordless illustration",
        "reason": "PREV reads straight into NEXT",
    })
    verdict = ai_continuity.check_narrative_continuity(
        _FakeClient(raw=reply), "prev text", "next text", model="claude-sonnet-4-6"
    )
    assert verdict["flows_continuously"] is True
    assert verdict["text_appears_missing"] is False
    assert verdict["confidence"] == "high"
    assert ai_continuity.should_skip_ocr(verdict) is True


def test_check_narrative_continuity_missing_text_forces_ocr():
    reply = json.dumps({
        "flows_continuously": False,
        "text_appears_missing": True,
        "confidence": "high",
        "expected_middle": "the wolf's reply",
        "reason": "a reply is clearly skipped",
    })
    verdict = ai_continuity.check_narrative_continuity(
        _FakeClient(raw=reply), "prev", "next", model="m"
    )
    assert ai_continuity.should_skip_ocr(verdict) is False


def test_check_narrative_continuity_judge_error_forces_ocr():
    # Fail-safe: any API error degrades to an OCR-forcing verdict (never a skip).
    verdict = ai_continuity.check_narrative_continuity(
        _FakeClient(exc=RuntimeError("boom")), "prev", "next", model="m"
    )
    assert verdict["flows_continuously"] is False
    assert verdict["text_appears_missing"] is True
    assert "failed" in verdict["reason"].lower()
    assert ai_continuity.should_skip_ocr(verdict) is False


def test_check_narrative_continuity_non_json_forces_ocr():
    # An unparseable reply also degrades safely to OCR.
    verdict = ai_continuity.check_narrative_continuity(
        _FakeClient(raw="I cannot answer that."), "prev", "next", model="m"
    )
    assert ai_continuity.should_skip_ocr(verdict) is False


def test_build_page_doc_records_continuity_verdict():
    verdict = {
        "flows_continuously": True,
        "text_appears_missing": False,
        "confidence": "high",
        "reason": "flows through",
    }
    doc = mod.build_page_doc(
        book_ref=mod.Ref("books", "owl_babies"),
        page_number=8,
        contains_story=True,
        text="",
        now=_now(),
        text_source="skipped_wordless",
        continuity=verdict,
    )
    assert doc["text_source"] == "skipped_wordless"
    assert doc["continuity"] == verdict
    # A normal page carries no continuity verdict.
    plain = mod.build_page_doc(
        book_ref=mod.Ref("books", "owl_babies"),
        page_number=6,
        contains_story=True,
        text="Once there were three baby owls",
        now=_now(),
    )
    assert plain["continuity"] is None
