"""C4 identifiers: synthetic books, in-memory writes and fake photo lookups."""
import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
import streamlit as st

from bibliography import normalize_isbn
from data_structures.book import Book
from research_export import ResearchExport
from text_content import BookForm


@pytest.mark.parametrize('raw', ['0-306-40615-2', '978-0-306-40615-7', ' 978 0306406157 '])
def test_isbn10_and_13_normalize_to_same_isbn13(raw):
    book = Book()
    book.isbn = raw
    assert book.isbn == normalize_isbn(raw) == '9780306406157'


def test_isbn10_x_and_979():
    assert normalize_isbn('0-8044-2957-x') == '9780804429573'
    assert normalize_isbn('9791090636071') == '9791090636071'


@pytest.fixture
def writes(monkeypatch):
    calls = []
    monkeypatch.setattr(st, 'session_state', SimpleNamespace(
        firestore=SimpleNamespace(update_field=lambda **kw: calls.append(kw))))
    return calls


@pytest.mark.parametrize('raw', ['9780306406158', '0306406153', '1234567890128', 'not an ISBN'])
def test_bad_isbn_refused_before_write(raw, writes):
    book = Book({'title': 'Synthetic', 'is_registered': True, 'isbn': '9780306406157'})
    with pytest.raises(ValueError, match='ISBN is invalid'):
        book.isbn = raw
    assert book.isbn == '9780306406157'
    assert writes == []


def test_isbn_assignment_uses_write_through(writes):
    book = Book({'title': 'Synthetic', 'is_registered': True})
    book.isbn = '0-306-40615-2'
    assert writes[0] == dict(collection='books', document='synthetic',
                             field='isbn', value='9780306406157')


def test_person_links_editions_without_changing_document_ids_or_isbns(writes):
    hardback = Book({'title': 'Synthetic (hardback)', 'is_registered': True,
                     'isbn': '9780306406157', 'edition': 'first hardback'})
    paperback = Book({'title': 'Synthetic (paperback)', 'is_registered': True,
                      'isbn': '9780804429573', 'edition': 'first UK paperback'})
    before = [(book.document_id, book.isbn) for book in (hardback, paperback)]
    work_id = hardback.assign_work_id()
    assert UUID(work_id).version == 4
    paperback.assign_work_id(work_id)  # A person copies the other edition's id.
    assert hardback.work_id == paperback.work_id
    assert [(book.document_id, book.isbn) for book in (hardback, paperback)] == before
    assert before[0][0] != before[1][0] and before[0][1] != before[1][1]
    assert {call['field'] for call in writes} == {'work_id', 'last_updated'}
    for book in (hardback, paperback):
        exported = ResearchExport().record('books', book.document_id, book.to_dict())
        assert (exported['isbn'], exported['edition'], exported['work_id']) == (
            book.isbn, book.edition, work_id)


def test_work_id_assigned_once_and_survives_reload_and_title_change():
    book = Book()
    work_id = book.assign_work_id()
    book.title = 'Changed title'
    assert book.assign_work_id() == work_id
    assert Book(book.to_dict()).work_id == work_id
    for replacement in (None, 'a60f5c40-cad0-4a84-9cca-1b4ff24e5549'):
        with pytest.raises(ValueError, match='cannot be changed'):
            book.work_id = replacement
    assert book.work_id == work_id


@pytest.mark.parametrize('extra', [{}, dict.fromkeys(('isbn', 'edition', 'work_id'), float('nan'))])
def test_old_book_loads_and_exports_missing_identifiers_without_writes(extra, writes):
    raw = {'title': 'Old synthetic book', 'is_registered': True, **extra}
    book = Book(raw)
    assert book.isbn is book.edition is book.work_id is None
    for record in ({'title': raw['title']}, book.to_dict()):
        exported = ResearchExport().record('books', book.document_id, record)
        assert all(exported[field] is None for field in ('isbn', 'edition', 'work_id'))
    assert writes == []


def test_reconstruction_lookup_stores_isbn_and_preserves_comment(monkeypatch):
    import book_reconstruction as reconstruction
    from utilities import apply_isbn_metadata

    metadata = apply_isbn_metadata({'isbn': '0-306-40615-2'},
        isbn_lookup=lambda isbn: {'title': 'Synthetic lookup title'})
    book = Book()
    book.comment = 'Keep this research note.'
    saved = []
    monkeypatch.setattr(reconstruction, 'Book', lambda: book)
    monkeypatch.setattr(book, 'register', lambda: saved.append(book.to_dict()))
    monkeypatch.setattr(book, 'get_ref', lambda: 'books/synthetic_lookup_title')
    monkeypatch.setattr(reconstruction, 'extract_photo_first_metadata', lambda *a: metadata)
    monkeypatch.setattr(reconstruction, '_process_pages', lambda *a: ({}, []))
    monkeypatch.setattr(reconstruction, '_detect_and_create_characters', lambda *a: (0, 0))
    monkeypatch.setattr(reconstruction, 'databot_entered_by', lambda: 'users/databot')
    monkeypatch.setattr(reconstruction.load_book_dict, 'clear', lambda: None)
    monkeypatch.setattr(st, 'session_state', {
        'firestore': SimpleNamespace(document_exists=lambda **kw: False)})

    result = reconstruction.reconstruct_book_from_photos(
        [('page.jpg', b'synthetic')], object(), fs=object())
    assert result['book'].isbn == saved[0]['isbn'] == '9780306406157'
    assert saved[0]['comment'] == book.comment == 'Keep this research note.'
    assert book.work_id is None


@pytest.mark.parametrize('raw, expected', [('0-306-40615-2', '9780306406157'),
                                         ('9780306406158', None)])
def test_single_photo_metadata_preserves_comment_and_refuses_invalid_isbn(monkeypatch, raw, expected):
    # Execute the actual function without running the page's upload/service UI.
    path = Path(__file__).resolve().parents[1] / 'pages/add_book_photos.py'
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == '_apply_extracted_metadata')
    book = Book()
    book.comment = 'Keep this note.'
    warnings = []
    fake_st = SimpleNamespace(session_state={'current_book': book}, warning=warnings.append)
    namespace = {'st': fake_st, 'BookForm': BookForm}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    namespace['_apply_extracted_metadata']({'isbn': raw, 'isbn_metadata': {'title': 'Lookup'}})
    assert book.isbn == expected
    assert book.comment == 'Keep this note.'
    assert warnings == ([] if expected else [BookForm.invalid_isbn])


def test_uploader_lookup_stores_isbn_without_touching_comment(monkeypatch):
    import pages.uploader as uploader

    book = Book()
    book.comment = 'Keep this note.'
    monkeypatch.setattr(st, 'session_state', {'current_book': book})
    monkeypatch.setattr(st, 'status', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(st, 'progress', lambda *a: None)
    monkeypatch.setattr(st, 'info', lambda *a: None)
    monkeypatch.setattr(uploader, 'get_ai_settings', lambda: {'extraction_model': 'fake'})
    monkeypatch.setattr(uploader, 'get_anthropic_client', lambda: object())
    monkeypatch.setattr(uploader, '_inline_ai_batch', lambda *a: ('ISBN 0-306-40615-2', []))
    monkeypatch.setattr(uploader, 'lookup_isbn', lambda *a, **kw: {'title': 'Lookup'})
    uploader._process_photo_batch([b'synthetic'], ['page.jpg'], object())
    assert book.isbn == '9780306406157'
    assert book.comment == 'Keep this note.'
