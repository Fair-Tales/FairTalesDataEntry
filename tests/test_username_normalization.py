"""Unit tests for email/username case-insensitivity (auth data-integrity bug).

Email addresses are this app's usernames. A test user re-registering with the
same email in a different case (``User@x.com`` vs ``user@x.com``) got a SECOND
account, and login with a different case than registration would fail — both
because identity lookups/writes compared/stored the raw, un-normalized string.

The fix is a single shared helper, ``utilities.normalize_username`` (#129
reuse), used at every identity point (register, login, password reset,
confirm, QR upload, cookie restore, ``FirestoreWrapper.username_to_doc_ref``).
These tests lock in the pure helper's behaviour; they do not touch Firestore.
"""

from utilities import normalize_username


def test_lowercases_mixed_case_email():
    assert normalize_username("User@X.com") == "user@x.com"


def test_strips_surrounding_whitespace():
    assert normalize_username("  user@x.com  ") == "user@x.com"


def test_different_case_and_whitespace_variants_collide():
    # This is the actual bug: these must normalize to the SAME identity so a
    # duplicate-check / lookup treats them as one account, not two.
    variants = ["User@x.com", "user@X.COM", "  USER@x.com  ", "user@x.com"]
    normalized = {normalize_username(v) for v in variants}
    assert normalized == {"user@x.com"}


def test_none_and_blank_return_empty_string():
    assert normalize_username(None) == ""
    assert normalize_username("") == ""


def test_already_normalized_is_unchanged():
    assert normalize_username("user@x.com") == "user@x.com"
