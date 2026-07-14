"""Tests for #198: re-extract must not assign to widget-backed session keys.

Re-running AI text extraction for the current page (#165) previously wrote the
fresh result straight into ``st.session_state["enter_text_page_text_<n>"]`` and
``st.session_state["enter_text_contains_story_<n>"]`` from inside the
re-extract button's ``if`` branch — i.e. mid-render, AFTER the contains-story
checkbox with that key had already been instantiated. Streamlit forbids
assigning to an already-instantiated widget's key, so every successful
re-extract crashed with ``StreamlitAPIException`` (#198).

The fix stages the refresh instead (``utilities.stage_reextract_refresh``) and
consumes it at the very top of the next script run
(``utilities.consume_reextract_refresh``), popping the widget keys before the
widgets exist so they re-seed from the freshly written-through
``current_page`` values. These are plain session-state helpers (same pattern
as the character-autodetect staging helpers, #129), tested here against plain
dicts — no Streamlit widgets, no network, no writes.
"""

import re
from pathlib import Path

from utilities import stage_reextract_refresh, consume_reextract_refresh


SUCCESS_MESSAGE = "Re-extracted this page's text."


def test_stage_records_page_and_flash_message():
    session_state = {}

    stage_reextract_refresh(session_state, 7, SUCCESS_MESSAGE)

    assert session_state['_reextract_refresh_page'] == 7
    assert session_state['_reextract_result'] == SUCCESS_MESSAGE


def test_consume_pops_only_the_staged_pages_widget_keys():
    # Widget state for pages 6, 7 and 8 exists (user paged around); only the
    # staged page's keys may be dropped, so the other pages keep their state.
    session_state = {
        'enter_text_page_text_6': "page six text",
        'enter_text_contains_story_6': True,
        'enter_text_page_text_7': "stale page seven text",
        'enter_text_contains_story_7': False,
        'enter_text_page_text_8': "page eight text",
        'enter_text_contains_story_8': True,
    }
    stage_reextract_refresh(session_state, 7, SUCCESS_MESSAGE)

    assert consume_reextract_refresh(session_state) == 7

    # The staged page's widget keys are gone (so its widgets re-seed from
    # value=), the neighbours are untouched.
    assert 'enter_text_page_text_7' not in session_state
    assert 'enter_text_contains_story_7' not in session_state
    assert session_state['enter_text_page_text_6'] == "page six text"
    assert session_state['enter_text_contains_story_8'] is True

    # The one-shot flag is consumed...
    assert '_reextract_refresh_page' not in session_state
    # ...but the flash message is left for the text-entry view to pop.
    assert session_state['_reextract_result'] == SUCCESS_MESSAGE

    # A second consume (the run after next) is a no-op.
    assert consume_reextract_refresh(session_state) is None


def test_consume_is_a_noop_when_nothing_staged():
    session_state = {'enter_text_page_text_3': "typed text"}

    assert consume_reextract_refresh(session_state) is None
    assert session_state == {'enter_text_page_text_3': "typed text"}


def test_consume_handles_missing_widget_keys():
    # The refreshed page's widgets may never have been instantiated (e.g. the
    # rerun landed after session churn) — consuming must not raise.
    session_state = {'_reextract_refresh_page': 2}

    assert consume_reextract_refresh(session_state) == 2
    assert session_state == {}


def test_enter_text_never_assigns_to_widget_backed_keys():
    """Regression guard for the #198 anti-pattern.

    ``pages/enter_text.py`` executes Streamlit page code at import time, so it
    cannot be imported here; instead scan its source for a direct assignment
    to either per-page widget key. Popping (``.pop(...)``) is fine — only
    ``st.session_state[<widget key>] = ...`` is the crash.
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
