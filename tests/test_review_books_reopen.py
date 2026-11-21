"""Tests for #200/#202: reopening submitted books + owner resolution.

The 2026-07 pilot diagnosis showed students "losing" all their books on the
Edit-my-books page: every missing book was simply ``entry_status ==
'completed'`` (submitted), which the page filtered out with no explanation.
The fix lists submitted books in their own section and lets the OWNER reopen
one — but only when it is NOT already validated and NOT currently being
validated. "Currently being validated" is a durable heartbeat
(``validation_active_at``) that validation.py writes when a validator OPENS a
book (throttled), so merely viewing a book blocks the reopen; recent
``edit_log`` ``context='validation'`` activity is kept as a backstop.

These tests cover the pure decision helpers in ``utilities`` (plain dicts, no
Streamlit, no network, no writes): ``entered_by_username`` (moved from
pages/validation.py for reuse, #129), ``validation_recently_active``,
``validation_marker_active``, ``validation_heartbeat_due`` and
``submitted_book_reopen_block``.

Also exercises Validation’s audited character/alias actions with real entities
and the existing in-memory database fake; no live services are used.
"""

from datetime import datetime, timedelta, timezone
import json
import runpy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import streamlit as st
from streamlit.runtime.scriptrunner import RerunData, RerunException

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1 import ArrayRemove, ArrayUnion

from alias_schema import alias_storage_id, canonical_bundle
from data_structures import Book
from test_character_autodetect import _AttrDict
from test_firestore_batch_helpers import FakeDb, FakeQuery, FakeRef as DocumentRef, FakeSnap


from utilities import (
    entered_by_username,
    validation_recently_active,
    validation_marker_active,
    validation_heartbeat_due,
    other_active_validator,
    submitted_book_reopen_block,
    REOPEN_BLOCK_VALIDATED,
    REOPEN_BLOCK_VALIDATION_ACTIVE,
    VALIDATION_ACTIVITY_WINDOW_MINUTES,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class FakeRef:
    """Duck-typed stand-in for a users-collection DocumentReference."""

    def __init__(self, doc_id):
        self.id = doc_id
        self.path = f"users/{doc_id}"


# ---------------------------------------------------------------------------
# entered_by_username — both stored shapes resolve to the same owner string.
# ---------------------------------------------------------------------------

def test_entered_by_username_handles_ref_string_and_missing():
    assert entered_by_username(FakeRef("martha@example.com")) == "martha@example.com"
    assert entered_by_username("pilot_import") == "pilot_import"
    assert entered_by_username(None) is None
    assert entered_by_username("") is None


# ---------------------------------------------------------------------------
# validation_recently_active — the "currently being validated" proxy.
# ---------------------------------------------------------------------------

def _record(context, minutes_ago, ts=True):
    record = {'context': context, 'field': 'text'}
    if ts:
        record['timestamp'] = NOW - timedelta(minutes=minutes_ago)
    return record


def test_recent_validation_record_blocks():
    records = [_record('validation', minutes_ago=5)]
    assert validation_recently_active(records, now=NOW) is True


def test_old_validation_record_does_not_block():
    records = [_record('validation', minutes_ago=VALIDATION_ACTIVITY_WINDOW_MINUTES + 1)]
    assert validation_recently_active(records, now=NOW) is False


def test_recent_non_validation_context_does_not_block():
    # A recent 'reopen' record (the owner's own earlier reopen) must not count
    # as validator activity.
    records = [_record('reopen', minutes_ago=1)]
    assert validation_recently_active(records, now=NOW) is False


def test_records_missing_timestamp_are_ignored():
    records = [_record('validation', minutes_ago=0, ts=False)]
    assert validation_recently_active(records, now=NOW) is False


def test_naive_timestamp_treated_as_utc():
    records = [{'context': 'validation',
                'timestamp': (NOW - timedelta(minutes=10)).replace(tzinfo=None)}]
    assert validation_recently_active(records, now=NOW) is True


def test_empty_iterable_does_not_block():
    assert validation_recently_active(iter(()), now=NOW) is False


# ---------------------------------------------------------------------------
# submitted_book_reopen_block — the reopen decision.
# ---------------------------------------------------------------------------

def test_validated_book_is_blocked_even_without_activity():
    assert (
        submitted_book_reopen_block({'validated': True}, validation_active=False)
        == REOPEN_BLOCK_VALIDATED
    )


def test_unvalidated_book_with_validator_activity_is_blocked():
    assert (
        submitted_book_reopen_block({'validated': False}, validation_active=True)
        == REOPEN_BLOCK_VALIDATION_ACTIVE
    )


def test_unvalidated_quiet_book_may_be_reopened():
    assert submitted_book_reopen_block({'validated': False}, validation_active=False) is None


def test_nan_validated_counts_as_not_validated():
    # A legacy book read via pandas surfaces a missing 'validated' as NaN,
    # which is truthy in Python — it must NOT read as "validated".
    assert submitted_book_reopen_block({'validated': float('nan')}, False) is None


def test_missing_validated_counts_as_not_validated():
    assert submitted_book_reopen_block({}, validation_active=False) is None


# ---------------------------------------------------------------------------
# validation_marker_active — the durable "opened for validation" heartbeat.
# This is the #200 fix: OPENING a book (not only editing it) blocks the reopen.
# ---------------------------------------------------------------------------

def test_marker_within_window_blocks():
    assert validation_marker_active(NOW - timedelta(minutes=5), now=NOW) is True


def test_marker_at_window_edge_blocks():
    assert validation_marker_active(
        NOW - timedelta(minutes=VALIDATION_ACTIVITY_WINDOW_MINUTES), now=NOW
    ) is True


def test_marker_outside_window_does_not_block():
    assert validation_marker_active(
        NOW - timedelta(minutes=VALIDATION_ACTIVITY_WINDOW_MINUTES + 1), now=NOW
    ) is False


def test_marker_sentinels_do_not_block():
    # A book never opened for validation stores -1 (the Field default) or None;
    # a legacy pandas row can surface NaN. None are datetimes -> not active.
    assert validation_marker_active(-1, now=NOW) is False
    assert validation_marker_active(None, now=NOW) is False
    assert validation_marker_active(float('nan'), now=NOW) is False


def test_marker_naive_timestamp_treated_as_utc():
    assert validation_marker_active(
        (NOW - timedelta(minutes=10)).replace(tzinfo=None), now=NOW
    ) is True


# ---------------------------------------------------------------------------
# validation_heartbeat_due — throttles the heartbeat write (one per book per
# throttle window) so it is not rewritten on every validation rerun.
# ---------------------------------------------------------------------------

def test_heartbeat_due_first_time_then_throttled():
    store = {}
    t0 = 1_000_000.0
    # First check for a book is always due (and records the time).
    assert validation_heartbeat_due(store, 'book_a', t0) is True
    # A rerun a few seconds later is throttled.
    assert validation_heartbeat_due(store, 'book_a', t0 + 5, throttle_seconds=30) is False
    # Past the throttle window it is due again.
    assert validation_heartbeat_due(store, 'book_a', t0 + 31, throttle_seconds=30) is True


def test_heartbeat_due_is_per_book():
    store = {}
    t0 = 1_000_000.0
    assert validation_heartbeat_due(store, 'book_a', t0) is True
    # A different book is independently due even within the throttle window.
    assert validation_heartbeat_due(store, 'book_b', t0 + 1) is True


# ---------------------------------------------------------------------------
# Activity-window length (#234): shortened from 120 to 10 minutes. The heartbeat
# refreshes every 30s while a book is actually open AND is now cleared on leave
# (Back-to-list / Approve). 30 minutes covers a careful read-through — Streamlit
# only reruns on INTERACTION, so the heartbeat does NOT refresh while a validator
# merely reads — while capping how long a crashed/closed tab can hold a stale
# reopen lock at a quarter of the original 2 hours.
# ---------------------------------------------------------------------------

def test_activity_window_is_thirty_minutes():
    assert VALIDATION_ACTIVITY_WINDOW_MINUTES == 30


def test_marker_within_window_still_blocks_reopen():
    # A validator reading without interacting for 15 minutes must KEEP the lock:
    # too short a window would drop it mid-review and let the owner reopen.
    assert validation_marker_active(NOW - timedelta(minutes=15), now=NOW) is True


def test_marker_beyond_window_is_stale():
    # Deliberate expectation update for #234: a heartbeat 45 minutes old blocked
    # the owner under the old 120-minute window but is stale under the new one.
    assert validation_marker_active(NOW - timedelta(minutes=45), now=NOW) is False


# ---------------------------------------------------------------------------
# other_active_validator — soft validator mutual-exclusion signal (#235).
# Returns the OTHER validator's name only when their heartbeat is live and is
# not our own; used to warn (not block) on a concurrent review.
# ---------------------------------------------------------------------------

def test_other_validator_fresh_foreign_heartbeat_returns_name():
    assert other_active_validator(
        'martha@example.com', NOW - timedelta(minutes=2), 'chris@example.com', now=NOW
    ) == 'martha@example.com'


def test_other_validator_own_heartbeat_returns_none():
    # My own live heartbeat must not warn me about myself.
    assert other_active_validator(
        'chris@example.com', NOW - timedelta(minutes=2), 'chris@example.com', now=NOW
    ) is None


def test_other_validator_stale_foreign_heartbeat_returns_none():
    # A crashed/closed tab whose heartbeat has aged past the window: no warning.
    assert other_active_validator(
        'martha@example.com',
        NOW - timedelta(minutes=VALIDATION_ACTIVITY_WINDOW_MINUTES + 1),
        'chris@example.com', now=NOW,
    ) is None


def test_other_validator_no_heartbeat_owner_returns_none():
    # Sentinels for "never opened for validation": -1 (Field default) / None / ''.
    assert other_active_validator(None, -1, 'chris@example.com', now=NOW) is None
    assert other_active_validator('', NOW, 'chris@example.com', now=NOW) is None
    assert other_active_validator(
        'martha@example.com', -1, 'chris@example.com', now=NOW
    ) is None


@pytest.fixture
def validation_editor(monkeypatch):
    """Real entities and audit writer against the existing in-memory DB fake."""
    import utilities

    db = FakeDb({'books': {'test_book': {
        'title': 'Test Book', 'is_registered': True, 'validated': True,
        'entered_by': 'pilot_import', 'characters': [],
    }}, 'characters': {}, 'aliases': {}, 'edit_log': {}})
    def rows(ref):
        return db.store[ref.path.split('/')[0]]
    monkeypatch.setattr(DocumentRef, 'get', lambda ref: FakeSnap(ref, rows(ref).get(ref.id)), raising=False)
    monkeypatch.setattr(DocumentRef, 'set', lambda ref, data, **kw: rows(ref).__setitem__(ref.id, dict(data)), raising=False)
    def create(ref, data):
        if ref.id in rows(ref):
            raise AlreadyExists('record exists')
        ref.set(data)
        return SimpleNamespace(update_time=NOW)
    def update(ref, data, **kwargs):
        target = rows(ref)[ref.id]
        for field, value in data.items():
            if isinstance(value, ArrayUnion):
                target[field] = target.get(field, []) + [
                    ref for ref in value.values if ref not in target.get(field, [])]
            elif isinstance(value, ArrayRemove):
                target[field] = [ref for ref in target.get(field, []) if ref not in value.values]
            else:
                target[field] = value
        return SimpleNamespace(update_time=NOW)
    monkeypatch.setattr(DocumentRef, 'create', create, raising=False)
    monkeypatch.setattr(DocumentRef, 'update', update, raising=False)
    monkeypatch.setattr(FakeSnap, 'update_time', NOW, raising=False)
    monkeypatch.setattr(DocumentRef, '__eq__', lambda ref, other: ref.path == getattr(other, 'path', None))
    def add(query, data):
        ref = DocumentRef(query._collection, str(len(db.store[query._collection])))
        ref.set(data)
        return None, ref
    monkeypatch.setattr(FakeQuery, 'add', add, raising=False)
    wrapper = object.__new__(utilities.FirestoreWrapper)
    monkeypatch.setattr(wrapper, 'connect_book', lambda: db)
    monkeypatch.setattr(wrapper, 'connect_user', lambda: db)
    monkeypatch.setattr(wrapper, 'get_all_documents_stream', lambda **kw: [])
    state = _AttrDict(firestore=wrapper, username='validator', character_dict={})
    monkeypatch.setattr(st, 'session_state', state)
    monkeypatch.setattr(utilities, 'check_authentication_status', lambda: None)
    monkeypatch.setattr(utilities, 'is_team_or_above', lambda: True)
    monkeypatch.setattr(utilities, 'page_layout', lambda **kw: None)
    values = {}
    monkeypatch.setattr(st, 'text_input', lambda label, key, value='', **kw: values.get(key, value))
    monkeypatch.setattr(st, 'selectbox', lambda label, options, key, index=0, **kw: values.get(key, (options[index] if index is not None else None)))
    monkeypatch.setattr(st, 'checkbox', lambda label, key, value=False, **kw: values.get(key, value))
    monkeypatch.setattr(st, 'form_submit_button', lambda label, key, **kw: values.get(key, False))
    monkeypatch.setattr(st, 'rerun', Mock(side_effect=RerunException(RerunData())))
    monkeypatch.setattr(st, 'warning', Mock())
    page = SimpleNamespace(**runpy.run_path('pages/validation.py'))
    book = Book(db_object=db.store['books']['test_book'])
    return page, book, db.store, values


def test_validation_add_character_fields_link_and_one_log(validation_editor):
    page, book, store, values = validation_editor
    values.update({
        'character_form_name_validation_new_test_book': ' New cast ',
        'character_form_gender_validation_new_test_book': 'Non-specific',
        'character_form_human_validation_new_test_book': False,
        'character_form_plural_validation_new_test_book': True,
        'character_form_protagonist_validation_new_test_book': True,
        'validation_add_character_submit_test_book': True,
    })
    with pytest.raises(RerunException):
        page.characters_editor(book)
    character = store['characters']['test_book_new_cast']
    assert {key: character[key] for key in ('name', 'gender', 'human', 'plural', 'protagonist')} == {
        'name': 'New cast', 'gender': 'Non-specific', 'human': False,
        'plural': True, 'protagonist': True,
    }
    assert character['is_registered'] is True
    assert character['book'].path == 'books/test_book'
    assert store['books']['test_book']['characters'] == [DocumentRef('characters', 'test_book_new_cast')]
    assert book.get_character_dict() == st.session_state['book_character_dict']
    log, = store['edit_log'].values()
    assert (log['entity_type'], log['entity_id'], log['field'], log['old_value']) == (
        'character', 'test_book_new_cast', 'document', None,
    )
    assert json.loads(log['new_value']) == json.loads(
        json.dumps(character, default=page.EditLog._coerce))
    assert log['entered_by'] == 'pilot_import'
    assert log['edited_by'].path == 'users/validator'
    values['character_form_gender_validation_new_test_book'] = 'Male'
    page._add_character(book)  # Same document id must not overwrite or log again.
    assert len(store['edit_log']) == len(store['characters']) == 1
    assert character['gender'] == 'Non-specific'
    st.warning.assert_called_with(page.Validation.rename_exists)


def test_validation_add_alias_stored_logged_and_collision_refused(validation_editor):
    page, book, store, values = validation_editor
    selected = DocumentRef('characters', 'test_book_selected')
    values.update({'validation_add_alias_name_test_book_selected': ' New alias ',
                   'validation_add_alias_submit_test_book_selected': True})
    with pytest.raises(RerunException):
        page._edit_aliases(book, selected)
    alias_id = alias_storage_id(book.get_ref(), selected, ' New alias ')
    alias = store['aliases'][alias_id]
    assert alias['name'] == ' New alias ' and alias['is_registered'] is True
    assert alias['character'] == selected and alias['book'].path == 'books/test_book'
    assert all(alias[field] == value for field, value in canonical_bundle(' New alias ').items())
    log, = store['edit_log'].values()
    assert (log['entity_type'], log['entity_id'], log['field'], log['old_value']) == (
        'alias', alias_id, 'document', None,
    )
    assert json.loads(log['new_value']) == json.loads(
        json.dumps(alias, default=page.EditLog._coerce))
    page._edit_aliases(book, selected)  # Exact tuple must not overwrite or log again.
    assert len(store['edit_log']) == len(store['aliases']) == 1
    st.warning.assert_called_with(page.Validation.alias_exists)
    other = DocumentRef('characters', 'test_book_other')
    values.update({'validation_add_alias_name_test_book_other': ' New alias ',
                   'validation_add_alias_submit_test_book_other': True})
    with pytest.raises(RerunException):
        page._edit_aliases(book, other)
    assert len(store['edit_log']) == len(store['aliases']) == 2
    assert store['aliases'][alias_storage_id(book.get_ref(), other, ' New alias ')]['character'] == other


def test_validation_remove_alias_confirmation_scope_and_rewind_log(validation_editor):
    page, book, store, values = validation_editor
    selected = DocumentRef('characters', 'test_book_selected')
    original = {'name': 'Old alias', 'book': book.get_ref(), 'character': selected,
                'is_registered': True, 'entered_by': 'pilot_import', 'datetime_created': NOW,
                'entry_status': 'accepted', 'raw_history': ['Earlier alias']}
    store['aliases']['legacy_stored_id'] = dict(original)
    store['aliases']['test_book_other_alias'] = dict(original, character=DocumentRef('characters', 'other'))
    values['validation_remove_alias_submit_legacy_stored_id'] = True
    page._edit_aliases(book, selected)
    assert len(store['aliases']) == 2 and not store['edit_log']
    st.warning.assert_called_with(page.Validation.remove_alias_confirmation_required)
    values['validation_remove_alias_confirm_legacy_stored_id'] = True
    with pytest.raises(RerunException):
        page._edit_aliases(book, selected)
    assert store['aliases']['legacy_stored_id'] == dict(original, entry_status='rejected')
    assert store['aliases']['test_book_other_alias']['entry_status'] == 'accepted'
    log, = store['edit_log'].values()
    assert (log['entity_type'], log['entity_id'], log['field'],
            log['old_value'], log['new_value']) == (
        'alias', 'legacy_stored_id', 'entry_status', 'accepted', 'rejected',
    )
    # Rewinding restores the status; all archival fields were retained.
    store['aliases'][log['entity_id']][log['field']] = log['old_value']
    assert store['aliases']['legacy_stored_id'] == original


def test_validation_remove_character_confirmation_and_log(validation_editor, monkeypatch):
    page, book, store, values = validation_editor
    selected = DocumentRef('characters', 'test_book_duplicate')
    original = {'name': 'Duplicate', 'book': book.get_ref(), 'is_registered': True,
                'entry_status': 'accepted'}
    store['characters'][selected.id] = dict(original)
    store['aliases']['duplicate_alias'] = dict(original, name='Old alias', character=selected)
    book.add_character(selected)
    delete = Mock(side_effect=page.Character.delete)
    monkeypatch.setattr(page.Character, 'delete', lambda character: delete(character))
    st.session_state['validation_character_select'] = 'Duplicate'
    st.session_state[f'validation_char_name_{selected.id}'] = 'Duplicate'
    values[f'validation_remove_character_submit_{selected.id}'] = True
    page.characters_editor(book)
    delete.assert_not_called()
    assert not store['edit_log']
    st.warning.assert_called_with(page.Validation.remove_character_confirmation_required)
    values[f'validation_remove_character_confirm_{selected.id}'] = True
    with pytest.raises(RerunException):
        page.characters_editor(book)
    delete.assert_called_once()
    assert store['characters'][selected.id] == dict(original, entry_status='rejected')
    assert store['aliases']['duplicate_alias']['entry_status'] == 'rejected'
    assert store['books']['test_book']['characters'] == []
    assert 'validation_character_select' not in st.session_state
    assert f'validation_char_name_{selected.id}' not in st.session_state
    log, = store['edit_log'].values()
    assert (log['entity_type'], log['entity_id'], log['field'],
            log['old_value'], log['new_value']) == (
        'character', selected.id, 'entry_status', 'accepted', 'rejected',
    )
    page.characters_editor(book)
    assert book.get_character_dict() == {}
    assert len(store['edit_log']) == 1
