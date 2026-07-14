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
"""

from datetime import datetime, timedelta, timezone

from utilities import (
    entered_by_username,
    validation_recently_active,
    validation_marker_active,
    validation_heartbeat_due,
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
