"""Missing-page recovery against a shared, failure-injectable Firestore fake.

Only enter-text's definitions execute here: importing the Streamlit page body
would initialize external services. Persistence, recovery and navigation tests
exercise the real functions, with source checks limited to page-level wiring.
"""

import ast
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from google.api_core.exceptions import AlreadyExists, ServiceUnavailable

from data_structures import Page
from data_structures.base_structure import Field


ENTER_TEXT_PATH = Path(__file__).resolve().parents[1] / "pages" / "enter_text.py"
_ENTER_TEXT_PAGE_MARKER = 'page_layout(current_page="./pages/enter_text.py")'


class _AttrDict(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


class _FakeBookRef:
    def __init__(self, doc_id):
        self.id = doc_id
        self.path = f"books/{doc_id}"


class _FakeSnapshot:
    def __init__(self, reference, data):
        self.reference = reference
        self.id = reference.id
        self.exists = data is not None
        self._data = None if data is None else dict(data)

    def to_dict(self):
        return None if self._data is None else dict(self._data)


class _FakeDocRef:
    def __init__(self, collection, doc_id, firestore):
        self.id = doc_id
        self.path = f"{collection}/{doc_id}"
        self._collection = collection
        self._firestore = firestore

    def set(self, data, merge=True):
        self._firestore.writes.append(("set", self.path, dict(data)))
        self._firestore._raise_failure("set")
        collection = self._firestore.store.setdefault(self._collection, {})
        if merge:
            collection.setdefault(self.id, {}).update(data)
        else:
            collection[self.id] = dict(data)
        self._firestore._raise_failure("set", after=True)

    def create(self, data):
        self._firestore.writes.append(("create", self.path, dict(data)))
        self._firestore._raise_failure("create")
        collection = self._firestore.store.setdefault(self._collection, {})
        if self.id in collection:
            raise AlreadyExists(f"Document already exists: {self.path}")
        collection[self.id] = dict(data)
        self._firestore._raise_failure("create", after=True)

    def update(self, data):
        self._firestore.writes.append(("update", self.path, dict(data)))
        for field in data:
            self._firestore._raise_failure("update", field=field)
        self._firestore.store[self._collection][self.id].update(data)
        for field in data:
            self._firestore._raise_failure("update", field=field, after=True)

    def get(self):
        self._firestore.reads.append(self.path)
        self._firestore._raise_failure("get")
        data = self._firestore.store.get(self._collection, {}).get(self.id)
        return _FakeSnapshot(self, data)


class _FakeCollection:
    def __init__(self, name, firestore):
        self._name = name
        self._firestore = firestore

    def document(self, doc_id):
        return _FakeDocRef(self._name, doc_id, self._firestore)


class _FakeDb:
    def __init__(self, firestore):
        self._firestore = firestore

    def collection(self, name):
        return _FakeCollection(name, self._firestore)


class _FakeFirestore:
    def __init__(self):
        self.store = {}
        self.writes = []  # Attempts, including rejected or unacknowledged writes.
        self.reads = []
        self.update_field_calls = []
        self._failures = []

    def fail_next(self, operation, exception=None, *, field=None, after=False):
        self._failures.append((
            operation, field, after,
            exception if exception is not None else ServiceUnavailable("Injected failure"),
        ))

    def _raise_failure(self, operation, *, field=None, after=False):
        for index, (op, target_field, fail_after, exception) in enumerate(self._failures):
            if op == operation and target_field in (None, field) and fail_after == after:
                self._failures.pop(index)
                raise exception

    def connect_book(self):
        return _FakeDb(self)

    def username_to_doc_ref(self, username):
        return _FakeDocRef("users", username, self)

    def update_field(self, collection, document, field, value):
        self.update_field_calls.append((collection, document, field, value))
        self.connect_book().collection(collection).document(document).update({field: value})

    def get_all_by_ids(self, collection, doc_ids):
        return {
            doc_id: self.connect_book().collection(collection).document(doc_id).get()
            for doc_id in doc_ids
        }


@pytest.fixture
def session(monkeypatch):
    state = _AttrDict(firestore=_FakeFirestore(), username="alice")
    monkeypatch.setattr(st, "session_state", state)
    monkeypatch.setattr(st, "error", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "spinner", lambda *args, **kwargs: nullcontext())
    return state


def _page(number=3):
    return Page(page_number=number, book=_FakeBookRef("the_gruffalo"))


def _load_enter_text_defs(monkeypatch):
    monkeypatch.setattr("utilities.check_authentication_status", lambda: None)
    prefix = ENTER_TEXT_PATH.read_text().partition(_ENTER_TEXT_PAGE_MARKER)[0]
    namespace = {"__name__": "pages._missing_page_defs_test", "__file__": str(ENTER_TEXT_PATH)}
    exec(compile(prefix, str(ENTER_TEXT_PATH), "exec"), namespace)
    return namespace


def _enter_session(session, page=None):
    page = _page() if page is None else page
    session.current_book = SimpleNamespace(
        document_id="the_gruffalo", title="The Gruffalo", page_count=5,
        get_ref=lambda: page.book,
    )
    session.current_page_number = page.page_number
    session.current_page = page
    session.book_pages_dict = {page.page_number: page}
    session.now_entering = "text"
    session[f"enter_text_page_text_{page.page_number}"] = page.text
    session[f"enter_text_contains_story_{page.page_number}"] = page.contains_story
    return page


def _recovery(flow="enter_text", book_id="the_gruffalo", page_number=3):
    from page_save_recovery import PageSaveRecovery

    return PageSaveRecovery(flow, book_id, page_number)


class _Element:
    """Record widget render order and use the same persisted values as Streamlit."""

    def __init__(self, state):
        self.state = state
        self.widgets = []
        self.messages = []

    def checkbox(self, label, *, value, key, **kwargs):
        value = self.state.get(key, value)
        self.widgets.append(("checkbox", key, value))
        return value

    def text_area(self, label, *, value, key, **kwargs):
        value = self.state.get(key, value)
        self.widgets.append(("text_area", key, value))
        return value

    def button(self, label, *, key, **kwargs):
        self.widgets.append(("button", key, False))
        return False

    def columns(self, count):
        return [self] * count

    def expander(self, label):
        return nullcontext()

    def _message(self, *args, **kwargs):
        self.messages.append(args)

    warning = caption = code = success = info = _message


def _consume_enter_text_refresh(enter_text):
    """Execute the actual pre-widget refresh statements from the page body."""
    tree = ast.parse(ENTER_TEXT_PATH.read_text())
    start = next(
        index for index, node in enumerate(tree.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_refreshed_page" for target in node.targets)
    )
    statements = tree.body[start:start + 2]
    assert isinstance(statements[1], ast.If)
    render_index = next(
        index for index, node in enumerate(tree.body)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(part, ast.Call) and isinstance(part.func, ast.Name)
            and part.func.id == "_entry_column_fragment"
            for part in ast.walk(node)
        )
    )
    assert start < render_index, "Refresh must precede the text/story widget render"
    exec(compile(ast.Module(body=statements, type_ignores=[]), str(ENTER_TEXT_PATH), "exec"), enter_text)


def _colliding_recovery(session):
    first, second = _page(), _page()
    first.save_edits({"text": "Already saved text", "contains_story": True})
    recovery = _recovery()
    assert recovery.save(
        second, {"text": "Local draft", "contains_story": False}, source="text"
    ) is False
    assert recovery.state["status"] == "collision"
    return recovery, second


# The fake itself must model merge and create accurately; an overwrite-only
# fake previously concealed which operation the real application performed.
def test_fake_merge_preserves_unspecified_fields_and_create_is_atomic(session):
    ref = _page().get_ref()
    ref.set({"text": "saved", "provenance": "alice"})
    ref.set({"contains_story": True}, merge=True)
    assert ref.get().to_dict() == {
        "text": "saved", "provenance": "alice", "contains_story": True,
    }
    with pytest.raises(AlreadyExists):
        ref.create({"text": "overwrite"})
    assert ref.get().to_dict()["text"] == "saved"
    ref.set({"text": "replacement"}, merge=False)
    assert ref.get().to_dict() == {"text": "replacement"}
    missing = _page(4).get_ref().get()
    assert missing.exists is False
    assert missing.to_dict() is None


def test_missing_page_placeholder_has_document_id_and_is_unregistered(session):
    page = _page()
    assert page.document_id == "the_gruffalo_3"
    assert page.is_registered is False
    assert page.text == ""
    assert session.firestore.writes == []


def test_old_bare_page_none_has_no_usable_document_id():
    page = Page(None)
    assert page.is_registered is False
    with pytest.raises(AttributeError):
        _ = page.document_id


def test_creation_acknowledges_registration_and_stores_provenance(session):
    page = _page()
    page.save_edits({"text": "Once upon a time."})

    stored = session.firestore.store["pages"][page.document_id]
    assert page.is_registered is True
    assert stored["is_registered"] is True
    assert stored["text"] == "Once upon a time."
    assert stored["page_number"] == 3
    assert stored["entered_by"].id == "alice"
    assert isinstance(stored["datetime_created"], datetime)
    assert page.entered_by is stored["entered_by"]
    assert page.datetime_created == stored["datetime_created"]
    assert [op for op, _, _ in session.firestore.writes] == ["create"]
    assert page.pending_edits == {}
    assert "pending_edits" not in stored


def test_two_placeholders_collision_preserves_complete_saved_document(session):
    first, second = _page(), _page()
    first.corrected = True
    first.rotation_uncertain = True
    first.save_edits({"text": "Alice's transcription"})
    original = dict(session.firestore.store["pages"][first.document_id])
    session.username = "bob"

    with pytest.raises(AlreadyExists):
        second.save_edits({"contains_story": True})

    assert session.firestore.store["pages"][first.document_id] == original
    assert original["entered_by"].id == "alice"
    assert second.is_registered is False
    assert second.entered_by is None
    assert second.datetime_created == -1
    assert second.pending_edits == {"contains_story": True}
    assert [op for op, _, _ in session.firestore.writes] == ["create", "create"]
    assert session.firestore.update_field_calls == []


def test_failed_creation_retains_state_and_identical_retry_attempts_create(session):
    page, other = _page(), _page(4)
    session.firestore.fail_next("create")
    with pytest.raises(ServiceUnavailable):
        page.save_edits({"text": "Keep this draft", "contains_story": True})

    assert page.is_registered is False
    assert page.entered_by is None
    assert page.datetime_created == -1
    assert page.text == "Keep this draft"
    assert page.contains_story is True
    assert page.pending_edits == {"text": "Keep this draft", "contains_story": True}
    assert other.pending_edits == {}
    assert "pending_edits" not in page.to_dict()
    with pytest.raises(ValueError):
        page.save_edits({"text": "Replacement must be refused"})

    page.retry_pending_edits()

    assert page.is_registered is True
    assert page.pending_edits == {}
    assert session.firestore.store["pages"][page.document_id]["text"] == "Keep this draft"
    assert [op for op, _, _ in session.firestore.writes] == ["create", "create"]


@pytest.mark.parametrize("changes", [{"corrected": True}, {"text": "draft", "book": None}])
def test_pending_edits_accept_only_text_and_story(session, changes):
    page = _page()
    with pytest.raises(ValueError):
        page.save_edits(changes)
    assert page.pending_edits == {}
    assert page.text == ""
    assert session.firestore.writes == []


def test_untouched_placeholder_and_unchanged_autosave_create_nothing(session, monkeypatch):
    enter_text = _load_enter_text_defs(monkeypatch)
    page = _enter_session(session)
    assert enter_text["_save_current_page_text"]() is True
    assert page.is_registered is False
    assert session.firestore.store == {}
    assert session.firestore.writes == []


def test_registered_edits_keep_write_through_path(session):
    page = _page()
    page.register()
    session.firestore.writes.clear()
    page.save_edits({"text": "Edited text."})
    assert ("pages", page.document_id, "text", "Edited text.") in session.firestore.update_field_calls
    assert [op for op, _, _ in session.firestore.writes] == ["update", "update"]
    assert page.pending_edits == {}


@pytest.mark.parametrize("failed_field", ["text", "last_updated"])
def test_registered_retry_bypasses_equal_value_shortcut(session, monkeypatch, failed_field):
    page = _page()
    page.register()
    original_set = Field.__set__

    def suppress_equal(self, instance, value):
        if self._name in instance.__dict__ and instance.__dict__[self._name] == value:
            return
        original_set(self, instance, value)

    monkeypatch.setattr(Field, "__set__", suppress_equal)
    session.firestore.fail_next("update", field=failed_field)
    with pytest.raises(ServiceUnavailable):
        page.save_edits({"text": "Same in memory", "contains_story": True})
    assert page.text == "Same in memory"
    assert page.pending_edits == {"text": "Same in memory", "contains_story": True}
    session.firestore.update_field_calls.clear()

    page.retry_pending_edits()

    assert ("pages", page.document_id, "text", "Same in memory") in session.firestore.update_field_calls
    assert ("pages", page.document_id, "contains_story", True) in session.firestore.update_field_calls
    assert page.pending_edits == {}
    stored = session.firestore.store["pages"][page.document_id]
    assert stored["text"] == "Same in memory"
    assert stored["contains_story"] is True


def test_registered_retry_keeps_only_unacknowledged_fields(session):
    page = _page()
    page.register()
    session.firestore.fail_next("update", field="contains_story")
    with pytest.raises(ServiceUnavailable):
        page.save_edits({"text": "Saved first", "contains_story": True})
    assert page.pending_edits == {"contains_story": True}
    session.firestore.update_field_calls.clear()
    page.retry_pending_edits()
    assert [field for _, _, field, _ in session.firestore.update_field_calls] == [
        "contains_story", "last_updated",
    ]


def test_ordinary_reprocessing_registration_retains_merge_semantics(session):
    first = _page()
    first.register()
    session.firestore.store["pages"][first.document_id]["external_metadata"] = "preserve"
    replacement = _page()
    replacement.text = "Reprocessed text"
    replacement.register()
    stored = session.firestore.store["pages"][replacement.document_id]
    assert stored["text"] == "Reprocessed text"
    assert stored["external_metadata"] == "preserve"
    assert stored["is_registered"] is True
    assert [op for op, _, _ in session.firestore.writes] == ["set", "set"]


def test_failed_ordinary_registration_does_not_acknowledge(session):
    page = _page()
    session.firestore.fail_next("set")
    with pytest.raises(ServiceUnavailable):
        page.register()
    assert page.is_registered is False
    assert page.entered_by is None
    assert page.datetime_created == -1


# Shared recovery: retained operations, conflict reloads and widget refreshes.
def test_controller_keeps_failed_operation_across_reruns_and_rejects_replacement(session):
    page = _page()
    session.firestore.fail_next("create")
    first = _recovery()
    assert first.save(page, {"text": "Retained text"}, source="text") is False
    second = _recovery()
    assert second.blocked is True
    assert second.state["page"] is page
    assert second.state["status"] == "failed"
    assert second.state["draft"]["text"] == "Retained text"
    assert second.save(_page(), {"text": "Must not replace"}, source="text") is False
    assert second.state["page"] is page
    assert len(session.firestore.writes) == 1
    assert second.retry() is True
    assert second.blocked is True
    assert second.consume_refresh(text_key="text", story_key="story") is page
    assert second.blocked is False
    assert session.firestore.store["pages"][page.document_id]["text"] == "Retained text"


def test_controller_scopes_pending_operations_by_flow_book_and_page(session):
    session.firestore.fail_next("create")
    recovery = _recovery()
    assert recovery.save(_page(), {"text": "Draft"}, source="text") is False
    assert _recovery(flow="validation").blocked is False
    assert _recovery(book_id="another_book").blocked is False
    assert _recovery(page_number=4).blocked is False


def test_lost_create_acknowledgement_is_conservatively_a_collision(session):
    page = _page()
    session.firestore.fail_next("create", after=True)
    recovery = _recovery()
    assert recovery.save(page, {"text": "Committed but unacknowledged"}, source="text") is False
    original = dict(session.firestore.store["pages"][page.document_id])
    assert recovery.state["status"] == "failed"
    assert recovery.retry() is False
    assert recovery.state["status"] == "collision"
    assert session.firestore.store["pages"][page.document_id] == original
    assert [op for op, _, _ in session.firestore.writes] == ["create", "create"]


@pytest.mark.parametrize("collision", [False, True])
def test_recovery_renders_only_the_appropriate_stable_action(session, collision):
    if collision:
        recovery, _ = _colliding_recovery(session)
        writes_before = list(session.firestore.writes)
        assert recovery.retry() is False
        assert session.firestore.writes == writes_before
        expected_key = "enter_text_page_load_saved_the_gruffalo_3"
    else:
        session.firestore.fail_next("create")
        recovery = _recovery()
        assert recovery.save(_page(), {"text": "Retain me"}, source="text") is False
        expected_key = "enter_text_page_retry_save_the_gruffalo_3"
    element = _Element(session)
    assert recovery.render(element) is True
    assert element.widgets == [("button", expected_key, False)]
    assert (recovery.state["draft"]["text"],) in element.messages


@pytest.mark.parametrize("missing", [False, True])
def test_failed_or_missing_reload_preserves_pending_page_and_draft(session, missing):
    recovery, page = _colliding_recovery(session)
    draft = dict(recovery.state["draft"])
    if missing:
        session.firestore.store["pages"].pop(page.document_id)
    else:
        session.firestore.fail_next("get")
    assert recovery.load_saved() is None
    assert recovery.blocked is True
    assert recovery.state["page"] is page
    assert recovery.state["draft"] == draft
    assert recovery.state["status"] == "collision"


def test_collision_reload_stages_both_widgets_and_blocks_stale_autosave(session, monkeypatch):
    enter_text = _load_enter_text_defs(monkeypatch)
    recovery, pending_page = _colliding_recovery(session)
    _enter_session(session, pending_page)
    session["enter_text_page_text_3"] = ""
    session["enter_text_contains_story_3"] = False
    local_draft = dict(recovery.state["draft"])
    session.firestore.writes.clear()

    loaded = recovery.load_saved()
    assert loaded.text == "Already saved text"
    assert loaded.contains_story is True
    assert recovery.blocked is True
    assert session["enter_text_page_text_3"] == ""
    assert enter_text["_save_current_page_text"]() is False
    assert session.firestore.writes == []
    _consume_enter_text_refresh(enter_text)
    assert session.current_page is loaded
    assert session.book_pages_dict[3] is loaded
    assert session["enter_text_page_text_3"] == "Already saved text"
    assert session["enter_text_contains_story_3"] is True
    element = _Element(session)
    enter_text["text_entry"](element, image_height=400)
    assert ("checkbox", "enter_text_contains_story_3", True) in element.widgets
    assert ("text_area", "enter_text_page_text_3", "Already saved text") in element.widgets
    assert recovery.blocked is False
    assert enter_text["_save_current_page_text"]() is True
    assert session.firestore.writes == []
    assert "_reextract_result" not in session
    assert session["_page_retained_drafts"][recovery.scope] == [local_draft]
    assert (local_draft["text"],) in element.messages


def test_recovery_does_not_catch_programming_errors(session, monkeypatch):
    page = _page()
    def invalid_save(changes):
        raise TypeError("Programming error")
    monkeypatch.setattr(page, "save_edits", invalid_save)
    with pytest.raises(TypeError, match="Programming error"):
        _recovery().save(page, {"text": "Draft"}, source="text")


# Execute real enter-text functions without constructing the Streamlit page.
def test_create_page_dict_builds_valid_write_free_placeholders(session, monkeypatch):
    enter_text = _load_enter_text_defs(monkeypatch)
    _enter_session(session)
    enter_text["create_page_dict_from_db"]()
    assert set(session.book_pages_dict) == {1, 2, 3, 4, 5}
    for number, page in session.book_pages_dict.items():
        assert page.document_id == f"the_gruffalo_{number}"
        assert page.is_registered is False
    assert session.firestore.writes == []


def test_autosave_checks_pending_before_equal_value_dirty_check(session, monkeypatch):
    enter_text = _load_enter_text_defs(monkeypatch)
    page = _enter_session(session)
    session["enter_text_page_text_3"] = "Draft already in memory"
    session.firestore.fail_next("create")
    assert enter_text["_save_current_page_text"]() is False
    assert page.text == session["enter_text_page_text_3"]
    assert enter_text["_save_current_page_text"]() is False
    assert len(session.firestore.writes) == 1
    assert _recovery().retry() is True
    assert enter_text["_save_current_page_text"]() is False
    _consume_enter_text_refresh(enter_text)
    assert enter_text["_save_current_page_text"]() is True
    assert len(session.firestore.writes) == 2


def test_story_toggle_retains_live_text_with_pending_operation(session, monkeypatch):
    enter_text = _load_enter_text_defs(monkeypatch)
    page = _enter_session(session)
    session["enter_text_page_text_3"] = "Text typed alongside the checkbox"
    session["enter_text_contains_story_3"] = True
    session.firestore.fail_next("create")
    element = _Element(session)
    enter_text["text_entry"](element, image_height=400)
    assert page.pending_edits == {
        "text": "Text typed alongside the checkbox", "contains_story": True,
    }
    assert _recovery().state["draft"] == {
        "text": "Text typed alongside the checkbox", "contains_story": True,
    }
    assert ("checkbox", "enter_text_contains_story_3", True) in element.widgets
    assert not any(widget[0] == "text_area" for widget in element.widgets)
    element.widgets.clear()
    enter_text["text_entry"](element, image_height=400)
    assert not any(widget[0] in {"checkbox", "text_area"} for widget in element.widgets)
    assert len(session.firestore.writes) == 1


@pytest.mark.parametrize("mode", ["text", "character", "alias", "manage", "detect"])
def test_recovery_blocks_every_entry_mode_before_editor_dispatch(session, monkeypatch, mode):
    enter_text = _load_enter_text_defs(monkeypatch)
    _enter_session(session)
    session.now_entering = mode
    session["enter_text_page_text_3"] = "Recover before opening any editor"
    session.firestore.fail_next("create")
    assert enter_text["_save_current_page_text"]() is False
    dispatched = []
    for editor in ("text_entry", "character_entry", "alias_entry", "manage_characters_entry", "detect_entry"):
        monkeypatch.setitem(
            enter_text, editor,
            lambda *args, editor=editor, **kwargs: dispatched.append(editor),
        )
    element = _Element(session)

    enter_text["user_entry_box"](element, image_height=400)

    assert dispatched == []
    assert session.now_entering == mode
    assert element.widgets == [("button", "enter_text_page_retry_save_the_gruffalo_3", False)]
    assert ("Recover before opening any editor",) in element.messages
    assert len(session.firestore.writes) == 1


def test_failed_ocr_save_retries_both_values_without_second_anthropic_call(session, monkeypatch):
    enter_text = _load_enter_text_defs(monkeypatch)
    page = _enter_session(session)
    calls = []
    monkeypatch.setitem(enter_text, "get_anthropic_client", lambda: object())
    monkeypatch.setitem(enter_text, "_load_page_bytes_for_extraction", lambda *args: b"jpeg")
    def extract(*args, **kwargs):
        calls.append((args, kwargs))
        return "Extracted text", True, "story"
    monkeypatch.setitem(enter_text, "extract_page_info", extract)
    monkeypatch.setattr(st, "rerun", lambda: None)
    session.firestore.fail_next("create")

    enter_text["reextract_current_page"](3)

    assert len(calls) == 1
    assert page.pending_edits == {"text": "Extracted text", "contains_story": True}
    recovery = _recovery()
    assert recovery.state["source"] == "reextract"
    assert recovery.state["draft"] == {"text": "Extracted text", "contains_story": True}
    assert "_reextract_refresh_page" not in session
    assert recovery.retry(on_saved=enter_text["_page_save_succeeded"]) is True
    assert len(calls) == 1
    assert session["_reextract_refresh_page"] == 3
    assert session["_reextract_text"] == "Extracted text"
    assert session["_reextract_contains_story"] is True
    stored = session.firestore.store["pages"][page.document_id]
    assert stored["text"] == "Extracted text"
    assert stored["contains_story"] is True


@pytest.mark.parametrize("function,args", [
    ("page_change", (1,)), ("adding_character", ()), ("adding_alias", ()),
    ("managing_characters", ()), ("rerun_detect", ()),
    ("reextract_current_page", (3,)),
])
def test_failed_save_prevents_navigation_modes_and_extraction(session, monkeypatch, function, args):
    enter_text = _load_enter_text_defs(monkeypatch)
    _enter_session(session)
    session["enter_text_page_text_3"] = "Unsaved draft"
    session["character_form_marker"] = "preserve"
    session["alias_form_marker"] = "preserve"
    session.firestore.fail_next("create")
    side_effects = []
    monkeypatch.setitem(enter_text, "get_anthropic_client", lambda: side_effects.append("client"))
    monkeypatch.setitem(enter_text, "stage_character_redetect", lambda *a, **k: side_effects.append("detect"))

    enter_text[function](*args)

    assert session.current_page_number == 3
    assert session.now_entering == "text"
    assert session["character_form_marker"] == "preserve"
    assert session["alias_form_marker"] == "preserve"
    assert side_effects == []
    assert _recovery().blocked is True


@pytest.mark.parametrize("button_name", ["return_button", "save_button"])
def test_failed_save_prevents_menu_navigation_and_submit(session, monkeypatch, button_name):
    enter_text = _load_enter_text_defs(monkeypatch)
    _enter_session(session)
    session["enter_text_page_text_3"] = "Unsaved draft"
    session.firestore.fail_next("create")
    effects = []
    monkeypatch.setattr(st, "switch_page", lambda path: effects.append(path))
    monkeypatch.setitem(enter_text, "confirm_submit", lambda: effects.append("submit"))
    enter_text.update(return_button=button_name == "return_button", save_button=button_name == "save_button")
    handlers = [
        node for node in ast.parse(ENTER_TEXT_PATH.read_text()).body
        if isinstance(node, ast.If)
        and any(isinstance(part, ast.Name) and part.id == button_name for part in ast.walk(node.test))
    ]
    assert len(handlers) == 1
    exec(compile(ast.Module(body=handlers, type_ignores=[]), str(ENTER_TEXT_PATH), "exec"), enter_text)
    assert effects == []
    assert _recovery().blocked is True


def test_story_checkbox_uses_shared_edit_helper():
    tree = ast.parse(ENTER_TEXT_PATH.read_text())
    text_entry = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "text_entry")
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_save_page_edits"
        for node in ast.walk(text_entry)
    )
    assert "EnterText.page_no_record_notice" in ENTER_TEXT_PATH.read_text()


def test_display_paths_guard_missing_image():
    source = ENTER_TEXT_PATH.read_text()
    assert source.count("except FileNotFoundError") >= 4
    assert source.count("EnterText.page_image_missing") >= 3


def test_missing_image_strings_exist():
    from text_content import EnterText

    assert isinstance(EnterText.page_image_missing, str) and EnterText.page_image_missing
    assert isinstance(EnterText.page_no_record_notice, str) and EnterText.page_no_record_notice
