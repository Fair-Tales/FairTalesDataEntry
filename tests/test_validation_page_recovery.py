"""Exercise the real validation editor and recovery controller without services."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from google.api_core.exceptions import AlreadyExists, ServiceUnavailable

from data_structures import Page
import page_save_recovery


class _State(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class _Snapshot:
    def __init__(self, data):
        self.exists = data is not None
        self._data = dict(data) if data is not None else None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _Reference:
    def __init__(self, firestore, collection, document_id):
        self.firestore = firestore
        self.collection = collection
        self.id = document_id
        self.path = f"{collection}/{document_id}"

    def create(self, data):
        fs = self.firestore
        fs.writes.append(("create", self.path, dict(data)))
        if fs.before_create is not None:
            callback, fs.before_create = fs.before_create, None
            callback()
        if fs.create_failure is not None:
            error, fs.create_failure = fs.create_failure, None
            raise error
        if self.path in fs.store:
            raise AlreadyExists("page already created")
        fs.store[self.path] = dict(data)

    def get(self):
        self.firestore.reads.append(self.path)
        return _Snapshot(self.firestore.store.get(self.path))


class _Firestore:
    def __init__(self):
        self.store = {}
        self.writes = []
        self.reads = []
        self.before_create = None
        self.create_failure = None

    def connect_book(self):
        return SimpleNamespace(collection=lambda collection: SimpleNamespace(
            document=lambda document_id: _Reference(self, collection, document_id)
        ))

    def username_to_doc_ref(self, username):
        return _Reference(self, "users", username)

    def get_by_reference(self, collection, document_ref):
        return _Reference(self, collection, document_ref).get()

    def update_field(self, collection, document, field, value):
        path = f"{collection}/{document}"
        self.writes.append(("update", path, {field: value}))
        self.store[path][field] = value


class _Rerun(Exception):
    pass


class _Streamlit:
    def __init__(self, state):
        self.session_state = state
        self.submitted = True
        self.clicked = None
        self.forms = 0
        self.widget_seeds = []
        self.notices = []

    def selectbox(self, label, *, options, key):
        return self.session_state.setdefault(key, options[0])

    def form(self, key):
        self.forms += 1
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def checkbox(self, label, *, value, key):
        self.widget_seeds.append((key, self.session_state.get(key, value)))
        return self.session_state.setdefault(key, value)

    def text_area(self, label, *, value="", key, **kwargs):
        self.widget_seeds.append((key, self.session_state.get(key, value)))
        return self.session_state.setdefault(key, value)

    def form_submit_button(self, label, *, key):
        return self.submitted

    def button(self, label, *, key, **kwargs):
        return key == self.clicked

    def expander(self, *args, **kwargs):
        return self

    def _notice(self, *args, **kwargs):
        self.notices.append(args)

    subheader = image = info = success = warning = error = caption = write = code = _notice

    def rerun(self):
        raise _Rerun()


@pytest.fixture
def editor(monkeypatch):
    # Execute only definitions/imports: the page's authentication and render
    # dispatch must not run during tests.
    path = Path(__file__).resolve().parents[1] / "pages" / "validation.py"
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if isinstance(
        node, (ast.Import, ast.ImportFrom, ast.FunctionDef)
    )]
    namespace = {"__name__": "pages._validation_defs_under_test"}
    exec(compile(tree, str(path), "exec"), namespace)

    firestore = _Firestore()
    state = _State(firestore=firestore, username="validator")
    fake_st = _Streamlit(state)
    monkeypatch.setattr(st, "session_state", state)
    monkeypatch.setattr(page_save_recovery, "st", fake_st)
    namespace["st"] = fake_st
    namespace["get_s3_filesystem"] = lambda: object()
    namespace["load_page_image"] = lambda *args, **kwargs: object()
    book_ref = _Reference(firestore, "books", "book_id")
    book = SimpleNamespace(
        document_id="book_id", title="Title absent from book lookup",
        page_count=1, get_ref=lambda: book_ref,
    )
    logs = []

    def log(*args):
        assert "pages/book_id_1" in firestore.store
        logs.append(args)

    namespace["_log"] = log
    return SimpleNamespace(
        run=lambda: namespace["page_text_editor"](book),
        run_book=namespace["page_text_editor"],
        fs=firestore, state=state, st=fake_st, book=book, logs=logs,
        recovery=page_save_recovery.PageSaveRecovery("validation", "book_id", 1),
    )


def test_missing_page_uses_book_ref_and_logs_only_after_create(editor):
    editor.state["validation_page_text_1"] = "A new transcription"
    editor.state["validation_page_contains_1"] = True

    editor.run()

    assert [kind for kind, _, _ in editor.fs.writes] == ["create"]
    saved = editor.fs.store["pages/book_id_1"]
    assert saved["book"] is editor.book.get_ref()
    assert saved["is_registered"] is True
    assert saved["text"] == "A new transcription"
    assert saved["contains_story"] is True
    assert [record[3:] for record in editor.logs] == [
        ("contains_story", False, True), ("text", "", "A new transcription")
    ]


def test_unchanged_missing_page_remains_write_free(editor):
    editor.run()

    assert editor.fs.writes == []
    assert editor.logs == []


def test_registered_page_writes_and_audits_only_changed_fields(editor):
    page = Page(page_number=1, book=editor.book.get_ref())
    page.save_edits({"text": "Original", "contains_story": True})
    editor.fs.writes.clear()
    editor.state["validation_page_text_1"] = "Corrected"

    editor.run()

    assert [list(patch) for _, _, patch in editor.fs.writes] == [
        ["text"], ["last_updated"]
    ]
    assert [record[3:] for record in editor.logs] == [
        ("text", "Original", "Corrected")
    ]


def test_pending_page_and_original_audit_survive_rerun_and_retry(editor):
    editor.state["validation_page_text_1"] = "Retained correction"
    editor.fs.create_failure = ServiceUnavailable("temporary failure")

    editor.run()
    pending_page = editor.recovery.state["page"]
    original_audit = editor.recovery.state["audit"]
    assert editor.logs == []
    assert pending_page.is_registered is False
    assert editor.fs.reads == ["pages/book_id_1"]
    assert editor.st.forms == 1

    editor.run()

    assert editor.recovery.state["page"] is pending_page
    assert editor.recovery.state["audit"] == original_audit
    assert editor.fs.reads == ["pages/book_id_1"]
    assert editor.st.forms == 1  # No replacement editor during recovery.

    editor.st.clicked = "validation_page_retry_save_book_id_1"
    with pytest.raises(_Rerun):
        editor.run()

    assert [kind for kind, _, _ in editor.fs.writes] == ["create", "create"]
    assert editor.fs.store["pages/book_id_1"]["text"] == "Retained correction"
    assert [record[3:] for record in editor.logs] == [
        ("text", "", "Retained correction")
    ]
    assert editor.recovery.state is None


def test_collision_load_refreshes_both_widgets_without_replaying_draft(editor):
    winner = Page(page_number=1, book=editor.book.get_ref()).to_dict()
    winner.update(
        text="Another session's saved text", contains_story=True,
        is_registered=True, entered_by="original author", datetime_created=123,
    )
    editor.fs.before_create = lambda: editor.fs.store.update({"pages/book_id_1": winner})
    editor.state["validation_page_text_1"] = "My refused correction"
    editor.state["validation_page_contains_1"] = False

    editor.run()

    assert editor.logs == []
    assert editor.fs.store["pages/book_id_1"] == winner
    assert editor.recovery.state["draft"] == {
        "text": "My refused correction", "contains_story": False,
    }
    assert [kind for kind, _, _ in editor.fs.writes] == ["create"]

    editor.st.clicked = "validation_page_load_saved_book_id_1"
    with pytest.raises(_Rerun):
        editor.run()

    assert editor.recovery.blocked is True
    editor.st.clicked = None
    editor.st.widget_seeds.clear()
    editor.run()

    assert ("validation_page_text_1", winner["text"]) in editor.st.widget_seeds
    assert ("validation_page_contains_1", True) in editor.st.widget_seeds
    assert editor.fs.store["pages/book_id_1"] == winner
    assert [kind for kind, _, _ in editor.fs.writes] == ["create"]
    assert editor.logs == []
    assert editor.recovery.blocked is False


def test_competing_creation_between_form_render_and_submit_is_refused(editor):
    editor.st.submitted = False
    editor.run()
    displayed_page = editor.state["_validation_page_cache"][("book_id", 1)]
    assert displayed_page.is_registered is False
    assert editor.fs.writes == []

    winner = Page(page_number=1, book=editor.book.get_ref()).to_dict()
    winner.update(
        text="Another session's transcription", contains_story=False,
        is_registered=True, entered_by="original author", datetime_created=123,
    )
    editor.fs.store["pages/book_id_1"] = dict(winner)
    editor.st.submitted = True
    editor.state["validation_page_contains_1"] = True
    editor.run()

    assert editor.fs.store["pages/book_id_1"] == winner
    assert [kind for kind, _, _ in editor.fs.writes] == ["create"]
    assert editor.recovery.state["status"] == "collision"
    assert editor.recovery.state["page"] is displayed_page
    assert editor.recovery.state["draft"] == {"text": "", "contains_story": True}
    assert editor.fs.reads == ["pages/book_id_1"]
    assert editor.logs == []


def test_switching_books_reseeds_existing_widget_keys(editor):
    editor.st.submitted = False
    editor.run()
    editor.state["validation_page_text_1"] = "The previous book's widget text"
    editor.state["validation_page_contains_1"] = True
    other_ref = _Reference(editor.fs, "books", "other_book")
    other_book = SimpleNamespace(
        document_id="other_book", title="Other book", page_count=1,
        get_ref=lambda: other_ref,
    )

    editor.run_book(other_book)

    assert editor.state["validation_page_text_1"] == ""
    assert editor.state["validation_page_contains_1"] is False
    assert editor.fs.writes == []
