"""Tests for the alias article-stripping hotfix (utilities.strip_leading_article).

Auto-detected aliases (and manually-typed ones) sometimes carry a leading
article — "the Butterfly", "a Rabbit". These are stripped so the stored alias is
"Butterfly" / "Rabbit". Only a *leading* article followed by whitespace is
removed, so real names that merely START with those letters ("Theodore",
"Anna", "Andrew") are never truncated. Pure helper — no Streamlit, no network.
"""

from pathlib import Path

import pytest

from utilities import strip_leading_article


@pytest.mark.parametrize("raw,expected", [
    ("the Butterfly", "Butterfly"),
    ("The Owl", "Owl"),
    ("THE Cat", "Cat"),
    ("a Rabbit", "Rabbit"),
    ("An Elephant", "Elephant"),
    ("an owl", "owl"),
    ("  the Fox", "Fox"),          # leading whitespace tolerated
    ("the  Big Bad Wolf", "Big Bad Wolf"),
])
def test_leading_article_is_stripped(raw, expected):
    assert strip_leading_article(raw) == expected


@pytest.mark.parametrize("name", [
    "Butterfly",
    "Peter",
    "Theodore",     # must NOT become "odore" — "the" needs a following space
    "Anna",         # must NOT become "na"
    "Andrew",       # must NOT become "drew"
    "Aardvark",
    "Jack the Ripper",   # only a LEADING article is stripped, not interior
    "Little Red",
])
def test_names_without_a_leading_article_are_unchanged(name):
    assert strip_leading_article(name) == name


def test_bare_article_is_kept():
    # Stripping "the" would leave nothing, so the original is kept rather than
    # producing an empty alias.
    assert strip_leading_article("the") == "the"
    assert strip_leading_article("The") == "The"


def test_non_string_is_returned_unchanged():
    assert strip_leading_article(None) is None
    assert strip_leading_article(123) == 123


def test_review_form_suggestions_are_alias_normalised():
    """Regression lock: the character-detection review form must DISPLAY aliases
    the way they'll be SAVED (leading article stripped), not just strip silently
    on save. pages/enter_text.py runs Streamlit page code at import so it can't
    be imported here; assert instead that _filter_existing_characters — which
    both detection paths (live re-run and precomputed auto-detect) route through
    before the review form — normalises each kept suggestion's aliases via
    _parse_aliases.
    """
    src = (
        Path(__file__).resolve().parent.parent / "pages" / "enter_text.py"
    ).read_text()
    body = src.split("def _filter_existing_characters", 1)[1].split("\ndef ", 1)[0]
    assert "_parse_aliases(" in body and "['aliases'] =" in body, (
        "_filter_existing_characters must normalise each kept suggestion's "
        "aliases (via _parse_aliases) so the review form matches what commit saves"
    )
