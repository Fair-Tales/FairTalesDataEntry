"""Offline batch submission, accounting and replay with synthetic SDK fakes."""
# ruff: noqa: F401, F811 - pytest fixtures are imported from sibling test modules and used as parameters
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import Mock
import json

import pytest

from scripts.extraction_eval import batches, cli
from scripts.extraction_eval.gateway import DryRunMiss, Gateway, request_ceiling, usage_charge
from scripts.extraction_eval.runner import run_extract
from scripts.extraction_eval.store import BudgetStopped, OutcomeUnknown, read_json
from scripts.extraction_eval.tuning import TuningRefused
from test_extraction_eval_runtime import (
    config, ledger_factory, request, response, FakeProvider, FakeCore,
    snapshot_fixture, example_pack,
)
from test_extraction_eval_cli import (
    offline_inputs, fake_core, system_temporary_dir, forbidden,
)


class FakeBatchClient:
    def __init__(self, statuses=('succeeded',), *, pending=False):
        self.messages = NS(batches=self)
        self.statuses = statuses
        self.pending = pending
        self.submissions = []
        self.interrupt = False

    def create(self, *, requests):
        self.submissions.append(requests)
        return NS(id='batch-fixture', processing_status='in_progress')

    def retrieve(self, batch_id):
        assert batch_id == 'batch-fixture'
        return NS(processing_status='in_progress' if self.pending else 'ended')

    def results(self, batch_id):
        assert batch_id == 'batch-fixture'
        # Deliberately reverse results: arrival order is not request identity.
        for row, status in reversed(list(zip(self.submissions[0], self.statuses))):
            yield NS(custom_id=row['custom_id'], result=NS(type=status, message=response()))
            if self.interrupt:
                raise OSError('interrupted download')

    def close(self):
        pass


def export(out, ledger, config, count=1):
    out.mkdir(exist_ok=True)
    gateway = Gateway(None, ledger, config, dry_run=True,
                      requests_path=out / 'batch-requests.jsonl')
    gateway.context = {'book_id': 'selected', 'stage': 'characters'}
    for i in range(count):
        with pytest.raises(DryRunMiss):
            gateway.create(**request(str(i)))
    return [json.loads(line) for line in gateway.requests_path.read_text().splitlines()]


def test_batch_uses_exact_sync_identity_and_half_price(tmp_path, config, ledger_factory):
    ledger = ledger_factory()
    rows = export(tmp_path, ledger, config)
    key = rows[0]['key']
    client = FakeBatchClient()
    result = batches.run_batch(tmp_path, ledger, config, client)
    sync = Gateway(FakeProvider(), ledger, config)
    reply = sync.create(**request('0'))
    assert reply._eval_key == key
    assert sync.paid_calls == 0 and sync.cache_hits == 1
    assert client.submissions == [[{'custom_id': 'request-0', 'params': request('0')}]]
    assert result['status'] == 'ended'
    row = ledger.rows()[0]
    assert row['batch_id'] == 'batch-fixture'
    assert row['reserved_usd'] == 0
    assert Decimal(str(row['cost_usd'])) == usage_charge(response(), config['pricing']['models']['fake-model']) / 2
    # Independently produce the synchronous key in a different ledger.
    from scripts.extraction_eval.store import Ledger
    other = Ledger(tmp_path / 'sync.sqlite', 'sync', 1, budgets=ledger.budgets)
    try:
        fresh = Gateway(FakeProvider(response()), other, config).create(**request('0'))
        assert fresh._eval_key == key
    finally:
        other.close()


def test_all_token_categories_receive_discount(config):
    raw = response(usage={'input_tokens': 100, 'output_tokens': 10,
                         'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 40})
    rate = config['pricing']['models']['fake-model']
    assert usage_charge(raw, rate, batch=True) == usage_charge(raw, rate) / 2


@pytest.mark.parametrize('scope', ['run', 'cycle'])
def test_batch_reservation_is_all_or_nothing(tmp_path, config, ledger_factory, scope):
    one = float(request_ceiling(request(), config['pricing']['models']) / 2)
    ledger = ledger_factory(cap=one * 1.5 if scope == 'run' else 1,
                            cycle_cap=one * 1.5 if scope == 'cycle' else 2)
    export(tmp_path, ledger, config, 2)
    client = FakeBatchClient()
    with pytest.raises(BudgetStopped):
        batches.run_batch(tmp_path, ledger, config, client)
    assert not client.submissions and not ledger.rows()
    assert not (tmp_path / 'batch.json').exists()


def test_pending_reserves_whole_batch_and_resume_never_submits(tmp_path, config, ledger_factory):
    ledger = ledger_factory()
    export(tmp_path, ledger, config, 2)
    client = FakeBatchClient(('succeeded', 'succeeded'), pending=True)
    batches.run_batch(tmp_path, ledger, config, client)
    expected = float(request_ceiling(request(), config['pricing']['models']) / 2)
    assert [row['reserved_usd'] for row in ledger.rows()] == [expected, expected]
    with pytest.raises(ValueError, match='pending'):
        batches.run_batch(tmp_path, ledger, config, client)
    # Model a new CLI invocation, including synchronous crash recovery.
    ledger.recover()
    reopened = ledger_factory()
    client.pending = False
    batches.run_batch(tmp_path, reopened, config, client, resume=True)
    batches.run_batch(tmp_path, reopened, config, client, resume=True)
    assert len(client.submissions) == 1
    assert all(row['reserved_usd'] == 0 for row in reopened.rows())


def test_non_success_releases_and_can_run_synchronously(tmp_path, config, ledger_factory):
    ledger = ledger_factory()
    exported = export(tmp_path, ledger, config, 4)
    client = FakeBatchClient(('succeeded', 'errored', 'expired', 'canceled'))
    state = batches.run_batch(tmp_path, ledger, config, client)
    assert len(ledger.rows()) == 1
    assert set(state['results'].values()) == {'succeeded', 'errored', 'expired', 'canceled'}
    for row in exported[1:]:
        assert ledger.get(row['key']) is None
    sync = Gateway(FakeProvider(response(), response(), response()), ledger, config)
    for i in range(1, 4):
        sync.create(**request(str(i)))
    assert sync.paid_calls == 3


def test_partial_collection_is_idempotent(tmp_path, config, ledger_factory):
    ledger = ledger_factory()
    export(tmp_path, ledger, config, 2)
    client = FakeBatchClient(('succeeded', 'succeeded'))
    client.interrupt = True
    with pytest.raises(OSError):
        batches.run_batch(tmp_path, ledger, config, client)
    assert sum(row['state'] == 'completed' for row in ledger.rows()) == 1
    client.interrupt = False
    state = batches.run_batch(tmp_path, ledger, config, client, resume=True)
    assert state['status'] == 'ended' and len(client.submissions) == 1
    assert sum(row['cost_usd'] for row in ledger.rows()) == .00002


def test_uncertain_submission_retains_reservations(tmp_path, config, ledger_factory):
    ledger = ledger_factory()
    export(tmp_path, ledger, config)
    client = FakeBatchClient()
    client.create = Mock(side_effect=TimeoutError('lost creation response'))
    with pytest.raises(TimeoutError):
        batches.run_batch(tmp_path, ledger, config, client)
    assert ledger.rows()[0]['reserved_usd'] > 0
    with pytest.raises(ValueError, match='pending'):
        batches.run_batch(tmp_path, ledger, config, client)
    with pytest.raises(ValueError, match='reconcile'):
        batches.run_batch(tmp_path, ledger, config, client, resume=True)
    assert client.create.call_count == 1


def test_rejected_submission_releases(tmp_path, config, ledger_factory):
    ledger = ledger_factory()
    export(tmp_path, ledger, config)
    error = ValueError('bad request')
    error.status_code = 400
    client = FakeBatchClient()
    client.create = Mock(side_effect=error)
    with pytest.raises(ValueError, match='bad request'):
        batches.run_batch(tmp_path, ledger, config, client)
    assert ledger.rows() == []


def test_missing_usage_retains_reservation(tmp_path, config, ledger_factory):
    ledger = ledger_factory()
    export(tmp_path, ledger, config)
    client = FakeBatchClient()
    client.results = lambda _: [NS(custom_id='request-0', result=NS(
        type='succeeded', message=response(usage=None)))]
    with pytest.raises(OutcomeUnknown):
        batches.run_batch(tmp_path, ledger, config, client)
    row = ledger.rows()[0]
    assert row['reserved_usd'] > 0 and row['state'] != 'completed'


def test_export_is_cohort_guarded_before_decode(tmp_path, config, ledger_factory, cohort_loader_factory, monkeypatch):
    ledger = ledger_factory()
    out = tmp_path / 'run'
    loader = cohort_loader_factory(out, iterate=('other',))
    export(out, ledger, config)
    loads = json.loads
    def guarded_loads(value, *args, **kwargs):
        assert not (isinstance(value, str) and '"messages"' in value)
        return loads(value, *args, **kwargs)
    monkeypatch.setattr(batches.json, 'loads', guarded_loads)
    with loader.activate(), pytest.raises(TuningRefused):
        batches.run_batch(out, ledger, config, FakeBatchClient())


def test_character_only_extract_replays_with_zero_paid_calls(
        tmp_path, config, ledger_factory, snapshot_fixture, cohort_loader_factory, example_pack):
    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'run'
    loader = cohort_loader_factory(out)
    ledger = ledger_factory()
    warm = Gateway(FakeProvider(response(), response(), response()), ledger, config)
    for text in ('metadata', 'selected:3', 'selected:7'):
        warm.create(**request(text))
    class CharacterCore(FakeCore):
        def extract_characters(self, pages, client, settings, sink):
            client.messages.create(**request('characters'))
            return super().extract_characters(pages, client, settings, sink)
    # Warm responses above must carry provenance for the real cohort gate.
    with ledger.transaction():
        ledger.db.execute("UPDATE calls SET request=json_set(request, '$.context.book_id', 'selected')")
    dry = Gateway(None, ledger, config, dry_run=True, requests_path=out / 'batch-requests.jsonl')
    first = run_extract(snapshot, manifest, selection, out, dry, config,
                        loader=loader, core=CharacterCore(paid=True), dry_run=True)
    assert first['incomplete']
    assert len((out / 'batch-requests.jsonl').read_text().splitlines()) == 1
    with loader.activate():
        batches.run_batch(out, ledger, config, FakeBatchClient())
    replay = Gateway(FakeProvider(), ledger, config)
    result = run_extract(snapshot, manifest, selection, out, replay, config,
                         loader=loader, core=CharacterCore(paid=True))
    assert not result['incomplete']
    assert replay.paid_calls == 0 and replay.cache_hits == 4


@pytest.mark.parametrize('answer', ['', 'yes', 'affirmative'])
def test_cli_confirmation_precedes_secrets(offline_inputs, fake_core, synthetic_cohort,
        system_temporary_dir, tmp_path, monkeypatch, answer):
    snapshot, config_path = offline_inputs
    out = tmp_path / 'run'
    assert cli.main(['extract', '--snapshot', str(snapshot), '--config', str(config_path),
                     '--out', str(out), '--books', 'book-02']) == 0
    monkeypatch.setattr('builtins.input', lambda _: answer)
    assert cli.main(['batch', '--run', str(out)]) == 1


def test_cli_batch_rounds_then_extract(offline_inputs, fake_core, synthetic_cohort,
        system_temporary_dir, tmp_path, monkeypatch):
    snapshot, config_path = offline_inputs
    out = tmp_path / 'run'
    args = ['extract', '--snapshot', str(snapshot), '--config', str(config_path),
            '--out', str(out), '--books', 'book-02']
    assert cli.main(args) == 0
    monkeypatch.setattr('builtins.input', lambda _: 'AFFIRMATIVE')
    monkeypatch.setattr('scripts.data_cleanup.load_secrets', lambda _: {'EVAL_ANTHROPIC_API_KEY': 'fake'})
    client = FakeBatchClient(('succeeded', 'succeeded'))
    monkeypatch.setattr(cli, '_live_provider', lambda *args: client)
    assert cli.main(['batch', '--run', str(out), '--wait']) == 0
    assert cli.main(args) == 0  # discovers dependent characters
    client = FakeBatchClient()
    assert cli.main(['batch', '--run', str(out)]) == 0
    sync = FakeProvider()
    sync.close = lambda: None
    monkeypatch.setattr(cli, '_live_provider', lambda *args: sync)
    assert cli.main(args + ['--execute']) == 0
    assert sync.calls == []
    assert not read_json(out / 'predictions.json')['incomplete']
