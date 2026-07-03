"""Unit tests for the pure mappers/helpers in ``scripts/import_pilot_data.py``.

These exercise only the pure, no-I/O helpers (mapping, id derivation, title
normalisation, page-range logic and the document builders), so they run without
Firestore / S3 / Anthropic / openpyxl / pypdf installed. The live import path is
NOT tested here — it must never write during CI.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import import_pilot_data as mod  # noqa: E402


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


# --- JSON extraction helper -------------------------------------------------

def test_extract_json_object():
    assert mod._extract_json_object('{"illustrator": "Patrick Benson"}') == \
        {"illustrator": "Patrick Benson"}
    assert mod._extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert mod._extract_json_object("here it is: {\"x\": 2} thanks") == {"x": 2}
    assert mod._extract_json_object("no json here") is None
    assert mod._extract_json_object("") is None
