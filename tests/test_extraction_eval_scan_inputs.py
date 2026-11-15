"""Original scan inputs: exact JPEG bytes, explicit mapping and cohort admission."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace

from PIL import Image
import pytest

from scripts.extraction_eval import scan_inputs
from scripts.extraction_eval.json_sources import JsonSources
from scripts.extraction_eval.store import atomic_json, read_json
from scripts.extraction_eval.tuning import TuningRefused


@pytest.fixture
def loader(cohort_loader_factory):
    return cohort_loader_factory(iterate=['book', 'missing'], check=['check'])


def make_scan(root, folder='A & B', filename='different-name.PDF', *, count=12):
    path = root / folder / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    pages = [Image.new('RGB', (8, 10), (index * 19, index * 11, 255 - index * 17))
             for index in range(count)]
    try:
        pages[0].save(path, 'PDF', save_all=True, append_images=pages[1:])
    finally:
        for page in pages:
            page.close()
    # Read the JPEG streams from the generated PDF itself. No Pillow decode or
    # second JPEG encoding can hide re-encoding in the production loader.
    pdf = path.read_bytes()
    matches = list(re.finditer(
        rb'/Filter\s*/DCTDecode\b[^>]*?/Length\s+(\d+)\s*>>\s*stream\r?\n', pdf))
    embedded = [pdf[match.end():match.end() + int(match.group(1))] for match in matches]
    assert len(embedded) == count
    assert len(set(embedded)) == count
    assert all(page.startswith(b'\xff\xd8') and page.endswith(b'\xff\xd9') for page in embedded)
    return path, embedded


def test_scan_pages_keep_embedded_jpeg_bytes_in_page_order(loader, tmp_path):
    root = tmp_path / 'scans'
    path, embedded = make_scan(root)
    with loader.activate():
        result = scan_inputs.load_scan('book', ' a AND b! ', scan_root=root)
    assert result['id'] == 'book'
    assert result['scan_file'] == path.relative_to(root).as_posix()
    assert result['page_count'] == 12
    assert result['pages'] == embedded
    assert result['page_sha256'] == [hashlib.sha256(page).hexdigest() for page in embedded]
    assert read_json(loader.path)['tuning']['loaded_ids'] == ['book']


@pytest.mark.parametrize(('title', 'folders', 'filenames', 'reason'), [
    ('', [], [], 'missing_title'),
    ('A and B', ['Almost A and B'], [], 'missing_folder'),
    ('A and B', ['A & B', 'a and b'], [], 'ambiguous_folder'),
    ('A and B', ['A & B'], ['notes.doc'], 'missing_pdf'),
    ('A and B', ['A & B'], ['A & B.pdf', 'other.PDF'], 'ambiguous_pdf'),
])
def test_unmapped_titles_are_not_guessed(loader, tmp_path, monkeypatch,
                                        title, folders, filenames, reason):
    root = tmp_path / 'scans'
    root.mkdir()
    for folder in folders:
        directory = root / folder
        directory.mkdir()
        for filename in filenames:
            (directory / filename).write_bytes(b'not read')

    def forbidden(*args, **kwargs):
        pytest.fail('Unmapped input reached Poppler')

    monkeypatch.setattr(subprocess, 'run', forbidden)
    with loader.activate(), pytest.raises(scan_inputs.UnmappedScan) as error:
        scan_inputs.load_scan('book', title, scan_root=root)
    assert error.value.reason == reason


def forbid_scan_reads(monkeypatch, root):
    original_open = Path.open
    original_iterdir = Path.iterdir

    def checked_open(path, *args, **kwargs):
        assert not path.is_relative_to(root), 'Refused scan reached a file reader'
        return original_open(path, *args, **kwargs)

    def checked_iterdir(path):
        assert not path.is_relative_to(root), 'Refused scan reached directory resolution'
        return original_iterdir(path)

    def forbidden(*args, **kwargs):
        pytest.fail('Refused scan reached Poppler')

    monkeypatch.setattr(Path, 'open', checked_open)
    monkeypatch.setattr(Path, 'iterdir', checked_iterdir)
    monkeypatch.setattr(subprocess, 'run', forbidden)


@pytest.mark.parametrize('book_id', ['heldout', 'protected', 'unknown'])
def test_outside_cohort_refused_before_scan_resolution_or_read(
        loader, tmp_path, monkeypatch, book_id):
    root = tmp_path / 'scans'
    make_scan(root, count=1)
    forbid_scan_reads(monkeypatch, root)
    with loader.activate(), pytest.raises(TuningRefused):
        scan_inputs.load_scan(book_id, 'A & B', scan_root=root)
    state = read_json(loader.path)['tuning']
    assert state['loaded_ids'] == []
    assert state['refused_attempts'][-1]['source_ids'] == [book_id]


def test_scan_requires_active_cohort_loader(tmp_path, monkeypatch):
    root = tmp_path / 'scans'
    forbid_scan_reads(monkeypatch, root)
    with pytest.raises(ValueError):
        scan_inputs.load_scan('book', 'A & B', scan_root=root)


@pytest.mark.parametrize('missing', ['pdfinfo', 'pdfimages'])
def test_missing_poppler_has_clear_failure(loader, tmp_path, monkeypatch, missing):
    root = tmp_path / 'scans'
    make_scan(root, count=1)
    original = shutil.which
    monkeypatch.setattr(shutil, 'which', lambda command: None if command == missing else original(command))
    with loader.activate(), pytest.raises(ValueError, match='[Pp]oppler') as error:
        scan_inputs.load_scan('book', 'A & B', scan_root=root)
    assert missing in str(error.value)


def test_inventory_contains_only_scan_metadata_and_never_decodes_pages(
        loader, tmp_path, monkeypatch):
    root = tmp_path / 'scans'
    path, embedded = make_scan(root, count=2)
    snapshot = tmp_path / 'snapshot'
    marker = 'PAGE OR REFERENCE CONTENT MUST NOT BE DECODED'
    atomic_json(snapshot / 'manifest.json', {'books': [
        {'id': 'book', 'title': 'A and B', 'pages': [{'text': marker}], 'reference': marker},
        {'id': 'check', 'title': 'Missing scan', 'pages': [{'text': marker}], 'reference': marker},
        {'id': 'heldout', 'title': marker, 'pages': [{'text': marker}], 'reference': marker},
    ]})
    original = JsonSources.decode

    def decode(source, span):
        start, end = span
        assert marker.encode() not in source._data[start:end], 'Inventory decoded book content'
        return original(source, span)

    monkeypatch.setattr(JsonSources, 'decode', decode)
    with loader.activate():
        result = scan_inputs.inventory(snapshot, ['book', 'check'], scan_root=root)
    assert result == {
        'books': [{'id': 'book', 'scan_file': path.relative_to(root).as_posix(),
                   'page_count': 2,
                   'page_sha256': [hashlib.sha256(page).hexdigest() for page in embedded]}],
        'unmapped': [{'id': 'check', 'reason': 'missing_folder'}],
    }


def test_inventory_refuses_entire_request_before_opening_any_scan(
        loader, tmp_path, monkeypatch):
    root = tmp_path / 'scans'
    snapshot = tmp_path / 'snapshot'
    atomic_json(snapshot / 'manifest.json', {'books': [
        {'id': 'book', 'title': 'A & B'},
        {'id': 'heldout', 'title': 'Excluded'},
    ]})
    forbid_scan_reads(monkeypatch, root)
    with loader.activate(), pytest.raises(TuningRefused):
        scan_inputs.inventory(snapshot, ['book', 'heldout'], scan_root=root)


def test_non_jpeg_pdf_is_rejected_without_rendering(loader, tmp_path, monkeypatch):
    root = tmp_path / 'scans'
    path = root / 'A & B' / 'A & B.pdf'
    path.parent.mkdir(parents=True)
    with Image.new('1', (8, 10)) as page:
        page.save(path, 'PDF')
    assert b'/CCITTFaxDecode' in path.read_bytes()
    original = subprocess.run
    commands = []

    def run(command, *args, **kwargs):
        commands.append(command)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', run)
    with loader.activate(), pytest.raises(ValueError, match='one embedded JPEG per page'):
        scan_inputs.load_scan('book', 'A & B', scan_root=root)
    assert len(commands) == 2  # Inspection only: no extraction or fallback rendering.


@pytest.mark.parametrize('has_render', [False, True])
def test_runner_uses_scan_order_and_bytes_for_every_image_stage(
        loader, tmp_path, monkeypatch, has_render):
    from scripts.extraction_eval import runner
    from scripts.extraction_eval.sampling import select_books

    root = tmp_path / 'scans'
    _, embedded = make_scan(root, count=3)
    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    render = b'REFERENCE RENDER MUST NEVER REACH EXTRACTION'
    if has_render:
        (snapshot / 'reference.jpg').write_bytes(render)
    manifest = {'snapshot_id': 'synthetic', 'books': [{
        'id': 'book', 'title': 'A and B', 'pages': [{
            'id': 'book_1', 'page_number': 1,
            'image': {'path': 'reference.jpg', 'sha256': hashlib.sha256(render).hexdigest()}
                     if has_render else None,
            'reference': {'text': {'value': 'REFERENCE TEXT MUST NEVER REACH EXTRACTION'}},
        }],
    }]}
    with loader.activate():
        scan = scan_inputs.load_scan('book', 'A and B', scan_root=root)
    inventory = [scan_inputs.scan_metadata(scan)]
    config = runner.configure_inputs({
        'settings': {'story_context_enabled': True, 'metadata_reconciliation_enabled': True},
    }, 'scans', scans=inventory, scan_root=root)
    extraction_manifest = scan_inputs.scan_manifest(manifest, inventory)
    selection = select_books(extraction_manifest, books=['book'])
    assert selection['book_ids'] == ['book']
    assert selection['page_count'] == 3
    assert manifest['books'][0]['pages'][0]['id'] == 'book_1'
    seen = {'metadata': [], 'page': [], 'context': [], 'reconcile': [], 'characters': []}

    def metadata(images, *args):
        seen['metadata'].append(images)
        return {'title': 'Scan prediction'}

    def extract_page(data, *args):
        seen['page'].append(data)
        return {'status': 'completed', 'text': f'Prediction {len(seen["page"])}',
                'contains_story': True}

    def context(pages, images, *args):
        seen['context'].append(images)
        return {'status': 'completed', 'fallback': False, 'pages': [
            {'page_number': page['page_number'], 'contains_story': True, 'page_type': 'story'}
            for page in pages]}

    def reconcile(metadata, pages, images, *args):
        seen['reconcile'].append(images)
        return metadata

    def characters(pages, *args):
        seen['characters'].append([page['text'] for page in pages])
        return []

    def forbidden(*args, **kwargs):
        pytest.fail('Scan run reached the stored-render reader')

    monkeypatch.setattr(runner, 'local_image', forbidden)
    core = SimpleNamespace(extract_metadata=metadata, extract_page=extract_page,
                           classify_book_context=context, reconcile_metadata=reconcile,
                           extract_characters=characters)
    result = runner.run_extract(snapshot, manifest, selection, loader.path.parent,
                                SimpleNamespace(record_usage=lambda *args: None), config,
                                loader=loader, core=core, progress=lambda *args: None)
    ordered = list(enumerate(embedded, 1))
    assert seen == {'metadata': [ordered], 'page': embedded, 'context': [ordered],
                    'reconcile': [ordered], 'characters': [['Prediction 1', 'Prediction 2', 'Prediction 3']]}
    assert result['inputs'] == 'scans'
    assert result['incomplete'] is False
    book = result['books']['book']
    assert list(book['pages']) == ['scan-1', 'scan-2', 'scan-3']
    assert all(stage['status'] == 'completed' for stage in [
        book['metadata'], book['story_context'], book['metadata_reconciliation'],
        book['characters'], *book['pages'].values()])
