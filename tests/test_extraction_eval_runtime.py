"""Budget, replay, and runner checks using local fixtures and fake providers."""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts.extraction_eval.gateway import (
    DryRunMiss, Gateway, RequestLimitError, request_ceiling, validate_prices,
)
from scripts.extraction_eval.runner import (
    cached_isbn_lookup, estimate, freeze_run, run_extract, validate_inputs,
)
from scripts.extraction_eval.store import (
    BudgetStopped, CachedFailure, Ledger, OutcomeUnknown, atomic_json, digest, read_json,
)


@pytest.fixture
def config():
    return {
        'request_version': '1', 'parser_version': 'eval-v1', 'max_attempts': 1,
        'settings': {'extraction_model': 'fake-model'},
        'pricing': {'verified': True, 'verified_at': 'fixture', 'source': 'fixture',
                    'models': {'fake-model': {
                        'input': 1, 'output': 2, 'cache_read': .5, 'cache_write': 1.25,
                        'max_input_tokens': 100, 'max_output_tokens': 100,
                        'max_images': 3, 'max_request_bytes': 10000,
                    }}},
    }


@pytest.fixture
def cli_config(config):
    from utilities import AI_SETTINGS_DEFAULTS

    config['settings'] = {
        key: 'fake-model' if key.endswith('_model') else value
        for key, value in AI_SETTINGS_DEFAULTS.items()
    }
    return config


@pytest.fixture
def ledger_factory(tmp_path):
    ledgers = []

    def create(run='run', cap=1, monthly=2):
        ledger = Ledger(tmp_path / 'ledger.sqlite', run, cap, monthly, month='2026-11')
        ledgers.append(ledger)
        return ledger

    yield create
    for ledger in ledgers:
        ledger.close()


def request(text='hello'):
    return {'model': 'fake-model', 'max_tokens': 10,
            'messages': [{'role': 'user', 'content': text}]}


def response(**changes):
    return {'id': 'fixture-response', 'content': [{'type': 'text', 'text': 'not-json'}],
            'stop_reason': 'end_turn', 'usage': {'input_tokens': 10, 'output_tokens': 5},
            **changes}


class FakeProvider:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = next(self.replies)
        if isinstance(value, BaseException):
            raise value
        return value


def test_reservation_exact_cap_and_shared_monthly_limit(ledger_factory):
    first = ledger_factory(cap=.1, monthly=.1)
    first.reserve('a', {}, Decimal('.1'))
    assert first.get('a')['reservation'] == 100_000_000
    with pytest.raises(BudgetStopped):
        first.reserve('b', {}, Decimal('.000000001'))
    second = ledger_factory(run='other', cap=.1, monthly=.1)
    with pytest.raises(BudgetStopped):
        second.reserve('c', {}, Decimal('.000000001'))
    assert first.get('b') is None
    assert second.get('c') is None


def test_run_and_month_caps_cannot_be_increased_on_resume(ledger_factory):
    ledger_factory(cap=.1, monthly=.2)
    with pytest.raises(ValueError, match='immutable'):
        ledger_factory(cap=.2, monthly=.2)
    with pytest.raises(ValueError, match='immutable'):
        ledger_factory(run='other', cap=.1, monthly=.3)


def test_free_cache_hit_does_not_charge_again_and_raw_is_retained(config, ledger_factory):
    ledger = ledger_factory()
    provider = FakeProvider(response())
    gateway = Gateway(provider, ledger, config)
    first = gateway.create(**request())
    gateway.record_usage(first, 'fake-model', 'ocr')
    row = ledger.get(first._eval_key)
    assert json.loads(row['response'])['content'][0]['text'] == 'not-json'
    assert row['cost'] == 20_000
    assert row['reservation'] == 0
    again = gateway.create(**request())
    gateway.record_usage(again, 'fake-model', 'ocr')
    assert again._eval_cached is True
    assert len(provider.calls) == 1
    assert len(ledger.rows()) == 1
    assert ledger.get(first._eval_key)['cost'] == row['cost']
    assert gateway.cache_hits == 1


def test_changed_content_replicate_and_request_version_invalidate_requests(config, ledger_factory):
    ledger = ledger_factory()
    provider = FakeProvider(*(response() for _ in range(4)))
    baseline = Gateway(provider, ledger, config)
    original = baseline.create(**request())
    changed = baseline.create(**request('changed prompt'))
    replicate = Gateway(provider, ledger, config, replicate='2').create(**request())
    updated = deepcopy(config)
    updated['request_version'] = '2'
    version = Gateway(provider, ledger, updated).create(**request())
    assert len({r._eval_key for r in [original, changed, replicate, version]}) == 4
    parser = deepcopy(config)
    parser['parser_version'] = 'eval-v2'
    replay = Gateway(provider, ledger, parser).create(**request())
    assert replay._eval_cached is True
    assert len(provider.calls) == 4


@pytest.mark.parametrize('provider_reply', [TimeoutError('lost response'), response(usage=None),
                                          response(usage={'input_tokens': -1, 'output_tokens': 0})])
def test_unknown_outcome_retains_reservation_and_never_repeats(provider_reply, config, ledger_factory):
    ledger = ledger_factory()
    provider = FakeProvider(provider_reply)
    gateway = Gateway(provider, ledger, config)
    with pytest.raises(OutcomeUnknown):
        gateway.create(**request())
    assert ledger.rows()[0]['state'] == 'outcome_unknown'
    assert ledger.rows()[0]['reserved_usd'] > 0
    with pytest.raises(OutcomeUnknown):
        gateway.create(**request())
    assert len(provider.calls) == 1


def test_interrupted_dispatch_is_not_repaid(config, ledger_factory):
    ledger = ledger_factory()
    identity = {'request_version': '1', 'replicate': '1',
                'provider': 'anthropic-messages', 'request': request()}
    key = digest({**identity, 'attempt': 0})
    ledger.reserve(key, identity, request_ceiling(request(), validate_prices(config)))
    ledger.recover()
    provider = FakeProvider()
    with pytest.raises(OutcomeUnknown):
        Gateway(provider, ledger, config).create(**request())
    assert ledger.get(key)['state'] == 'outcome_unknown'
    assert provider.calls == []


def test_only_definite_rate_limit_rejections_retry(config, ledger_factory):
    class Rejected(RuntimeError):
        status_code = 429

    config['max_attempts'] = 2
    ledger = ledger_factory()
    provider = FakeProvider(Rejected('rate limit'), response())
    result = Gateway(provider, ledger, config).create(**request())
    assert result.id == 'fixture-response'
    assert len(provider.calls) == 2
    rows = ledger.rows()
    assert {row['state'] for row in rows} == {'retryable', 'completed'}
    assert sum(row['reserved_usd'] for row in rows) == 0


def test_permanent_rejection_is_cached(config, ledger_factory):
    class Rejected(RuntimeError):
        status_code = 400

    provider = FakeProvider(Rejected('bad input'))
    ledger = ledger_factory()
    gateway = Gateway(provider, ledger, config)
    for _ in range(2):
        with pytest.raises(CachedFailure):
            gateway.create(**request())
    assert len(provider.calls) == 1
    assert ledger.rows()[0]['reserved_usd'] == 0


def test_provider_limits_fail_before_dispatch(config, ledger_factory):
    provider = FakeProvider()
    ledger = ledger_factory()
    gateway = Gateway(provider, ledger, config)
    oversized = request()
    oversized['messages'][0]['content'] = [{'type': 'image'}] * 4
    with pytest.raises(RequestLimitError, match='images'):
        gateway.create(**oversized)
    with pytest.raises(RequestLimitError, match='Unpriced'):
        gateway.create(**request(), tools=[])
    with pytest.raises(ValueError, match='Unknown'):
        gateway.create(**{**request(), 'model': 'unknown'})
    assert provider.calls == []
    assert ledger.rows() == []


def test_dry_run_never_calls_provider_or_reserves(config, ledger_factory):
    provider = FakeProvider()
    ledger = ledger_factory()
    with pytest.raises(DryRunMiss):
        Gateway(provider, ledger, config, dry_run=True).create(**request())
    assert provider.calls == []
    assert ledger.rows() == []


@pytest.mark.parametrize('raw', [object(), response(extra=object()), response(extra=float('nan'))])
def test_unserializable_paid_response_is_unknown(raw, config, ledger_factory):
    ledger = ledger_factory()
    gateway = Gateway(FakeProvider(raw), ledger, config)
    with pytest.raises(OutcomeUnknown):
        gateway.create(**request())
    assert ledger.rows()[0]['state'] == 'outcome_unknown'
    assert ledger.rows()[0]['reserved_usd'] > 0


def test_usage_over_ceiling_retains_at_least_known_charge(config, ledger_factory):
    ledger = ledger_factory()
    gateway = Gateway(FakeProvider(response(usage={'input_tokens': 200, 'output_tokens': 0})), ledger, config)
    with pytest.raises(OutcomeUnknown):
        gateway.create(**request())
    row = ledger.rows()[0]
    assert row['state'] == 'outcome_unknown'
    assert row['cost_usd'] + row['reserved_usd'] >= .0002


@pytest.fixture
def snapshot_fixture(tmp_path):
    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    books = []
    for book_id in ('selected', 'heldout'):
        pages = []
        for page_number in (3, 7):
            data = f'{book_id}:{page_number}'.encode()
            name = f'{book_id}_{page_number}.jpg'
            (snapshot / name).write_bytes(data)
            pages.append({'id': name, 'page_number': page_number, 'text': 'GOLD MUST NOT LEAK',
                          'contains_story': False, 'image': {'path': name,
                           'sha256': hashlib.sha256(data).hexdigest()}})
        books.append({'id': book_id, 'title': book_id, 'pages': pages})
    return snapshot, {'snapshot_id': 'fixture', 'books': books}, {'book_ids': ['selected']}


class FakeCore:
    def __init__(self, paid=False):
        self.calls = []
        self.paid = paid

    def extract_metadata(self, pages, client, settings, sink, isbn):
        self.calls.append(('metadata', pages))
        if self.paid:
            client.messages.create(**request('metadata'))
        return {'title': 'Predicted title'}

    def extract_page(self, data, client, settings, sink):
        self.calls.append(('page', data))
        if self.paid:
            client.messages.create(**request(data.decode()))
        return {'status': 'completed', 'text': f'Predicted {data.decode()}', 'contains_story': True}

    def extract_characters(self, pages, client, settings, sink):
        self.calls.append(('characters', pages))
        return [{'name': 'Predicted character'}]


def test_runner_subset_resume_and_predicted_character_inputs(snapshot_fixture, tmp_path, config):
    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'run'
    out.mkdir()
    core = FakeCore()
    gateway = SimpleNamespace(record_usage=lambda *args: None)
    def run():
        return run_extract(snapshot, manifest, selection, out, gateway, config,
                           loader=None, core=core, isbn_lookup=lambda isbn: None, progress=lambda *args: None)
    result = run()
    assert list(result['books']) == ['selected']
    assert result['incomplete'] is False
    assert len(core.calls) == 4
    metadata_pages = core.calls[0][1]
    assert [number for number, data in metadata_pages] == [3, 7]
    predicted = core.calls[-1][1]
    assert all(page['text'].startswith('Predicted selected:') for page in predicted)
    assert all(page['contains_story'] is True for page in predicted)
    assert 'GOLD MUST NOT LEAK' not in json.dumps(predicted)
    assert run() == result
    assert len(core.calls) == 4
    assert estimate(manifest, selection, result)['estimated_usd'] == 0


def test_runner_dry_run_no_provider_calls(snapshot_fixture, tmp_path, config, ledger_factory):
    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'dry-run'
    out.mkdir()
    core = FakeCore(paid=True)
    provider = FakeProvider()
    ledger = ledger_factory()
    gateway = Gateway(provider, ledger, config, dry_run=True)
    result = run_extract(snapshot, manifest, selection, out, gateway, config,
                         dry_run=True, loader=None, core=core, isbn_lookup=lambda isbn: None,
                         progress=lambda *args: None)
    assert result['incomplete'] is True
    assert provider.calls == []
    assert ledger.rows() == []
    assert (out / 'dry_run_predictions.json').exists()
    assert not (out / 'predictions.json').exists()


def test_runner_budget_stop_preserves_incomplete_denominator(snapshot_fixture, tmp_path, config, ledger_factory):
    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'stopped'
    out.mkdir()
    core = FakeCore(paid=True)
    provider = FakeProvider()
    gateway = Gateway(provider, ledger_factory(cap=0), config)
    result = run_extract(snapshot, manifest, selection, out, gateway, config,
                         loader=None, core=core, isbn_lookup=lambda isbn: None, progress=lambda *args: None)
    book = result['books']['selected']
    assert result['incomplete'] is True
    assert len(book['pages']) == 2
    assert all(page['status'] == 'budget_stopped' for page in book['pages'].values())
    assert book['characters']['status'] == 'budget_stopped'
    assert provider.calls == []


def test_snapshot_integrity_is_verified_before_extraction(snapshot_fixture):
    snapshot, manifest, selection = snapshot_fixture
    validate_inputs(snapshot, manifest, selection, loader=None)
    path = snapshot / manifest['books'][0]['pages'][0]['image']['path']
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        validate_inputs(snapshot, manifest, selection, loader=None)


def test_isbn_genuine_absence_is_cached_and_dry_run_never_looks_up(tmp_path):
    calls = []
    def provider(isbn, *, response_sink, outcome_sink):
        calls.append(isbn)
        response_sink({'totalItems': 0})
        outcome_sink({'status': 'not_found', 'attempts': 1})
        return None
    lookup = cached_isbn_lookup(tmp_path, provider)
    outcomes = []
    assert lookup('123', outcome_sink=outcomes.append) is None
    assert lookup('123') is None
    assert calls == ['123']
    assert outcomes == [{'status': 'not_found', 'attempts': 1}]
    saved = json.loads((tmp_path / 'lookups' / f'{digest("123")}.json').read_text())
    assert saved['raw_response'] == {'totalItems': 0}
    dry = cached_isbn_lookup(tmp_path, lambda isbn: pytest.fail('live lookup'), dry_run=True)
    assert dry('123', outcome_sink=outcomes.append) is None
    assert outcomes[0] == outcomes[1]
    with pytest.raises(DryRunMiss):
        dry('456')


def test_isbn_failed_lookup_is_not_cached_and_next_call_retries(tmp_path):
    calls, outcomes = [], []
    def provider(isbn, *, response_sink, outcome_sink):
        calls.append(isbn)
        if len(calls) == 1:
            outcome_sink({'status': 'failed', 'reason': 'http_error', 'http_status': 429})
            return None
        response_sink({'totalItems': 1})
        outcome_sink({'status': 'found'})
        return {'title': 'Recovered'}
    lookup = cached_isbn_lookup(tmp_path, provider)
    path = tmp_path / 'lookups' / f'{digest("123")}.json'
    assert lookup('123', outcome_sink=outcomes.append) is None
    assert not path.exists()
    assert outcomes[0]['status'] == 'failed'
    assert outcomes[0]['http_status'] == 429
    assert lookup('123') == {'title': 'Recovered'}
    assert path.exists()
    assert lookup('123') == {'title': 'Recovered'}
    assert calls == ['123', '123']


def test_legacy_failed_isbn_cache_is_ignored_but_not_changed_during_dry_run(tmp_path):
    path = tmp_path / 'lookups' / f'{digest("123")}.json'
    poisoned = {'isbn': '123', 'value': None, 'raw_response': None}
    atomic_json(path, poisoned)
    dry = cached_isbn_lookup(tmp_path, lambda isbn: pytest.fail('live lookup'), dry_run=True)
    with pytest.raises(DryRunMiss):
        dry('123')
    assert json.loads(path.read_text()) == poisoned
    lookup = cached_isbn_lookup(tmp_path, lambda isbn: {'title': 'Recovered'})
    assert lookup('123') == {'title': 'Recovered'}
    assert json.loads(path.read_text())['value'] == {'title': 'Recovered'}


@pytest.mark.parametrize('capture_raw', [False, True])
def test_one_argument_isbn_lookup_without_answer_is_not_assumed_absent(tmp_path, capture_raw):
    calls, outcomes = [], []
    lookup = cached_isbn_lookup(tmp_path, lambda isbn: calls.append(isbn), capture_raw=capture_raw)
    assert lookup('123', outcome_sink=outcomes.append) is None
    assert lookup('123') is None
    assert calls == ['123', '123']
    assert outcomes[0]['status'] == 'failed'
    assert not (tmp_path / 'lookups').exists()


def test_isbn_lookup_with_opaque_signature_retains_single_argument_contract(tmp_path):
    class Provider:
        @property
        def __signature__(self):
            raise ValueError('No signature available')

        def __call__(self, isbn):
            return {'title': isbn}

    lookup = cached_isbn_lookup(tmp_path, Provider(), capture_raw=True)
    assert lookup('123') == {'title': '123'}


@pytest.mark.parametrize('legacy', [False, True])
def test_resume_retries_isbn_failure_without_repeating_vision(snapshot_fixture, tmp_path, config, legacy):
    from scripts.extraction_eval.runner import Stages

    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'run'
    out.mkdir()
    value = {'title': 'Vision title', 'authors': [], 'publisher': None, 'isbn': '123',
             'sources': {'title': {'source': 'title_page'}}}
    if legacy:
        atomic_json(out / 'lookups' / f'{digest("123")}.json',
                    {'isbn': '123', 'value': None, 'raw_response': None})
    else:
        value.update(isbn_lookup={'status': 'failed', 'reason': 'http_error'},
                     lookup_failed_fields=['authors', 'publisher'])
    stages = Stages(out)
    stages.put('selected:metadata', {'status': 'completed', 'value': value})
    stages.close()
    core, calls = FakeCore(), []
    def provider(isbn):
        calls.append(isbn)
        return {'authors': ['Recovered author'], 'publisher': 'Recovered publisher'}
    result = run_extract(snapshot, manifest, selection, out,
                         SimpleNamespace(record_usage=lambda *args: None), config,
                         loader=None, core=core, isbn_lookup=provider, progress=lambda *args: None)
    metadata = result['books']['selected']['metadata']['value']
    assert calls == ['123']
    assert all(operation != 'metadata' for operation, _ in core.calls)
    assert metadata['title'] == 'Vision title'
    assert metadata['authors'] == ['Recovered author']
    assert metadata['publisher'] == 'Recovered publisher'
    assert metadata['isbn_lookup']['status'] == 'found'
    assert metadata['lookup_failed_fields'] == []


def test_run_contract_preserves_initial_tuning_record(snapshot_fixture, tmp_path, config):
    _, manifest, selection = snapshot_fixture
    out = tmp_path / 'frozen'
    tuning = {'loaded_work_ids': ['selected'], 'refused_attempts': []}
    atomic_json(out / 'run.json', {'tuning': tuning})
    record = freeze_run(out, manifest, selection, config, '1', 16, 200,
                        tmp_path / 'shared.sqlite')
    assert record['tuning'] == tuning
    assert read_json(out / 'run.json')['tuning'] == tuning


def test_run_contract_rejects_changed_inputs(snapshot_fixture, tmp_path, config):
    _, manifest, selection = snapshot_fixture
    out = tmp_path / 'frozen'
    ledger = tmp_path / 'shared.sqlite'
    first = freeze_run(out, manifest, selection, config, '1', 16, 200, ledger)
    assert freeze_run(out, manifest, selection, config, '1', 16, 200, ledger) == first
    changed = deepcopy(config)
    changed['parser_version'] = 'new-parser'
    with pytest.raises(ValueError, match='changed'):
        freeze_run(out, manifest, selection, changed, '1', 16, 200, ledger)


def test_cli_dry_run_does_not_construct_clients_or_read_secrets(snapshot_fixture, tmp_path, cli_config, monkeypatch):
    from scripts.extraction_eval import cli
    import extraction_core
    import utilities

    snapshot, manifest, _ = snapshot_fixture
    manifest.update(complete=True, source_consistent=True)
    atomic_json(snapshot / 'manifest.json', manifest)
    config_path = tmp_path / 'config.json'
    atomic_json(config_path, cli_config)
    fake = FakeCore(paid=True)
    for operation in ('extract_page', 'extract_metadata', 'extract_characters'):
        monkeypatch.setattr(extraction_core, operation, getattr(fake, operation))

    def forbidden(*args, **kwargs):
        pytest.fail('Dry run attempted service/client/credential access')

    monkeypatch.setattr(cli, '_live_anthropic', forbidden)
    monkeypatch.setattr(utilities, 'lookup_isbn', forbidden)
    monkeypatch.setattr(utilities, 'get_s3_filesystem', forbidden)
    monkeypatch.setattr(utilities, 'FirestoreWrapper', forbidden)
    monkeypatch.setattr('builtins.input', forbidden)
    assert cli.main(['extract', '--original-experiment', '--snapshot', str(snapshot), '--out', str(tmp_path / 'cli-run'),
                     '--config', str(config_path), '--limit', '1', '--dry-run']) == 0
    assert len(fake.calls) == 3  # metadata and two OCR requests; all cache misses


def test_cli_refused_confirmation_never_constructs_client(snapshot_fixture, tmp_path, cli_config, monkeypatch):
    from scripts.extraction_eval import cli

    snapshot, manifest, _ = snapshot_fixture
    manifest.update(complete=True, source_consistent=True)
    atomic_json(snapshot / 'manifest.json', manifest)
    config_path = tmp_path / 'config.json'
    atomic_json(config_path, cli_config)
    out = tmp_path / 'refused'
    monkeypatch.setattr('builtins.input', lambda prompt: 'yes')
    monkeypatch.setattr(cli, '_live_anthropic', lambda *args: pytest.fail('Unauthorized client'))
    assert cli.main(['extract', '--original-experiment', '--snapshot', str(snapshot), '--out', str(out),
                     '--config', str(config_path), '--limit', '1', '--execute']) == 1
    assert not (out / 'run.json').exists()
