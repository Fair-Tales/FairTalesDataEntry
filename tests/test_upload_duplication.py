"""Tests for the upload-duplication fix (flaky-WiFi phone uploads).

Production signature (2026-07-20, "Molly's Moon Mission"): a 23-photo book came
out with 25 pages — ``page_24.jpg`` byte-identical to ``page_1.jpg`` and
``page_25.jpg`` to ``page_2.jpg``. Root cause: the uploader JS assigned page
slots from a monotonically increasing counter that never reset, so re-selecting
photos after a flaky-WiFi failure APPENDED them as new slots instead of
retrying the original ones; separately, re-minting presigned URLs on every
Streamlit rerun changed the iframe srcdoc (URLs embed a fresh signature), so
any rerun remounted the iframe, killed in-flight PUTs and reset the slot
counter over a half-filled prefix.

The fix (all exercised here where pure/Python-side):
  * slots are assigned by FILE IDENTITY and resumed across mounts via the
    manifest's ``files`` map (``known_files_from_manifest``) plus the prefix's
    existing page files, both seeded into the template
    (``build_uploader_html``);
  * the uploader render state (presigned URLs + mount seed) is cached in
    session state so the iframe HTML is byte-stable across reruns
    (``get_uploader_render_state`` / ``invalidate_uploader_state``);
  * byte-identical photos under two slots are surfaced by the ETag-based
    content-duplicate guard (``duplicate_content_slots``).

The JS behaviour itself (bounded concurrency, timeout + retry, slot reuse) is
browser-side and not unit-testable here — see the template comments.
"""

import json
from io import BytesIO

import pytest
import streamlit as st

import photo_upload
from photo_upload import (
    S3_BUCKET,
    MANIFEST_FILENAME,
    upload_prefix,
    manifest_matches,
    known_files_from_manifest,
    duplicate_content_slots,
    list_uploaded_entries,
    build_uploader_html,
    get_uploader_render_state,
    invalidate_uploader_state,
    reset_upload_session,
)

FLOW = "single"
SID = "user_1_1700000000"
PREFIX = f"{S3_BUCKET}/{upload_prefix(FLOW, SID)}"


class FakeDetailFS:
    """s3fs stand-in whose ``ls(detail=True)`` returns entry dicts with ETags."""

    def __init__(self, entries=None, blobs=None):
        # entries: list of dicts with "name" (full path) and optional "ETag".
        self.entries = list(entries or [])
        self.blobs = dict(blobs or {})

    def ls(self, path, detail=False, refresh=True):
        prefix = path.rstrip("/") + "/"
        matched = [e for e in self.entries if e["name"].startswith(prefix)]
        if not matched:
            raise FileNotFoundError(path)
        if detail:
            return [dict(e) for e in matched]
        return [e["name"] for e in matched]

    def open(self, path, mode="rb"):
        if path not in self.blobs:
            raise FileNotFoundError(path)
        return BytesIO(self.blobs[path])


def _entry(name, etag=None):
    entry = {"name": f"{PREFIX}/{name}"}
    if etag is not None:
        entry["ETag"] = etag
    return entry


@pytest.fixture()
def session(monkeypatch):
    state = {}
    monkeypatch.setattr(st, "session_state", state)
    return state


# ---------------------------------------------------------------------------
# duplicate_content_slots — the content-level duplicate guard.
# ---------------------------------------------------------------------------


def test_duplicate_content_slots_finds_identical_pages():
    # The Molly's Moon Mission signature: first pages re-uploaded as new slots.
    entries = [
        _entry("page_1.jpg", etag='"aaa"'),
        _entry("page_2.jpg", etag='"bbb"'),
        _entry("page_24.jpg", etag='"aaa"'),
        _entry("page_25.jpg", etag='"bbb"'),
        _entry("page_3.jpg", etag='"ccc"'),
    ]
    assert duplicate_content_slots(entries) == [
        ["page_1.jpg", "page_24.jpg"],
        ["page_2.jpg", "page_25.jpg"],
    ]


def test_duplicate_content_slots_no_duplicates():
    entries = [_entry("page_1.jpg", '"a"'), _entry("page_2.jpg", '"b"')]
    assert duplicate_content_slots(entries) == []


def test_duplicate_content_slots_ignores_non_pages_and_missing_etags():
    entries = [
        _entry(MANIFEST_FILENAME, '"same"'),
        _entry("page_1.jpg", '"same"'),
        _entry("page_2.jpg"),  # no ETag: skipped, never grouped
        _entry("page_3.jpg"),
        f"{PREFIX}/page_4.jpg",  # string entry (detail-less ls): skipped
    ]
    assert duplicate_content_slots(entries) == []


# ---------------------------------------------------------------------------
# known_files_from_manifest — the cross-mount slot-resume map.
# ---------------------------------------------------------------------------


def test_known_files_from_manifest_passes_valid_map():
    manifest = {
        "uploaded": ["page_1.jpg"],
        "files": {
            "IMG_1.jpg|123|1700": "page_1.jpg",
            "IMG_2.jpg|456|1701": "page_2.jpg",
        },
    }
    assert known_files_from_manifest(manifest) == {
        "IMG_1.jpg|123|1700": "page_1.jpg",
        "IMG_2.jpg|456|1701": "page_2.jpg",
    }


def test_known_files_from_manifest_rejects_malformed():
    assert known_files_from_manifest(None) == {}
    assert known_files_from_manifest({"uploaded": []}) == {}  # pre-fix manifest
    assert known_files_from_manifest({"files": "nope"}) == {}
    # Non-slot names and non-string values are dropped, valid ones kept.
    manifest = {
        "files": {
            "a|1|2": "page_1.jpg",
            "b|3|4": "../evil.jpg",
            "c|5|6": 7,
        }
    }
    assert known_files_from_manifest(manifest) == {"a|1|2": "page_1.jpg"}


def test_manifest_with_files_map_still_matches():
    # Regression: the new ``files`` key must not break #199 readiness matching.
    manifest = {
        "uploaded": ["page_1.jpg", "page_2.jpg"],
        "count": 2,
        "failed": 0,
        "files": {"IMG_1.jpg|1|2": "page_1.jpg", "IMG_2.jpg|3|4": "page_2.jpg"},
    }
    keys = [f"{PREFIX}/page_1.jpg", f"{PREFIX}/page_2.jpg"]
    assert manifest_matches(manifest, keys) is True


# ---------------------------------------------------------------------------
# list_uploaded_entries — detail listing powering the guards.
# ---------------------------------------------------------------------------


def test_list_uploaded_entries_sorted_and_filtered():
    fs = FakeDetailFS(
        [
            _entry("page_10.jpg", '"j"'),
            _entry("page_2.jpg", '"b"'),
            _entry(MANIFEST_FILENAME),
        ]
    )
    entries = list_uploaded_entries(fs, FLOW, SID)
    assert [e["name"].rsplit("/", 1)[-1] for e in entries] == [
        "page_2.jpg",
        "page_10.jpg",
    ]


def test_list_uploaded_entries_empty_prefix():
    assert list_uploaded_entries(FakeDetailFS(), FLOW, SID) == []


# ---------------------------------------------------------------------------
# build_uploader_html — mount-seed injection.
# ---------------------------------------------------------------------------


def test_build_uploader_html_injects_seed_and_leaves_no_placeholders():
    html = build_uploader_html(
        ["https://put/1", "https://put/2"],
        "https://put/manifest",
        existing_names=["page_1.jpg", MANIFEST_FILENAME, "not_a_page.jpg"],
        known_files={"IMG_1.jpg|1|2": "page_1.jpg"},
    )
    assert 'var EXISTING = ["page_1.jpg"];' in html
    assert json.dumps({"IMG_1.jpg|1|2": "page_1.jpg"}) in html
    # Every template placeholder must be substituted.
    assert "__" not in html


def test_build_uploader_html_defaults_are_empty_seed():
    html = build_uploader_html(["https://put/1"], None)
    assert "var EXISTING = [];" in html
    assert "var KNOWN_FILES = {};" in html
    assert "var MANIFEST_URL = null;" in html


# ---------------------------------------------------------------------------
# get_uploader_render_state — byte-stable iframe HTML across reruns.
# ---------------------------------------------------------------------------


@pytest.fixture()
def counted_presign(monkeypatch):
    calls = {"urls": 0, "manifest": 0}

    def fake_urls(flow_key, session_id, count=photo_upload.MAX_UPLOAD_PAGES):
        calls["urls"] += 1
        return [f"https://signed/{flow_key}/{session_id}/{calls['urls']}"]

    def fake_manifest(flow_key, session_id):
        calls["manifest"] += 1
        return f"https://signed/{flow_key}/{session_id}/manifest/{calls['manifest']}"

    monkeypatch.setattr(photo_upload, "generate_put_urls", fake_urls)
    monkeypatch.setattr(photo_upload, "generate_manifest_put_url", fake_manifest)
    return calls


def test_render_state_cached_across_reruns(session, counted_presign):
    fs = FakeDetailFS([_entry("page_1.jpg", '"a"')])
    first = get_uploader_render_state(fs, FLOW, SID)
    second = get_uploader_render_state(fs, FLOW, SID)
    # Identical objects/values -> identical iframe HTML -> no remount.
    assert first == second
    assert counted_presign["urls"] == 1
    assert counted_presign["manifest"] == 1
    assert first["existing_names"] == ["page_1.jpg"]


def test_render_state_reminted_after_expiry(session, counted_presign, monkeypatch):
    fs = FakeDetailFS()
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(photo_upload.time, "time", lambda: now["t"])
    get_uploader_render_state(fs, FLOW, SID)
    now["t"] += photo_upload.URL_REMINT_SECONDS + 1
    get_uploader_render_state(fs, FLOW, SID)
    assert counted_presign["urls"] == 2


def test_render_state_reminted_for_new_session_id(session, counted_presign):
    fs = FakeDetailFS()
    get_uploader_render_state(fs, FLOW, SID)
    other = get_uploader_render_state(fs, FLOW, "user_2_1700000001")
    assert other["session_id"] == "user_2_1700000001"
    assert counted_presign["urls"] == 2


def test_render_state_recaptures_seed_after_invalidate(session, counted_presign):
    fs = FakeDetailFS()
    state = get_uploader_render_state(fs, FLOW, SID)
    assert state["existing_names"] == []
    # A photo lands; the CACHED state must not change (stable HTML)...
    fs.entries.append(_entry("page_1.jpg", '"a"'))
    assert get_uploader_render_state(fs, FLOW, SID)["existing_names"] == []
    # ...until the state is explicitly invalidated (cleanup / start over).
    invalidate_uploader_state(FLOW)
    assert get_uploader_render_state(fs, FLOW, SID)["existing_names"] == ["page_1.jpg"]


def test_reset_upload_session_drops_render_state(session, counted_presign):
    fs = FakeDetailFS()
    session[f"upload_session_{FLOW}"] = SID
    get_uploader_render_state(fs, FLOW, SID)
    reset_upload_session(FLOW)
    assert photo_upload._uploader_state_key(FLOW) not in session
