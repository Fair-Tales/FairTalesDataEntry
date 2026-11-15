"""Uncleaned reference pages, explicit fallback and admission before reads."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.extraction_eval import reference_inputs
from scripts.extraction_eval.json_sources import JsonSources
from scripts.extraction_eval.store import atomic_json, read_json
from scripts.extraction_eval.tuning import TuningRefused


@pytest.fixture
def loader(cohort_loader_factory):
    return cohort_loader_factory(iterate=['book', 'missing'], check=['check'])


def make_snapshot(tmp_path, books=None):
    snapshot = tmp_path / 'snapshot'
    atomic_json(snapshot / 'manifest.json', {'books': books if books is not None else [
        {'id': 'book', 'title': ' a AND b! '},
    ]})
    return snapshot


def make_pdf(root, filename='A & B.DoC.PDF', pages=('Short!', '', 'Third page', '')):
    """A tiny text PDF; no renderer, PDF library or research input is needed."""
    root.mkdir(parents=True, exist_ok=True)
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               ('<< /Type /Pages /Count %d /Kids [%s] >>' % (
                   len(pages), ' '.join(f'{4 + 2 * index} 0 R' for index in range(len(pages))))).encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for index, text in enumerate(pages):
        escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        stream = f'BT /F1 12 Tf 72 700 Td ({escaped}) Tj ET\n'.encode('ascii') if text else b''
        objects.extend([
            ('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
             '/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>' % (5 + 2 * index)).encode(),
            f'<< /Length {len(stream)} >>\nstream\n'.encode() + stream + b'endstream',
        ])
    content = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(content)
    content.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        content.extend(f'{offset:010d} 00000 n \n'.encode())
    content.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    path = root / filename
    path.write_bytes(content)
    return path


def forbid_reads(monkeypatch, *paths):
    original_open, original_iterdir = Path.open, Path.iterdir

    def checked_open(path, *args, **kwargs):
        assert not any(path.is_relative_to(root) for root in paths), 'Refused input reached a file reader'
        return original_open(path, *args, **kwargs)

    def checked_iterdir(path):
        assert not any(path.is_relative_to(root) for root in paths), 'Refused input reached directory resolution'
        return original_iterdir(path)

    def forbidden(*args, **kwargs):
        pytest.fail('Refused input reached Poppler')

    monkeypatch.setattr(Path, 'open', checked_open)
    monkeypatch.setattr(Path, 'iterdir', checked_iterdir)
    monkeypatch.setattr(subprocess, 'run', forbidden)


def test_short_empty_and_final_empty_pages_survive_in_order(loader, tmp_path, monkeypatch):
    root = tmp_path / 'references'
    path = make_pdf(root)
    snapshot = make_snapshot(tmp_path)
    published = tmp_path / 'must-not-open.json'
    original_open, original_run = Path.open, subprocess.run
    extracted = []

    def checked_open(path, *args, **kwargs):
        assert path != published, 'Matching Word export reached published fallback'
        return original_open(path, *args, **kwargs)

    def run(command, *args, **kwargs):
        result = original_run(command, *args, **kwargs)
        if Path(command[0]).name == 'pdftotext':
            extracted.append(result.stdout)
        return result

    monkeypatch.setattr(Path, 'open', checked_open)
    monkeypatch.setattr(subprocess, 'run', run)
    with loader.activate():
        result = reference_inputs.load_reference('book', snapshot, root, published_path=published)
    assert len(extracted) == 1
    raw = extracted[0].decode('utf-8') if isinstance(extracted[0], bytes) else extracted[0]
    assert raw.endswith('\f')
    assert result['pages'] == raw.split('\f')[:-1]
    assert [text.strip() for text in result['pages']] == ['Short!', '', 'Third page', '']
    assert result['pages'][1] == result['pages'][3] == ''
    assert len(result['pages'][0]) < 20
    assert result['pages'][0] != result['pages'][0].strip()  # Poppler's newline survives.
    assert result['id'] == 'book'
    assert result['source'] == 'word_export'
    assert result['reference_file'] == path.name
    assert result['page_count'] == 4
    assert result['page_sha256'] == [hashlib.sha256(text.encode()).hexdigest() for text in result['pages']]
    assert result['page_char_counts'] == [len(text) for text in result['pages']]
    assert read_json(loader.path)['tuning']['loaded_ids'] == ['book']


@pytest.mark.parametrize(('books', 'filenames', 'reason'), [
    ([], [], 'missing_snapshot_id'),
    ([{'id': 'book', 'title': ''}], [], 'missing_title'),
    ([{'id': 'book', 'title': 'A and B'}], ['Almost A and B.pdf'], 'missing_pdf'),
    ([{'id': 'book', 'title': 'A and B'}], ['A & B.pdf', 'A & B.doc.pdf'], 'ambiguous_pdf'),
])
def test_unmapped_titles_are_reported_without_guessing(
        loader, tmp_path, monkeypatch, books, filenames, reason):
    root = tmp_path / 'references'
    root.mkdir()
    for filename in filenames:
        (root / filename).write_bytes(b'Unmapped PDFs must not be opened')
    snapshot = make_snapshot(tmp_path, books)
    published = tmp_path / 'published.json'
    atomic_json(published, {'Title': {'0': 'A & B'}, 'Text': {'0': 'Fallback'}})
    original_open = Path.open

    def checked_open(path, *args, **kwargs):
        assert path != published, 'Invalid or ambiguous mapping reached fallback'
        return original_open(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail('Unmapped reference reached Poppler')

    monkeypatch.setattr(Path, 'open', checked_open)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    # A missing PDF can fall back only when explicitly requested. Ambiguity cannot.
    fallback = published if reason == 'ambiguous_pdf' else None
    with loader.activate(), pytest.raises(reference_inputs.UnmappedReference) as error:
        reference_inputs.load_reference('book', snapshot, root, published_path=fallback)
    assert error.value.reason == reason


def test_explicit_fallback_is_unaltered_recorded_and_decodes_only_matching_text(
        loader, tmp_path, monkeypatch):
    root = tmp_path / 'references'
    root.mkdir()
    snapshot = make_snapshot(tmp_path)
    published = tmp_path / 'published.json'
    text = '  Six!\t\n\n'
    marker = 'OTHER BOOK TEXT MUST NOT BE DECODED'
    atomic_json(published, {'Title': {'3': 'A & B', '9': 'Another book'},
                            'Text': {'3': text, '9': marker}})
    original_decode = JsonSources.decode

    def decode(source, span):
        start, end = span
        assert marker.encode() not in source._data[start:end], 'Fallback decoded another book text'
        return original_decode(source, span)

    monkeypatch.setattr(JsonSources, 'decode', decode)
    with loader.activate():
        result = reference_inputs.load_reference('book', snapshot, root, published_path=published)
    assert result == {
        'id': 'book', 'source': 'published_text', 'reference_file': published.name,
        'published_row': '3', 'fallback_reason': 'missing_pdf',
        'page_count': None, 'page_sha256': [], 'page_char_counts': [],
        'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'text_char_count': len(text),
    }


@pytest.mark.parametrize(('titles', 'texts', 'reason'), [
    ({'0': 'Almost A and B'}, {'0': 'Text'}, 'missing_published_title'),
    ({'0': 'A & B', '1': 'a and b'}, {'0': 'One', '1': 'Two'}, 'ambiguous_published_title'),
    ({'0': 'A & B'}, {}, 'missing_published_text'),
])
def test_fallback_mapping_failures_are_explicit(loader, tmp_path, titles, texts, reason):
    root = tmp_path / 'references'
    root.mkdir()
    snapshot = make_snapshot(tmp_path)
    published = tmp_path / 'published.json'
    atomic_json(published, {'Title': titles, 'Text': texts})
    with loader.activate(), pytest.raises(reference_inputs.UnmappedReference) as error:
        reference_inputs.load_reference('book', snapshot, root, published_path=published)
    assert error.value.reason == reason


@pytest.mark.parametrize('book_id', ['heldout', 'protected', 'unknown'])
def test_outside_cohort_refused_before_any_reference_read(loader, tmp_path, monkeypatch, book_id):
    snapshot, root, published = [tmp_path / name for name in ('snapshot', 'references', 'published.json')]
    forbid_reads(monkeypatch, snapshot, root, published)
    with loader.activate(), pytest.raises(TuningRefused):
        reference_inputs.load_reference(book_id, snapshot, root, published_path=published)
    state = read_json(loader.path)['tuning']
    assert state['loaded_ids'] == []
    assert state['refused_attempts'][-1]['source_ids'] == [book_id]


def test_reference_requires_active_cohort_loader(tmp_path, monkeypatch):
    snapshot, root, published = [tmp_path / name for name in ('snapshot', 'references', 'published.json')]
    forbid_reads(monkeypatch, snapshot, root, published)
    with pytest.raises(ValueError):
        reference_inputs.load_reference('book', snapshot, root, published_path=published)


def test_inventory_has_metadata_and_unmapped_ids_without_text(loader, tmp_path, monkeypatch):
    root = tmp_path / 'references'
    make_pdf(root)
    marker = 'SNAPSHOT REFERENCE CONTENT MUST NOT BE DECODED'
    snapshot = make_snapshot(tmp_path, [
        {'id': 'book', 'title': 'A and B', 'pages': [{'text': marker}]},
        {'id': 'check', 'title': 'Fallback book'},
        {'id': 'missing', 'title': 'No reference'},
        {'id': 'heldout', 'title': marker, 'pages': [{'text': marker}]},
    ])
    published = tmp_path / 'published.json'
    atomic_json(published, {'Title': {'0': 'Fallback book'}, 'Text': {'0': 'Book text'}})
    original_decode = JsonSources.decode

    def decode(source, span):
        start, end = span
        assert marker.encode() not in source._data[start:end], 'Inventory decoded snapshot content'
        return original_decode(source, span)

    monkeypatch.setattr(JsonSources, 'decode', decode)
    with loader.activate():
        result = reference_inputs.inventory(snapshot, ['missing', 'check', 'book'], root,
                                            published_path=published)
    assert [book['id'] for book in result['books']] == ['book', 'check']
    assert result['unmapped'] == [{'id': 'missing', 'reason': 'missing_published_title'}]
    assert [book['source'] for book in result['books']] == ['word_export', 'published_text']
    assert [book['page_count'] for book in result['books']] == [4, None]
    assert result['books'][1]['fallback_reason'] == 'missing_pdf'
    assert all('text' not in book and 'pages' not in book for book in result['books'])


def test_inventory_refuses_entire_request_before_any_read(loader, tmp_path, monkeypatch):
    snapshot, root, published = [tmp_path / name for name in ('snapshot', 'references', 'published.json')]
    forbid_reads(monkeypatch, snapshot, root, published)
    with loader.activate(), pytest.raises(TuningRefused):
        reference_inputs.inventory(snapshot, ['book', 'heldout'], root, published_path=published)
    assert read_json(loader.path)['tuning']['loaded_ids'] == []


@pytest.mark.parametrize('missing', ['pdfinfo', 'pdftotext'])
def test_missing_poppler_has_clear_failure(loader, tmp_path, monkeypatch, missing):
    root = tmp_path / 'references'
    make_pdf(root)
    snapshot = make_snapshot(tmp_path)
    original = shutil.which
    monkeypatch.setattr(shutil, 'which', lambda name: None if name == missing else original(name))
    with loader.activate(), pytest.raises(ValueError, match='[Pp]oppler') as error:
        reference_inputs.load_reference('book', snapshot, root)
    assert missing in str(error.value)
