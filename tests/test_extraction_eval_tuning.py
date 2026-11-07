"""Offline tuning gates: refusal precedes every content/cache read."""

import hashlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts.extraction_eval.json_sources import JsonSources
from scripts.extraction_eval.store import atomic_json, digest, read_json
from scripts.extraction_eval.tuning import TuningExposure, TuningLoader, TuningRefused


@pytest.fixture
def freeze_dir(tmp_path):
    path = tmp_path / 'freeze'
    atomic_json(path / 'holdout_ids.json', ['denied'])
    atomic_json(path / 'freeze.json', {
        'imported80_ids': ['denied'], 'protected40_ids': ['protected'],
        'tune41_ids': ['allowed'], 'tune81_ids': ['allowed', 'protected'],
        'imported80_ids_sha256': digest(['denied']),
        'holdout_ids_file_sha256': hashlib.sha256((path / 'holdout_ids.json').read_bytes()).hexdigest(),
    })
    return path


@pytest.fixture
def loader(tmp_path, freeze_dir):
    return TuningLoader(tmp_path / 'run', freeze_dir)


@pytest.fixture
def paired_lock():
    return {'run_id': 'original-paired-run', 'locked_at': '2000-01-01T00:00:00+00:00',
            'results_sha256': 'a' * 64, 'configuration_sha256': 'b' * 64}


def never_read():
    pytest.fail('Refused content reached its reader')


def tuning_record(loader):
    return read_json(loader.path)['tuning']


def forbid_marker_decode(monkeypatch, marker='FORBIDDEN CONTENT'):
    original = JsonSources.decode

    def checked(source, span):
        start, end = span
        assert marker.encode() not in source._data[start:end], 'Excluded content was deserialized'
        return original(source, span)

    monkeypatch.setattr(JsonSources, 'decode', checked)


def write_snapshot(tmp_path, books):
    path = tmp_path / 'snapshot'
    atomic_json(path / 'manifest.json', {
        'snapshot_id': 'offline-fixture', 'complete': True, 'source_consistent': True,
        'books': books, 'raw_documents': {'books/denied': 'FORBIDDEN CONTENT'},
    })
    return path


def book(work_id, *, content='FORBIDDEN CONTENT', **extra):
    return {'id': work_id, 'title': work_id, 'pages': [],
            'reference': {'text': content}, **extra}


def test_allowed_source_loads_and_records_work_id(loader):
    calls = []
    assert loader.load(['allowed'], 'historical_labels', lambda: calls.append('read') or [1, 0]) == [1, 0]
    assert calls == ['read']
    assert tuning_record(loader)['loaded_ids'] == ['allowed']
    loader.audit()
    assert tuning_record(loader)['breaches'] == []


@pytest.mark.parametrize('sources', [['denied'], ['protected'], ['unknown'], [], [None], None])
def test_excluded_and_unresolvable_sources_fail_before_reader(loader, sources):
    with pytest.raises(TuningRefused):
        loader.load(sources, 'image', never_read)
    loader.audit()
    record = tuning_record(loader)
    assert record['loaded_ids'] == []
    assert record['breaches'] == []
    assert record['refused_attempts'][0]['source_ids'] == sources
    assert record['refused_attempts'][0]['kind'] == 'image'


@pytest.mark.parametrize('damage', ['missing', 'changed', 'invalid_json', 'wrong_shape', 'duplicate_ids'])
def test_bad_exclusion_file_stops_run(tmp_path, freeze_dir, damage):
    path = freeze_dir / 'holdout_ids.json'
    if damage == 'missing':
        path.unlink()
    elif damage == 'changed':
        path.write_bytes(path.read_bytes() + b'\n')
    else:
        path.write_bytes({'invalid_json': b'{', 'wrong_shape': b'{"ids":["denied"]}',
                          'duplicate_ids': b'["denied","denied"]'}[damage])
        # A matching byte hash must not make malformed contents acceptable.
        freeze = read_json(freeze_dir / 'freeze.json')
        freeze['holdout_ids_file_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_json(freeze_dir / 'freeze.json', freeze)
    out = tmp_path / 'bad-run'
    with pytest.raises(ValueError):
        TuningLoader(out, freeze_dir)
    assert not (out / 'run.json').exists()


def test_inconsistent_id_set_hash_stops_run(tmp_path, freeze_dir):
    freeze = read_json(freeze_dir / 'freeze.json')
    freeze['imported80_ids_sha256'] = '0' * 64
    atomic_json(freeze_dir / 'freeze.json', freeze)
    with pytest.raises(ValueError):
        TuningLoader(tmp_path / 'bad-run', freeze_dir)


def test_protected_release_records_lock_without_changing_frozen_draw(loader, tmp_path, freeze_dir, paired_lock):
    original = {name: (freeze_dir / name).read_bytes() for name in ('freeze.json', 'holdout_ids.json')}
    with pytest.raises(TuningRefused):
        loader.load(['protected'], 'reference', never_read)
    released = TuningLoader(tmp_path / 'released', freeze_dir, paired_experiment_lock=paired_lock)
    assert released.load(['protected'], 'reference', lambda: 'released reference') == 'released reference'
    assert tuning_record(released)['paired_experiment_lock'] == paired_lock
    assert tuning_record(released)['loaded_ids'] == ['protected']
    resumed = TuningLoader(tmp_path / 'released', freeze_dir)
    assert resumed.load(['protected'], 'reference', lambda: 'cached reference') == 'cached reference'
    with pytest.raises(TuningRefused):
        resumed.load(['denied'], 'reference', never_read)
    assert {name: (freeze_dir / name).read_bytes() for name in original} == original


def test_release_state_cannot_change_within_existing_run(loader, freeze_dir, paired_lock):
    with pytest.raises(ValueError, match='changed'):
        TuningLoader(loader.path.parent, freeze_dir, paired_experiment_lock=paired_lock)
    assert tuning_record(loader)['paired_experiment_lock'] is None


@pytest.mark.parametrize('change', [
    {'run_id': ''}, {'locked_at': 'yesterday'}, {'locked_at': '2000-01-01'},
    {'locked_at': '2999-01-01T00:00:00Z'}, {'results_sha256': 'unchecked'},
    {'configuration_sha256': None},
])
def test_incomplete_or_invalid_paired_lock_is_refused(tmp_path, freeze_dir, paired_lock, change):
    with pytest.raises(ValueError):
        TuningLoader(tmp_path / 'bad-lock', freeze_dir, paired_experiment_lock={**paired_lock, **change})


def test_recorded_exposure_invalidates_proof_without_replacement(loader, freeze_dir):
    original = (freeze_dir / 'holdout_ids.json').read_bytes()
    record = read_json(loader.path)
    record['tuning']['loaded_ids'].append('denied')
    atomic_json(loader.path, record)
    with pytest.raises(TuningExposure, match='do not replace or redraw'):
        loader.audit()
    breach = tuning_record(loader)['breaches'][0]
    assert breach['work_id'] == 'denied'
    assert breach['proof_status'] == 'invalidated'
    assert (freeze_dir / 'holdout_ids.json').read_bytes() == original
    with pytest.raises(TuningExposure):
        TuningLoader(loader.path.parent, freeze_dir)
    # Removing a later load-list entry cannot silently restore invalidated proof.
    record = read_json(loader.path)
    record['tuning']['loaded_ids'] = []
    atomic_json(loader.path, record)
    with pytest.raises(TuningExposure):
        loader.audit()
    assert tuning_record(loader)['breaches'][0] == breach


def test_missing_run_record_leaves_exposure_unresolved(loader):
    loader.path.unlink()
    with pytest.raises(ValueError, match='unresolved'):
        loader.audit()


def test_mixed_snapshot_decodes_only_allowed_source(loader, tmp_path, monkeypatch):
    path = write_snapshot(tmp_path, [book('denied'), book('protected'), book('unknown'),
                                    book('allowed', content='usable reference')])
    forbid_marker_decode(monkeypatch)
    manifest = loader.snapshot(path)
    assert manifest['books'] == [book('allowed', content='usable reference')]
    assert 'raw_documents' not in manifest
    assert tuning_record(loader)['loaded_ids'] == ['allowed']


@pytest.mark.parametrize('selected', ['denied', 'protected', 'unknown', 'absent'])
def test_explicit_denied_snapshot_source_refused_before_deserialization(loader, tmp_path, monkeypatch, selected):
    path = write_snapshot(tmp_path, [book('denied'), book('protected'), book('unknown'),
                                    book('allowed', content='usable reference')])
    forbid_marker_decode(monkeypatch)
    with pytest.raises(TuningRefused):
        loader.snapshot(path, [selected])
    assert tuning_record(loader)['loaded_ids'] == []
    assert tuning_record(loader)['refused_attempts'][-1]['source_ids'] == [selected]


@pytest.mark.parametrize('link', ['title', 'image', 'declared_group'])
def test_equivalent_edition_of_excluded_work_is_refused(loader, tmp_path, monkeypatch, link):
    allowed, denied = book('allowed'), book('denied')
    if link == 'title':
        allowed['title'], denied['title'] = 'The Same Work!', 'the same work'
    elif link == 'image':
        allowed['pages'] = denied['pages'] = [{'image': {'sha256': 'same-original-image'}}]
    else:
        allowed['holdout_group'] = denied['holdout_group'] = 'known-editions'
    path = write_snapshot(tmp_path, [allowed, denied])
    forbid_marker_decode(monkeypatch)
    with pytest.raises(TuningRefused):
        loader.snapshot(path, ['allowed'])
    assert tuning_record(loader)['loaded_ids'] == []
    assert tuning_record(loader)['refused_attempts'][-1]['resolved_ids'] == ['allowed', 'denied']


def test_prediction_cache_is_refused_before_content_decode(loader, tmp_path, monkeypatch):
    path = tmp_path / 'predictions.json'
    atomic_json(path, {'books': {'allowed': {'text': 'usable'}, 'denied': {'text': 'FORBIDDEN CONTENT'}}})
    forbid_marker_decode(monkeypatch)
    with pytest.raises(TuningRefused):
        loader.predictions(path)
    assert tuning_record(loader)['loaded_ids'] == []
    assert tuning_record(loader)['refused_attempts'][-1]['kind'] == 'predictions'


def test_allowed_prediction_cache_loads(loader, tmp_path):
    path = tmp_path / 'predictions.json'
    value = {'books': {'allowed': {'text': 'usable'}}}
    atomic_json(path, value)
    assert loader.predictions(path) == value
    assert tuning_record(loader)['loaded_ids'] == ['allowed']


@pytest.mark.parametrize('work_id', ['denied', 'unknown', None])
def test_prompt_example_refused_before_content_decode(loader, tmp_path, monkeypatch, work_id):
    path = tmp_path / 'examples.json'
    atomic_json(path, {'examples': [{'book_id': work_id, 'source_text': 'FORBIDDEN CONTENT'}]})
    forbid_marker_decode(monkeypatch)
    with pytest.raises(TuningRefused):
        loader.examples(path)
    assert tuning_record(loader)['loaded_ids'] == []
    assert tuning_record(loader)['refused_attempts'][-1]['kind'] == 'prompt_example'


def test_allowed_prompt_example_loads(loader, tmp_path):
    path = tmp_path / 'examples.json'
    value = {'pack_version': 'fixture', 'examples': [{'book_id': 'allowed', 'source_text': 'usable'}]}
    atomic_json(path, value)
    assert loader.examples(path) == value
    assert tuning_record(loader)['loaded_ids'] == ['allowed']


def test_runner_cannot_reuse_denied_stage_cache(loader, tmp_path, monkeypatch):
    from scripts.extraction_eval.runner import Stages, run_extract

    stages = Stages(loader.path.parent)
    stages.put('denied:metadata', {'status': 'completed', 'value': {'title': 'FORBIDDEN CONTENT'}})
    stages.close()
    monkeypatch.setattr(Stages, 'get', lambda *args: never_read())
    gateway = SimpleNamespace(record_usage=lambda *args: None)
    with pytest.raises(TuningRefused):
        run_extract(tmp_path, {'books': [book('denied')]}, {'book_ids': ['denied']},
                    loader.path.parent, gateway, {}, loader=loader, core=SimpleNamespace(),
                    progress=lambda *args: None)
    assert tuning_record(loader)['loaded_ids'] == []
    assert tuning_record(loader)['breaches'] == []


def test_runner_reuses_allowed_stage_cache_and_records_source(loader, tmp_path):
    from scripts.extraction_eval.runner import Stages, run_extract

    stages = Stages(loader.path.parent)
    stages.put('allowed:metadata', {'status': 'completed', 'value': {'title': 'Cached title'}})
    stages.put('allowed:characters', {'status': 'completed', 'value': []})
    stages.close()
    gateway = SimpleNamespace(record_usage=lambda *args: None)
    result = run_extract(tmp_path, {'books': [book('allowed', content='usable')]},
                         {'book_ids': ['allowed']}, loader.path.parent, gateway, {},
                         loader=loader, core=SimpleNamespace(), progress=lambda *args: None)
    assert result['books']['allowed']['metadata']['value']['title'] == 'Cached title'
    assert result['incomplete'] is False
    assert tuning_record(loader)['loaded_ids'] == ['allowed']


@pytest.mark.parametrize('reader', ['cached_lookup', 'metadata_outcome'])
@pytest.mark.parametrize('source_ids', [None, ['denied'], ['unknown']])
def test_isbn_cache_provenance_refused_before_decode(loader, monkeypatch, reader, source_ids):
    from scripts.extraction_eval.runner import cached_isbn_lookup, metadata_with_lookup_outcome

    out = loader.path.parent
    path = out / 'lookups' / f'{digest("123")}.json'
    saved = {'isbn': '123', 'value': {'title': 'FORBIDDEN CONTENT'}}
    if source_ids is not None:
        saved['source_work_ids'] = source_ids
    atomic_json(path, saved)
    forbid_marker_decode(monkeypatch)
    with loader.activate(), pytest.raises(TuningRefused):
        if reader == 'cached_lookup':
            cached_isbn_lookup(out, lambda *args: never_read(), dry_run=True)('123')
        else:
            metadata_with_lookup_outcome({'isbn': '123'}, out)
    assert tuning_record(loader)['loaded_ids'] == []
    assert tuning_record(loader)['refused_attempts'][-1]['kind'] == 'isbn_cache'


def test_allowed_isbn_cache_and_metadata_outcome_load(loader):
    from scripts.extraction_eval.runner import cached_isbn_lookup, metadata_with_lookup_outcome

    out = loader.path.parent
    atomic_json(out / 'lookups' / f'{digest("123")}.json', {
        'isbn': '123', 'value': {'title': 'Usable title'}, 'source_work_ids': ['allowed'],
    })
    with loader.activate():
        lookup = cached_isbn_lookup(out, lambda *args: never_read(), dry_run=True)
        assert lookup('123') == {'title': 'Usable title'}
        assert metadata_with_lookup_outcome({'isbn': '123'}, out)['isbn_lookup'] == {'status': 'found'}
    assert tuning_record(loader)['loaded_ids'] == ['allowed']


def test_new_isbn_cache_carries_source_and_replays_without_lookup(loader):
    from scripts.extraction_eval.runner import cached_isbn_lookup

    out = loader.path.parent
    calls = []

    def local_lookup(isbn):
        calls.append(isbn)
        return {'title': 'Local fixture title'}

    with loader.activate():
        lookup = cached_isbn_lookup(out, local_lookup, source_ids=lambda: ['allowed'])
        assert lookup('123') == {'title': 'Local fixture title'}
        replay = cached_isbn_lookup(out, lambda *args: never_read(), dry_run=True)
        assert replay('123') == {'title': 'Local fixture title'}
    assert calls == ['123']
    assert read_json(out / 'lookups' / f'{digest("123")}.json')['source_work_ids'] == ['allowed']
    assert tuning_record(loader)['loaded_ids'] == ['allowed']


@pytest.mark.parametrize('sources', [None, ['denied']])
def test_uncached_isbn_lookup_checks_source_before_callback(loader, sources):
    from scripts.extraction_eval.runner import cached_isbn_lookup

    callback = (lambda: sources) if sources is not None else None
    with loader.activate(), pytest.raises(TuningRefused):
        cached_isbn_lookup(loader.path.parent, lambda *args: never_read(), source_ids=callback)('123')
    assert tuning_record(loader)['loaded_ids'] == []
    assert tuning_record(loader)['refused_attempts'][-1]['kind'] == 'isbn_lookup'


def test_existing_predictions_without_run_record_refuse_resume(tmp_path, freeze_dir):
    out = tmp_path / 'orphaned-run'
    atomic_json(out / 'predictions.json', {'books': {'allowed': {'text': 'prior content'}}})
    with pytest.raises(ValueError, match='unresolved'):
        TuningLoader(out, freeze_dir)
    assert not (out / 'run.json').exists()


def test_fresh_cli_import_does_not_open_prompt_examples():
    code = '''
from pathlib import Path
original = Path.open
def guarded(path, *args, **kwargs):
    if path.name == 'pipeline_examples.json':
        raise AssertionError('Prompt examples loaded before a tuning run started')
    return original(path, *args, **kwargs)
Path.open = guarded
import scripts.extraction_eval.cli
'''
    result = subprocess.run([sys.executable, '-c', code],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_snapshot_audit_skips_embedded_content_and_keeps_folder_guard(loader, tmp_path, monkeypatch):
    path = write_snapshot(tmp_path, [book('allowed', content='usable reference'), book('denied')])
    manifest = read_json(path / 'manifest.json')
    manifest['audit'] = [
        {'issue': 'invalid_page_number', 'book_id': 'denied',
         'document': {'text': 'FORBIDDEN CONTENT'}},
        {'issue': 'ambiguous_book_folder', 'book_ids': ['allowed', 'denied'],
         'folder': 'shared-folder'},
    ]
    atomic_json(path / 'manifest.json', manifest)
    forbid_marker_decode(monkeypatch)
    loaded = loader.snapshot(path)
    assert loaded['audit'] == [
        {'issue': 'invalid_page_number', 'book_id': 'denied'},
        {'issue': 'ambiguous_book_folder', 'book_ids': ['allowed', 'denied'],
         'folder': 'shared-folder'},
    ]
    assert tuning_record(loader)['loaded_ids'] == ['allowed']


def test_missing_snapshot_source_id_refusal_is_recorded(loader, tmp_path, monkeypatch):
    path = write_snapshot(tmp_path, [{'title': 'Unresolved source',
                                     'reference': {'text': 'FORBIDDEN CONTENT'}}])
    forbid_marker_decode(monkeypatch)
    with pytest.raises(TuningRefused):
        loader.snapshot(path)
    record = tuning_record(loader)
    assert record['loaded_ids'] == []
    assert record['refused_attempts'][-1]['source_ids'] == [None]
    assert record['refused_attempts'][-1]['kind'] == 'snapshot_source'


def test_historical_label_record_refused_before_content_decode(loader, tmp_path, monkeypatch):
    path = tmp_path / 'historical-labels.json'
    atomic_json(path, {'records': [{'book_id': 'denied', 'text': 'FORBIDDEN CONTENT',
                                   'speech_label': True}]})
    forbid_marker_decode(monkeypatch)
    with pytest.raises(TuningRefused):
        loader.source_json(path, 'historical_labels')
    record = tuning_record(loader)
    assert record['loaded_ids'] == []
    assert record['refused_attempts'][-1]['source_ids'] == ['denied']
    assert record['refused_attempts'][-1]['kind'] == 'historical_labels'


def test_declared_sources_cannot_hide_excluded_historical_record(loader, tmp_path, monkeypatch):
    path = tmp_path / 'labels.json'
    atomic_json(path, {'source_work_ids': ['allowed'],
                      'records': [{'book_id': 'denied', 'label': 'FORBIDDEN CONTENT'}]})
    forbid_marker_decode(monkeypatch)
    with pytest.raises(TuningRefused):
        loader.source_json(path, 'historical_labels')
    assert tuning_record(loader)['loaded_ids'] == []


def test_runner_refuses_unknown_selection_missing_from_manifest(loader, tmp_path):
    from scripts.extraction_eval.runner import run_extract

    with pytest.raises(TuningRefused):
        run_extract(tmp_path, {'books': [book('allowed', content='usable')]},
                    {'book_ids': ['unknown']}, loader.path.parent, SimpleNamespace(), {},
                    loader=loader, core=SimpleNamespace(), progress=lambda *args: None)
    assert tuning_record(loader)['refused_attempts'][-1]['source_ids'] == ['unknown']


def test_known_id_absent_from_snapshot_is_recorded_as_refused(loader, tmp_path):
    path = write_snapshot(tmp_path, [book('denied')])
    with pytest.raises(TuningRefused):
        loader.snapshot(path, ['allowed'])
    assert tuning_record(loader)['loaded_ids'] == []
    assert tuning_record(loader)['refused_attempts'][-1]['source_ids'] == ['allowed']
