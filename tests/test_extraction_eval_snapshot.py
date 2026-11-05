"""Snapshot/sampling fixtures: no cloud credentials, clients or network."""
import copy
import json

import pytest

from scripts.extraction_eval.sampling import save_selection, select_books
from scripts.extraction_eval.snapshot import (
    EXCLUDED_METADATA_ONLY_TITLES, create_snapshot, json_value,
)


class FakeBackend:
    def __init__(self):
        self.documents = {}
        self.images = {}
        self.reads = []
        self.fail_image = None
        self.changed_document = None

    def add(self, collection, doc_id, **data):
        path = f"{collection}/{doc_id}"
        self.documents[path] = {"id": doc_id, "path": path, "data": data, "update_time": "v1"}
        return path

    def image(self, title, number, content=None, filename=None):
        path = f"sawimages/{title}/{filename or f'page_{number}.jpg'}"
        self.images[path] = {"bytes": content or str(number).encode(),
                             "info": {"path": path, "version_id": "1", "etag": str(number)}}
        return path

    def iter_books(self, entered_by):
        return [copy.deepcopy(d) for path, d in self.documents.items()
                if path.startswith("books/") and d.get("data", {}).get("entered_by") == entered_by]

    def iter_related(self, collection, book_id):
        return [copy.deepcopy(d) for path, d in self.documents.items()
                if path.startswith(f"{collection}/") and d.get("data", {}).get("book") == f"books/{book_id}"]

    def get_document(self, path):
        value = copy.deepcopy(self.documents.get(path))
        if path == self.changed_document:
            value["update_time"] = "v2"
        return value

    def list_images(self, folder):
        return [copy.deepcopy(value.get("info")) for path, value in self.images.items()
                if path.startswith(f"sawimages/{folder}/")]

    def image_info(self, path):
        return copy.deepcopy(self.images.get(path, {}).get("info"))

    def read_image(self, path, version_id=None):
        self.reads.append(path)
        if path == self.fail_image:
            raise OSError("fixture interrupted download")
        return self.images.get(path, {}).get("bytes")


@pytest.fixture
def source():
    backend = FakeBackend()
    author = backend.add("authors", "jane", forename="Jane", surname="Doe")
    illustrator = backend.add("illustrators", "legacy", forename="Ivy", surname="Ink")
    book = backend.add("books", "story", title="Story", entered_by="pilot_import",
                       author=author, illustrator=illustrator, publisher="publishers/deleted",
                       published=-1, validated=True, validated_by="pilot_import")
    for number, text, story in [(1, "Once upon a time", True), (2, "", True), (3, "", False)]:
        backend.add("pages", f"story_{number}", book=book, page_number=number,
                    text=text, contains_story=story, text_source="skipped_wordless" if number == 2 else "pdf")
        backend.image("Story", number)
    character = backend.add("characters", "story_fox", book=book, name="Fox")
    backend.add("aliases", "story_freddy", book=book, name="Freddy", character=character)
    backend.add("aliases", "story_lost", book=book, name="Lost", character="characters/missing")
    return backend


def test_frozen_eligibility_wordless_nonstory_and_raw_schema(tmp_path, source):
    manifest = create_snapshot(tmp_path, source, audit=True)
    book = manifest.get("books")[0]
    first, wordless, nonstory = book.get("pages")
    assert first.get("reference", {}).get("text", {}).get("status") == "observed"
    assert wordless.get("reference", {}).get("text", {}).get("kind") == "wordless"
    assert wordless.get("reference", {}).get("text", {}).get("status") == "observed"
    assert nonstory.get("reference", {}).get("text", {}).get("status") == "not_annotated"
    assert "corrected" not in first.get("raw")  # no app model defaults injected
    metadata = book.get("reference", {}).get("metadata", {})
    assert metadata.get("authors", {}).get("value") == ["Jane Doe"]
    assert metadata.get("authors", {}).get("complete") is False
    assert metadata.get("illustrators", {}).get("value") == ["Ivy Ink"]
    assert metadata.get("publisher", {}).get("status") == "missing"
    assert metadata.get("published_year", {}).get("status") == "missing"
    assert book.get("reference", {}).get("themes", {}).get("status") == "not_annotated"
    assert book.get("reference", {}).get("characters", {}).get("value")[0].get("aliases") == ["Freddy"]
    assert any(row.get("issue") == "dangling_alias_attachment" for row in manifest.get("audit"))
    assert manifest.get("source_consistent") is True


def test_five_metadata_only_shells_excluded(tmp_path, source):
    for title in EXCLUDED_METADATA_ONLY_TITLES:
        source.add("books", title.replace(" ", "_"), title=title, entered_by="pilot_import")
    manifest = create_snapshot(tmp_path, source)
    assert len(manifest.get("books")) == 1
    assert {book.get("title") for book in manifest.get("excluded_books")} == EXCLUDED_METADATA_ONLY_TITLES


def test_missing_text_and_image_only_page_are_missing_not_wordless(tmp_path, source):
    source.documents.get("pages/story_2").get("data").pop("text")
    source.image("Story", 5)
    manifest = create_snapshot(tmp_path, source)
    pages = manifest.get("books")[0].get("pages")
    assert pages[1].get("reference", {}).get("text", {}).get("status") == "missing"
    assert pages[-1].get("page_number") == 5
    assert pages[-1].get("reference", {}).get("text", {}).get("status") == "missing"
    assert any(row.get("issue") == "page_number_gaps" and row.get("numbers") == [4]
               for row in manifest.get("audit"))


def test_variants_ignored_duplicates_and_document_id_mismatch_audited(tmp_path, source):
    source.image("Story", 1, filename="page_1_cropped.jpg")
    source.image("Story", 1, filename="page_1_display.jpg")
    source.image("Story", 2, filename="PAGE_2.JPG")
    original = source.documents.pop("pages/story_3")
    original["id"], original["path"] = "legacy_3", "pages/legacy_3"
    source.documents["pages/legacy_3"] = original
    source.image("Story", 4, content=b"1")
    result = create_snapshot(tmp_path, source)
    assert not any("cropped" in path or "display" in path for path in source.reads)
    assert result.get("books")[0].get("pages")[1].get("reference", {}).get("text", {}).get("status") == "ambiguous"
    issues = {row.get("issue") for row in result.get("audit")}
    assert {"duplicate_page_alignment", "mismatched_page_id", "duplicate_image_bytes"} <= issues


def test_resume_partial_download_reuses_images_and_frozen_reference(tmp_path, source):
    source.fail_image = "sawimages/Story/page_2.jpg"
    with pytest.raises(OSError, match="interrupted"):
        create_snapshot(tmp_path, source, audit=True)
    assert (tmp_path / "inventory.json").is_file()
    assert not (tmp_path / "manifest.json").exists()
    source.fail_image = None
    source.documents.get("pages/story_1").get("data")["text"] = "Changed after freeze"
    manifest = create_snapshot(tmp_path, source, audit=True)
    assert source.reads.count("sawimages/Story/page_1.jpg") == 1
    assert manifest.get("books")[0].get("pages")[0].get("reference", {}).get("text", {}).get("value") == "Once upon a time"
    assert manifest.get("source_consistent") is False
    assert any(row.get("issue") == "source_document_changed" for row in manifest.get("audit"))


def test_completed_snapshot_replay_is_local_and_immutable(tmp_path, source):
    manifest = create_snapshot(tmp_path, source)
    assert create_snapshot(tmp_path, object()) == manifest
    with pytest.raises(ValueError, match="selection differs"):
        create_snapshot(tmp_path, object(), books=["story"])
    image = manifest.get("books")[0].get("pages")[0].get("image")
    (tmp_path / image.get("path")).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity failure"):
        create_snapshot(tmp_path, object())


def test_selection_unknown_requested_book_is_rejected(tmp_path, source):
    with pytest.raises(ValueError, match="Unknown requested books"):
        create_snapshot(tmp_path, source, books=["typo"])


def _manifest(count=48):
    books = []
    for index in range(count):
        pages = [{"image": {"path": f"image-{index}-{page}.jpg"},
                  "reference": {"text": {"status": "observed", "kind": "text",
                                              "value": "word " * (10 if index % 2 else 80)}}}
                 for page in range(2 + index)]
        books.append({"id": f"book-{index:02}", "title": f"Book {index}", "pages": pages})
    return {"snapshot_id": "fixture", "books": books}


def test_selection_twenty_books_sixteen_random_four_challenge_replays(tmp_path):
    manifest = _manifest()
    challenge = [f"book-{n:02}" for n in range(4)]
    result = select_books(manifest, limit=20, challenge_books=challenge)
    assert result == select_books(manifest, limit=20, challenge_books=challenge)
    assert len(result.get("book_ids")) == 20
    assert result.get("probability_sample_count") == 16
    assert result.get("challenge_count") == 4
    assert all(0 < row.get("inclusion_probability") <= 1
               for row in result.get("selected") if row.get("role") == "probability")
    assert all(row.get("inclusion_probability") is None
               for row in result.get("selected") if row.get("role") == "challenge")
    assert result.get("page_count") == sum(row.get("image_count") for row in result.get("selected"))
    path = tmp_path / "selection.json"
    save_selection(path, result)
    assert save_selection(path, result) == json.loads(path.read_text())
    with pytest.raises(ValueError, match="immutable"):
        save_selection(path, select_books(manifest, limit=20, seed=99))


def test_explicit_selection_and_small_probability_sample_are_honest():
    manifest = _manifest()
    explicit = select_books(manifest, books=["Book 2", "book-03"])
    assert explicit.get("book_ids") == ["book-02", "book-03"]
    assert all(row.get("role") == "explicit" and row.get("inclusion_probability") is None
               for row in explicit.get("selected"))
    sampled = select_books(manifest, limit=1)
    assert sampled.get("selected")[0].get("inclusion_probability") == 1 / 48
    with pytest.raises(ValueError, match="smaller"):
        select_books(manifest, limit=1, books=["book-00", "book-01"])


def test_empty_imported_text_never_means_picture_only():
    manifest = _manifest(1)
    for page in manifest.get("books")[0].get("pages"):
        page.get("reference", {}).get("text").update(value="", kind="wordless")
    result = select_books(manifest, limit=1)
    assert result.get("inventory")[0].get("text_density") == "unknown"
    assert result.get("inventory")[0].get("wordless_reference_pages") == 2


def test_document_reference_serialized_without_get():
    class Ref:
        path, id = "authors/jane", "jane"
        def get(self):
            raise AssertionError("Never dereference while serializing")
    assert json_value({"author": Ref()}) == {"author": {"document_reference": "authors/jane"}}


def test_duplicate_editions_are_excluded_from_heldout_without_extra_paid_books():
    manifest = _manifest(5)
    books = manifest.get("books")
    books[1]["title"] = "BOOK 0!"
    for index in (1, 2):
        for page in books[index].get("pages"):
            page.get("image")["sha256"] = "identical-original"
    result = select_books(manifest, books=["book-00"])
    assert result.get("book_ids") == ["book-00"]
    assert result.get("held_out_book_ids") == ["book-03", "book-04"]
    assert {row.get("id") for row in result.get("held_out_exclusions")} == {"book-01", "book-02"}
    assert len(result.get("duplicate_groups")) == 1
    group = result.get("selected")[0].get("holdout_group")
    assert all(row.get("holdout_group") == group for row in result.get("inventory")[:3])


def test_sampling_tolerates_a_missing_image_without_losing_other_pages():
    manifest = _manifest(1)
    manifest.get("books")[0].get("pages")[0]["image"] = None
    result = select_books(manifest, limit=1)
    assert result.get("book_ids") == ["book-00"]
    assert result.get("page_count") == 1
