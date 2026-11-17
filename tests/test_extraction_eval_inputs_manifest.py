"""Frozen file pairs and the later whole-book scoring contract, using synthetic PDFs."""
from __future__ import annotations

import hashlib

import pytest

from scripts.extraction_eval import inputs_manifest, reference_inputs, scan_inputs
from scripts.extraction_eval.json_sources import JsonSources
from scripts.extraction_eval.store import atomic_json, read_json
from scripts.extraction_eval.tuning import TuningRefused
from test_extraction_eval_reference_inputs import make_pdf, make_snapshot
from test_extraction_eval_scan_inputs import make_scan


@pytest.fixture
def loader(cohort_loader_factory):
    return cohort_loader_factory(iterate=['book', 'missing'], check=['check'])


def make_inputs(tmp_path, *, scan_count=4, reference_pages=('Short!', '', 'Third page', '')):
    scan_root, reference_root = tmp_path / 'scans', tmp_path / 'references'
    scan_file, _ = make_scan(scan_root, count=scan_count)
    reference_file = make_pdf(reference_root, pages=reference_pages)
    snapshot = make_snapshot(tmp_path)
    return snapshot, scan_root, reference_root, scan_file, reference_file


def test_outside_cohort_overrides_are_ignored_without_file_access(loader, tmp_path, monkeypatch):
    snapshot, scans, references, _, _ = make_inputs(tmp_path)
    overrides = [{'id': 'heldout', 'scan_file': 'must-not-open.pdf', 'reason': 'Unused'},
                 {'id': 'protected', 'reference_file': 'must-not-open.pdf', 'reason': 'Unused'}]
    original = scan_inputs._input_file

    def checked_file(root, filename):
        assert filename != 'must-not-open.pdf', 'Outside-cohort override reached the filesystem'
        return original(root, filename)

    monkeypatch.setattr(scan_inputs, '_input_file', checked_file)
    monkeypatch.setattr(reference_inputs, '_input_file', checked_file)
    with loader.activate():
        assert scan_inputs._override_file('heldout', 'scan', scans, overrides) is None
        assert scan_inputs._override_file('protected', 'reference', references, overrides) is None
        manifest = inputs_manifest.build_manifest(snapshot, ['book'], scans, references,
                                                  overrides=overrides)
    assert [row['id'] for row in manifest['books']] == ['book']
    assert manifest['books'][0]['status'] == 'ready'


def test_exact_resolution_wins_without_validating_bad_overrides(loader, tmp_path):
    snapshot, scans, references, _, _ = make_inputs(tmp_path)
    overrides = [{'id': 'book', f'{kind}_file': ['missing.pdf', 'second.pdf'], 'reason': 'Bad'}
                 for kind in ('scan', 'reference')]
    # Duplicate, invalid overrides are irrelevant once the exact rule succeeds.
    with loader.activate():
        manifest = inputs_manifest.build_manifest(snapshot, ['book'], scans, references,
                                                  overrides=overrides + overrides)
    row = manifest['books'][0]
    assert row['status'] == 'ready'
    assert row['scan']['resolved_by'] == row['reference']['resolved_by'] == 'exact'


@pytest.mark.parametrize('kind', ['scan', 'reference'])
def test_override_resolves_one_named_file_with_reason(loader, tmp_path, kind):
    snapshot, scans, references, scan_file, reference_file = make_inputs(tmp_path)
    if kind == 'scan':
        renamed = scans / 'Different spelling'
        scan_file.parent.rename(renamed)
        filename = str((renamed / scan_file.name).relative_to(scans))
    else:
        make_pdf(references, filename='A & B.pdf')  # Two exact-title candidates.
        filename = reference_file.name
    reason = 'Synthetic filename evidence fixes the otherwise ambiguous mapping.'
    overrides = [{'id': 'book', f'{kind}_file': filename, 'reason': reason}]
    with loader.activate():
        manifest = inputs_manifest.build_manifest(snapshot, ['book'], scans, references,
                                                  overrides=overrides)
    row = manifest['books'][0]
    assert row['status'] == 'ready'
    assert row[kind][f'{kind}_file'] == filename
    assert row[kind]['resolved_by'] == 'override'
    assert row[kind]['resolution_reason'] == reason


@pytest.mark.parametrize('kind', ['scan', 'reference'])
@pytest.mark.parametrize('invalid', ['missing', 'list', 'duplicate', 'second_field'])
def test_override_missing_or_second_file_is_rejected(loader, tmp_path, kind, invalid):
    snapshot, scans, references, scan_file, reference_file = make_inputs(tmp_path)
    atomic_json(snapshot / 'manifest.json', {'books': [{'id': 'book', 'title': 'Unmatched title'}]})
    filename = str(scan_file.relative_to(scans)) if kind == 'scan' else reference_file.name
    entry = {'id': 'book', f'{kind}_file': filename, 'reason': 'Synthetic exception'}
    overrides = [entry]
    if invalid == 'missing':
        entry[f'{kind}_file'] = 'missing.pdf'
    elif invalid == 'list':
        entry[f'{kind}_file'] = [filename, filename]
    elif invalid == 'duplicate':
        overrides.append(dict(entry))
    else:
        entry['second_file'] = filename
    with loader.activate(), pytest.raises(ValueError, match='[Oo]verride|Input file'):
        inputs_manifest.build_manifest(snapshot, ['book'], scans, references, overrides=overrides)


def test_unavailable_books_remain_with_reasons_and_available_metadata(loader, tmp_path):
    snapshot, scans, references, scan_file, reference_file = make_inputs(tmp_path)
    atomic_json(snapshot / 'manifest.json', {'books': [
        {'id': 'book', 'title': 'A and B'}, {'id': 'check', 'title': 'No scan'},
    ]})
    make_pdf(references, filename='No scan.pdf', pages=('Reference exists',))
    with loader.activate():
        manifest = inputs_manifest.build_manifest(snapshot, ['missing', 'check', 'book'],
                                                  scans, references, overrides=[])
        output = tmp_path / 'inputs_manifest.json'
        atomic_json(output, manifest)
        loaded = inputs_manifest.load_manifest(output, ['missing', 'check', 'book'])
        extraction = inputs_manifest.extraction_manifest(read_json(snapshot / 'manifest.json'), loaded)
    assert loaded == manifest
    rows = {row['id']: row for row in loaded['books']}
    assert rows['missing']['status'] == rows['check']['status'] == 'unavailable'
    assert rows['missing']['reason'] == 'missing_snapshot_id'
    assert rows['check']['reason'] == 'scan:missing_folder'
    assert rows['check']['scan'] is None
    assert rows['check']['reference']['reference_file'] == 'No scan.pdf'
    assert [book['id'] for book in extraction['books']] == ['book', 'check', 'missing']
    assert [len(book['pages']) for book in extraction['books']] == [4, 0, 0]
    for kind, path in [('scan', scan_file), ('reference', reference_file)]:
        assert rows['book'][kind]['file_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert len(rows['book'][kind]['page_sha256']) == 4
    assert 'Short!' not in output.read_text()  # The public file contains metadata only.


def test_unequal_counts_are_book_level_only_without_page_correspondence(loader, tmp_path):
    snapshot, scans, references, _, _ = make_inputs(tmp_path, reference_pages=('One', '', 'Three'))
    with loader.activate():
        manifest = inputs_manifest.build_manifest(snapshot, ['book'], scans, references, overrides=[])
    row = manifest['books'][0]
    assert row['status'] == 'ready'
    assert (row['scan']['page_count'], row['reference']['page_count']) == (4, 3)
    assert row['pairing'] == 'book_level_only'
    assert row['pairing_reason'] == 'unequal_page_counts'
    assert 'page_mapping' not in row


@pytest.mark.parametrize('kind', ['scan', 'reference'])
def test_frozen_file_hash_change_is_rejected_even_with_same_pages(loader, tmp_path, kind):
    snapshot, scans, references, scan_file, reference_file = make_inputs(tmp_path)
    with loader.activate():
        manifest = inputs_manifest.build_manifest(snapshot, ['book'], scans, references, overrides=[])
    path = scan_file if kind == 'scan' else reference_file
    path.write_bytes(path.read_bytes() + b'\n% File changed, page content unchanged\n')
    with loader.activate(), pytest.raises(ValueError, match='changed since manifest'):
        if kind == 'scan':
            inputs_manifest.load_manifest_scan(manifest['books'][0], scans)
        else:
            inputs_manifest.scoring_inputs(manifest, {'books': {}}, references)


def test_scoring_contract_keeps_order_wordless_failed_and_unavailable_books(loader, tmp_path):
    snapshot, scans, references, _, _ = make_inputs(tmp_path)
    with loader.activate():
        manifest = inputs_manifest.build_manifest(snapshot, ['missing', 'book'], scans, references,
                                                  overrides=[])
        reference = reference_inputs.load_reference('book', snapshot, references, overrides=[])
        predictions = {'books': {
            'book': {'pages': {
                'scan-4': {'status': 'completed', 'contains_story': True, 'text': 'Last'},
                'scan-2': {'status': 'completed', 'contains_story': False, 'text': 'Inserted on blank'},
                'scan-1': {'status': 'completed', 'contains_story': True, 'text': 'First'},
                'scan-3': {'status': 'failed', 'text': 'Failed output must be empty'},
                'scan-99': {'status': 'completed', 'text': 'Outside scan'},
            }},
            'missing': {'pages': {'scan-1': {'status': 'completed', 'text': 'Cannot earn a score'}}},
        }}
        rows = inputs_manifest.scoring_inputs(manifest, predictions, references)
    from scripts.extraction_eval.scoring import score_page_failures
    assert rows[0]['page_failures']['failed_pages_per_100'] == 25
    assert rows[0]['page_failures']['attempted'] == 4
    assert rows[0]['page_failures']['failed'] == 1
    assert rows == [
        {'id': 'book', 'status': 'ready', 'reason': None,
         'reference_text': '\n'.join(reference['pages']),
         'prediction_text': 'First\nInserted on blank\n\nLast',
         'story_reference_text': None, 'story_prediction_text': 'First\nLast',
         'failed_pages': [3], 'forced_score': None, 'unrun_pages': [],
         'page_failures': score_page_failures([predictions['books']['book']['pages'][f'scan-{i}']
                                             for i in range(1, 5)])},
        {'id': 'missing', 'status': 'unavailable', 'reason': 'missing_snapshot_id',
         'reference_text': None, 'prediction_text': '', 'failed_pages': [], 'forced_score': 0,
         'story_reference_text': None, 'story_prediction_text': '',
         'unrun_pages': [], 'page_failures': score_page_failures([])},
    ]
    with loader.activate():
        no_output = inputs_manifest.scoring_inputs(manifest, {'books': {}}, references)
    assert no_output[0]['prediction_text'] == '\n\n\n'
    assert no_output[0]['failed_pages'] == []
    assert no_output[0]['unrun_pages'] == [1, 2, 3, 4]
    assert no_output[0]['page_failures']['attempted'] == 0


def test_missing_frozen_scan_records_each_page_failure(loader, tmp_path):
    from types import SimpleNamespace
    from scripts.extraction_eval.runner import run_extract
    from test_extraction_eval_runtime import FakeCore

    snapshot, scans, references, scan_file, _ = make_inputs(tmp_path)
    with loader.activate():
        inputs = inputs_manifest.build_manifest(snapshot, ['book'], scans, references, overrides=[])
        manifest = loader.snapshot(snapshot, ['book'])
    scan_file.rename(scan_file.with_suffix('.missing'))
    result = run_extract(snapshot, manifest, {'book_ids': ['book']}, loader.path.parent,
                         SimpleNamespace(record_usage=lambda *args: None),
                         {'inputs': 'scans', 'inputs_manifest': inputs, 'scan_root': str(scans)},
                         loader=loader, core=FakeCore(), progress=lambda _: None)
    pages = result['books']['book']['pages']
    assert len(pages) == 4
    assert all(page['status'] == 'failed' and page['failure_kind'] == 'missing_input'
               for page in pages.values())


def test_published_fallback_stays_on_frozen_file_and_row_without_reresolving(
        loader, tmp_path, monkeypatch):
    scans, references = tmp_path / 'scans', tmp_path / 'references'
    make_scan(scans, count=2)
    references.mkdir()
    snapshot = make_snapshot(tmp_path)
    published = tmp_path / 'published.json'
    atomic_json(published, {'Title': {'3': 'A & B'}, 'Text': {'3': 'Frozen text'}})
    with loader.activate():
        manifest = inputs_manifest.build_manifest(snapshot, ['book'], scans, references,
                                                  published_path=published, overrides=[])
    assert manifest['books'][0]['reference']['published_row'] == '3'
    assert manifest['books'][0]['pairing_reason'] == 'published_text'
    # Later files and title matches cannot silently replace the pre-prediction choice.
    make_pdf(references)
    atomic_json(published, {'Title': {'3': 'Changed title', '9': 'A & B'},
                            'Text': {'3': 'Frozen text', '9': 'Wrong replacement'}})

    def forbidden(*args, **kwargs):
        pytest.fail('Scoring re-resolved a frozen reference')

    monkeypatch.setattr(reference_inputs, '_load_reference', forbidden)
    monkeypatch.setattr(inputs_manifest, 'load_reference_file', forbidden)
    with loader.activate():
        rows = inputs_manifest.scoring_inputs(manifest, {'books': {}}, references,
                                              published_path=published)
        with pytest.raises(ValueError, match='recorded file'):
            inputs_manifest.scoring_inputs(manifest, {'books': {}}, references,
                                            published_path=tmp_path / 'another.json')
    assert rows[0]['reference_text'] == 'Frozen text'
    atomic_json(published, {'Title': {'3': 'A & B'}, 'Text': {'3': 'Changed text'}})
    with loader.activate(), pytest.raises(ValueError, match='changed since manifest'):
        inputs_manifest.scoring_inputs(manifest, {'books': {}}, references, published_path=published)


def test_manifest_loader_admits_before_read_and_never_drops_requested_id(loader, tmp_path, monkeypatch):
    snapshot, scans, references, _, _ = make_inputs(tmp_path)
    with loader.activate():
        manifest = inputs_manifest.build_manifest(snapshot, ['book'], scans, references, overrides=[])
    marker = 'UNSELECTED ROW MUST NOT BE DECODED'
    manifest['books'].append({'id': 'heldout', 'reason': marker})
    path = tmp_path / 'inputs_manifest.json'
    atomic_json(path, manifest)
    original = JsonSources.decode

    def decode(source, span):
        start, end = span
        assert marker.encode() not in source._data[start:end]
        return original(source, span)

    monkeypatch.setattr(JsonSources, 'decode', decode)
    with loader.activate():
        assert len(inputs_manifest.load_manifest(path, ['book'])['books']) == 1
        with pytest.raises(ValueError, match=r"missing requested ids: \['check', 'missing'\]"):
            inputs_manifest.load_manifest(path)
        with pytest.raises(ValueError, match='missing requested ids'):
            inputs_manifest.load_manifest(path, ['missing'])
        with pytest.raises(TuningRefused):
            inputs_manifest.load_manifest(tmp_path / 'must-not-open.json', ['heldout'])


def test_story_text_filters_reference_and_predictions_independently(loader, tmp_path):
    from scripts.extraction_eval.scoring import score_book_text
    snapshot, scans, references, _, _ = make_inputs(
        tmp_path, scan_count=4, reference_pages=('Copyright', 'Mia waved.', 'Buy more books'))
    reference_books = [{'id': 'book', 'pages': [
        {'page_number': n, 'reference': {'contains_story': {'value': n == 2, 'status': 'observed'}}}
        for n in range(1, 4)]}]
    predictions = {'books': {'book': {'pages': {
        'scan-1': {'status': 'completed', 'contains_story': False, 'text': 'Cover'},
        'scan-2': {'status': 'completed', 'contains_story': True, 'text': 'Mia'},
        'scan-3': {'status': 'completed', 'contains_story': True, 'text': 'waved.\n\nBANG',
                   'regions': [{'kind': 'narrative', 'text': 'waved.'},
                               {'kind': 'illustration_lettering', 'text': 'BANG'}]},
        'scan-4': {'status': 'failed', 'contains_story': True, 'text': 'Never credit failed text'},
    }}}}
    with loader.activate():
        manifest = inputs_manifest.build_manifest(snapshot, ['book'], scans, references, overrides=[])
        row, = inputs_manifest.scoring_inputs(manifest, predictions, references, reference_books=reference_books)
    score = score_book_text([row['story_reference_text']], [row['story_prediction_text']])
    assert (score['accuracy'], score['reference_tokens'], score['insertions']) == (1, 2, 0)
    assert row['prediction_text'] == 'Cover\nMia\nwaved.\n\nBANG\n'
    assert row['failed_pages'] == [4]
    # A predicted false/unknown story flag loses the word: reference inclusion stays fixed.
    for flag in (False, None):
        predictions['books']['book']['pages']['scan-2']['contains_story'] = flag
        with loader.activate():
            changed, = inputs_manifest.scoring_inputs(manifest, predictions, references, reference_books=reference_books)
        score = score_book_text([changed['story_reference_text']], [changed['story_prediction_text']])
        assert score['accuracy'] == .5 and score['deletions'] == 1
    # Missing reference labels cannot silently turn into an empty perfect reference.
    reference_books[0]['pages'].pop()
    with loader.activate():
        changed, = inputs_manifest.scoring_inputs(manifest, predictions, references, reference_books=reference_books)
    assert changed['story_reference_text'] is None
