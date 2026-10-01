"""Regression locks for issue #224 (title lock) and its #229 residual.

Background (#224, HIGH): a ``Book``'s ``document_id`` is derived from its title
(``title.lower().replace(" ", "_")``), and that id also keys the book's page and
character documents and its S3 photo folder. The metadata edit form
(``data_structures.book.form_content``) previously left the title editable for a
registered book, so editing it wrote a *new* title through the ``Field``
descriptor — either raising ``NotFound`` on the now-missing document mid-submit,
or silently overwriting a DIFFERENT book that happened to share the new title.

The fix mirrors the validation page (``pages/validation.py``): for a registered
book the title input is rendered ``disabled`` with an explanatory caption, and
on submit the title is never reassigned. A brand-new (unregistered) book keeps
the old behaviour (title editable + required-check).

Residual (#229): the add-flow leaves ``current_author``/``current_illustrator``/
``current_publisher`` in session state to seed the NEXT book's selectboxes. When
a user instead opens an EXISTING book to edit its metadata, those leftovers seed
another book's people into the defaults and the form saves them on submit. The
edit entry point (``pages.book_edit_home.edit_book_details``) now pops those
three keys so editing never seeds from another book's leftovers.

These exercise the real functions against in-memory fakes — no Streamlit
runtime, no Firestore/S3/secrets — mirroring the style of
``tests/test_add_books_batch_page_isolation.py``.
"""

import streamlit as st

from text_content import BookForm
import data_structures.book as book_mod


class _AttrDict(dict):
    """Minimal stand-in for Streamlit's ``session_state`` (supports both
    ``st.session_state['x']`` and ``st.session_state.x`` access)."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


class _FakeFirestore:
    def document_exists(self, collection, doc_id):
        return False


class _FakeBook:
    """Stand-in for a ``Book`` exposing only what ``form_content`` reads/writes.

    ``title`` is a property that records every write to ``title_writes`` so a
    test can assert whether the submit path wrote the title through (which, for
    a registered book, would corrupt data via the real ``Field`` descriptor)."""

    def __init__(self, *, is_registered, title):
        self._title = title
        self.title_writes = []
        self.is_registered = is_registered
        self.editing = is_registered
        self.published = -1
        self.comment = ""
        self.photos_uploaded = True
        # Selectbox targets — start as None; the fake selectboxes below return
        # non-None sentinels so the submit lands in the clean navigate branch.
        self.author = None
        self.publisher = None
        self.illustrator = None
        for theme in BookForm.theme_options:
            setattr(self, theme, False)

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        self.title_writes.append(value)
        self._title = value

    @property
    def document_id(self):
        return self._title.lower().replace(" ", "_")


class _FakeSt:
    """Records the widget calls ``form_content`` makes and returns seeded
    values, so the function runs end-to-end without a Streamlit runtime."""

    def __init__(self, session_state, *, submit, title_widget_return):
        self.session_state = session_state
        self._submit = submit
        self._title_widget_return = title_widget_return
        self.text_input_calls = []
        self.captions = []
        self.warnings = []

    def header(self, *_a, **_k):
        pass

    def write(self, *_a, **_k):
        pass

    def caption(self, text, *_a, **_k):
        self.captions.append(text)

    def warning(self, text, *_a, **_k):
        self.warnings.append(text)

    def text_input(self, label, value=None, disabled=False, key=None, help=None, **_k):
        self.text_input_calls.append(
            {"label": label, "value": value, "disabled": disabled, "key": key}
        )
        if label == BookForm.title_label:
            # Simulate a (possibly stale) widget value distinct from the seed to
            # prove the registered-book guard never writes it through.
            return self._title_widget_return
        return value if value is not None else ""

    def selectbox(self, label, options=None, index=0, **_k):
        # The published-year select is wrapped in int(); return a valid year.
        if label == BookForm.published_label:
            return 2000
        # Return a non-None sentinel for author/publisher/illustrator so all
        # three are set and the submit reaches the clean navigate branch.
        return f"sentinel::{label}"

    def multiselect(self, label, options=None, default=None, **_k):
        return list(default or [])

    def form_submit_button(self, *_a, **_k):
        return self._submit


def _wire(monkeypatch, *, is_registered, submit, title_widget_return):
    session = _AttrDict()
    session["author_dict"] = {}
    session["publisher_dict"] = {}
    session["illustrator_dict"] = {}
    session["firestore"] = _FakeFirestore()
    fake_st = _FakeSt(
        session, submit=submit, title_widget_return=title_widget_return
    )
    monkeypatch.setattr(book_mod, "st", fake_st)
    navigations = []
    monkeypatch.setattr(book_mod, "navigate_to", lambda path: navigations.append(path))
    book = _FakeBook(is_registered=is_registered, title="The Gruffalo")
    return book, fake_st, session, navigations


# ---------------------------------------------------------------------------
# Part 1 — title lock (#224)
# ---------------------------------------------------------------------------

def test_registered_book_renders_title_disabled_with_caption(monkeypatch):
    book, fake_st, _session, _nav = _wire(
        monkeypatch, is_registered=True, submit=False, title_widget_return="Tampered"
    )

    book_mod.form_content(book)

    title_call = next(
        c for c in fake_st.text_input_calls if c["label"] == BookForm.title_label
    )
    assert title_call["disabled"] is True
    # The read-only explanation is shown, sourced from text_content (not inline).
    assert BookForm.title_readonly_caption in fake_st.captions


def test_registered_book_submit_does_not_reassign_title(monkeypatch):
    # Even if the (disabled) widget returns a tampered value, the guard must
    # keep the registered book's title untouched — no write-through to Firestore.
    book, fake_st, _session, navigations = _wire(
        monkeypatch, is_registered=True, submit=True, title_widget_return="Different Title"
    )

    book_mod.form_content(book)

    assert book.title == "The Gruffalo"
    assert book.title_writes == []  # title was never assigned on submit
    # Other metadata still flows through (proves we reached the submit body).
    assert navigations == ["./pages/enter_text.py"]


def test_unregistered_book_title_editable_and_written(monkeypatch):
    book, fake_st, _session, _nav = _wire(
        monkeypatch, is_registered=False, submit=False, title_widget_return="X"
    )

    book_mod.form_content(book)

    title_call = next(
        c for c in fake_st.text_input_calls if c["label"] == BookForm.title_label
    )
    assert title_call["disabled"] is False
    # No read-only caption for a new book.
    assert BookForm.title_readonly_caption not in fake_st.captions


def test_unregistered_book_submit_writes_title(monkeypatch):
    book, fake_st, _session, _nav = _wire(
        monkeypatch, is_registered=False, submit=True, title_widget_return="A New Book"
    )

    book_mod.form_content(book)

    # The new-book path assigns the entered title (old behaviour preserved).
    assert book.title == "A New Book"
    assert book.title_writes == ["A New Book"]


def test_unregistered_book_blank_title_is_rejected(monkeypatch):
    book, fake_st, session, _nav = _wire(
        monkeypatch, is_registered=False, submit=True, title_widget_return="   "
    )

    book_mod.form_content(book)

    # Required-check fires and the title is never written.
    assert BookForm.title_required in fake_st.warnings
    assert book.title_writes == []


# ---------------------------------------------------------------------------
# Part 2 — stale current_* seeding hole on the edit path (#229 residual)
# ---------------------------------------------------------------------------

# Importing pages/book_edit_home.py executes its page-level render code at import
# time (it is not guarded behind ``if __name__ == "__main__"``). Route the import
# through a throwaway authenticated session with a secrets stand-in that never
# claims to hold a key, then restore both — exactly as
# tests/test_add_books_batch_page_isolation.py does.
class _FakeSecrets(dict):
    def __contains__(self, _key):
        return False


class _ImportBook:
    title = "Import Placeholder"
    last_updated = -1
    page_count = 0
    photos_uploaded = False


_import_state = _AttrDict()
_import_state["authentication_status"] = True
_import_state["current_book"] = _ImportBook()

_real_secrets = st.secrets
_real_session_state = st.session_state
st.secrets = _FakeSecrets()
st.session_state = _import_state
try:
    import pages.book_edit_home as book_edit_home  # noqa: E402
finally:
    st.secrets = _real_secrets
    st.session_state = _real_session_state


class _CurrentBook:
    def __init__(self):
        self.editing = False


def test_edit_details_clears_stale_person_keys(monkeypatch):
    session = _AttrDict()
    current = _CurrentBook()
    session["current_book"] = current
    # Leftovers from a previous add-flow that must NOT seed this edit.
    session["current_author"] = "someone else's author"
    session["current_illustrator"] = "someone else's illustrator"
    session["current_publisher"] = "someone else's publisher"

    monkeypatch.setattr(st, "session_state", session)
    switched = []
    monkeypatch.setattr(st, "switch_page", lambda path: switched.append(path))

    book_edit_home.edit_book_details()

    assert current.editing is True
    assert "current_author" not in session
    assert "current_illustrator" not in session
    assert "current_publisher" not in session
    assert switched == ["./pages/add_book.py"]


def test_edit_details_is_a_noop_when_no_stale_keys(monkeypatch):
    # Popping keys that are absent must not raise (uses pop(..., None)).
    session = _AttrDict()
    current = _CurrentBook()
    session["current_book"] = current

    monkeypatch.setattr(st, "session_state", session)
    monkeypatch.setattr(st, "switch_page", lambda path: None)

    book_edit_home.edit_book_details()

    assert current.editing is True
