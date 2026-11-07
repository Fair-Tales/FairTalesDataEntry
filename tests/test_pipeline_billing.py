"""Billing guard tests use local fake credentials and fake provider failures."""
import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.extraction_eval.billing import BillingStopped, validate_billing
from scripts.extraction_eval.gateway import Gateway
from scripts.extraction_eval.store import Ledger


def configuration():
    return {'billing': {'funding': 'subscription_credits', 'confirmed': True,
                        'organisation': 'fixture-org', 'project': 'fixture-project',
                        'confirmed_by': 'owner', 'confirmed_at': '2026-11-05',
                        'secret_name': 'EVAL_ANTHROPIC_API_KEY',
                        'key_sha256': hashlib.sha256(b'fixture-only').hexdigest()},
            'settings': {'extraction_model': 'fixture-model'},
            'max_attempts': 3,
            'pricing': {'models': {'fixture-model': {
                'input': 1, 'output': 1, 'cache_read': 1, 'cache_write': 1,
                'max_input_tokens': 100, 'max_output_tokens': 100,
                'max_images': 100, 'max_request_bytes': 10000}}}}


def test_billing_requires_explicit_binding_and_never_falls_back():
    config = configuration()
    assert validate_billing(config, {'EVAL_ANTHROPIC_API_KEY': 'fixture-only'}) == 'fixture-only'
    with pytest.raises(ValueError, match='absent'):
        validate_billing(config, {'ANTHROPIC_API_KEY': 'production-fixture'})
    with pytest.raises(ValueError, match='does not match'):
        validate_billing(config, {'EVAL_ANTHROPIC_API_KEY': 'different-fixture'})
    for key in config['billing']:
        missing = deepcopy(config)
        del missing['billing'][key]
        with pytest.raises(ValueError):
            validate_billing(missing)


@pytest.mark.parametrize('code,message', [(400, 'Your credit balance is too low'),
    (402, 'payment required'), (401, 'invalid key'), (403, 'forbidden'),
    (429, 'billing usage limit')])
def test_billing_rejection_halts_gateway_without_retry_or_key_switch(tmp_path, code, message):
    class Rejected(RuntimeError):
        status_code = code
    calls = []
    def dispatch(**request):
        calls.append(request)
        raise Rejected(message)
    provider = SimpleNamespace(messages=SimpleNamespace(create=dispatch))
    ledger = Ledger(tmp_path / 'spend.sqlite', 'test-run', 1, 1)
    try:
        gateway = Gateway(provider, ledger, configuration())
        request = {'model': 'fixture-model', 'max_tokens': 10,
                   'messages': [{'role': 'user', 'content': 'fixture'}]}
        with pytest.raises(BillingStopped, match='Subscription credits'):
            gateway.create(**request)
        with pytest.raises(BillingStopped):
            gateway.create(**{**request, 'max_tokens': 20})
        assert len(calls) == 1
        assert ledger.rows()[0]['state'] == 'failed'
        assert ledger.rows()[0]['reserved_usd'] == 0
        replay = Gateway(provider, ledger, configuration())
        with pytest.raises(BillingStopped):
            replay.create(**request)
        assert len(calls) == 1
    finally:
        ledger.close()


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
                         gateway, configuration(), loader=None, core=fake_core, isbn_lookup=lambda _: None,
                         progress=lambda _: None)
    assert result['incomplete'] is True
    assert result['stop_reason'][0] == 'billing_stopped'
    assert result['books']['book']['pages']['p2']['status'] == 'pending'
    assert calls == ['page']
