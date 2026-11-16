"""Billing guard tests use local fake credentials and fake provider failures."""
import hashlib
from types import SimpleNamespace

import pytest

from scripts.extraction_eval.tuning import current_loader


from scripts.extraction_eval.billing import BillingStopped, validate_billing, cycle_budgets
from scripts.extraction_eval.gateway import Gateway
from scripts.extraction_eval.store import Ledger


def configuration():
    return {'billing_cycles': {'anthropic': {'start_day': 1}, 'openai': {'start_day': 10}},
            'settings': {'extraction_model': 'fixture-model'},
            'max_attempts': 3,
            'pricing': {'models': {'fixture-model': {
                'input': 1, 'output': 1, 'cache_read': 1, 'cache_write': 1,
                'max_input_tokens': 100, 'max_output_tokens': 100,
                'max_images': 100, 'max_request_bytes': 10000}}}}


@pytest.mark.parametrize('secrets,name,key', [
    ({'EVAL_ANTHROPIC_API_KEY': 'eval-fixture', 'ANTHROPIC_API_KEY': 'app-fixture'},
     'EVAL_ANTHROPIC_API_KEY', 'eval-fixture'),
    ({'ANTHROPIC_API_KEY': 'app-fixture'}, 'ANTHROPIC_API_KEY', 'app-fixture'),
    ({'EVAL_ANTHROPIC_API_KEY': ' ', 'ANTHROPIC_API_KEY': 'app-fixture'},
     'ANTHROPIC_API_KEY', 'app-fixture'),
])
def test_billing_without_attestation_selects_anthropic_key(secrets, name, key):
    assert validate_billing(configuration(), secrets) == (key, name)


@pytest.mark.parametrize('provider', ['openai', 'openai-responses', 'openai-chat-completions'])
def test_openai_requires_only_dedicated_key(provider):
    config = {**configuration(), 'provider': provider}
    assert validate_billing(config, {'EVAL_OPENAI_API_KEY': 'fixture-only'}) == (
        'fixture-only', 'EVAL_OPENAI_API_KEY')
    for missing in ({'OPENAI_API_KEY': 'fixture-only'},
                    {'EVAL_ANTHROPIC_API_KEY': 'fixture-only'},
                    {'EVAL_OPENAI_API_KEY': '  '}):
        with pytest.raises(ValueError, match='EVAL_OPENAI_API_KEY.*absent') as error:
            validate_billing(config, missing)
        assert 'fixture-only' not in str(error.value)


@pytest.mark.parametrize('code,message', [(400, 'Your credit balance is too low'),
    (402, 'payment required'), (401, 'invalid key'), (403, 'forbidden'),
    (429, 'billing usage limit'), (429, 'insufficient_quota'),
    (429, 'credit_balance_exhausted'), (400, 'enforced_spend_limit_reached')])
def test_billing_rejection_halts_gateway_without_retry_or_key_switch(tmp_path, code, message):
    class Rejected(RuntimeError):
        status_code = code
    calls = []
    def dispatch(**request):
        calls.append(request)
        raise Rejected(message)
    provider = SimpleNamespace(messages=SimpleNamespace(create=dispatch))
    ledger = Ledger(tmp_path / 'spend.sqlite', 'test-run', 1, budgets=cycle_budgets(configuration(), 1))
    try:
        gateway = Gateway(provider, ledger, configuration())
        request = {'model': 'fixture-model', 'max_tokens': 10,
                   'messages': [{'role': 'user', 'content': 'fixture'}]}
        with pytest.raises(BillingStopped, match='Evaluation credits'):
            gateway.create(**request)
        with pytest.raises(BillingStopped):
            gateway.create(**{**request, 'max_tokens': 20})
        assert len(calls) == 1
        assert ledger.rows()[0]['state'] == 'failed'
        assert ledger.rows()[0]['reserved_usd'] > 0
        replay = Gateway(provider, ledger, configuration())
        with pytest.raises(BillingStopped):
            replay.create(**request)
        assert len(calls) == 1
        # The old assertion pinned an indefinite cached stop. Keep the stop
        # until explicit recovery, then prove renewed credit can be used.
        before = ledger.get(ledger.rows()[0]['key'])
        assert ledger.retry_failed()['keys'] == [before['key']]
        assert ledger.get(before['key']) == before
        provider.messages.create = lambda **kwargs: {
            'content': [], 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        recovered = Gateway(provider, ledger, configuration()).create(**request)
        assert not recovered._eval_cached
        assert Gateway(provider, ledger, configuration()).create(**request)._eval_cached
        assert len(ledger.rows()) == 2
    finally:
        ledger.close()


@pytest.mark.usefixtures("synthetic_cohort")
def test_billing_stop_marks_run_incomplete_and_skips_later_work(tmp_path):
    from scripts.extraction_eval.runner import run_extract
    image = tmp_path / 'photo.jpg'
    image.write_bytes(b'fixture')
    manifest = {'snapshot_id': 'fixture', 'books': [{'id': 'book', 'pages': [
        {'id': f'p{n}', 'page_number': n, 'image': {'path': 'photo.jpg',
            'sha256': hashlib.sha256(b'fixture').hexdigest()}} for n in (1, 2)]}]}
    calls = []
    def metadata(*args):
        return {'title': 'Fixture'}
    def page(*args):
        calls.append('page')
        raise BillingStopped('Subscription credits exhausted')
    fake_core = SimpleNamespace(extract_metadata=metadata, extract_page=page,
                                extract_characters=lambda *a: pytest.fail('characters dispatched'))
    gateway = SimpleNamespace(record_usage=lambda *a: None, context={})
    result = run_extract(tmp_path, manifest, {'book_ids': ['book']}, tmp_path,
                         gateway, configuration(), loader=current_loader(), core=fake_core, isbn_lookup=lambda _: None,
                         progress=lambda _: None)
    assert result['incomplete'] is True
    assert result['stop_reason'][0] == 'billing_stopped'
    assert result['books']['book']['pages']['p2']['status'] == 'pending'
    assert calls == ['page']


@pytest.mark.parametrize('evaluation_key', [None, 'eval-fixture'])
def test_anthropic_factory_uses_selected_key(monkeypatch, evaluation_key, capsys, caplog):
    from scripts import data_cleanup
    from scripts.extraction_eval import cli
    import utilities
    secrets = {'ANTHROPIC_API_KEY': 'app-fixture'}
    if evaluation_key:
        secrets['EVAL_ANTHROPIC_API_KEY'] = evaluation_key
    monkeypatch.setattr(data_cleanup, 'load_secrets', lambda path: secrets)
    client = object()
    def create_client(**kwargs):
        assert kwargs == {'api_key': evaluation_key or 'app-fixture', 'max_retries': 0}
        return client
    monkeypatch.setattr(utilities, 'get_anthropic_client', create_client)
    assert cli._live_provider('unused-path', configuration()) is client
    captured = capsys.readouterr()
    for key in secrets.values():
        assert key not in captured.out + captured.err + caplog.text
