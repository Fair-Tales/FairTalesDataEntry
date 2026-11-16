"""Tests may use in-memory fakes and the local Streamlit server only."""
import hashlib
import ipaddress
import json
import socket

import pytest


def load_dev20_predictions():
    """Load the unchanged dev20 evidence kept outside the public repository."""
    from scripts.extraction_eval.evidence import evidence_dir

    path = evidence_dir() / 'dev20-run' / 'predictions.json'
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        pytest.skip(f'Dev20 evidence file is absent: {path}')
    expected = '7976646dec39ab8798684894d0db64a3cf2e30affdd4e185ec6e4a2e807199cf'
    if hashlib.sha256(data).hexdigest() != expected:
        pytest.fail(f'Dev20 evidence SHA-256 mismatch: {path}')
    return json.loads(data)


@pytest.fixture(autouse=True)
def no_live_services(monkeypatch):
    """Fail before any test can open a live Firestore, S3, or AI connection."""
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def check(address):
        if not isinstance(address, tuple):  # local Unix-domain sockets
            return
        host = address[0]
        if host == 'localhost':
            return
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local:
            raise AssertionError(f'Live network access is forbidden in tests: {host}')

    def local_connect(sock, address):
        check(address)
        return connect(sock, address)

    def local_connect_ex(sock, address):
        check(address)
        return connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', local_connect_ex)


@pytest.fixture
def cohort_loader_factory(tmp_path, monkeypatch):
    """Real loader with synthetic ids; only fixed production cohort sizes vary."""
    from scripts.extraction_eval import cli, freeze, tuning
    from scripts.extraction_eval.store import atomic_json, digest

    monkeypatch.setattr(freeze, 'SELECTION_COUNTS', {})
    serial = 0

    def make(out=None, *, iterate=('selected', 'book', 'a', 'b'), check=('check',),
             protected=('protected',), holdout=('heldout', 'denied'), cohort='tune'):
        nonlocal serial
        serial += 1
        directory = tmp_path / f'cohort-fixture-{serial}'
        ids_path = directory / 'holdout_ids.json'
        atomic_json(ids_path, list(holdout))
        tune = sorted(set(iterate) | set(check))
        atomic_json(directory / 'freeze.json', {
            'imported80_ids': list(holdout), 'protected40_ids': list(protected),
            'tune41_ids': tune, 'tune81_ids': sorted(set(tune) | set(protected)),
            'imported80_ids_sha256': digest(sorted(holdout)),
            'holdout_ids_file_sha256': hashlib.sha256(ids_path.read_bytes()).hexdigest(),
        })
        selections = {
            'imported80_ids': list(holdout), 'protected40_ids': list(protected),
            'tune41_ids': tune, 'exposed34_ids': [], 'check20_ids': list(check),
            'iterate55_ids': list(iterate), 'example_source_ids': [],
            'heldout_author_shared_ids': [], 'heldout_alias_quoted_ids': [],
            'imported80_ids_sha256': digest(sorted(holdout)),
        }
        selections['book_strata'] = dict.fromkeys(
            set(tune) | set(protected) | set(holdout), 'alias_zero')
        selections['counts'] = {field: len(selections[field]) for field in freeze.SELECTION_FIELDS}
        selections_path = directory / 'selections.json'
        atomic_json(selections_path, selections)
        monkeypatch.setattr(cli, 'DEFAULT_FREEZE_DIR', directory)
        monkeypatch.setattr(tuning, 'SELECTIONS_PATH', selections_path)
        original = freeze.load_selections
        monkeypatch.setattr(tuning, 'load_selections',
                            lambda path=None: original(path or selections_path))
        return tuning.TuningLoader(out or directory / 'run', directory,
                                   cohort=cohort, selections_path=selections_path)

    return make


@pytest.fixture
def synthetic_cohort(cohort_loader_factory):
    """Active admission for the named synthetic books in integration fixtures."""
    ids = ['selected', 'book', 'a', 'b', 'b1', 'book-1', 'fixture']
    ids += [f'book-{index:02}' for index in range(25)]
    loader = cohort_loader_factory(iterate=ids)
    with loader.activate():
        yield loader
