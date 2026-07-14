"""Tests for the alias article-stripping hotfix (utilities.strip_leading_article).

Auto-detected aliases (and manually-typed ones) sometimes carry a leading
article — "the Butterfly", "a Rabbit". These are stripped so the stored alias is
"Butterfly" / "Rabbit". Only a *leading* article followed by whitespace is
removed, so real names that merely START with those letters ("Theodore",
"Anna", "Andrew") are never truncated. Pure helper — no Streamlit, no network.
"""

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
