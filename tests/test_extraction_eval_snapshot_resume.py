"""Read amplification and interrupted Firestore capture use only in-memory fakes."""
import json
from collections import Counter

import pytest
from google.api_core.exceptions import ResourceExhausted

from scripts.extraction_eval.snapshot import create_snapshot
from test_extraction_eval_snapshot import FakeBackend


class CountingBackend(FakeBackend):
    """Model ordered query cursors while recording successful document reads."""

    def __init__(self):
        super().__init__()
        self.query_calls = []
        self.query_reads = []
        self.get_attempts = []
        self.get_reads = []
        self.image_lists = []
        self.image_info_reads = []
        self.fail_get = None
        self.fail_stream = None

    def _stream(self, collection, owner, documents, cursor, checkpoint):
        key = (collection, owner)
        self.query_calls.append(key)
        invocation = self.query_calls.count(key)
        last_id = cursor.get("last_id")
        yielded = 0
        for document in sorted(documents, key=lambda item: item.get("id")):
            if last_id and document.get("id") <= last_id:
                continue
            if self.fail_stream == (collection, owner, invocation, yielded):
                raise ResourceExhausted("daily Firestore quota exhausted")
            self.query_reads.append(document.get("path"))
            cursor.update(value_index=0, last_id=document.get("id"))
            yielded += 1
            yield document
        cursor.update(value_index=1, last_id=None)
        checkpoint()

    def iter_books_resumable(self, entered_by, cursor, checkpoint):
        return self._stream("books", entered_by, self.iter_books(entered_by), cursor, checkpoint)

    def iter_related_resumable(self, collection, book_id, cursor, checkpoint):
        return self._stream(collection, book_id, self.iter_related(collection, book_id), cursor, checkpoint)

    def get_document(self, path):
        self.get_attempts.append(path)
        if path == self.fail_get:
            raise ResourceExhausted("daily Firestore quota exhausted")
        self.get_reads.append(path)
        return super().get_document(path)

    def list_images(self, folder):
        self.image_lists.append(folder)
        return super().list_images(folder)

    def image_info(self, path):
        self.image_info_reads.append(path)
        return super().image_info(path)


@pytest.fixture
def counted_source():
    source = CountingBackend()
    author = source.add("authors", "jane", forename="Jane", surname="Doe")
    illustrator = source.add("illustrators", "ivy", name="Ivy Ink")
    book = source.add("books", "story", title="Story", entered_by="pilot_import",
                      author=author, illustrator=illustrator, publisher="publishers/missing")
    for number in (1, 2):
        source.add("pages", f"story_{number}", book=book, page_number=number,
                   text=f"Page {number}", contains_story=True)
        source.image("Story", number)
    character = source.add("characters", "fox", book=book, name="Fox")
    source.add("aliases", "freddy", book=book, name="Freddy", character=character)
    return source


def _assert_no_manifest(path):
    assert not (path / "manifest.json").exists()
    assert not (path / "manifest.json.tmp").exists()


@pytest.mark.parametrize("options, audits, get_reads, query_reads, query_count", [
    ({}, {"source_changes": False, "membership": False}, 3, 5, 4),
    ({"audit_source_changes": True}, {"source_changes": True, "membership": False}, 11, 5, 4),
    ({"audit_membership": True}, {"source_changes": False, "membership": True}, 3, 9, 7),
    ({"audit": True}, {"source_changes": True, "membership": True}, 11, 9, 7),
    ({"audit_source_changes": True, "audit_membership": True},
     {"source_changes": True, "membership": True}, 11, 9, 7),
])
def test_default_one_pass_and_explicit_audit_read_costs(
        tmp_path, counted_source, options, audits, get_reads, query_reads, query_count):
    manifest = create_snapshot(tmp_path, counted_source, **options)
    assert manifest.get("audits") == audits
    assert manifest.get("source_consistent") is (True if all(audits.values()) else None)
    assert len(counted_source.get_reads) == get_reads
    assert len(counted_source.query_reads) == query_reads
    assert len(counted_source.query_calls) == query_count
    assert len(counted_source.image_lists) == (2 if audits.get("membership") else 1)
    assert len(counted_source.image_info_reads) == (2 if audits.get("source_changes") else 0)
    assert len(manifest.get("raw_documents", {})) == 7
    if not any(audits.values()):
        existing_reads = counted_source.query_reads + [
            path for path in counted_source.get_reads if path in counted_source.documents]
        assert Counter(existing_reads) == Counter(counted_source.documents.keys())
        assert not (tmp_path / "audit-progress.json").exists()


@pytest.mark.parametrize("options", [{"audit_source_changes": True}, {"audit": True}])
def test_requested_source_audit_retains_change_detection(tmp_path, counted_source, options):
    counted_source.changed_document = "pages/story_1"
    manifest = create_snapshot(tmp_path, counted_source, **options)
    assert manifest.get("source_consistent") is False
    assert {"issue": "source_document_changed", "path": "pages/story_1"} in manifest.get("audit", [])


def test_reference_quota_failure_checkpoints_successful_reads_and_releases_lock(tmp_path, counted_source):
    counted_source.fail_get = "illustrators/ivy"
    with pytest.raises(ResourceExhausted):
        create_snapshot(tmp_path, counted_source)
    _assert_no_manifest(tmp_path)
    checkpoint = json.loads((tmp_path / "inventory-progress.json").read_text())
    assert "authors/jane" in checkpoint.get("raw_documents", {})
    assert counted_source.get_attempts == ["authors/jane", "illustrators/ivy"]
    queries_before = list(counted_source.query_reads)
    counted_source.fail_get = None
    manifest = create_snapshot(tmp_path, counted_source)
    assert manifest.get("complete") is True
    assert counted_source.query_reads == queries_before
    assert counted_source.get_reads.count("authors/jane") == 1
    assert counted_source.get_reads.count("illustrators/ivy") == 1


@pytest.mark.parametrize("collection,owner", [("books", "pilot_import"), ("pages", "story")])
def test_partial_discovery_and_related_queries_resume_after_last_document(
        tmp_path, counted_source, collection, owner):
    counted_source.add("books", "zebra", title="Zebra", entered_by="pilot_import")
    counted_source.fail_stream = (collection, owner, 1, 1)
    with pytest.raises(ResourceExhausted):
        create_snapshot(tmp_path, counted_source)
    _assert_no_manifest(tmp_path)
    checkpoint = json.loads((tmp_path / "inventory-progress.json").read_text())
    query = checkpoint.get("queries", {}).get(json.dumps([collection, owner]), {})
    assert query.get("done") is False
    assert query.get("cursor", {}).get("last_id")
    assert len(query.get("paths", [])) == 1
    first_read = query.get("paths")[0]
    manifest = create_snapshot(tmp_path, counted_source)
    assert len(manifest.get("books", [])) == 2
    assert counted_source.query_reads.count(first_read) == 1
    assert all(count == 1 for count in Counter(counted_source.query_reads).values())


@pytest.mark.parametrize("changed", [False, True])
def test_source_audit_resume_skips_documents_already_checked(tmp_path, counted_source, changed):
    if changed:
        counted_source.changed_document = "pages/story_1"
    counted_source.fail_get = "pages/story_2"
    with pytest.raises(ResourceExhausted):
        create_snapshot(tmp_path, counted_source, audit=True)
    _assert_no_manifest(tmp_path)
    progress = json.loads((tmp_path / "audit-progress.json").read_text())
    checked = progress.get("checked_documents", [])
    assert checked and "pages/story_1" in checked
    assert "pages/story_2" not in checked
    previous_counts = Counter(counted_source.get_reads)
    counted_source.fail_get = None
    manifest = create_snapshot(tmp_path, counted_source, audit=True)
    assert manifest.get("source_consistent") is (not changed)
    for path in checked:
        assert counted_source.get_reads.count(path) == previous_counts.get(path)
    assert counted_source.get_reads.count("pages/story_2") == 1
    changes = [item for item in manifest.get("audit", []) if item.get("issue") == "source_document_changed"]
    assert len(changes) == (1 if changed else 0)


@pytest.mark.parametrize("collection, yielded", [("pages", 1), ("aliases", 0)])
def test_membership_audit_resume_skips_capture_and_completed_audit_reads(
        tmp_path, counted_source, collection, yielded):
    counted_source.fail_stream = (collection, "story", 2, yielded)
    with pytest.raises(ResourceExhausted):
        create_snapshot(tmp_path, counted_source, audit_membership=True)
    _assert_no_manifest(tmp_path)
    assert counted_source.query_reads.count("pages/story_1") == 2
    assert counted_source.query_reads.count("pages/story_2") == (1 if collection == "pages" else 2)
    get_counts = Counter(counted_source.get_reads)
    manifest = create_snapshot(tmp_path, counted_source, audit_membership=True)
    assert manifest.get("source_consistent") is None
    assert manifest.get("audits") == {"source_changes": False, "membership": True}
    assert counted_source.query_reads.count("books/story") == 1
    assert counted_source.query_reads.count("pages/story_1") == 2
    assert counted_source.query_reads.count("pages/story_2") == 2
    assert counted_source.query_reads.count("characters/fox") == 2
    assert Counter(counted_source.get_reads) == get_counts
    assert counted_source.reads == ["sawimages/Story/page_1.jpg", "sawimages/Story/page_2.jpg"]


def test_requested_membership_audit_detects_added_document(tmp_path, counted_source, monkeypatch):
    original_read_image = counted_source.read_image

    def read_and_add_page(path, version_id=None):
        content = original_read_image(path, version_id)
        counted_source.add("pages", "story_3", book="books/story", page_number=3,
                           text="Concurrent addition", contains_story=True)
        return content

    monkeypatch.setattr(counted_source, "read_image", read_and_add_page)
    manifest = create_snapshot(tmp_path, counted_source, audit_membership=True)
    assert manifest.get("source_consistent") is False
    assert {"issue": "source_collection_membership_changed", "book_id": "story", "collection": "pages"} in manifest.get("audit", [])


@pytest.mark.parametrize("change, options, issue", [
    ("deleted_document", {"audit_source_changes": True}, "source_document_changed"),
    ("appeared_document", {"audit_source_changes": True}, "source_document_appeared"),
    ("deleted_document", {"audit_membership": True}, "source_collection_membership_changed"),
    ("changed_image", {"audit_source_changes": True}, "source_image_changed"),
    ("added_image", {"audit_membership": True}, "source_image_inventory_changed"),
    ("added_image", {"audit_source_changes": True}, None),
    ("changed_document", {"audit_membership": True}, None),
])
def test_optional_audits_preserve_document_and_image_checks(
        tmp_path, counted_source, monkeypatch, change, options, issue):
    original_read_image = counted_source.read_image

    def read_then_change_source(path, version_id=None):
        content = original_read_image(path, version_id)
        if change == "deleted_document":
            counted_source.documents.pop("pages/story_1", None)
        elif change == "appeared_document":
            counted_source.add("publishers", "missing", name="New publisher")
        elif change == "changed_image":
            counted_source.images.get("sawimages/Story/page_1.jpg", {}).get("info", {})["etag"] = "new-revision"
        elif change == "added_image":
            counted_source.image("Story", 3)
        elif change == "changed_document":
            counted_source.documents.get("pages/story_1", {}).get("data", {})["text"] = "New text"
        return content

    monkeypatch.setattr(counted_source, "read_image", read_then_change_source)
    manifest = create_snapshot(tmp_path, counted_source, **options)
    source_findings = [finding for finding in manifest.get("audit", [])
                       if finding.get("issue", "").startswith("source_")]
    assert manifest.get("source_consistent") is (False if issue else None)
    if issue:
        assert [finding.get("issue") for finding in source_findings] == [issue]
    else:
        assert source_findings == []


def test_legacy_inventory_and_image_progress_resume_without_firestore_reads(tmp_path, counted_source):
    counted_source.fail_image = "sawimages/Story/page_2.jpg"
    with pytest.raises(OSError, match="interrupted"):
        create_snapshot(tmp_path, counted_source)
    _assert_no_manifest(tmp_path)
    inventory_path = tmp_path / "inventory.json"
    inventory = json.loads(inventory_path.read_text())
    inventory.pop("audits")
    inventory_path.write_text(json.dumps(inventory))
    previous_gets, previous_queries = list(counted_source.get_reads), list(counted_source.query_reads)
    counted_source.fail_image = None
    manifest = create_snapshot(tmp_path, counted_source)
    assert manifest.get("complete") is True
    assert manifest.get("audits") == {"source_changes": False, "membership": False}
    assert counted_source.get_reads == previous_gets
    assert counted_source.query_reads == previous_queries
    assert counted_source.reads.count("sawimages/Story/page_1.jpg") == 1


@pytest.mark.parametrize("stage", ["capture", "images", "complete"])
@pytest.mark.parametrize("options, message", [
    ({"books": ["story"]}, "selection differs"),
    ({"entered_by": "another_operator"}, "selection differs"),
    ({"audit_source_changes": True}, "audit settings differ"),
    ({"audit_membership": True}, "audit settings differ"),
    ({"audit": True}, "audit settings differ"),
])
def test_resume_rejects_selection_or_audit_changes_before_reading(
        tmp_path, counted_source, stage, options, message):
    if stage == "capture":
        counted_source.fail_get = "illustrators/ivy"
        with pytest.raises(ResourceExhausted):
            create_snapshot(tmp_path, counted_source)
    elif stage == "images":
        counted_source.fail_image = "sawimages/Story/page_2.jpg"
        with pytest.raises(OSError):
            create_snapshot(tmp_path, counted_source)
    else:
        create_snapshot(tmp_path, counted_source)
    gets, queries = list(counted_source.get_attempts), list(counted_source.query_calls)
    with pytest.raises(ValueError, match=message):
        create_snapshot(tmp_path, counted_source, **options)
    assert counted_source.get_attempts == gets
    assert counted_source.query_calls == queries


def test_missing_references_are_cached_across_books_and_resume(tmp_path, counted_source):
    counted_source.add("books", "zebra", title="Zebra", entered_by="pilot_import",
                       author="authors/jane", publisher="publishers/missing")
    counted_source.fail_stream = ("pages", "zebra", 1, 0)
    counted_source.add("pages", "zebra_1", book="books/zebra", page_number=1,
                       contains_story=True, text="Zebra")
    with pytest.raises(ResourceExhausted):
        create_snapshot(tmp_path, counted_source)
    checkpoint = json.loads((tmp_path / "inventory-progress.json").read_text())
    assert checkpoint.get("missing_documents") == ["publishers/missing"]
    manifest = create_snapshot(tmp_path, counted_source)
    assert counted_source.get_attempts.count("publishers/missing") == 1
    assert counted_source.get_attempts.count("authors/jane") == 1
    dangling = [item for item in manifest.get("audit", []) if item.get("issue") == "dangling_reference"]
    assert dangling == [{"issue": "dangling_reference", "path": "publishers/missing"}]
