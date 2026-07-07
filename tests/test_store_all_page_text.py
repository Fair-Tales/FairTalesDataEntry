"""Regression test for #195 — store ALL detected page text, not just story text.

A copyright / publisher / front-or-back-matter page carries the book's own
printed text but is NOT a story page (``is_story_page`` / ``contains_story``
stays ``false``). Before #195 the extraction prompt marked ``has_text`` false on
such a page because it had no *story* text, and ``attempt_page_extraction``'s
``has_text`` guard then blanked the transcribed text — discarding the copyright
text even though the AI had read it.

This locks in the decoupled semantics at the consume path in
``pages.uploader.attempt_page_extraction``:

* ``has_text=true`` + ``is_story_page=false`` (a copyright page) -> the
  transcribed text is PRESERVED, story flag stays false.
* ``has_text=false`` (a genuinely wordless illustration) -> text is still
  blanked.

Exercised against an in-memory ``vision_json`` stub — no network, no Streamlit
runtime — the same style as ``tests/test_uploader_page_isolation.py``.
"""

import pytest

import pages.uploader as uploader
from utilities import AI_SETTINGS_DEFAULTS


@pytest.fixture
def ai_settings():
    return dict(AI_SETTINGS_DEFAULTS)


def _stub_vision_json(monkeypatch, reply):
    """Make ``uploader.vision_json`` return ``(reply, raw)`` without any real
    Anthropic call, so we test the pure parse/normalise logic."""
    monkeypatch.setattr(
        uploader, "vision_json",
        lambda *a, **k: (reply, "<raw>"),
    )


def test_copyright_page_text_is_preserved(monkeypatch, ai_settings):
    """has_text=true + is_story_page=false must keep the transcribed text."""
    _stub_vision_json(monkeypatch, {
        "has_text": True,
        "text": "First published in 2019 by Example Press. ISBN 978-0-00-000000-0.",
        "is_story_page": False,
        "page_type": "copyright",
    })

    status, payload = uploader.attempt_page_extraction(
        b"page-bytes", object(), ai_settings, label="test",
    )

    assert status == "ok"
    text, is_story_page, page_type = payload
    # The copyright text is NOT discarded even though the page is non-story.
    assert text == "First published in 2019 by Example Press. ISBN 978-0-00-000000-0."
    assert is_story_page is False
    assert page_type == "copyright"


def test_wordless_page_text_is_blanked(monkeypatch, ai_settings):
    """has_text=false (a genuinely wordless illustration) still blanks text,
    even if the model narrated the illustration into ``text``."""
    _stub_vision_json(monkeypatch, {
        "has_text": False,
        "text": "A rabbit hops across a green meadow.",  # illustration narration
        "is_story_page": True,
        "page_type": "story",
    })

    status, payload = uploader.attempt_page_extraction(
        b"page-bytes", object(), ai_settings, label="test",
    )

    assert status == "ok"
    text, is_story_page, page_type = payload
    assert text == ""
    assert is_story_page is True


def test_story_page_text_is_preserved(monkeypatch, ai_settings):
    """The ordinary story-page happy path is unchanged."""
    _stub_vision_json(monkeypatch, {
        "has_text": True,
        "text": "Once upon a time, a small rabbit lived in the forest.",
        "is_story_page": True,
        "page_type": "story",
    })

    status, payload = uploader.attempt_page_extraction(
        b"page-bytes", object(), ai_settings, label="test",
    )

    assert status == "ok"
    text, is_story_page, page_type = payload
    assert text == "Once upon a time, a small rabbit lived in the forest."
    assert is_story_page is True
    assert page_type == "story"
