"""Unit tests for scripts/data_cleanup.py (issue #120).

These exercise the *pure* logic only and never touch the network:

  * junk-name heuristics + allowlist
  * image-count classification + page-image filename filter
  * markdown / JSON / CSV report rendering
  * reviewed-record loading (report JSON, list JSON, plain ids file)
  * the audit and the dry-run / execute delete planner, driven by an in-memory
    fake backend that mimics Firestore + S3 (refs as "collection/id" strings)

The live audit/delete against the real Firestore + S3 must be run by Chris where
the secrets exist; that path is not exercised here by design.
"""

import json
import os
import sys

import pytest

# Make scripts/ importable (it has no __init__.py and a hyphenated sibling).
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import data_cleanup as dc  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory fake backend.
# ---------------------------------------------------------------------------

class FakeBackend(dc.CleanupBackend):
    """In-memory Firestore + S3 stand-in.

    ``docs``: {collection: {doc_id: data}} where ref fields are "collection/id".
    ``s3``:   {folder_name: [object_path, ...]} under one bucket.
    """

    def __init__(self, docs=None, s3=None):
        self.docs = docs or {}
        self.s3 = s3 or {}
        self.deleted_docs = []
        self.deleted_s3 = []

    # --- Firestore ---
    def iter_docs(self, collection):
        for doc_id, data in self.docs.get(collection, {}).items():
            yield doc_id, dict(data)

    def doc_exists(self, collection, doc_id):
        return doc_id in self.docs.get(collection, {})

    def delete_doc(self, collection, doc_id):
        self.deleted_docs.append(f"{collection}/{doc_id}")
        self.docs.get(collection, {}).pop(doc_id, None)

    def query_referencing_ids(self, collection, field, target):
        out = []
        for doc_id, data in self.docs.get(collection, {}).items():
            if data.get(field) == target:
                out.append(doc_id)
        return out

    # --- S3 ---
    def list_book_folders(self, bucket):
        return list(self.s3.keys())

    def list_objects(self, bucket, folder):
        return list(self.s3.get(folder, []))

    def count_page_images(self, bucket, folder):
        return sum(1 for p in self.s3.get(folder, []) if dc.is_page_image(p))

    def delete_prefix(self, bucket, folder):
        paths = list(self.s3.get(folder, []))
        self.deleted_s3.extend(paths)
        self.s3.pop(folder, None)
        return paths


# ---------------------------------------------------------------------------
# Junk-name heuristics.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "test", "Test", "  test  ", "asdf", "qwerty", "xxx", "foo", "bar",
    "aaaa", "1234", "asdfgh",  # keyboard-walk gibberish
    "zz", "ab", "", "   ", "untitled", "new book", "test book",
    "qwertyuiop", "zxcvbnm", "0000", "Lorem Ipsum",
])
def test_junk_names_flagged(name):
    assert dc.junk_name_reason(name) is not None


@pytest.mark.parametrize("name", [
    "The Gruffalo", "Beatrix Potter", "Roald Dahl", "Where the Wild Things Are",
    "A A Milne", "Dr Seuss", "Li", "Wu",  # allowlisted short names
    "Eric Carle", "Julia Donaldson",
])
def test_real_names_not_flagged(name):
    assert dc.junk_name_reason(name) is None


def test_allowlist_overrides_short_heuristic():
    assert dc.junk_name_reason("Li") is None          # allowlisted
    assert dc.junk_name_reason("Qz") is not None       # not allowlisted, short


def test_all_words_test_tokens():
    assert dc.junk_name_reason("test test") is not None
    assert dc.junk_name_reason("foo bar") is not None


def test_custom_allowlist_and_tokens():
    assert dc.junk_name_reason("zzz", tokens={"zzz"}) is not None
    assert dc.junk_name_reason("realname", tokens={"realname"}, allowlist={"realname"}) is None


# ---------------------------------------------------------------------------
# Image-count + page-image filename filter.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("count,flagged", [(0, True), (1, True), (2, True), (3, False), (10, False)])
def test_image_count_reason(count, flagged):
    assert (dc.image_count_reason(count) is not None) == flagged


def test_image_count_singular_plural_wording():
    assert "1 page image " in dc.image_count_reason(1)
    assert "0 page images " in dc.image_count_reason(0)


@pytest.mark.parametrize("name,is_page", [
    ("page_1.jpg", True),
    ("page_12.jpg", True),
    ("sawimages/Title/page_3.jpg", True),
    ("page_1_cropped.jpg", False),
    ("page_3_cropped.jpg", False),
    ("cover.png", False),
    ("page_.jpg", False),
])
def test_is_page_image(name, is_page):
    assert dc.is_page_image(name) is is_page


def test_book_folder_name():
    assert dc.book_folder_name("My Book", "sawimages/My Book") == "My Book"
    assert dc.book_folder_name("My Book", "") == "My Book"
    assert dc.book_folder_name("My Book", "sawimages/My Book/") == "My Book"


# ---------------------------------------------------------------------------
# Audit.
# ---------------------------------------------------------------------------

def _sample_backend():
    docs = {
        "books": {
            "the_gruffalo": {"title": "The Gruffalo", "photos_url": "sawimages/The Gruffalo"},
            "asdf": {"title": "asdf", "photos_url": "sawimages/asdf"},
            "thin_book": {"title": "Thin Book", "photos_url": "sawimages/Thin Book"},
        },
        "authors": {
            "julia_donaldson": {"forename": "Julia", "surname": "Donaldson"},
            "test_author": {"forename": "test", "surname": ""},
        },
        "illustrators": {},
        "publishers": {"qwerty": {"name": "qwerty"}},
        "pages": {
            "the_gruffalo_1": {"book": "books/the_gruffalo", "page_number": 1},
            "ghost_5": {"book": "books/deleted_book", "page_number": 5},  # dangling
        },
        "characters": {
            "the_gruffalo_mouse": {"book": "books/the_gruffalo", "name": "Mouse"},
            "orphan_char": {"book": "books/gone", "name": "Ghost"},  # dangling
        },
        "aliases": {
            "the_gruffalo_mousey": {"character": "characters/the_gruffalo_mouse", "book": "books/the_gruffalo", "name": "Mousey"},
            "bad_alias": {"character": "characters/nonexistent", "book": "books/the_gruffalo", "name": "Bad"},  # dangling
        },
    }
    s3 = {
        "The Gruffalo": [
            "sawimages/The Gruffalo/page_1.jpg",
            "sawimages/The Gruffalo/page_2.jpg",
            "sawimages/The Gruffalo/page_3.jpg",
            "sawimages/The Gruffalo/page_1_cropped.jpg",  # ignored
        ],
        "asdf": ["sawimages/asdf/page_1.jpg"],            # too few (1)
        "Thin Book": [],                                   # too few (0)
        "Orphan Folder": ["sawimages/Orphan Folder/page_1.jpg"],  # orphan
        "uploads": ["sawimages/uploads/x/page_1.jpg"],     # ignored prefix
    }
    return FakeBackend(docs, s3)


def test_audit_categories():
    report = dc.run_audit(_sample_backend())
    cats = report["categories"]

    too_few = {r["id"] for r in cats[dc.CAT_TOO_FEW_IMAGES]}
    assert too_few == {"asdf", "thin_book"}   # gruffalo has 3, excluded

    orphans = {r["s3_path"] for r in cats[dc.CAT_ORPHANED_IMAGES]}
    assert orphans == {"sawimages/Orphan Folder"}  # uploads ignored

    junk = {(r["collection"], r["id"]) for r in cats[dc.CAT_JUNK_NAMES]}
    assert ("books", "asdf") in junk
    assert ("authors", "test_author") in junk
    assert ("publishers", "qwerty") in junk
    assert ("authors", "julia_donaldson") not in junk

    dangling = {(r["collection"], r["id"]) for r in cats[dc.CAT_DANGLING_REFS]}
    assert ("pages", "ghost_5") in dangling
    assert ("characters", "orphan_char") in dangling
    assert ("aliases", "bad_alias") in dangling
    assert ("pages", "the_gruffalo_1") not in dangling


def test_audit_report_is_serialisable_and_renders():
    report = dc.run_audit(_sample_backend())
    # JSON round-trip.
    text = json.dumps(report)
    assert "categories" in json.loads(text)
    # Markdown renders and mentions a known target.
    md = dc.build_report_markdown(report)
    assert "# Data cleanup audit report" in md
    assert "Thin Book" in md or "thin_book" in md


def test_write_report_files(tmp_path):
    report = dc.run_audit(_sample_backend())
    paths = dc.write_report_files(report, str(tmp_path))
    for key in ("markdown", "json", "csv"):
        assert os.path.exists(paths[key])
    # JSON reloads to an equivalent structure.
    with open(paths["json"]) as f:
        assert "categories" in json.load(f)


# ---------------------------------------------------------------------------
# Reviewed-record loading.
# ---------------------------------------------------------------------------

def test_load_reviewed_from_report_json(tmp_path):
    report = dc.run_audit(_sample_backend())
    p = tmp_path / "cand.json"
    p.write_text(json.dumps(report))
    records = dc.load_reviewed_records(str(p))
    assert len(records) == sum(len(v) for v in report["categories"].values())


def test_load_reviewed_from_list_json(tmp_path):
    recs = [dc.make_record(category="x", delete_kind=dc.KIND_DOC, collection="pages", doc_id="a_1", reason="r")]
    p = tmp_path / "list.json"
    p.write_text(json.dumps(recs))
    assert dc.load_reviewed_records(str(p)) == recs


def test_load_reviewed_from_ids_file(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text(
        "# comment\n"
        "books/test_book\n"
        "authors/test_author\n"
        "characters/b_goblin\n"
        "pages/b_3\n"
        "s3:sawimages/Orphan Folder\n"
    )
    records = dc.load_reviewed_records(str(p))
    kinds = {(r["delete_kind"], r.get("collection") or r.get("s3_path")) for r in records}
    assert (dc.KIND_BOOK, "books") in kinds
    assert (dc.KIND_PERSON_DOC, "authors") in kinds
    assert (dc.KIND_CHARACTER, "characters") in kinds
    assert (dc.KIND_DOC, "pages") in kinds
    assert (dc.KIND_S3_PREFIX, "sawimages/Orphan Folder") in kinds


# ---------------------------------------------------------------------------
# Delete planner — dry-run must mutate NOTHING; execute must mutate exactly.
# ---------------------------------------------------------------------------

def _delete_backend():
    docs = {
        "books": {"junk_book": {"title": "Junk Book", "photos_url": "sawimages/Junk Book"}},
        "authors": {"a1": {}, "a2": {}},
        "publishers": {},
        "illustrators": {},
        "pages": {
            "junk_book_1": {"book": "books/junk_book"},
            "junk_book_2": {"book": "books/junk_book"},
            "other_1": {"book": "books/other"},
        },
        "characters": {"junk_book_hero": {"book": "books/junk_book", "name": "Hero"}},
        "aliases": {
            "junk_book_heroine": {"character": "characters/junk_book_hero", "book": "books/junk_book"},
            "junk_book_direct": {"character": "characters/elsewhere", "book": "books/junk_book"},
        },
    }
    s3 = {"Junk Book": ["sawimages/Junk Book/page_1.jpg", "sawimages/Junk Book/page_1_cropped.jpg"]}
    return FakeBackend(docs, s3)


def test_dry_run_deletes_nothing_but_plans_everything():
    backend = _delete_backend()
    recs = [dc.make_record(category="x", delete_kind=dc.KIND_BOOK, collection="books",
                           doc_id="junk_book", title="Junk Book", s3_path="sawimages/Junk Book", reason="r")]
    plan = dc.execute_deletions(recs, backend, dry_run=True)

    # Nothing actually removed.
    assert backend.deleted_docs == []
    assert backend.deleted_s3 == []
    assert backend.docs["books"]  # still present

    # But the plan covers book + its pages + character + aliases + S3 objects.
    assert "books/junk_book" in plan.firestore_deletes
    assert "pages/junk_book_1" in plan.firestore_deletes
    assert "pages/junk_book_2" in plan.firestore_deletes
    assert "pages/other_1" not in plan.firestore_deletes
    assert "characters/junk_book_hero" in plan.firestore_deletes
    assert "aliases/junk_book_heroine" in plan.firestore_deletes
    assert "aliases/junk_book_direct" in plan.firestore_deletes
    assert "sawimages/Junk Book/page_1.jpg" in plan.s3_deletes
    assert all(line.startswith("[DRY-RUN]") for line in plan.actions)


def test_execute_book_deletes_cascade():
    backend = _delete_backend()
    recs = [dc.make_record(category="x", delete_kind=dc.KIND_BOOK, collection="books",
                           doc_id="junk_book", title="Junk Book", s3_path="sawimages/Junk Book", reason="r")]
    plan = dc.execute_deletions(recs, backend, dry_run=False)

    assert "books/junk_book" in backend.deleted_docs
    assert "pages/junk_book_1" in backend.deleted_docs
    assert "characters/junk_book_hero" in backend.deleted_docs
    assert "aliases/junk_book_heroine" in backend.deleted_docs
    assert "pages/other_1" not in backend.deleted_docs
    assert "sawimages/Junk Book/page_1.jpg" in backend.deleted_s3
    assert plan.skipped == []


def test_person_skipped_when_referenced():
    backend = _delete_backend()
    # a1 referenced by a book, a2 not.
    backend.docs["books"]["junk_book"]["author"] = "authors/a1"
    recs = [
        dc.make_record(category="x", delete_kind=dc.KIND_PERSON_DOC, collection="authors", doc_id="a1", reason="r"),
        dc.make_record(category="x", delete_kind=dc.KIND_PERSON_DOC, collection="authors", doc_id="a2", reason="r"),
    ]
    plan = dc.execute_deletions(recs, backend, dry_run=False)
    assert "authors/a2" in backend.deleted_docs
    assert "authors/a1" not in backend.deleted_docs
    assert any(label == "authors/a1" for label, _ in plan.skipped)


def test_orphan_s3_prefix_delete():
    backend = _delete_backend()
    backend.s3["Orphan"] = ["sawimages/Orphan/page_1.jpg", "sawimages/Orphan/page_2.jpg"]
    recs = [dc.make_record(category="x", delete_kind=dc.KIND_S3_PREFIX, s3_path="sawimages/Orphan", reason="r")]
    plan = dc.execute_deletions(recs, backend, dry_run=False)
    assert backend.deleted_docs == []
    assert "sawimages/Orphan/page_1.jpg" in backend.deleted_s3
    assert "Orphan" not in backend.s3


def test_character_delete_takes_aliases():
    backend = _delete_backend()
    recs = [dc.make_record(category="x", delete_kind=dc.KIND_CHARACTER, collection="characters",
                           doc_id="junk_book_hero", reason="r")]
    dc.execute_deletions(recs, backend, dry_run=False)
    assert "characters/junk_book_hero" in backend.deleted_docs
    assert "aliases/junk_book_heroine" in backend.deleted_docs


def test_protected_collection_guard():
    backend = _delete_backend()
    recs = [dc.make_record(category="x", delete_kind=dc.KIND_DOC, collection="users", doc_id="chris", reason="r")]
    with pytest.raises(ValueError):
        dc.execute_deletions(recs, backend, dry_run=False)


def test_logger_called_on_execute():
    backend = _delete_backend()
    logged = []
    recs = [dc.make_record(category="x", delete_kind=dc.KIND_S3_PREFIX, s3_path="sawimages/Junk Book", reason="r")]
    dc.execute_deletions(recs, backend, dry_run=False, logger=logged.append)
    assert any("DELETED" in line for line in logged)
