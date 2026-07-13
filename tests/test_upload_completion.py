"""Tests for #199: explicit upload-completion manifest + durable session id.

The direct-to-S3 uploader PUTs all selected files CONCURRENTLY, but batch
completion used to be INFERRED ("key count stable across two listing
samples") — a premise that concurrency breaks: a stalled connection made a
partial batch look settled, and an early read then assigned page numbers
positionally over a hole-y listing, permanently corrupting page order. #199
replaces inference with an explicit ``manifest.json`` the uploader JS writes
whenever a selection batch has no PUTs left in flight; the app treats a batch
as ready only when the manifest lists exactly the page files present.

Separately, the upload-session id used to live only in ``st.session_state``,
so a websocket drop / reload / re-login minted a fresh id and the page watched
a new EMPTY prefix while the photos sat in the old one ("my photos never
register"). The active id is now also recorded on the user's Firestore doc and
recovered by a fresh session; the "start a new entry" choke points clear it.

All tests use in-memory fakes (no S3, no Firestore, no network).
"""

import json
from io import BytesIO

import pytest
import streamlit as st
from google.api_core.exceptions import GoogleAPIError

from photo_upload import (
    S3_BUCKET,
    MANIFEST_FILENAME,
    USER_UPLOAD_SESSIONS_FIELD,
    upload_prefix,
    manifest_matches,
    missing_upload_slots,
    read_upload_manifest,
    upload_batch_ready,
    uploads_settled,
    build_uploader_html,
    get_upload_session_id,
    reset_upload_session,
)

FLOW = "single"
SID = "user_1_1700000000"
PREFIX = f"{S3_BUCKET}/{upload_prefix(FLOW, SID)}"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeFS:
    """Minimal s3fs stand-in: a dict of full-path -> bytes."""

    def __init__(self, files=None):
        self.files = dict(files or {})

    def ls(self, path, detail=False, refresh=True):
        prefix = path.rstrip("/") + "/"
        entries = [p for p in self.files if p.startswith(prefix)]
        if not entries:
            raise FileNotFoundError(path)
        return entries

    def open(self, path, mode="rb"):
        if path not in self.files:
            raise FileNotFoundError(path)
        return BytesIO(self.files[path])


def _fs_with(slots, manifest=None):
    files = {f"{PREFIX}/page_{n}.jpg": b"img" for n in slots}
    if manifest is not None:
        files[f"{PREFIX}/{MANIFEST_FILENAME}"] = json.dumps(manifest).encode()
    return FakeFS(files)


def _manifest(slots, failed=0):
    names = [f"page_{n}.jpg" for n in slots]
    return {"uploaded": names, "count": len(names), "failed": failed}


class FakeUserRef:
    def __init__(self, wrapper, doc_id):
        self._wrapper = wrapper
        self.id = doc_id

    def get(self):
        if self._wrapper.read_error:
            raise GoogleAPIError("boom")
        return FakeSnapshot(self._wrapper.users.get(self.id))


class FakeSnapshot:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class FakeFirestoreWrapper:
    """Duck-typed FirestoreWrapper: username_to_doc_ref + update_field only."""

    def __init__(self, users=None):
        self.users = users or {}
        self.read_error = False
        self.updates = []

    def username_to_doc_ref(self, username):
        return FakeUserRef(self, username.strip().lower())

    def update_field(self, collection, document, field, value):
        self.updates.append((collection, document, field, value))
        base, _, key = field.partition(".")
        self.users.setdefault(document, {}).setdefault(base, {})[key] = value


@pytest.fixture()
def session(monkeypatch):
    state = {}
    monkeypatch.setattr(st, "session_state", state)
    return state


# ---------------------------------------------------------------------------
# manifest_matches / missing_upload_slots — the pure readiness logic.
# ---------------------------------------------------------------------------

def test_manifest_matching_exactly_is_ready():
    keys = [f"{PREFIX}/page_1.jpg", f"{PREFIX}/page_2.jpg"]
    assert manifest_matches(_manifest([1, 2]), keys) is True


def test_manifest_listing_more_than_present_is_not_ready():
    # A listed file's PUT response arrived at the JS but the key is not visible
    # yet / was deleted: completion cannot be assumed.
    keys = [f"{PREFIX}/page_1.jpg"]
    assert manifest_matches(_manifest([1, 2]), keys) is False


def test_manifest_listing_fewer_than_present_is_not_ready():
    # A further selection batch is still PUTting: the manifest is stale.
    keys = [f"{PREFIX}/page_1.jpg", f"{PREFIX}/page_2.jpg", f"{PREFIX}/page_3.jpg"]
    assert manifest_matches(_manifest([1, 2]), keys) is False


def test_missing_or_malformed_manifest_never_matches():
    keys = [f"{PREFIX}/page_1.jpg"]
    assert manifest_matches(None, keys) is False
    assert manifest_matches({"count": 1}, keys) is False
    assert manifest_matches({"uploaded": "page_1.jpg"}, keys) is False
    assert manifest_matches({"uploaded": []}, []) is False


def test_missing_upload_slots_names_the_holes():
    assert missing_upload_slots(["page_1.jpg", "page_2.jpg", "page_5.jpg", "page_9.jpg"]) == [3, 4, 6, 7, 8]
    assert missing_upload_slots(["page_1.jpg", "page_2.jpg"]) == []
    assert missing_upload_slots([]) == []
    # Non-slot names (e.g. manifest.json) are ignored.
    assert missing_upload_slots(["manifest.json"]) == []


# ---------------------------------------------------------------------------
# read_upload_manifest / upload_batch_ready / uploads_settled against FakeFS.
# ---------------------------------------------------------------------------

def test_read_upload_manifest_absent_and_corrupt():
    assert read_upload_manifest(_fs_with([1]), FLOW, SID) is None
    corrupt = FakeFS({f"{PREFIX}/{MANIFEST_FILENAME}": b"{not json"})
    assert read_upload_manifest(corrupt, FLOW, SID) is None


def test_upload_batch_ready_requires_matching_manifest():
    ready, keys, manifest = upload_batch_ready(_fs_with([1, 2], _manifest([1, 2])), FLOW, SID)
    assert ready is True
    assert [k.rsplit("/", 1)[-1] for k in keys] == ["page_1.jpg", "page_2.jpg"]
    assert manifest["count"] == 2

    # Same keys, stale manifest -> not ready.
    ready, _keys, _m = upload_batch_ready(_fs_with([1, 2], _manifest([1])), FLOW, SID)
    assert ready is False

    # No manifest at all -> never auto-ready.
    ready, _keys, manifest = upload_batch_ready(_fs_with([1, 2]), FLOW, SID)
    assert ready is False
    assert manifest is None


def test_manifest_is_not_listed_as_a_page_key():
    _ready, keys, _m = upload_batch_ready(_fs_with([1], _manifest([1])), FLOW, SID)
    assert all(not k.endswith(MANIFEST_FILENAME) for k in keys)


def test_uploads_settled_uses_manifest_when_present():
    settled, keys = uploads_settled(_fs_with([1, 2], _manifest([1, 2])), FLOW, SID)
    assert settled is True and len(keys) == 2

    # Manifest present but mid-second-batch: NOT settled, regardless of the
    # (stable) count — the exact case the legacy heuristic got wrong.
    settled, keys = uploads_settled(_fs_with([1, 2, 3], _manifest([1, 2])), FLOW, SID)
    assert settled is False and len(keys) == 3


def test_uploads_settled_falls_back_to_two_samples_without_manifest():
    # Legacy fallback (manifest PUT failed / pre-manifest upload): stable count
    # across the two samples counts as settled so the manual path cannot
    # dead-end. settle_seconds=0 keeps the test instant.
    settled, keys = uploads_settled(_fs_with([1, 2]), FLOW, SID, settle_seconds=0)
    assert settled is True and len(keys) == 2

    # Empty prefix: settled with no keys (caller shows "nothing uploaded").
    settled, keys = uploads_settled(FakeFS(), FLOW, SID, settle_seconds=0)
    assert settled is True and keys == []


# ---------------------------------------------------------------------------
# Uploader HTML: the manifest URL must reach the JS.
# ---------------------------------------------------------------------------

def test_build_uploader_html_injects_manifest_url():
    html = build_uploader_html(["https://put/1"], "https://put/manifest")
    assert '"https://put/manifest"' in html
    assert "MANIFEST_URL" in html
    # Disabled (None) still renders valid JS with a null manifest URL.
    html = build_uploader_html(["https://put/1"], None)
    assert "var MANIFEST_URL = null;" in html


# ---------------------------------------------------------------------------
# Durable upload-session id (#199): record on mint, recover on a fresh
# session, clear on reset.
# ---------------------------------------------------------------------------

def test_minted_session_id_is_recorded_durably(session):
    wrapper = FakeFirestoreWrapper(users={"martha@example.com": {}})
    session.update({"username": "martha@example.com", "firestore": wrapper})

    sid = get_upload_session_id(FLOW)

    assert session[f"upload_session_{FLOW}"] == sid
    assert wrapper.users["martha@example.com"][USER_UPLOAD_SESSIONS_FIELD][FLOW] == sid


def test_fresh_session_recovers_recorded_id(session):
    # A websocket drop / re-login empties session_state; the recorded id must
    # be resumed so the page keeps watching the prefix the photos landed in.
    recorded = "martha_example_com_1_1699999999"
    wrapper = FakeFirestoreWrapper(
        users={"martha@example.com": {USER_UPLOAD_SESSIONS_FIELD: {FLOW: recorded}}}
    )
    session.update({"username": "martha@example.com", "firestore": wrapper})

    assert get_upload_session_id(FLOW) == recorded
    # Recovery must not overwrite the durable record with a new id.
    assert wrapper.users["martha@example.com"][USER_UPLOAD_SESSIONS_FIELD][FLOW] == recorded


def test_reset_clears_session_and_durable_record(session):
    wrapper = FakeFirestoreWrapper(users={"martha@example.com": {}})
    session.update({"username": "martha@example.com", "firestore": wrapper})

    first = get_upload_session_id(FLOW)
    reset_upload_session(FLOW)

    assert f"upload_session_{FLOW}" not in session
    assert wrapper.users["martha@example.com"][USER_UPLOAD_SESSIONS_FIELD][FLOW] is None

    # The next entry gets a genuinely fresh prefix (not the cleared one).
    second = get_upload_session_id(FLOW)
    assert second != first


def test_recovery_failure_degrades_to_minting(session):
    wrapper = FakeFirestoreWrapper(users={"martha@example.com": {}})
    wrapper.read_error = True
    session.update({"username": "martha@example.com", "firestore": wrapper})

    sid = get_upload_session_id(FLOW)
    assert sid  # upload flow keeps working session-only


def test_anonymous_session_never_touches_firestore(session):
    wrapper = FakeFirestoreWrapper()
    session.update({"firestore": wrapper})  # no username (shouldn't happen, but guarded)

    sid = get_upload_session_id(FLOW)
    assert sid.startswith("anon_")
    assert wrapper.updates == []
