"""ISBN degradation stays visible through batch review and saved progress."""
from contextlib import nullcontext
from types import SimpleNamespace
import runpy

import pytest

import batch_upload_ui as ui
from text_content import BookPhotoEntry
from text_content.batch_pipeline import BatchPipeline as Text


@pytest.fixture
def warnings(monkeypatch):
    messages = []
    for method in ('header', 'info', 'write'):
        monkeypatch.setattr(ui.st, method, lambda *args, **kwargs: None)
    monkeypatch.setattr(ui.st, 'warning', messages.append)
    monkeypatch.setattr(ui.st, 'expander', lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(ui.st, 'text_input', lambda *args, value, **kwargs: value)
    monkeypatch.setattr(ui.st, 'checkbox', lambda *args, **kwargs: False)
    monkeypatch.setattr(ui.st, 'button', lambda *args, **kwargs: False)
    return messages


def metadata_rows():
    return [
        {'title': 'First', 'isbn_lookup': {'status': 'failed'}},
        {'title': 'Second', 'isbn_lookup': {'status': 'not_found'}},
        {'title': 'Third', 'isbn_lookup': {'status': 'failed'}},
        {'title': 'Fourth', 'isbn_lookup': {'status': 'found'}},
        {'title': 'Fifth'},
    ]


def test_batch_review_counts_failed_lookups_and_identifies_books(monkeypatch, warnings):
    groups = [SimpleNamespace(metadata=metadata, metadata_state='done', ordinal=i,
                              document_id=str(i), positions=[])
              for i, metadata in enumerate(metadata_rows(), 1)]
    monkeypatch.setattr(ui.batches, 'groups_for', lambda *args: groups)
    monkeypatch.setattr(ui.batches, 'sources_for', lambda *args: [])
    batch = SimpleNamespace(_db=object(), document_id='batch', split_method='black_frame')

    ui._review(batch, object(), object())

    assert warnings == [Text.isbn_lookup_failures.format(count=2),
                        BookPhotoEntry.isbn_lookup_failed, BookPhotoEntry.isbn_lookup_failed]


def test_saved_batch_progress_keeps_enrichment_failure_count(monkeypatch, warnings):
    rows = [dict(metadata=metadata, ordinal=i, state='queued', pages=0)
            for i, metadata in enumerate(metadata_rows(), 1)]
    monkeypatch.setattr(ui.batches, 'finalize_ready', lambda *args: None)
    monkeypatch.setattr(ui.batches, 'progress_snapshot', lambda *args: rows)

    ui._show_progress(object(), object(), object())

    assert warnings == [Text.isbn_lookup_failures.format(count=2)]


def test_absent_or_successful_enrichment_has_no_failure_warning(warnings):
    ui._show_isbn_lookup_failures([{}, {'isbn_lookup': {'status': 'not_found'}},
                                 {'isbn_lookup': {'status': 'found'}}])
    assert warnings == []


@pytest.mark.parametrize('status', ['failed', 'found', 'not_found'])
def test_single_book_form_preserves_photo_lookup_warning(monkeypatch, warnings, status):
    import utilities

    monkeypatch.setattr(utilities, 'check_authentication_status', lambda: None)
    monkeypatch.setattr(utilities, 'page_layout', lambda **kwargs: None)
    monkeypatch.setattr(ui.st, 'form', lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(ui.st, 'session_state', {
        'current_book': SimpleNamespace(to_form=lambda: None),
        'book_extraction': {'isbn_lookup': {'status': status}},
    })

    runpy.run_path('pages/add_book.py')

    assert warnings == ([BookPhotoEntry.isbn_lookup_failed] if status == 'failed' else [])
