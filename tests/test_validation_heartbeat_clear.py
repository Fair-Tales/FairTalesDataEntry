"""Validation review lifecycle, exercising real Book lease transactions.

The import shim and rerun sentinel keep the Streamlit page executable without
services. Persistence uses the versioned transaction fake from the model tests;
editor spies prove stale submitted forms cannot bypass the ownership gate.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import streamlit as st
from google.api_core.exceptions import Aborted, ServiceUnavailable

import data_structures.book as book_module
import utilities
from validation_lease_fakes import FakeLeaseStore


class _AttrDict(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class _FakeSecrets(dict):
    def __contains__(self, _key):
        return False


class _FakeDoc:
    def __init__(self, exists=True, data=None):
        self.exists = exists
        self._data = data

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _ImportFirestore:
    def get_by_reference(self, collection, document_ref):
        return _FakeDoc(exists=False)


# Retain the import shim: neutralise authentication/layout and dispatch through
# the missing-document early exit, then immediately restore all patched globals.
_real_secrets = st.secrets
_real_session_state = st.session_state
_real_rerun = st.rerun
_real_check = utilities.check_authentication_status
_real_layout = utilities.page_layout
_real_team = utilities.is_team_or_above
st.secrets = _FakeSecrets()
st.session_state = _AttrDict(
    authentication_status=True, username="importer",
    _validation_book_id="__import__", firestore=_ImportFirestore(),
)
st.rerun = lambda *a, **k: None
utilities.check_authentication_status = lambda *a, **k: None
utilities.page_layout = lambda *a, **k: None
utilities.is_team_or_above = lambda: True
try:
    import pages.validation as validation  # noqa: E402
finally:
    st.secrets = _real_secrets
    st.session_state = _real_session_state
    st.rerun = _real_rerun
    utilities.check_authentication_status = _real_check
    utilities.page_layout = _real_layout
    utilities.is_team_or_above = _real_team


class _Rerun(Exception):
    """Unwind the page where Streamlit requests a new render."""


class _DummyTab:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSt:
    def __init__(self, session_state):
        self.session_state = session_state
        self.clicked = None
        self.button_hooks = {}
        self.button_keys = []
        self.warnings = []
        self.errors = []
        self.successes = []
        self.headers = []

    def button(self, label, *, key, **kwargs):
        self.button_keys.append(key)
        clicked = key == self.clicked and not kwargs.get("disabled", False)
        hook = self.button_hooks.pop(key, None) if clicked else None
        if hook is not None:
            hook()
        return clicked

    def toggle(self, label, *, value, key, **kwargs):
        return self.session_state.get(key, value)

    def radio(self, label, *, options, key, **kwargs):
        return self.session_state.get(key, options[0])

    def selectbox(self, label, *, options, key, **kwargs):
        return self.session_state.get(key, options[0])

    def tabs(self, labels):
        return [_DummyTab() for _ in labels]

    def warning(self, msg):
        self.warnings.append(msg)

    def error(self, msg):
        self.errors.append(msg)

    def success(self, msg):
        self.successes.append(msg)

    def header(self, msg):
        self.headers.append(msg)

    def _output(self, *args, **kwargs):
        pass

    caption = write = subheader = divider = info = _output

    def rerun(self, *args, **kwargs):
        raise _Rerun()


BOOK_ID = "the_test_book"
BOOK_PATH = f"books/{BOOK_ID}"
USERNAME = "chris@example.com"
TOKEN = "review-session-a"
EDITOR_NAMES = ("metadata_editor", "page_text_editor", "characters_editor")


def _lease(token=TOKEN, username=USERNAME, *, when=None):
    return {
        "validation_active_token": token,
        "validation_active_by": username,
        "validation_active_at": when or datetime.now(timezone.utc),
    }


def _review_state(env):
    """Local state which must survive unsuccessful persistence."""
    return {
        key: dict(env.state[key]) if isinstance(env.state[key], dict) else env.state[key]
        for key in (
            "_validation_book_id", "_validation_review_session", "_validation_heartbeat"
        )
    }


def _environment(monkeypatch, *, data=None, token=TOKEN, username=USERNAME,
                 throttle_due=False):
    stored = {
        "title": "The Test Book", "is_registered": True,
        "validated": False, "validated_by": None,
        "validation_active_at": -1, "validation_active_by": None,
        "validation_active_token": None,
    }
    stored.update(data or {})
    store = FakeLeaseStore(stored, book_id=BOOK_ID)
    state = _AttrDict(
        _validation_book_id=BOOK_ID,
        _validation_review_session={
            "token": token, "book_id": BOOK_ID, "username": username,
        },
        _validation_heartbeat={
            BOOK_ID: 1.0 if throttle_due else datetime.now(timezone.utc).timestamp(),
        },
        username=username, firestore=store.firestore,
    )
    fake_st = FakeSt(state)
    monkeypatch.setattr(st, "session_state", state)
    monkeypatch.setattr(validation, "st", fake_st)
    monkeypatch.setattr(book_module.firestore, "transactional", store.transactional)
    editor_calls = []
    for name in EDITOR_NAMES:
        monkeypatch.setattr(
            validation, name,
            lambda book, editor_name=name: editor_calls.append((editor_name, book)),
        )

    def run(*, list_view=False):
        try:
            (validation.render_list if list_view else validation.render_review)()
        except _Rerun:
            pass

    return SimpleNamespace(
        store=store, state=state, st=fake_st, editor_calls=editor_calls, run=run,
    )


def _assert_exited(env):
    assert "_validation_book_id" not in env.state
    assert "_validation_review_session" not in env.state
    assert BOOK_ID not in env.state.get("_validation_heartbeat", {})


def test_back_to_list_clears_all_lease_fields_atomically(monkeypatch):
    env = _environment(monkeypatch, data=_lease())
    env.st.clicked = "validation_back_to_list_button"
    env.run()
    saved = env.store.documents[BOOK_PATH]
    assert saved["validation_active_at"] == -1
    assert saved["validation_active_by"] is None
    assert saved["validation_active_token"] is None
    assert len(env.store.writes) == 1
    assert set(env.store.writes[0][1]) == {
        "validation_active_at", "validation_active_by", "validation_active_token",
        "last_updated",
    }
    assert env.editor_calls == []
    _assert_exited(env)


def test_approve_atomically_approves_and_releases(monkeypatch):
    env = _environment(monkeypatch, data=_lease())
    env.st.clicked = "validation_approve_button"
    env.run()
    saved = env.store.documents[BOOK_PATH]
    assert saved["validated"] is True
    assert saved["validated_by"].path == f"users/{USERNAME}"
    assert saved["validation_active_at"] == -1
    assert saved["validation_active_by"] is None
    assert saved["validation_active_token"] is None
    assert len(env.store.writes) == 1
    assert set(env.store.writes[0][1]) == {
        "validated", "validated_by", "validation_active_at", "validation_active_by",
        "validation_active_token", "last_updated",
    }
    assert env.st.successes
    _assert_exited(env)


@pytest.mark.parametrize("second_username", ["martha@example.com", USERNAME])
def test_other_review_opens_and_backs_out_without_changing_owner(monkeypatch, second_username):
    env = _environment(monkeypatch)
    env.run()  # A acquires through the real transaction callback.
    original = dict(env.store.documents[BOOK_PATH])
    writes = list(env.store.writes)
    env.editor_calls.clear()
    env.state["username"] = second_username
    env.state["_validation_review_session"] = {
        "token": "review-session-b", "book_id": BOOK_ID, "username": second_username,
    }
    env.st.clicked = "validation_back_to_list_button"
    env.run()
    assert env.store.documents[BOOK_PATH] == original
    assert env.store.writes == writes
    assert env.editor_calls == []
    assert env.st.warnings
    _assert_exited(env)


def test_foreign_live_heartbeat_names_owner_and_shows_stable_retry(monkeypatch):
    env = _environment(monkeypatch, data=_lease("other-token", "martha@example.com"))
    env.run()
    assert any("martha@example.com" in warning for warning in env.st.warnings)
    assert env.st.headers
    assert env.editor_calls == []
    assert "validation_retry_review_button" in env.st.button_keys
    assert "validation_back_to_list_button" in env.st.button_keys
    assert all(TOKEN not in key for key in env.st.button_keys)
    assert env.store.writes == []


def test_stale_foreign_heartbeat_can_be_claimed(monkeypatch):
    stale = datetime.now(timezone.utc) - timedelta(
        minutes=utilities.VALIDATION_ACTIVITY_WINDOW_MINUTES + 5,
    )
    env = _environment(
        monkeypatch, data=_lease("other-token", "martha@example.com", when=stale),
    )
    env.run()
    assert env.st.warnings == []
    assert len(env.editor_calls) == 3
    assert env.store.documents[BOOK_PATH]["validation_active_token"] == TOKEN
    assert len(env.store.writes) == 1


def test_matching_token_and_username_does_not_warn(monkeypatch):
    env = _environment(monkeypatch, data=_lease())
    env.run()
    assert env.st.warnings == []
    assert len(env.editor_calls) == 3


@pytest.mark.parametrize("book_state", [
    {"entry_status": "started", "validated": False},
    {"entry_status": "completed", "validated": True},
])
def test_review_keeps_in_progress_and_already_validated_books_editable(monkeypatch, book_state):
    env = _environment(monkeypatch, data=book_state)
    env.run()
    assert len(env.editor_calls) == 3
    assert env.store.documents[BOOK_PATH]["validation_active_token"] == TOKEN
    assert env.st.warnings == []


@pytest.mark.parametrize("legacy_username", [USERNAME, "martha@example.com", None])
def test_live_tokenless_legacy_marker_blocks_even_its_named_user(monkeypatch, legacy_username):
    # Deliberately changes #235's username-only expectation: the username cannot
    # establish which browser tab owns a live legacy heartbeat.
    env = _environment(monkeypatch, data=_lease(None, legacy_username))
    original = dict(env.store.documents[BOOK_PATH])
    env.run()
    assert env.editor_calls == []
    assert env.st.warnings
    assert env.store.documents[BOOK_PATH] == original
    assert env.store.writes == []


@pytest.mark.parametrize("submitted_key", [
    "validation_meta_submit_button", "validation_page_submit_button",
    "validation_char_submit_character", "validation_alias_submit_alias",
    "validation_approve_button",
])
def test_stale_submitted_form_is_refused_before_any_editor(monkeypatch, submitted_key):
    env = _environment(monkeypatch, data=_lease())
    env.run()
    env.editor_calls.clear()
    env.store.write(_lease("new-owner", "martha@example.com"))
    original = dict(env.store.documents[BOOK_PATH])
    writes = list(env.store.writes)
    env.state[submitted_key] = True
    env.state["validation_page_text_1"] = "stale submitted transcription"
    env.st.clicked = submitted_key
    env.run()
    assert env.editor_calls == []
    assert env.store.documents[BOOK_PATH] == original
    assert env.store.writes == writes
    assert env.st.successes == []


def _exhausted_contention():
    error = ValueError("transaction attempts exhausted")
    error.__cause__ = Aborted("conflicting write")
    return error


@pytest.mark.parametrize("error_factory", [
    lambda: ServiceUnavailable("datastore unavailable"), _exhausted_contention,
])
def test_failed_refresh_keeps_throttle_and_blocks_editors(monkeypatch, error_factory):
    env = _environment(monkeypatch, data=_lease(), throttle_due=True)
    before = _review_state(env)
    original = dict(env.store.documents[BOOK_PATH])
    env.store.failure = error_factory()
    env.run()
    assert _review_state(env) == before
    assert env.store.documents[BOOK_PATH] == original
    assert env.store.writes == []
    assert env.editor_calls == []
    assert env.st.errors
    assert env.st.successes == []


@pytest.mark.parametrize("button_key", [
    "validation_back_to_list_button", "validation_approve_button",
])
@pytest.mark.parametrize("error_factory", [
    lambda: ServiceUnavailable("datastore unavailable"), _exhausted_contention,
])
def test_failed_release_or_approval_retains_review_state(monkeypatch, button_key, error_factory):
    env = _environment(monkeypatch, data=_lease())
    before = _review_state(env)
    original = dict(env.store.documents[BOOK_PATH])
    env.st.clicked = button_key
    env.st.button_hooks[button_key] = lambda: setattr(env.store, "failure", error_factory())
    env.run()
    assert _review_state(env) == before
    assert env.store.documents[BOOK_PATH] == original
    assert env.store.writes == []
    assert env.st.errors
    assert env.st.successes == []


def test_approval_after_ownership_lost_cannot_approve_or_clear_new_owner(monkeypatch):
    env = _environment(monkeypatch, data=_lease())
    before = _review_state(env)
    env.st.clicked = "validation_approve_button"
    new_lease = _lease("new-owner", "martha@example.com")
    env.st.button_hooks[env.st.clicked] = lambda: env.store.write(new_lease)
    env.run()
    saved = env.store.documents[BOOK_PATH]
    assert all(saved[field] == value for field, value in new_lease.items())
    assert saved["validated"] is False
    assert saved["validated_by"] is None
    assert _review_state(env) == before
    assert env.st.successes == []
    assert env.st.warnings or env.st.errors


def test_within_throttle_always_verifies_without_heartbeat_write(monkeypatch):
    env = _environment(monkeypatch, data=_lease())
    initial_attempts = env.store.attempts
    initial_throttle = dict(env.state["_validation_heartbeat"])
    env.run()
    first_attempts = env.store.attempts
    env.run()
    assert first_attempts > initial_attempts
    assert env.store.attempts > first_attempts
    assert env.store.writes == []
    assert env.state["_validation_heartbeat"] == initial_throttle
    assert len(env.editor_calls) == 6


def test_missing_document_cleans_local_review_without_recreation(monkeypatch):
    env = _environment(monkeypatch, data=_lease())
    env.store.delete()
    env.run()
    assert BOOK_PATH not in env.store.documents
    assert env.store.writes == []
    assert env.editor_calls == []
    _assert_exited(env)


def test_missing_session_token_cannot_release_persisted_lease(monkeypatch):
    env = _environment(monkeypatch, data=_lease())
    env.state.pop("_validation_review_session")
    before = dict(env.store.documents[BOOK_PATH])
    validation._clear_validation_heartbeat(env.store.make_book())
    assert env.store.documents[BOOK_PATH] == before
    assert env.store.writes == []


def test_retry_busy_review_reuses_token_and_only_edits_after_acquisition(monkeypatch):
    env = _environment(monkeypatch, data=_lease("other-token", "martha@example.com"))
    original_session = dict(env.state["_validation_review_session"])
    env.st.clicked = "validation_retry_review_button"
    env.run()
    assert env.editor_calls == []
    assert env.state["_validation_review_session"] == original_session
    env.store.write({"validation_active_at": -1})
    env.st.clicked = None
    env.run()
    assert env.state["_validation_review_session"] == original_session
    assert len(env.editor_calls) == 3
    assert env.store.documents[BOOK_PATH]["validation_active_token"] == TOKEN


def test_open_token_survives_reruns_and_failure_but_changes_after_exit(monkeypatch):
    env = _environment(monkeypatch)
    env.state.pop("_validation_book_id")
    env.state.pop("_validation_review_session")
    env.st.clicked = "validation_open_review_button"
    env.run(list_view=True)
    first = dict(env.state["_validation_review_session"])
    assert first["token"]
    assert first["book_id"] == BOOK_ID
    assert first["username"] == USERNAME
    env.st.clicked = None
    env.store.failure = ServiceUnavailable("temporary failure")
    env.run()
    assert env.state["_validation_review_session"] == first
    assert env.editor_calls == []
    env.run()
    env.run()
    assert env.state["_validation_review_session"] == first
    env.st.clicked = "validation_back_to_list_button"
    env.run()
    _assert_exited(env)
    env.st.clicked = "validation_open_review_button"
    env.run(list_view=True)
    assert env.state["_validation_review_session"]["token"] != first["token"]
    assert all(
        first["token"] not in key
        and env.state["_validation_review_session"]["token"] not in key
        for key in env.st.button_keys
    )
