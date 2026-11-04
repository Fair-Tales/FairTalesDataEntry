"""Exercise Book's real lease callbacks with replaying, versioned transactions."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import streamlit as st
from google.api_core.exceptions import Aborted, ServiceUnavailable

import data_structures.book as book_module
from data_structures.base_structure import Field
from data_structures.book import Book, ValidationLeaseStatus as Status, ValidationLeaseUnavailable
from validation_lease_fakes import AttrDict, FakeLeaseStore


NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
LEASE_FIELDS = {"validation_active_at", "validation_active_by", "validation_active_token"}


@pytest.fixture
def lease_env(monkeypatch):
    store = FakeLeaseStore()
    clock = SimpleNamespace(now=NOW)
    state = AttrDict(firestore=store.firestore, username="alice")
    monkeypatch.setattr(st, "session_state", state)
    monkeypatch.setattr(book_module.firestore, "transactional", store.transactional)
    monkeypatch.setattr(
        book_module, "datetime", SimpleNamespace(now=lambda tz: clock.now)
    )
    return SimpleNamespace(store=store, clock=clock, state=state)


def _seed_owned(env, *, token="token-a", username="alice", at=NOW):
    env.store.documents[env.store.reference().path].update({
        "validation_active_token": token,
        "validation_active_by": username,
        "validation_active_at": at,
        "last_updated": at,
        "validated": False,
        "validated_by": None,
    })
    return env.store.make_book()


def _operate(book, operation):
    if operation == "acquire":
        return book.acquire_validation_lease("token-a", "alice", True)
    if operation == "release":
        return book.release_validation_lease("token-a", "alice")
    return book.approve_with_validation_lease("token-a", "alice", "users/alice")


def test_token_is_a_serialized_field_with_legacy_default(lease_env):
    book = lease_env.store.make_book()
    assert isinstance(vars(Book).get("validation_active_token"), Field)
    assert book.validation_active_token is None
    assert book.to_dict().get("validation_active_token") is None


def test_acquire_then_foreign_open_and_back_preserve_exact_owner(lease_env):
    store = lease_env.store
    first, second = store.make_book(), store.make_book()
    assert first.acquire_validation_lease("token-a", "alice", True) is Status.REFRESHED
    saved = dict(store.documents[store.reference().path])

    assert second.acquire_validation_lease("token-b", "bob", True) is Status.BUSY
    assert second.release_validation_lease("token-b", "bob") is Status.NOT_OWNED

    assert store.documents[store.reference().path] == saved
    assert second.validation_active_token == "token-a"
    assert second.validation_active_by == "alice"
    assert second.validation_active_at == NOW
    assert len(store.writes) == 1
    assert set(store.writes[0][1]) == LEASE_FIELDS | {"last_updated"}


@pytest.mark.parametrize("token,username", [("token-b", "alice"), ("token-a", "bob")])
def test_both_token_and_username_must_match(lease_env, token, username):
    book = _seed_owned(lease_env)
    assert book.acquire_validation_lease(token, username, True) is Status.BUSY
    assert book.release_validation_lease(token, username) is Status.NOT_OWNED
    assert book.approve_with_validation_lease(token, username, "users/bob") is Status.NOT_OWNED
    assert lease_env.store.writes == []


def test_two_empty_snapshot_claims_conflict_and_replay_to_busy(lease_env):
    store = lease_env.store
    first, second = store.make_book(), store.make_book()
    initial_second = second.to_dict()

    def competing_claim(tx):
        assert tx.snapshots[store.reference().path].to_dict().get("validation_active_token") is None
        assert tx.staged[0][1].get("validation_active_token") == "token-b"
        assert store.writes == []
        assert second.to_dict() == initial_second
        assert first.acquire_validation_lease("token-a", "alice", True) is Status.REFRESHED

    store.before_commit = competing_claim
    assert second.acquire_validation_lease("token-b", "bob", True) is Status.BUSY

    assert store.attempts == 3  # B's original attempt, A's commit, B's replay.
    assert store.conflicts == 1
    assert len(store.writes) == 1
    assert second.validation_active_token == "token-a"
    assert store.documents[store.reference().path].get("validation_active_token") == "token-a"


def test_release_racing_takeover_replays_and_preserves_new_lease(lease_env):
    store = lease_env.store
    first = _seed_owned(lease_env, at=NOW - timedelta(minutes=31))
    second = store.make_book()

    def takeover(tx):
        assert tx.staged[0][1].get("validation_active_token") is None
        assert first.validation_active_token == "token-a"
        assert second.acquire_validation_lease("token-b", "bob", True) is Status.REFRESHED

    store.before_commit = takeover
    assert first.release_validation_lease("token-a", "alice") is Status.NOT_OWNED
    assert store.conflicts == 1
    assert len(store.writes) == 1
    assert first.validation_active_token == "token-b"
    assert first.validation_active_by == "bob"
    assert first.validation_active_at == NOW


@pytest.mark.parametrize("minutes_old", [0, 31])
def test_owner_release_atomically_clears_even_an_expired_current_token(lease_env, minutes_old):
    book = _seed_owned(lease_env, at=NOW - timedelta(minutes=minutes_old))
    assert book.release_validation_lease("token-a", "alice") is Status.RELEASED
    assert lease_env.store.writes == [(lease_env.store.reference().path, {
        "validation_active_at": -1,
        "validation_active_by": None,
        "validation_active_token": None,
        "last_updated": NOW,
    })]
    assert book.validation_active_at == -1
    assert book.validation_active_by is None
    assert book.validation_active_token is None


def test_owner_approval_atomically_approves_and_releases(lease_env):
    book = _seed_owned(lease_env)
    validator = lease_env.store.firestore.username_to_doc_ref("alice")
    assert book.approve_with_validation_lease("token-a", "alice", validator) is Status.APPROVED
    assert lease_env.store.writes == [(lease_env.store.reference().path, {
        "validation_active_at": -1,
        "validation_active_by": None,
        "validation_active_token": None,
        "validated": True,
        "validated_by": validator,
        "last_updated": NOW,
    })]
    assert book.validated is True
    assert book.validated_by is validator
    assert book.validation_active_token is None


def test_approval_racing_takeover_does_not_approve_or_clear(lease_env):
    store = lease_env.store
    first = _seed_owned(lease_env)
    second = store.make_book()

    def takeover(tx):
        assert tx.staged[0][1].get("validated") is True
        assert first.validated is False
        lease_env.clock.now += timedelta(minutes=31)
        assert second.acquire_validation_lease("token-b", "bob", True) is Status.REFRESHED

    store.before_commit = takeover
    assert first.approve_with_validation_lease("token-a", "alice", "users/alice") is Status.NOT_OWNED
    assert store.conflicts == 1
    assert len(store.writes) == 1
    saved = store.documents[store.reference().path]
    assert saved.get("validated") is False
    assert saved.get("validated_by") is None
    assert saved.get("validation_active_token") == "token-b"


def test_expired_owner_cannot_approve_without_reacquiring(lease_env):
    book = _seed_owned(lease_env, at=NOW - timedelta(minutes=31))
    assert book.approve_with_validation_lease("token-a", "alice", "users/alice") is Status.NOT_OWNED
    assert lease_env.store.writes == []


def test_exact_thirty_minute_boundary_is_active_then_takeover_succeeds(lease_env):
    book = _seed_owned(lease_env, at=NOW - timedelta(minutes=30))
    assert book.acquire_validation_lease("token-b", "bob", True) is Status.BUSY
    lease_env.clock.now += timedelta(microseconds=1)
    assert book.acquire_validation_lease("token-b", "bob", True) is Status.REFRESHED
    assert len(lease_env.store.writes) == 1


@pytest.mark.parametrize("username", ["alice", "bob", None])
@pytest.mark.parametrize("token", [None, ""])
def test_live_tokenless_legacy_marker_is_foreign_until_expiry(lease_env, username, token):
    book = _seed_owned(lease_env, token=token, username=username)
    assert book.acquire_validation_lease("token-a", "alice", True) is Status.BUSY
    assert book.release_validation_lease("token-a", "alice") is Status.NOT_OWNED
    assert lease_env.store.writes == []
    lease_env.clock.now += timedelta(minutes=30, microseconds=1)
    assert book.acquire_validation_lease("token-a", "alice", False) is Status.REFRESHED


@pytest.mark.parametrize("operation", ["acquire", "release", "approve"])
def test_missing_document_is_never_recreated(lease_env, operation):
    book = _seed_owned(lease_env)
    initial = book.to_dict()
    lease_env.store.delete()
    assert _operate(book, operation) is Status.MISSING
    assert lease_env.store.documents == {}
    assert lease_env.store.writes == []
    assert book.to_dict() == initial


@pytest.mark.parametrize("operation", ["acquire", "release", "approve"])
def test_commit_failure_does_not_change_memory_or_storage(lease_env, operation):
    book = _seed_owned(lease_env, at=NOW - timedelta(seconds=60))
    local_before = book.to_dict()
    stored_before = dict(lease_env.store.documents[lease_env.store.reference().path])
    lease_env.store.failure = ServiceUnavailable("offline")
    with pytest.raises(ServiceUnavailable):
        _operate(book, operation)
    assert book.to_dict() == local_before
    assert lease_env.store.documents[lease_env.store.reference().path] == stored_before
    assert lease_env.store.writes == []
    assert book.reading_from_db is False


def test_exhausted_conflicts_normalize_only_aborted_caused_value_error(lease_env):
    store = lease_env.store
    book = store.make_book()
    initial = book.to_dict()

    def conflict(tx):
        store.write({"unrelated": store.attempts})
        store.before_commit = conflict

    store.before_commit = conflict
    with pytest.raises(ValidationLeaseUnavailable) as caught:
        book.acquire_validation_lease("token-a", "alice", True)
    assert isinstance(caught.value.__cause__, ValueError)
    assert isinstance(caught.value.__cause__.__cause__, Aborted)
    assert store.attempts == store.max_attempts == store.conflicts
    assert all("validation_active_token" not in patch for _, patch in store.writes)
    assert book.to_dict() == initial


@pytest.mark.parametrize("cause", [None, TypeError("unrelated")])
def test_unrelated_transaction_value_error_propagates(lease_env, cause):
    error = ValueError("programming error")
    error.__cause__ = cause
    lease_env.store.failure = error
    with pytest.raises(ValueError) as caught:
        lease_env.store.make_book().acquire_validation_lease("token-a", "alice", True)
    assert caught.value is error
    assert lease_env.store.writes == []


@pytest.mark.parametrize("previous_guard", [True, False])
def test_local_application_restores_guard_and_is_outside_error_normalization(
    lease_env, monkeypatch, previous_guard
):
    book = lease_env.store.make_book()
    book.reading_from_db = previous_guard
    original_set = Field.__set__
    local_error = ValueError("local descriptor failure")
    local_error.__cause__ = Aborted("not a transaction failure")

    def fail_local_set(descriptor, instance, value):
        if instance is book and descriptor._name == "validation_active_token":
            assert instance.reading_from_db is True
            raise local_error
        original_set(descriptor, instance, value)

    monkeypatch.setattr(Field, "__set__", fail_local_set)
    with pytest.raises(ValueError) as caught:
        book.acquire_validation_lease("token-a", "alice", True)
    assert caught.value is local_error
    assert book.reading_from_db is previous_guard
    assert len(lease_env.store.writes) == 1  # Commit preceded local application.


@pytest.mark.parametrize("operation", ["acquire", "release", "approve"])
def test_callback_never_uses_streamlit_or_field_setters(lease_env, monkeypatch, operation):
    book = _seed_owned(lease_env)
    original_set = Field.__set__
    inside_callback = False

    class ForbiddenStreamlit:
        def __getattr__(self, name):
            raise AssertionError(f"Streamlit used in callback: {name}")

    def guarded_set(descriptor, instance, value):
        assert not inside_callback
        return original_set(descriptor, instance, value)

    def guarded_transactional(callback):
        def wrapped(tx):
            nonlocal inside_callback
            real_st = book_module.st
            inside_callback = True
            book_module.st = ForbiddenStreamlit()
            try:
                return callback(tx)
            finally:
                inside_callback = False
                book_module.st = real_st

        return lease_env.store.transactional(wrapped)

    monkeypatch.setattr(Field, "__set__", guarded_set)
    monkeypatch.setattr(book_module.firestore, "transactional", guarded_transactional)
    before_session = dict(lease_env.state)
    _operate(book, operation)
    assert dict(lease_env.state) == before_session


def test_throttled_owner_interactions_read_without_heartbeat_write(lease_env):
    store = lease_env.store
    book = _seed_owned(lease_env)
    initial_reads = store.reads
    assert book.acquire_validation_lease("token-a", "alice", False) is Status.OWNED
    assert store.reads == initial_reads + 1
    assert store.writes == []
    store.write({"validation_active_token": "token-b", "validation_active_by": "bob"})
    assert book.acquire_validation_lease("token-a", "alice", False) is Status.BUSY
    assert store.reads == initial_reads + 2
    assert len(store.writes) == 1


def test_same_owner_refreshes_when_due_and_reclaims_after_expiry(lease_env):
    book = _seed_owned(lease_env, at=NOW - timedelta(seconds=30))
    assert book.acquire_validation_lease("token-a", "alice", True) is Status.REFRESHED
    assert book.validation_active_at == NOW
    lease_env.clock.now += timedelta(minutes=31)
    assert book.acquire_validation_lease("token-a", "alice", False) is Status.REFRESHED
    assert book.validation_active_at == lease_env.clock.now
    assert len(lease_env.store.writes) == 2


@pytest.mark.parametrize("token", [None, ""])
def test_empty_token_never_creates_or_releases_a_lease(lease_env, token):
    book = lease_env.store.make_book()
    assert book.acquire_validation_lease(token, "alice", True) is Status.NOT_OWNED
    assert book.release_validation_lease(token, "alice") is Status.NOT_OWNED
    assert book.approve_with_validation_lease(token, "alice", "users/alice") is Status.NOT_OWNED
    assert lease_env.store.writes == []


def test_deletion_between_read_and_commit_replays_to_missing(lease_env):
    book = lease_env.store.make_book()
    lease_env.store.before_commit = lambda tx: lease_env.store.delete()
    assert book.acquire_validation_lease("token-a", "alice", True) is Status.MISSING
    assert lease_env.store.conflicts == 1
    assert lease_env.store.attempts == 2
    assert lease_env.store.documents == {}
    assert lease_env.store.writes == []
