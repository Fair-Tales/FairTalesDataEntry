"""Tests for #148 — transactional page reordering (``page_reorder``).

The module is Streamlit-free by design; these tests drive it with in-memory
S3/Firestore fakes and cover: the move→permutation maths, the two-phase
S3 migration (no collisions, derived variants follow, stale variants removed),
doc-content permutation (text follows its photo), the atomic sentinel, abort
paths that leave everything untouched, and crash-recovery in both the
committed and uncommitted windows.
"""

import json

import pytest

from page_reorder import (
    REORDER_COLLECTION,
    ReorderError,
    execute_reorder,
    move_page_permutation,
    read_pending_manifest,
    resume_pending_reorder,
    validate_permutation,
)


# ---------------------------------------------------------------------------
# move_page_permutation.
# ---------------------------------------------------------------------------

def _apply(perm, order):
    """Apply an {old_pos: new_pos} permutation to a list of page labels indexed
    by position (1-based) and return the new order."""
    result = list(order)
    for old, new in perm.items():
        result[new - 1] = order[old - 1]
    return result


def test_move_later_page_earlier():
    # Book pages labelled by content; page 5 was a forgotten page appended last.
    order = ["a", "b", "c", "d", "FORGOTTEN"]
    perm = move_page_permutation(5, from_page=5, to_page=2)
    assert perm == {5: 2, 2: 3, 3: 4, 4: 5}
    assert _apply(perm, order) == ["a", "FORGOTTEN", "b", "c", "d"]


def test_move_earlier_page_later():
    order = ["a", "b", "c", "d"]
    perm = move_page_permutation(4, from_page=1, to_page=3)
    assert perm == {1: 3, 2: 1, 3: 2}
    assert _apply(perm, order) == ["b", "c", "a", "d"]


def test_move_noop_and_validation():
    assert move_page_permutation(4, 2, 2) == {}
    with pytest.raises(ReorderError):
        move_page_permutation(4, 0, 2)
    with pytest.raises(ReorderError):
        move_page_permutation(4, 2, 5)


def test_validate_permutation_rejects_non_bijection():
    with pytest.raises(ReorderError):
        validate_permutation({1: 2, 3: 2}, 4)
    with pytest.raises(ReorderError):
        validate_permutation({1: 9, 9: 1}, 4)
    validate_permutation({1: 2, 2: 1}, 4)  # fine


# ---------------------------------------------------------------------------
# Fakes.
# ---------------------------------------------------------------------------

class FakeFs:
    """Dict-backed 'S3': path -> bytes. Directory semantics for exists()/rm()."""

    def __init__(self, files=None):
        self.files = dict(files or {})

    def exists(self, path):
        if path in self.files:
            return True
        prefix = path.rstrip("/") + "/"
        return any(p.startswith(prefix) for p in self.files)

    def copy(self, src, dst):
        self.files[dst] = self.files[src]

    def open(self, path, mode="rb"):
        import io

        if "w" in mode:
            fake = self

            class _W(io.BytesIO):
                def __exit__(self, *exc):
                    fake.files[path] = self.getvalue()
                    return False

            return _W()
        if path not in self.files:
            raise FileNotFoundError(path)
        import io as _io
        buf = _io.BytesIO(self.files[path])
        return buf

    def rm(self, path, recursive=False):
        if path in self.files:
            del self.files[path]
            return
        prefix = path.rstrip("/") + "/"
        matches = [p for p in self.files if p.startswith(prefix)]
        if not matches:
            raise FileNotFoundError(path)
        for p in matches:
            del self.files[p]


class FakeSnapshot:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class FakeDocRef:
    def __init__(self, store, collection, doc_id):
        self._store = store
        self._key = (collection, doc_id)

    def get(self):
        return FakeSnapshot(self._store.get(self._key))

    def set(self, data):
        self._store[self._key] = data


class FakeCollection:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def document(self, doc_id):
        return FakeDocRef(self._store, self._name, doc_id)


class FakeBatch:
    """Atomic batch: buffers writes; commit applies all at once (or none, when
    told to fail — modelling the crash-before-commit window)."""

    def __init__(self, store, fail=False):
        self._store = store
        self._ops = []
        self._fail = fail

    def set(self, ref, data):
        self._ops.append((ref, data))

    def commit(self):
        if self._fail:
            raise RuntimeError("simulated commit failure")
        for ref, data in self._ops:
            ref.set(data)


class FakeDb:
    def __init__(self, docs=None):
        self.store = dict(docs or {})
        self.fail_commit = False

    def collection(self, name):
        return FakeCollection(self.store, name)

    def batch(self):
        return FakeBatch(self.store, fail=self.fail_commit)


FOLDER = "sawimages/Test Book"
BOOK = "test_book"


def _book_fixture():
    """4-page book: page 2 has a _cropped variant, others don't; page 4 is the
    forgotten page (appended last) with text already entered on 1-3."""
    files = {}
    for n in (1, 2, 3, 4):
        files[f"{FOLDER}/page_{n}.jpg"] = f"raw-{n}".encode()
        files[f"{FOLDER}/page_{n}_display.jpg"] = f"disp-{n}".encode()
    files[f"{FOLDER}/page_2_cropped.jpg"] = b"crop-2"
    docs = {
        ("pages", f"{BOOK}_{n}"): {
            "page_number": n,
            "text": f"text-{n}",
            "book": f"ref-to-{BOOK}",
        }
        for n in (1, 2, 3, 4)
    }
    return FakeFs(files), FakeDb(docs)


# ---------------------------------------------------------------------------
# execute_reorder happy path.
# ---------------------------------------------------------------------------

def test_reorder_moves_files_docs_and_text_together():
    fs, db = _book_fixture()
    perm = move_page_permutation(4, from_page=4, to_page=2)  # 4->2, 2->3, 3->4

    execute_reorder(fs, db, BOOK, FOLDER, perm, 4, edited_by="alice")

    # Images permuted: new page 2 is old raw-4, etc. Page 1 untouched.
    assert fs.files[f"{FOLDER}/page_1.jpg"] == b"raw-1"
    assert fs.files[f"{FOLDER}/page_2.jpg"] == b"raw-4"
    assert fs.files[f"{FOLDER}/page_3.jpg"] == b"raw-2"
    assert fs.files[f"{FOLDER}/page_4.jpg"] == b"raw-3"
    # Display derivatives follow their page.
    assert fs.files[f"{FOLDER}/page_2_display.jpg"] == b"disp-4"
    assert fs.files[f"{FOLDER}/page_3_display.jpg"] == b"disp-2"
    # Old page 2's _cropped follows it to position 3; and since incoming page 4
    # had NO _cropped, the stale one must NOT remain at position 2.
    assert fs.files[f"{FOLDER}/page_3_cropped.jpg"] == b"crop-2"
    assert f"{FOLDER}/page_2_cropped.jpg" not in fs.files
    # Staging cleaned up.
    assert not fs.exists(f"{FOLDER}/_reorder_tmp")

    # Docs permuted WITH their text (text follows the photo), page_number fixed.
    assert db.store[("pages", f"{BOOK}_2")]["text"] == "text-4"
    assert db.store[("pages", f"{BOOK}_2")]["page_number"] == 2
    assert db.store[("pages", f"{BOOK}_3")]["text"] == "text-2"
    assert db.store[("pages", f"{BOOK}_4")]["text"] == "text-3"
    assert db.store[("pages", f"{BOOK}_1")]["text"] == "text-1"
    # book ref untouched on moved docs.
    assert db.store[("pages", f"{BOOK}_2")]["book"] == f"ref-to-{BOOK}"

    # Sentinel recorded (audit + recovery marker).
    sentinel = db.store[(REORDER_COLLECTION, BOOK)]
    assert sentinel["edited_by"] == "alice"
    assert sentinel["permutation"] == {"4": 2, "2": 3, "3": 4}


def test_noop_permutation_changes_nothing():
    fs, db = _book_fixture()
    before_files = dict(fs.files)
    before_docs = dict(db.store)
    execute_reorder(fs, db, BOOK, FOLDER, {}, 4)
    assert fs.files == before_files
    assert db.store == before_docs


# ---------------------------------------------------------------------------
# Abort paths: nothing may change.
# ---------------------------------------------------------------------------

def test_missing_page_doc_aborts_before_any_write():
    fs, db = _book_fixture()
    del db.store[("pages", f"{BOOK}_3")]
    before_files = dict(fs.files)
    before_docs = dict(db.store)
    with pytest.raises(ReorderError):
        execute_reorder(fs, db, BOOK, FOLDER, move_page_permutation(4, 4, 2), 4)
    assert fs.files == before_files
    assert db.store == before_docs


def test_missing_page_photo_aborts_and_cleans_staging():
    fs, db = _book_fixture()
    del fs.files[f"{FOLDER}/page_3.jpg"]
    before_docs = dict(db.store)
    with pytest.raises(ReorderError):
        execute_reorder(fs, db, BOOK, FOLDER, move_page_permutation(4, 4, 2), 4)
    assert db.store == before_docs
    assert not fs.exists(f"{FOLDER}/_reorder_tmp")
    # Finals untouched.
    assert fs.files[f"{FOLDER}/page_2.jpg"] == b"raw-2"


# ---------------------------------------------------------------------------
# Crash recovery.
# ---------------------------------------------------------------------------

def test_crash_before_commit_is_discarded_untouched():
    """Staging + manifest written but the atomic batch never committed: the
    resume path must discard the staging and change nothing."""
    fs, db = _book_fixture()
    db.fail_commit = True
    before_docs = dict(db.store)
    with pytest.raises(RuntimeError):
        execute_reorder(fs, db, BOOK, FOLDER, move_page_permutation(4, 4, 2), 4)
    # Manifest exists (phase A completed) but no sentinel.
    assert read_pending_manifest(fs, FOLDER) is not None

    db.fail_commit = False
    outcome = resume_pending_reorder(fs, db, BOOK, FOLDER)
    assert outcome == "discarded"
    assert db.store == before_docs
    assert fs.files[f"{FOLDER}/page_2.jpg"] == b"raw-2"
    assert not fs.exists(f"{FOLDER}/_reorder_tmp")


def test_crash_after_commit_is_finished_by_resume():
    """Docs committed but the process died before the final file copies: the
    resume path must complete the file moves from staging."""
    fs, db = _book_fixture()
    perm = move_page_permutation(4, from_page=4, to_page=2)

    # Simulate the crash by stopping _finish_from_staging from running.
    import page_reorder as pr
    original = pr._finish_from_staging
    try:
        pr._finish_from_staging = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("simulated crash after commit")
        )
        with pytest.raises(RuntimeError):
            execute_reorder(fs, db, BOOK, FOLDER, perm, 4)
    finally:
        pr._finish_from_staging = original

    # Docs are already permuted (atomic commit), files are not yet.
    assert db.store[("pages", f"{BOOK}_2")]["text"] == "text-4"
    assert fs.files[f"{FOLDER}/page_2.jpg"] == b"raw-2"

    outcome = resume_pending_reorder(fs, db, BOOK, FOLDER)
    assert outcome == "finished"
    assert fs.files[f"{FOLDER}/page_2.jpg"] == b"raw-4"
    assert f"{FOLDER}/page_2_cropped.jpg" not in fs.files
    assert not fs.exists(f"{FOLDER}/_reorder_tmp")


def test_pending_reorder_blocks_new_one():
    fs, db = _book_fixture()
    # Plant a pending manifest.
    fs.files[f"{FOLDER}/_reorder_tmp/manifest.json"] = json.dumps(
        {"token": "t1", "moves": {}}
    ).encode()
    with pytest.raises(ReorderError):
        execute_reorder(fs, db, BOOK, FOLDER, move_page_permutation(4, 4, 2), 4)


def test_resume_with_no_pending_reorder_is_none():
    fs, db = _book_fixture()
    assert resume_pending_reorder(fs, db, BOOK, FOLDER) is None
