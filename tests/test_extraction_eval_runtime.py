"""Budget, replay, and runner checks using local fixtures and fake providers."""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts.extraction_eval.tuning import current_loader
from scripts.extraction_eval.billing import BillingStopped, cycle_budgets
from text_content.pipeline_prompts import CHARACTER_RULE_REPLACEMENTS


from scripts.extraction_eval.gateway import (
    DryRunMiss, Gateway, RequestLimitError, request_ceiling, validate_prices,
)
from scripts.extraction_eval.runner import (
    cached_isbn_lookup, estimate, freeze_run, run_extract, validate_inputs,
)
from scripts.extraction_eval.store import (
    BudgetStopped, CachedFailure, Ledger, OutcomeUnknown, atomic_json, canonical, digest, read_json,
)


@pytest.fixture
def config():
    return {
        'billing_cycles': {'anthropic': {'start_day': 1}},
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
    config['settings']['prompt_variant'] = 'rules-plus-icl'
    return config


@pytest.fixture
def ledger_factory(tmp_path):
    ledgers = []

    def create(run='run', cap=1, cycle_cap=2):
        budgets = cycle_budgets({'billing_cycles': {'anthropic': {'start_day': 1}}}, cycle_cap)
        ledger = Ledger(tmp_path / 'ledger.sqlite', run, cap, budgets=budgets)
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


def test_reservation_exact_cap_and_shared_provider_cycle_limit(ledger_factory):
    first = ledger_factory(cap=.1, cycle_cap=.1)
    first.reserve('a', {}, Decimal('.1'))
    assert first.get('a')['reservation'] == 100_000_000
    with pytest.raises(BudgetStopped):
        first.reserve('b', {}, Decimal('.000000001'))
    second = ledger_factory(run='other', cap=.1, cycle_cap=.1)
    with pytest.raises(BudgetStopped):
        second.reserve('c', {}, Decimal('.000000001'))
    assert first.get('b') is None
    assert second.get('c') is None


def test_run_cap_is_immutable_and_cycle_cap_cannot_be_restored(ledger_factory):
    ledger_factory(cap=.1, cycle_cap=.2)
    with pytest.raises(ValueError, match='immutable'):
        ledger_factory(cap=.2, cycle_cap=.2)
    other = ledger_factory(run='other', cap=.1, cycle_cap=.3)
    assert other.db.execute('SELECT cap FROM billing_cycles').fetchone()[0] == 200_000_000


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


@pytest.mark.parametrize('label', ['page_extraction', 'character_detection', 'joint_metadata',
                                  'story_context', 'locate_key_pages', 'fake-model'])
@pytest.mark.parametrize('stages', [None, ['character_detection'], ['page_extraction', 'fake-model']])
def test_selective_replicates_preserve_legacy_keys(config, ledger_factory, label, stages):
    from ai_runtime import create_message, current_request_label

    ledger = ledger_factory()
    provider = FakeProvider(response(), response())
    baseline = Gateway(provider, ledger, config)
    first = create_message(baseline, label=label, **request())
    same = create_message(Gateway(provider, ledger, config, replicate_stages=stages),
                          label=label, **request())
    assert same._eval_key == first._eval_key
    repeated = create_message(Gateway(provider, ledger, config, replicate='2', replicate_stages=stages),
                              label=label, **request())
    refresh = stages is None or label in stages
    assert (repeated._eval_key != first._eval_key) == refresh
    assert repeated._eval_cached is not refresh
    assert repeated._eval_key == digest({'request_version': '1', 'provider': 'anthropic-messages',
                                         'replicate': '2' if refresh else '1',
                                         'request': request(), 'attempt': 0})
    assert current_request_label() == ''
    assert len(provider.calls) == (2 if refresh else 1)


def test_shared_helpers_supply_labels_before_dispatch(config, ledger_factory, monkeypatch):
    from ai_runtime import current_request_label, extraction_runtime
    from utilities import _claude_json, vision_text

    monkeypatch.setattr('utilities.build_vision_content', lambda *a, **kw: [])
    provider = FakeProvider(*(response(content=[{'type': 'text', 'text': '[]'}]) for _ in range(5)))
    ledger = ledger_factory()
    for replicate in ('1', '2'):
        gateway = Gateway(provider, ledger, config, replicate=replicate,
                          replicate_stages=['character_detection'])
        with extraction_runtime(client=gateway, settings=config['settings'], usage_sink=gateway.record_usage):
            for label in ('page_extraction', ''):
                vision_text(gateway, [], 'synthetic', model='fake-model', max_tokens=10, label=label)
            _claude_json(gateway, 'synthetic', model='fake-model', max_tokens=10)
    # Vision calls are identical regardless of label: legacy identity stays intact.
    assert len(provider.calls) == 3
    assert gateway.cache_hits == 2
    assert {row['label'] for row in ledger.rows()} == {'fake-model', 'character_detection'}
    assert current_request_label() == ''


def test_request_label_resets_on_cache_miss(config, ledger_factory):
    from ai_runtime import create_message, current_request_label

    with pytest.raises(DryRunMiss):
        create_message(Gateway(None, ledger_factory(), config, dry_run=True),
                       label='character_detection', **request())
    assert current_request_label() == ''


def test_input_source_separates_identical_requests(config, ledger_factory, tmp_path):
    from scripts.extraction_eval.runner import configure_inputs

    ledger = ledger_factory()
    provider = FakeProvider(response(), response())
    renders = configure_inputs(config, 'renders')
    paired = {'books': []}
    scans = configure_inputs(config, 'scans', inputs_manifest=paired, scan_root=tmp_path)
    paired['books'].append({'id': 'later-mutation'})
    assert scans['inputs_manifest'] == {'books': []}
    first = Gateway(provider, ledger, renders).create(**request())
    legacy = Gateway(provider, ledger, config).create(**request())
    second = Gateway(provider, ledger, scans).create(**request())
    replay = Gateway(provider, ledger, scans).create(**request())
    assert first._eval_key != second._eval_key
    assert legacy._eval_key == first._eval_key
    assert legacy._eval_cached is True
    assert replay._eval_key == second._eval_key
    assert replay._eval_cached is True
    assert len(provider.calls) == 2


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


def test_definite_rate_limit_rejections_retry(config, ledger_factory):
    class Rejected(RuntimeError):
        status_code = 429

    config['max_attempts'] = 2
    ledger = ledger_factory()
    provider = FakeProvider(Rejected('rate limit'), response())
    result = Gateway(provider, ledger, config, wait=lambda _: None).create(**request())
    assert result.id == 'fixture-response'
    assert len(provider.calls) == 2
    rows = ledger.rows()
    assert {row['state'] for row in rows} == {'retryable', 'completed'}
    assert sum(row['reserved_usd'] for row in rows) > 0


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
    assert ledger.rows()[0]['reserved_usd'] > 0


@pytest.mark.parametrize('status', [429, 500, 529])
def test_retry_exhaustion_explicit_recovery_and_free_replay(config, ledger_factory, status):
    class Rejected(RuntimeError):
        status_code = status

    config['max_attempts'] = 5
    ledger = ledger_factory()
    provider = FakeProvider(response(), *(Rejected('busy') for _ in range(5)), response())
    waits = []
    gateway = Gateway(provider, ledger, config, wait=waits.append)
    completed = gateway.create(**request('already paid'))
    paid = ledger.get(completed._eval_key)
    with pytest.raises(CachedFailure, match='retry-failed'):
        gateway.create(**request())
    assert len(waits) == 4
    assert all(2 ** attempt / 2 <= delay <= 2 ** attempt for attempt, delay in enumerate(waits))
    before = [dict(row) for row in ledger.db.execute('SELECT * FROM calls ORDER BY rowid')]
    for _ in range(2):
        with pytest.raises(CachedFailure, match='retry-failed'):
            gateway.create(**request())
    assert len(provider.calls) == 6
    result = ledger.retry_failed()
    assert result['keys'] == [before[-1]['key']]
    assert ledger.retry_failed()['keys'] == []  # Repeated command is harmless.
    assert [dict(row) for row in ledger.db.execute('SELECT * FROM calls ORDER BY rowid')] == before
    event = dict(ledger.db.execute('SELECT * FROM retry_events').fetchone())
    assert event == dict(key=before[-1]['key'], run_id=ledger.run_id, created=result['created'])
    gateway = Gateway(provider, ledger, config, wait=waits.append)
    assert gateway.create(**request('already paid'))._eval_cached
    assert not gateway.create(**request())._eval_cached
    assert gateway.create(**request())._eval_cached
    assert len(provider.calls) == 7
    assert len(waits) == 4  # Cache traversal never sleeps.
    assert ledger.get(completed._eval_key) == paid
    assert ledger.retry_failed()['keys'] == []  # Earlier rejected attempts stay history.
    assert ledger_factory(run='another-run').retry_failed()['keys'] == []


@pytest.mark.parametrize('header,expected', [('4.5', 4.5), ('600', 600),
    ('Mon, 16 Nov 2026 12:10:00 GMT', 600),
    ('Mon, 16 Nov 2026 12:00:09 GMT', 9), ('invalid', None), ('-1', None), ('nan', None)])
def test_sdk_retry_after_or_bounded_backoff(config, ledger_factory, header, expected):
    from datetime import datetime, timezone

    class Rejected(RuntimeError):
        status_code = 529
        response = SimpleNamespace(headers={'retry-after': header})

    ledger = ledger_factory()
    ledger.clock = lambda: datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    config['max_attempts'] = 2
    provider = FakeProvider(Rejected('overloaded'), response())
    waits = []
    Gateway(provider, ledger, config, wait=waits.append).create(**request())
    assert len(waits) == 1
    assert waits == [expected] if expected is not None else .5 <= waits[0] <= 1


@pytest.mark.parametrize('attempts', [1, 5])
@pytest.mark.parametrize('header,seconds', [
    ('601', 601), ('3600', 3600), ('Mon, 16 Nov 2026 12:10:01 GMT', 601),
])
def test_long_retry_wait_stops_until_explicit_recovery(config, ledger_factory, header, seconds, attempts):
    from datetime import datetime, timezone

    class Rejected(RuntimeError):
        status_code = 529
        response = SimpleNamespace(headers={'retry-after': header})

    config['max_attempts'] = attempts
    ledger = ledger_factory()
    ledger.clock = lambda: datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    provider = FakeProvider(Rejected('overloaded'), response())
    waits = []
    for _ in range(2):  # Fresh rejection and ordinary resume both stop.
        gateway = Gateway(provider, ledger, config, wait=waits.append)
        with pytest.raises(CachedFailure, match=rf'{seconds} seconds.*retry-failed'):
            gateway.create(**request())
        assert waits == []
        assert len(provider.calls) == 1
    rows = ledger.rows()
    assert len(rows) == 1
    assert rows[0]['state'] == 'retryable'
    assert ledger.retry_failed()['keys'] == [rows[0]['key']]
    assert not gateway.create(**request())._eval_cached
    assert len(provider.calls) == 2
    assert waits == []


def test_resume_interrupted_retry_waits_before_next_dispatch(config, ledger_factory):
    class Rejected(RuntimeError):
        status_code = 429
        response = SimpleNamespace(headers={'retry-after': '3'})

    def interrupted_wait(_):
        raise KeyboardInterrupt()

    config['max_attempts'] = 2
    ledger = ledger_factory()
    provider = FakeProvider(Rejected('busy'), response())
    with pytest.raises(KeyboardInterrupt):
        Gateway(provider, ledger, config, wait=interrupted_wait).create(**request())
    assert len(provider.calls) == 1
    waits = []
    Gateway(provider, ledger, config, wait=waits.append).create(**request())
    assert waits == [3]
    assert len(provider.calls) == 2


def test_retry_failed_only_reopens_eligible_requests_in_selected_run(config, ledger_factory):
    ledger = ledger_factory()
    for key in ('retry', 'permanent', 'unknown', 'dispatched', 'paid'):
        ledger.reserve(key, {'request': key}, .01)
    ledger.fail('retry', 'busy', rejected=True, retryable=True)
    ledger.fail('permanent', 'bad request', rejected=True)
    ledger.fail('unknown', 'timeout')
    ledger.settle('paid', response(), .001)
    other = ledger_factory(run='other')
    other.reserve('other-retry', {'request': 'other'}, .01)
    other.fail('other-retry', 'busy', rejected=True, retryable=True)
    result = ledger.retry_failed()
    assert result['keys'] == ['retry']
    assert result['unknown'] == ['dispatched', 'unknown']
    assert not ledger.retry_authorized('other-retry')


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


@pytest.fixture
def example_pack(tmp_path, monkeypatch):
    from text_content import pipeline_prompts as prompts

    examples = []
    for stage in prompts.RULES:
        for book in ('selected', 'a', 'b'):
            text = f'Synthetic {book} {stage} evidence.'
            examples.append({
                'id': f'{book}_{stage}', 'book_id': book, 'stage': stage,
                'holdout_group': 'shared' if book in ('selected', 'a') else 'other',
                'page_number': 1, 'source_text': text, 'quotes': [text],
                'source_text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'source_image_sha256': hashlib.sha256(b'synthetic image').hexdigest(),
                'approval_status': 'synthetic', 'decision': f'Expected {text}',
            })
    pack = {'development_book_ids': ['selected', 'a', 'b'], 'examples': examples}
    atomic_json(tmp_path / 'pipeline_examples.json', pack)
    monkeypatch.setattr(prompts, '__file__', str(tmp_path / 'pipeline_prompts.py'))
    return pack


@pytest.mark.parametrize('variant', ['rules-plus-icl', 'rules-only', 'legacy-v1'])
@pytest.mark.parametrize('book', ['selected', 'book'])
def test_evaluation_prompts_leave_out_book_and_group(
        example_pack, cohort_loader_factory, variant, book):
    from text_content import pipeline_prompts as prompts

    loader = cohort_loader_factory()
    stages = prompts.LEGACY_PROMPTS if variant == 'legacy-v1' else prompts.RULES
    baseline = {stage: prompts.prompt_for(stage, variant) for stage in stages}
    with loader.activate(), prompts.evaluation_prompt_context(book) as excluded:
        for stage in stages:
            rendered = prompts.prompt_for(stage, variant)
            manifest = prompts.prompt_request_manifest(stage, variant, rendered)
            if variant != 'rules-plus-icl':
                assert rendered == baseline[stage]
                assert manifest['examples'] == []
                continue
            expected = [example for example in example_pack['examples']
                        if example['stage'] == stage and
                        (book != 'selected' or example['book_id'] == 'b')]
            assert [example['id'] for example in manifest['examples']] == [
                example['id'] for example in expected]
            for example in example_pack['examples']:
                if example['stage'] == stage:
                    assert (example['source_text'] in rendered) == (example in expected)
                    assert (example['decision'] in rendered) == (example in expected)
            assert excluded[stage] == ([f'selected_{stage}', f'a_{stage}']
                                        if book == 'selected' else [])
            assert (rendered == baseline[stage]) == (book != 'selected')
    assert {stage: prompts.prompt_for(stage, variant) for stage in stages} == baseline


def test_evaluation_prompt_scope_requires_loader_and_resets_on_error(
        example_pack, cohort_loader_factory):
    from text_content import pipeline_prompts as prompts

    baseline = prompts.prompt_for('character_detection')
    with prompts.evaluation_prompt_context('selected') as excluded:
        assert prompts.prompt_for('character_detection') == baseline
        assert excluded == {}
    with cohort_loader_factory().activate():
        with pytest.raises(RuntimeError, match='interrupted'):
            with prompts.evaluation_prompt_context('selected'):
                assert prompts.prompt_for('character_detection') != baseline
                raise RuntimeError('interrupted')
        assert prompts.prompt_for('character_detection') == baseline


def test_runner_records_exclusions_for_every_rendered_stage(
        example_pack, snapshot_fixture, tmp_path, config, cohort_loader_factory):
    from text_content import pipeline_prompts as prompts

    snapshot, manifest, selection = snapshot_fixture
    # Run a source book and a non-source book consecutively to catch context leaks.
    other = deepcopy(manifest['books'][0])
    other['id'] = 'book'
    manifest['books'] = [manifest['books'][0], other]
    selection['book_ids'] = ['selected', 'book']
    out = tmp_path / 'run'
    loader = cohort_loader_factory(out)
    config['settings'].update(story_context_enabled=True, metadata_reconciliation_enabled=True)
    gateway = SimpleNamespace(record_usage=lambda *args: None)
    rendered = []

    def render(stage):
        prompt = prompts.prompt_for(stage)
        rendered.append((gateway.context['book_id'], stage, prompt))
        return prompts.prompt_request_manifest(stage, rendered_prompt=prompt)

    class RenderingCore(FakeCore):
        def extract_metadata(self, *args):
            render('locate_key_pages')
            return {'prompt_manifest': render('joint_metadata_extraction')}

        def extract_page(self, *args):
            return {'status': 'completed', 'text': 'Synthetic story.', 'contains_story': True,
                    'prompt_manifest': render('page_extraction')}

        def classify_book_context(self, *args):
            return {'status': 'completed', 'prompt_manifest': render('story_classification')}

        def reconcile_metadata(self, *args):
            return {'prompt_manifest': render('joint_metadata_extraction')}

        def extract_characters(self, *args):
            render('character_detection')
            return []

    def run():
        return run_extract(snapshot, manifest, selection, out, gateway, config,
                           loader=loader, core=RenderingCore(), progress=lambda *args: None)

    result = run()
    assert result['incomplete'] is False
    expected = {book: {stage: ([f'selected_{stage}', f'a_{stage}'] if book == 'selected' else [])
                       for stage in prompts.RULES} for book in selection['book_ids']}
    assert read_json(out / 'run.json')['tuning']['excluded_prompt_examples'] == expected
    for book, stage, prompt in rendered:
        assert f'EXAMPLE b_{stage} ' in prompt
        assert (f'EXAMPLE selected_{stage} ' in prompt) == (book != 'selected')
        assert (f'EXAMPLE a_{stage} ' in prompt) == (book != 'selected')
    count = len(rendered)
    assert run() == result
    assert len(rendered) == count  # Cached stages preserve the exclusion record.
    assert read_json(out / 'run.json')['tuning']['excluded_prompt_examples'] == expected


@pytest.mark.parametrize('stage', [
    'character_detection', 'page_extraction', 'story_classification', 'joint_metadata_extraction'])
def test_leave_one_out_cannot_hit_full_pack_request_cache(
        example_pack, cohort_loader_factory, config, ledger_factory, stage):
    from text_content import pipeline_prompts as prompts

    config['pricing']['models']['fake-model']['max_request_bytes'] = 100000
    ledger = ledger_factory()
    provider = FakeProvider(response(), response())
    gateway = Gateway(provider, ledger, config)
    gateway.context = {'book_id': 'selected'}
    full = gateway.create(**request(prompts.prompt_for(stage)))
    with cohort_loader_factory().activate(), prompts.evaluation_prompt_context('selected'):
        filtered_request = request(prompts.prompt_for(stage))
        with pytest.raises(DryRunMiss):
            Gateway(None, ledger, config, dry_run=True).create(**filtered_request)
        filtered = gateway.create(**filtered_request)
        assert filtered._eval_key != full._eval_key
        assert filtered._eval_cached is False
        assert gateway.create(**filtered_request)._eval_cached is True
    assert len(provider.calls) == 2


@pytest.mark.usefixtures("synthetic_cohort")
def test_runner_subset_resume_and_predicted_character_inputs(snapshot_fixture, tmp_path, config):
    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'run'
    out.mkdir()
    core = FakeCore()
    gateway = SimpleNamespace(record_usage=lambda *args: None)
    def run():
        return run_extract(snapshot, manifest, selection, out, gateway, config,
                           loader=current_loader(), core=core, isbn_lookup=lambda isbn: None, progress=lambda *args: None)
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


@pytest.mark.usefixtures("synthetic_cohort")
def test_runner_dry_run_no_provider_calls(snapshot_fixture, tmp_path, config, ledger_factory):
    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'dry-run'
    out.mkdir()
    core = FakeCore(paid=True)
    provider = FakeProvider()
    ledger = ledger_factory()
    gateway = Gateway(provider, ledger, config, dry_run=True)
    result = run_extract(snapshot, manifest, selection, out, gateway, config,
                         dry_run=True, loader=current_loader(), core=core, isbn_lookup=lambda isbn: None,
                         progress=lambda *args: None)
    assert result['incomplete'] is True
    assert provider.calls == []
    assert ledger.rows() == []
    assert (out / 'dry_run_predictions.json').exists()
    assert not (out / 'predictions.json').exists()


@pytest.mark.usefixtures('synthetic_cohort')
@pytest.mark.parametrize('stages,changed_text', [(['character_detection'], False),
                                              (['page_extraction'], False),
                                              (['page_extraction'], True)])
def test_selective_runner_replay_estimate_and_downstream_inputs(
        snapshot_fixture, tmp_path, config, ledger_factory, stages, changed_text):
    from ai_runtime import create_message

    class LabeledCore(FakeCore):
        def extract_metadata(self, pages, client, settings, sink, isbn):
            create_message(client, label='joint_metadata', **request('metadata'))
            return super().extract_metadata(pages, client, settings, sink, isbn)

        def extract_page(self, data, client, settings, sink):
            raw = create_message(client, label='page_extraction', **request(data.decode()))
            return {'status': 'completed', 'text': raw.content[0].text, 'contains_story': True}

        def extract_characters(self, pages, client, settings, sink):
            create_message(client, label='character_detection', **request(json.dumps(pages)))
            return super().extract_characters(pages, client, settings, sink)

    snapshot, manifest, selection = snapshot_fixture
    ledger = ledger_factory()
    fresh = response(content=[{'type': 'text', 'text': 'changed story'}]) if changed_text else response()
    provider = FakeProvider(response(), response(), response(), response(), fresh, fresh, response())

    def run(name, gateway, dry_run=False):
        out = tmp_path / name
        out.mkdir()
        return run_extract(snapshot, manifest, selection, out, gateway, config,
                           dry_run=dry_run, loader=current_loader(), core=LabeledCore(),
                           progress=lambda *args: None)

    run('baseline', Gateway(provider, ledger, config))
    assert len(provider.calls) == 4
    dry = Gateway(None, ledger, config, replicate='2', replicate_stages=stages, dry_run=True)
    result = run('dry', dry, dry_run=True)
    assert len(provider.calls) == 4
    if stages == ['character_detection']:
        plan = estimate(manifest, selection, result)
        assert plan['pending_pages'] == 0
        assert plan['pending_books'] == 1
        assert plan['estimated_usd'] == .0206  # Historical book allowance only.
        assert dry.cache_hits == 3
        assert result['books']['selected']['characters']['status'] == 'pending'
    repeated = Gateway(provider, ledger, config, replicate='2', replicate_stages=stages)
    result = run('replicate', repeated)
    assert not result['incomplete']
    expected_calls = 1 if stages == ['character_detection'] else 2 + changed_text
    assert len(provider.calls) == 4 + expected_calls
    assert repeated.cache_hits == 4 - expected_calls


@pytest.mark.usefixtures("synthetic_cohort")
def test_runner_budget_stop_preserves_incomplete_denominator(snapshot_fixture, tmp_path, config, ledger_factory):
    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'stopped'
    out.mkdir()
    core = FakeCore(paid=True)
    provider = FakeProvider()
    gateway = Gateway(provider, ledger_factory(cap=0), config)
    result = run_extract(snapshot, manifest, selection, out, gateway, config,
                         loader=current_loader(), core=core, isbn_lookup=lambda isbn: None, progress=lambda *args: None)
    book = result['books']['selected']
    assert result['incomplete'] is True
    assert len(book['pages']) == 2
    assert all(page['status'] == 'budget_stopped' for page in book['pages'].values())
    assert book['characters']['status'] == 'budget_stopped'
    assert provider.calls == []


@pytest.mark.usefixtures("synthetic_cohort")
def test_snapshot_integrity_is_verified_before_extraction(snapshot_fixture):
    snapshot, manifest, selection = snapshot_fixture
    validate_inputs(snapshot, manifest, selection, loader=current_loader())
    path = snapshot / manifest['books'][0]['pages'][0]['image']['path']
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        validate_inputs(snapshot, manifest, selection, loader=current_loader())


@pytest.mark.usefixtures("synthetic_cohort")
def test_isbn_genuine_absence_is_cached_and_dry_run_never_looks_up(tmp_path):
    calls = []
    def provider(isbn, *, response_sink, outcome_sink):
        calls.append(isbn)
        response_sink({'totalItems': 0})
        outcome_sink({'status': 'not_found', 'attempts': 1})
        return None
    lookup = cached_isbn_lookup(tmp_path, provider, source_ids=lambda: ["selected"])
    outcomes = []
    assert lookup('123', outcome_sink=outcomes.append) is None
    assert lookup('123') is None
    assert calls == ['123']
    assert outcomes == [{'status': 'not_found', 'attempts': 1}]
    saved = json.loads((tmp_path / 'lookups' / f'{digest("123")}.json').read_text())
    assert saved['raw_response'] == {'totalItems': 0}
    dry = cached_isbn_lookup(tmp_path, lambda isbn: pytest.fail('live lookup'), dry_run=True, source_ids=lambda: ["selected"])
    assert dry('123', outcome_sink=outcomes.append) is None
    assert outcomes[0] == outcomes[1]
    with pytest.raises(DryRunMiss):
        dry('456')


@pytest.mark.usefixtures("synthetic_cohort")
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
    lookup = cached_isbn_lookup(tmp_path, provider, source_ids=lambda: ["selected"])
    path = tmp_path / 'lookups' / f'{digest("123")}.json'
    assert lookup('123', outcome_sink=outcomes.append) is None
    assert not path.exists()
    assert outcomes[0]['status'] == 'failed'
    assert outcomes[0]['http_status'] == 429
    assert lookup('123') == {'title': 'Recovered'}
    assert path.exists()
    assert lookup('123') == {'title': 'Recovered'}
    assert calls == ['123', '123']


@pytest.mark.usefixtures("synthetic_cohort")
def test_legacy_failed_isbn_cache_is_ignored_but_not_changed_during_dry_run(tmp_path):
    path = tmp_path / 'lookups' / f'{digest("123")}.json'
    poisoned = {'isbn': '123', 'value': None, 'raw_response': None, 'source_work_ids': ['selected']}
    atomic_json(path, poisoned)
    dry = cached_isbn_lookup(tmp_path, lambda isbn: pytest.fail('live lookup'), dry_run=True, source_ids=lambda: ["selected"])
    with pytest.raises(DryRunMiss):
        dry('123')
    assert json.loads(path.read_text()) == poisoned
    lookup = cached_isbn_lookup(tmp_path, lambda isbn: {'title': 'Recovered'}, source_ids=lambda: ["selected"])
    assert lookup('123') == {'title': 'Recovered'}
    assert json.loads(path.read_text())['value'] == {'title': 'Recovered'}


@pytest.mark.parametrize('capture_raw', [False, True])
@pytest.mark.usefixtures("synthetic_cohort")
def test_one_argument_isbn_lookup_without_answer_is_not_assumed_absent(tmp_path, capture_raw):
    calls, outcomes = [], []
    lookup = cached_isbn_lookup(tmp_path, lambda isbn: calls.append(isbn), capture_raw=capture_raw, source_ids=lambda: ["selected"])
    assert lookup('123', outcome_sink=outcomes.append) is None
    assert lookup('123') is None
    assert calls == ['123', '123']
    assert outcomes[0]['status'] == 'failed'
    assert not (tmp_path / 'lookups').exists()


@pytest.mark.usefixtures("synthetic_cohort")
def test_isbn_lookup_with_opaque_signature_retains_single_argument_contract(tmp_path):
    class Provider:
        @property
        def __signature__(self):
            raise ValueError('No signature available')

        def __call__(self, isbn):
            return {'title': isbn}

    lookup = cached_isbn_lookup(tmp_path, Provider(), capture_raw=True, source_ids=lambda: ["selected"])
    assert lookup('123') == {'title': '123'}


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.usefixtures("synthetic_cohort")
def test_resume_retries_isbn_failure_without_repeating_vision(snapshot_fixture, tmp_path, config, legacy):
    from scripts.extraction_eval.runner import Stages

    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'run'
    out.mkdir()
    value = {'title': 'Vision title', 'authors': [], 'publisher': None, 'isbn': '123',
             'sources': {'title': {'source': 'title_page'}}}
    if legacy:
        atomic_json(out / 'lookups' / f'{digest("123")}.json',
                    {'isbn': '123', 'value': None, 'raw_response': None, 'source_work_ids': ['selected']})
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
                         loader=current_loader(), core=core, isbn_lookup=provider, progress=lambda *args: None)
    metadata = result['books']['selected']['metadata']['value']
    assert calls == ['123']
    assert all(operation != 'metadata' for operation, _ in core.calls)
    assert metadata['title'] == 'Vision title'
    assert metadata['authors'] == ['Recovered author']
    assert metadata['publisher'] == 'Recovered publisher'
    assert metadata['isbn_lookup']['status'] == 'found'
    assert metadata['lookup_failed_fields'] == []


@pytest.mark.usefixtures("synthetic_cohort")
def test_run_contract_preserves_initial_tuning_record(snapshot_fixture, tmp_path, config):
    _, manifest, selection = snapshot_fixture
    out = tmp_path / 'frozen'
    tuning = current_loader().state.copy()
    atomic_json(out / 'run.json', {'tuning': tuning})
    record = freeze_run(out, manifest, selection, config, '1', 16, 200,
                        tmp_path / 'shared.sqlite')
    assert record['tuning'] == tuning
    assert read_json(out / 'run.json')['tuning'] == tuning


@pytest.mark.usefixtures("synthetic_cohort")
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


@pytest.mark.usefixtures('synthetic_cohort')
def test_run_contract_records_and_freezes_replicate_stages(snapshot_fixture, tmp_path, config):
    _, manifest, selection = snapshot_fixture
    args = (tmp_path / 'frozen', manifest, selection, config, '2', 16, 200, tmp_path / 'ledger.sqlite')
    first = freeze_run(*args, replicate_stages=['page_extraction', 'character_detection', 'page_extraction'])
    assert first['contract']['replicate_stages'] == ['character_detection', 'page_extraction']
    assert freeze_run(*args, replicate_stages=['character_detection', 'page_extraction']) == first
    with pytest.raises(ValueError, match='changed'):
        freeze_run(*args, replicate_stages=['character_detection'])


@pytest.mark.usefixtures("synthetic_cohort")
@pytest.mark.parametrize('stages', [None, ['character_detection'], ['page_extraction', 'fake-model']])
def test_cli_dry_run_does_not_construct_clients_or_read_secrets(snapshot_fixture, tmp_path, cli_config, monkeypatch, stages):
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
    options = ['--replicate', '2'] + (['--replicate-stages', *stages] if stages else [])
    assert cli.main(['extract', '--snapshot', str(snapshot), '--out', str(tmp_path / 'cli-run'),
                     '--config', str(config_path), '--limit', '1', '--dry-run', *options]) == 0
    contract = read_json(tmp_path / 'cli-run' / 'run.json')['contract']
    assert contract['replicate_stages'] == (sorted(stages) if stages else None)
    assert len(fake.calls) == 3  # metadata and two OCR requests; all cache misses


@pytest.mark.usefixtures('synthetic_cohort')
def test_cli_rejects_unknown_replicate_stage_before_reading_content(tmp_path, cli_config, monkeypatch, capsys):
    from scripts.extraction_eval import cli

    config_path = tmp_path / 'config.json'
    atomic_json(config_path, cli_config)
    monkeypatch.setattr(cli, '_load_snapshot', lambda *a, **kw: pytest.fail('Read before label validation'))
    with pytest.raises(SystemExit) as exc:
        cli.main(['extract', '--snapshot', str(tmp_path / 'snapshot'), '--out', str(tmp_path / 'run'),
                  '--config', str(config_path), '--replicate-stages', 'characters', '--dry-run'])
    assert exc.value.code == 2
    assert 'Unknown --replicate-stages label' in capsys.readouterr().err


@pytest.mark.usefixtures("synthetic_cohort")
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
    assert cli.main(['extract', '--snapshot', str(snapshot), '--out', str(out),
                     '--config', str(config_path), '--limit', '1', '--execute']) == 1
    assert read_json(out / 'run.json')['tuning']['loaded_ids']


def test_runner_requires_cohort_before_content_reader(snapshot_fixture, tmp_path, config, monkeypatch):
    from scripts.extraction_eval import runner

    snapshot, manifest, selection = snapshot_fixture
    reads = []
    monkeypatch.setattr(runner, 'local_image', lambda *a, **k: reads.append(True))
    with pytest.raises(ValueError, match='run record'):
        run_extract(snapshot, manifest, selection, tmp_path,
                    SimpleNamespace(), config, loader=None, core=FakeCore())
    assert reads == []


@pytest.mark.parametrize('kind', ['response_cache', 'call_report'])
def test_cached_request_refusal_precedes_content_decode(
        cohort_loader_factory, monkeypatch, kind):
    from scripts.extraction_eval.json_sources import JsonSources
    from scripts.extraction_eval.store import guard_cached_request
    from scripts.extraction_eval.tuning import TuningRefused

    loader = cohort_loader_factory()
    original = JsonSources.decode
    decoded = []

    def checked(source, span):
        data = source._data[span[0]:span[1]]
        assert b'FORBIDDEN PAYLOAD' not in data
        decoded.append(data)
        return original(source, span)

    monkeypatch.setattr(JsonSources, 'decode', checked)
    with loader.activate(), pytest.raises(TuningRefused):
        guard_cached_request(json.dumps({'context': {'book_id': 'heldout'},
                                         'request': {'content': 'FORBIDDEN PAYLOAD'}}), kind)
    assert b'"heldout"' in decoded
    assert read_json(loader.path)['tuning']['refused_attempts'][-1]['kind'] == kind


@pytest.mark.parametrize('dry_run', [False, True])
def test_only_completed_rehearsal_produces_release_evidence(
        snapshot_fixture, tmp_path, config, cohort_loader_factory, dry_run):
    from datetime import datetime, timezone
    from scripts.extraction_eval.tuning import TuningLoader

    snapshot, original, selection = snapshot_fixture
    manifest = {**original, 'books': original['books'][:1]}
    out = tmp_path / 'rehearsal'
    loader = cohort_loader_factory(out, iterate=['iterate'], protected=['selected'],
                                   holdout=['heldout'], cohort='rehearsal')
    with loader.activate():
        frozen = freeze_run(out, manifest, selection, config, '1', 16, 200, tmp_path / 'spend.sqlite')
    result = run_extract(snapshot, manifest, selection, out,
                         SimpleNamespace(record_usage=lambda *args: None,
                                         ledger=SimpleNamespace(run_id=frozen['run_id'])), config,
                         loader=loader, core=FakeCore(), isbn_lookup=lambda _: None,
                         dry_run=dry_run, progress=lambda _: None)
    assert result['incomplete'] is False
    record = read_json(out / 'run.json')
    if dry_run:
        assert 'completion' not in record
    else:
        completion = record['completion']
        lock = {key: completion[key] for key in ('results_sha256', 'configuration_sha256')}
        lock.update(run_record=str(loader.path), run_id=record['run_id'],
                    locked_at=datetime.now(timezone.utc).isoformat())
        released = TuningLoader(tmp_path / 'released', loader.freeze_dir,
                                selections_path=loader.selections_path, rehearsal_lock=lock)
        calls = []
        released.load(['selected'], 'image', lambda: calls.append(True))
        assert calls == [True]


def test_stage_cache_cannot_attribute_another_book_key_to_an_allowed_id(cohort_loader_factory):
    from scripts.extraction_eval.runner import Stages
    from scripts.extraction_eval.tuning import TuningRefused

    loader = cohort_loader_factory()
    reads = []
    stages = object.__new__(Stages)
    stages.db = SimpleNamespace(execute=lambda *args: reads.append(True))
    with loader.activate(), pytest.raises(TuningRefused):
        stages.get('heldout:metadata', book_id='selected')
    assert reads == []
    assert read_json(loader.path)['tuning']['refused_attempts'][-1]['kind'] == 'stage_cache'


@pytest.mark.usefixtures('synthetic_cohort')
@pytest.mark.parametrize('stage', ['page', 'characters'])
@pytest.mark.parametrize('error, kind', [
    (CachedFailure('Request rejected: provider error'), 'provider_error'),
    (CachedFailure('Output blocked by content filtering policy'), 'output_blocked'),
    (ValueError('Invalid schema'), 'invalid_response'),
    (json.JSONDecodeError('Invalid JSON', '', 0), 'invalid_response'),
    (OutcomeUnknown('Timeout; charge unknown'), 'outcome_unknown'),
    (FileNotFoundError('Missing image'), 'missing_input'),
    (RequestLimitError('Too many images'), 'request_limit'),
    (BillingStopped('Provider billing rejection'), 'provider_error'),
])
def test_runner_failure_kinds_and_stage_cache(
        snapshot_fixture, tmp_path, config, stage, error, kind):
    from scripts.extraction_eval.scoring import score_page_failures

    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'run'
    out.mkdir()
    core = FakeCore()
    calls = []

    def fail(*args):
        calls.append(True)
        raise error

    setattr(core, 'extract_' + stage, fail)
    def run():
        return run_extract(snapshot, manifest, selection, out,
                           SimpleNamespace(record_usage=lambda *args: None), config,
                           loader=current_loader(), core=core, progress=lambda _: None)

    result = run()
    book = result['books']['selected']
    record = next(iter(book['pages'].values())) if stage == 'page' else book['characters']
    assert record['status'] == 'failed'
    assert record['failure_kind'] == kind
    if stage == 'page':
        figures = score_page_failures(book['pages'].values())
        assert figures['failed'] == (1 if kind == 'outcome_unknown' or isinstance(error, BillingStopped) else 2)
        assert figures['failed_pages_per_100'] == 100
    count = len(calls)
    assert run() == result
    assert len(calls) == count


@pytest.mark.usefixtures('synthetic_cohort')
@pytest.mark.parametrize('stage', ['page', 'characters'])
@pytest.mark.parametrize('stop, expected', [
    ('end_turn', None), ('refusal', 'refusal'), ('max_tokens', 'truncated')])
def test_runner_rejects_stop_reason_even_with_valid_empty_json(
        snapshot_fixture, tmp_path, config, monkeypatch, stage, stop, expected):
    from scripts.extraction_eval.gateway import _namespace

    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'run'
    out.mkdir()
    # Exercise the real JSON parsers and the runner's injected usage sink.
    from extraction_core import attempt_page_extraction
    from ai_runtime import extraction_runtime
    from utilities import _claude_json
    monkeypatch.setattr('utilities.build_vision_content', lambda *args, **kwargs: [])
    config['settings']['prompt_variant'] = 'rules-only'
    core = FakeCore()
    usage = []
    label = 'page_extraction' if stage == 'page' else 'character_detection'

    def parse(*args):
        sink = args[-1]
        raw = response(stop_reason=stop, content=[{'type': 'text', 'text':
            '{"has_text": false, "text": "", "is_story_page": false}' if stage == 'page' else '[]'}])
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: _namespace(raw)))
        with extraction_runtime(client=client, settings=config['settings'], usage_sink=sink):
            if stage == 'characters':
                return _claude_json(client, 'synthetic', model='fake-model')
            # Skip only image encoding; vision_json and strict page schema run.
            from utilities import vision_json
            outcome, payload = attempt_page_extraction(
                b'image', client, {**config['settings'], 'extraction_max_edge': 512,
                                   'extraction_max_tokens': 100}, strict=True,
                label=label, vision_json_call=lambda *a, **kw: vision_json(
                    client, [], 'synthetic', label=label, reject_partial=True))
            assert outcome == 'ok'
            return {'status': 'completed', 'text': payload[0], 'contains_story': payload[1]}

    setattr(core, 'extract_' + stage, parse)
    result = run_extract(snapshot, manifest, selection, out,
                         SimpleNamespace(record_usage=lambda *args: usage.append(args)), config,
                         loader=current_loader(), core=core, progress=lambda _: None)
    book = result['books']['selected']
    records = list(book['pages'].values()) if stage == 'page' else [book['characters']]
    assert usage
    for record in records:
        assert record['status'] == ('failed' if expected else 'completed')
        assert record.get('failure_kind') == expected
        if expected:
            assert record['raw_response']['stop_reason'] == stop
        elif stage == 'page':
            assert record['text'] == ''


@pytest.mark.usefixtures('synthetic_cohort')
def test_missing_image_reaches_page_failure_after_preflight(snapshot_fixture, tmp_path, config):
    snapshot, manifest, selection = snapshot_fixture
    missing = manifest['books'][0]['pages'][0]
    missing['image']['path'] = 'absent.jpg'
    validate_inputs(snapshot, manifest, selection, loader=current_loader())
    out = tmp_path / 'run'
    out.mkdir()
    result = run_extract(snapshot, manifest, selection, out,
                         SimpleNamespace(record_usage=lambda *args: None), config,
                         loader=current_loader(), core=FakeCore(), progress=lambda _: None)
    page = result['books']['selected']['pages'][missing['id']]
    assert page['status'] == 'failed'
    assert page['failure_kind'] == 'missing_input'


@pytest.mark.usefixtures('synthetic_cohort')
@pytest.mark.parametrize('kind', ['provider_error', 'output_blocked', 'outcome_unknown'])
def test_provider_failure_and_request_cache_never_complete_blank(
        snapshot_fixture, tmp_path, config, ledger_factory, kind):
    class Rejected(Exception):
        status_code = 400

    error = (TimeoutError('timeout') if kind == 'outcome_unknown' else
             Rejected('Output blocked by content filtering policy' if kind == 'output_blocked'
                      else 'Bad provider request'))
    provider = FakeProvider(error, error)
    ledger = ledger_factory()
    gateway = Gateway(provider, ledger, config)
    snapshot, manifest, selection = snapshot_fixture

    class Core(FakeCore):
        def extract_page(self, data, client, settings, sink):
            client.messages.create(**request(data.decode()))
            return {'status': 'completed', 'text': ''}

    for attempt in range(2):
        out = tmp_path / f'run-{attempt}'  # No stage cache: replay request outcomes.
        out.mkdir()
        result = run_extract(snapshot, manifest, selection, out, gateway, config,
                             loader=current_loader(), core=Core(), progress=lambda _: None)
        page = next(iter(result['books']['selected']['pages'].values()))
        assert page['status'] == 'failed'
        assert page['failure_kind'] == kind
        assert len(provider.calls) == (1 if kind == 'outcome_unknown' else 2)
        if kind == 'outcome_unknown':
            assert result['incomplete'] is True
            assert ledger.rows()[0]['reserved_usd'] > 0


@pytest.mark.usefixtures('synthetic_cohort')
@pytest.mark.parametrize('status', [429, 529, 402, None])
def test_retry_failed_command_recovers_cached_stages_but_never_unknown(
        snapshot_fixture, tmp_path, config, ledger_factory, status, capsys):
    from scripts.extraction_eval.cli import main

    class Rejected(RuntimeError):
        status_code = status

    error = Rejected('busy') if status else TimeoutError('lost response')
    # Metadata completes, then a page fails; the second page is attempted only
    # for nonfatal rejections. Successful stages must remain free on recovery.
    provider = FakeProvider(response(), error, response(), response())
    ledger = ledger_factory()
    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'retry-run'
    out.mkdir()
    atomic_json(out / 'run.json', {'run_id': ledger.run_id,
        'contract': {'ledger': str(tmp_path / 'ledger.sqlite'), 'max_spend_usd': 1}})
    core = FakeCore(paid=True)

    def run():
        return run_extract(snapshot, manifest, selection, out,
                           Gateway(provider, ledger, config), config,
                           loader=current_loader(), core=core, progress=lambda _: None)

    initial = run()
    assert any(page['status'] == 'failed' for page in initial['books']['selected']['pages'].values())
    count = len(provider.calls)
    paid = [dict(row) for row in ledger.db.execute("SELECT * FROM calls WHERE state='completed'")]
    run()  # Merely resuming does not authorize another charge.
    assert len(provider.calls) == count
    assert main(['retry-failed', '--run', str(out)]) == 0
    output = capsys.readouterr().out
    assert len(provider.calls) == count
    recovered = run()
    if status is None:
        assert recovered['incomplete']
        assert 'unknown outcome; not retried' in output
        assert ledger.rows()[-1]['key'] in output
        assert len(provider.calls) == count
    else:
        assert not recovered['incomplete']
        assert all(page['status'] == 'completed' for page in recovered['books']['selected']['pages'].values())
        assert len(recovered['books']['selected']['characters']['input_pages']) == 2
        assert 'Retry authorized for 1 requests' in output
        assert len(provider.calls) == 4  # Metadata + 2 pages + one rejection.
    for row in paid:
        assert ledger.get(row['key']) == row


def test_character_variant_reuses_exact_page_request_and_cache_key(config, ledger_factory):
    import io
    from PIL import Image
    import extraction_core
    from ai_runtime import extraction_runtime
    from pipeline_config import validate_settings

    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'white').save(buffer, format='PNG')
    config['pricing']['models']['fake-model'].update(
        max_input_tokens=100000, max_output_tokens=20000, max_request_bytes=1000000)
    base = validate_settings({**{key: 'fake-model' for key in validate_settings({}) if key.endswith('_model')},
                              'prompt_variant': 'legacy-v1'}, model_ids=['fake-model'])
    page_reply = {'has_text': True, 'text': 'Mia waved.', 'is_story_page': True, 'page_type': 'story'}
    provider = FakeProvider(*(response(content=[{'type': 'text', 'text': json.dumps(value)}])
                              for value in (page_reply, {'characters': []}, {'characters': []})))
    ledger = ledger_factory()
    keys = []
    for variant in (None, 'rules-only'):
        settings = {**base, 'character_prompt_variant': variant}
        gateway = Gateway(provider, ledger, {**config, 'settings': settings})
        captured = []
        original = gateway.create

        def capture(**request):
            result = original(**request)
            captured.append((json.dumps(request, sort_keys=True).encode(), result._eval_key))
            return result

        gateway.create = capture
        with extraction_runtime(client=gateway, settings=settings, usage_sink=gateway.record_usage):
            assert extraction_core.attempt_page_extraction(
                buffer.getvalue(), gateway, settings, label='page_extraction')[0] == 'ok'
            extraction_core.extract_characters(
                [{'page_number': 1, 'contains_story': True, 'text': 'Mia waved.'}],
                gateway, settings, gateway.record_usage)
        keys.append(captured)
    assert keys[0][0] == keys[1][0]  # Complete serialized page request AND actual gateway cache key.
    assert keys[0][1][0] != keys[1][1][0]
    assert keys[0][1][1] != keys[1][1][1]
    assert len(provider.calls) == 3  # Page once, each character prompt once.


def test_human_w2_preserves_upstream_requests(ledger_factory, example_pack, synthetic_cohort):
    variant = 'human-w2'
    import io
    from pathlib import Path
    from PIL import Image
    import extraction_core
    import utilities
    from ai_runtime import extraction_runtime
    from pipeline_config import validate_settings
    from text_content.pipeline_prompts import prompt_for

    directory = Path(__file__).resolve().parents[1] / 'scripts/extraction_eval'
    baseline = read_json(directory / 'baseline.json')
    candidate = read_json(directory / f'character_{variant}.json')
    assert candidate == {**baseline, 'settings': {
        **baseline['settings'], 'character_prompt_variant': variant}}
    # Pin the whole historical character template, independently of the new blocks.
    assert hashlib.sha256(prompt_for('character_detection', 'legacy-v1').encode()).hexdigest() == (
        '61c356bb44df9484872e87943bf0a76e0649aaf848b974bfd8f6b322dee83b3a')
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'white').save(buffer, format='PNG')
    page = {'has_text': True, 'text': 'A traveller waved.', 'is_story_page': True,
            'page_type': 'story'}
    details = {'title': {'value': 'Fixture', 'evidence': []}, 'credits': [],
               'publisher_statements': [], 'isbns': [], 'conflicts': []}
    located = {'title_page': 1, 'copyright_page': 1}
    provider = FakeProvider(*(response(content=[{'type': 'text', 'text': json.dumps(value)}])
                              for value in (page, located, details, {'characters': []}, {'characters': []})))
    ledger = ledger_factory()
    requests = []
    for config in (baseline, candidate):
        settings = validate_settings(config['settings'])
        gateway = Gateway(provider, ledger, config)
        gateway.context = {'book_id': 'fixture'}
        captured = []
        original = gateway.create

        def capture(**request):
            result = original(**request)
            captured.append((request, result._eval_key))
            return result

        gateway.create = capture
        with extraction_runtime(client=gateway, settings=settings, usage_sink=gateway.record_usage):
            assert extraction_core.attempt_page_extraction(
                buffer.getvalue(), gateway, settings, label='page_extraction')[0] == 'ok'
            assert utilities.extract_photo_first_metadata(
                [(1, buffer.getvalue())], gateway)['metadata_extraction_status'] == 'completed'
            characters = extraction_core.extract_characters(
                [{'page_number': 1, 'contains_story': True, 'text': page['text']}],
                gateway, settings, gateway.record_usage)
            assert characters.raw_response['prompt_manifest']['examples'] == []
        requests.append(captured)
    assert len(requests[0]) == len(requests[1]) == 4
    # Complete upstream request bytes AND actual gateway keys remain identical.
    assert canonical(requests[0][:3]) == canonical(requests[1][:3])
    old, old_key = requests[0][3]
    new, new_key = requests[1][3]
    expected = deepcopy(old)
    legacy = prompt_for('character_detection', 'legacy-v1')
    restored = prompt_for('character_detection', variant)
    before, after = CHARACTER_RULE_REPLACEMENTS[variant]
    assert legacy.count(before) == restored.count(after) == 1
    assert before not in restored
    assert restored.replace(after, before) == legacy  # Only the declared replacement.
    expected['messages'][0]['content'] = expected['messages'][0]['content'].replace(before, after)
    assert canonical(new) == canonical(expected)
    assert new_key != old_key
    assert len(provider.calls) == 5  # Page, locator and details once; characters twice.


def test_human_vision_leaves_character_request_and_cache_unchanged(ledger_factory):
    from pathlib import Path
    from extraction_core import extract_characters
    from pipeline_config import validate_settings

    config = read_json(Path(__file__).resolve().parents[1] /
                       'scripts/extraction_eval/character_human-w2.json')
    provider = FakeProvider(response(content=[{'type': 'text', 'text': '{"characters": []}'}]))
    ledger = ledger_factory()
    for enabled in (None, False, True):
        settings = dict(config['settings'])
        if enabled is not None:
            settings['human_vision_enabled'] = enabled
        gateway = Gateway(provider, ledger, {**config, 'settings': settings})
        extract_characters([{'page_number': 1, 'contains_story': True, 'text': 'A sailor waved.'}],
                           gateway, validate_settings(settings), gateway.record_usage)
    assert len(provider.calls) == len(ledger.rows()) == 1  # Same full request and gateway identity.


def test_human_vision_selection_and_image_cache_identity(ledger_factory, synthetic_cohort):
    import io
    from pathlib import Path
    from PIL import Image
    from extraction_core import classify_character_human
    from pipeline_config import validate_settings

    directory = Path(__file__).resolve().parents[1] / 'scripts/extraction_eval'
    base = read_json(directory / 'character_human-w2.json')
    config = read_json(directory / 'character_human-vision.json')
    assert config == {**base, 'settings': {**base['settings'], 'human_vision_enabled': True}}
    characters = [{'name': 'Mira', 'aliases': ['the sailor'], 'human': True,
                   'gender': 'secret-code', 'protagonist': True},
                  {'name': 'Bo', 'aliases': []}, {'name': 'Unmentioned', 'aliases': []}]
    pages = [{'page_id': str(i), 'page_number': i, 'text': text} for i, text in enumerate(
        ['Miras and boats.', 'The sailor saw Bo.', 'Mira met Bo.', 'Mira and Bo left.'], 1)]
    replies = {'characters': [{'index': 1, 'human': False, 'image_number': 1}]}
    provider = FakeProvider(*(response(content=[{'type': 'text', 'text': json.dumps(replies)}])
                              for _ in range(2)))
    gateway = Gateway(provider, ledger_factory(), config)
    gateway.context = {'book_id': 'selected'}
    keys = []
    for color in ('white', 'white', 'black'):
        buffer = io.BytesIO()
        Image.new('RGB', (1000, 800), color).save(buffer, format='PNG')
        reads = []

        def read(page):
            reads.append(page['page_number'])
            return buffer.getvalue()

        result = classify_character_human(characters, list(reversed(pages)), read, gateway,
                                          validate_settings(config['settings']), gateway.record_usage)
        assert reads == [2, 3]  # First two matches; shared pages sent once.
        assert [row['image_numbers'] for row in result['cast']] == [[1, 2], [1, 2], []]
        assert result['value'] == replies['characters']
        keys.append([row['key'] for row in gateway.ledger.rows()])
    assert keys[0] == keys[1] and len(keys[2]) == 2
    assert len(provider.calls) == 2  # Changing only image bytes makes a new request.
    prompt = provider.calls[0]['messages'][0]['content'][-1]['text']
    assert 'secret-code' not in prompt and '"protagonist"' not in prompt
    assert '"human": true' not in prompt.split('Numbered cast')[1]
    unmatched = classify_character_human(characters[2:], pages, read, gateway,
                                         validate_settings(config['settings']), gateway.record_usage)
    assert unmatched['value'] == [] and unmatched['image_pages'] == []
    assert len(provider.calls) == 2


@pytest.mark.usefixtures('synthetic_cohort')
@pytest.mark.parametrize('outcome', ['off', 'merge', 'null', 'missing', 'failed', 'pending', 'bad_image'])
def test_human_vision_runner_fallback_and_only_human_merge(
        snapshot_fixture, tmp_path, config, monkeypatch, outcome):
    from extraction_core import classify_character_human

    snapshot, manifest, selection = snapshot_fixture
    out = tmp_path / 'human-run'
    out.mkdir()
    config['settings']['human_vision_enabled'] = outcome != 'off'
    original = [{'name': 'Predicted', 'aliases': [], 'human': True,
                 'gender': 'female', 'protagonist': True, 'plural': False}]
    core = FakeCore()
    core.extract_characters = lambda *args: deepcopy(original)
    core.classify_character_human = classify_character_human
    calls = []

    def vision(client, images, prompt, **kwargs):
        calls.append((images, prompt, kwargs))
        if outcome == 'failed':
            raise CachedFailure('Request rejected: provider error')
        if outcome == 'pending':
            raise DryRunMiss('Awaiting batch result')
        decision = {'index': 1, 'human': None if outcome == 'null' else False,
                    'image_number': 99 if outcome == 'bad_image' else 1,
                    'gender': 'male', 'protagonist': False, 'name': 'Wrong'}
        return {'characters': [] if outcome == 'missing' else [decision]}, 'synthetic reply'

    monkeypatch.setattr('utilities.vision_json', vision)
    gateway = SimpleNamespace(record_usage=lambda *args: None)
    def run():
        return run_extract(snapshot, manifest, selection, out, gateway, config,
                           loader=current_loader(), core=core, progress=lambda _: None)

    result = run()
    book = result['books']['selected']
    expected = deepcopy(original)
    if outcome == 'merge':
        expected[0]['human'] = False
    assert book['characters']['value'] == expected
    assert original[0]['human'] is True
    assert result['incomplete'] is (outcome == 'pending')
    if outcome == 'off':
        assert not calls and 'human_vision' not in book and 'characters_initial' not in book
    else:
        assert book['characters_initial']['value'] == original
        assert calls[0][0] == [b'selected:3', b'selected:7']
        assert calls[0][2]['max_edge'] == 784 and calls[0][2]['max_tokens'] == 2048
        assert 'GOLD MUST NOT LEAK' not in calls[0][1]
        if outcome not in {'failed', 'pending'}:
            assert book['human_vision']['raw_response'] == 'synthetic reply'
        if outcome != 'pending':
            assert run() == result and len(calls) == 1  # Replay never reapplies to the original.
