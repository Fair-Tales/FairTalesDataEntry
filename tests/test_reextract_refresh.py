"""Tests for #198: re-extract must refresh the on-screen text without crashing.

Re-running AI text extraction for the current page (#165) previously wrote the
fresh result straight into ``st.session_state["enter_text_page_text_<n>"]`` and
``st.session_state["enter_text_contains_story_<n>"]`` from inside the re-extract
button's ``if`` branch — i.e. mid-render, AFTER the contains-story checkbox with
that key had already been instantiated. Streamlit forbids assigning to an
already-instantiated widget's key, so every successful re-extract crashed with
``StreamlitAPIException`` (#198).

The fix stages the extracted text/story (``utilities.stage_reextract_refresh``)
and consumes it at the very top of the next script run
(``utilities.consume_reextract_refresh``) — BEFORE the widgets are instantiated,
where writing their keys is legal — seeding the text area and checkbox with the
fresh values. (An earlier version merely POPPED the keys and relied on ``value=``
re-seeding, but that did not reliably replace the on-screen text in production;
the current version writes the values directly.) These are plain session-state
helpers, tested here against plain dicts — no Streamlit widgets, no network.
"""

import re
from pathlib import Path

from utilities import stage_reextract_refresh, consume_reextract_refresh


SUCCESS_MESSAGE = "Re-extracted this page's text."
NEW_TEXT = "Once upon a time there were four little Rabbits."


def test_stage_records_page_text_story_and_flash_message():
    session_state = {}

    stage_reextract_refresh(session_state, 7, SUCCESS_MESSAGE, NEW_TEXT, True)

    assert session_state['_reextract_refresh_page'] == 7
    assert session_state['_reextract_result'] == SUCCESS_MESSAGE
    assert session_state['_reextract_text'] == NEW_TEXT
    assert session_state['_reextract_contains_story'] is True


def test_consume_seeds_only_the_staged_pages_widget_keys():
    # Widget state for pages 6, 7 and 8 exists (the user paged around); only the
    # staged page's keys are overwritten with the fresh result — this is the fix
    # for "re-extract doesn't replace the text". Neighbours keep their state.
    session_state = {
        'enter_text_page_text_6': "page six text",
        'enter_text_contains_story_6': True,
        'enter_text_page_text_7': "stale page seven text",
        'enter_text_contains_story_7': False,
        'enter_text_page_text_8': "page eight text",
        'enter_text_contains_story_8': True,
    }
    stage_reextract_refresh(session_state, 7, SUCCESS_MESSAGE, NEW_TEXT, True)

    assert consume_reextract_refresh(session_state) == 7

    # The staged page's widgets now show the freshly extracted values...
    assert session_state['enter_text_page_text_7'] == NEW_TEXT
    assert session_state['enter_text_contains_story_7'] is True
    # ...and the neighbours are untouched.
    assert session_state['enter_text_page_text_6'] == "page six text"
    assert session_state['enter_text_page_text_8'] == "page eight text"

    # The one-shot staging keys are consumed...
    assert '_reextract_refresh_page' not in session_state
    assert '_reextract_text' not in session_state
    assert '_reextract_contains_story' not in session_state
    # ...but the flash message is left for the text-entry view to display.
    assert session_state['_reextract_result'] == SUCCESS_MESSAGE

    # A second consume (the run after next) is a no-op.
    assert consume_reextract_refresh(session_state) is None


def test_consume_seeds_keys_that_did_not_exist_yet():
    # The refreshed page's widgets may never have been instantiated; consume must
    # create the keys with the fresh values (not raise).
    session_state = {}
    stage_reextract_refresh(session_state, 2, SUCCESS_MESSAGE, NEW_TEXT, False)

    assert consume_reextract_refresh(session_state) == 2
    assert session_state['enter_text_page_text_2'] == NEW_TEXT
    assert session_state['enter_text_contains_story_2'] is False


def test_consume_wordless_reextract_seeds_empty_text():
    # A wordless-page re-extract returns "" — the text area must be seeded blank
    # rather than keep a stale value.
    session_state = {'enter_text_page_text_4': "stale words"}
    stage_reextract_refresh(session_state, 4, SUCCESS_MESSAGE, "", False)

    assert consume_reextract_refresh(session_state) == 4
    assert session_state['enter_text_page_text_4'] == ""
    assert session_state['enter_text_contains_story_4'] is False


def test_consume_is_a_noop_when_nothing_staged():
    session_state = {'enter_text_page_text_3': "typed text"}

    assert consume_reextract_refresh(session_state) is None
    assert session_state == {'enter_text_page_text_3': "typed text"}


def test_enter_text_never_assigns_to_widget_backed_keys():
    """Regression guard for the #198 anti-pattern.

    ``pages/enter_text.py`` executes Streamlit page code at import time, so it
    cannot be imported here; instead scan its source for a direct assignment to
    either per-page widget key. The staged refresh writes those keys inside
    ``utilities.consume_reextract_refresh`` (called at the TOP of the run, before
    the widgets exist — a legal assignment); enter_text.py itself must never
    assign to them mid-render.
    """
    source = (
        Path(__file__).resolve().parent.parent / "pages" / "enter_text.py"
    ).read_text()
    assignment = re.compile(
        r"st\.session_state\[f?['\"]enter_text_(page_text|contains_story)_"
        r"[^\]]*\]\s*=[^=]"
    )
    matches = assignment.findall(source)
    assert not matches, (
        "pages/enter_text.py assigns directly to a widget-backed session key; "
        "use stage_reextract_refresh/consume_reextract_refresh instead (#198)"
    )
