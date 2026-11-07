"""Client setup is tested with fake keys and constructors, never live requests."""
import hashlib

import pytest

from scripts.extraction_eval import cli, openai_client


def configuration(provider='openai-responses'):
    return {'provider': provider, 'billing': {
        'funding': 'prepaid_credits', 'confirmed': True,
        'organisation': 'fixture-org', 'project': 'fixture-project',
        'confirmed_by': 'fixture', 'confirmed_at': '2026-11-07',
        'secret_name': 'EVAL_OPENAI_API_KEY',
        'key_sha256': hashlib.sha256(b'fixture-only').hexdigest(),
    }}


@pytest.mark.parametrize('key', [None, '', '  '])
def test_openai_factory_missing_key_fails_before_client_creation(monkeypatch, key):
    monkeypatch.setattr(openai_client.httpx, 'Client',
                        lambda **kw: pytest.fail('Client created without a key'))
    monkeypatch.setattr(openai_client.httpx, 'HTTPTransport',
                        lambda **kw: pytest.fail('Transport created without a key'))
    with pytest.raises(ValueError, match='EVAL_OPENAI_API_KEY.*absent'):
        openai_client.create_openai_client(key)


def test_openai_factory_disables_transport_retries_and_redirect_replay(monkeypatch):
    created = {}
    transport = object()
    client = object()

    def make_transport(**kwargs):
        created['transport'] = kwargs
        return transport

    def make_client(**kwargs):
        created['client'] = kwargs
        return client

    monkeypatch.setattr(openai_client.httpx, 'HTTPTransport', make_transport)
    monkeypatch.setattr(openai_client.httpx, 'Client', make_client)
    assert openai_client.create_openai_client('fixture-only') is client
    assert created['transport'] == {'retries': 0}
    assert created['client']['transport'] is transport
    assert created['client']['base_url'] == 'https://api.openai.com/v1/'
    assert created['client']['headers'] == {'Authorization': 'Bearer fixture-only'}
    assert created['client']['follow_redirects'] is False
    assert created['client']['timeout'].connect == 10
    assert created['client']['timeout'].read == 600


@pytest.mark.parametrize('provider', openai_client.OPENAI_PROVIDERS)
def test_cli_uses_confirmed_openai_key_and_shared_factory(monkeypatch, provider):
    from scripts import data_cleanup
    reads = []
    clients = []
    client = object()

    def read_secrets(path):
        reads.append(path)
        return {'EVAL_OPENAI_API_KEY': 'fixture-only', 'OPENAI_API_KEY': 'unused'}

    def create_client(key):
        clients.append(key)
        return client

    monkeypatch.setattr(data_cleanup, 'load_secrets', read_secrets)
    monkeypatch.setattr(openai_client, 'create_openai_client', create_client)
    monkeypatch.setattr(cli, '_live_anthropic',
                        lambda *a: pytest.fail('Anthropic selected for OpenAI'))
    assert cli._live_provider('fixture-secrets', configuration(provider)) is client
    assert reads == ['fixture-secrets']
    assert clients == ['fixture-only']


def test_cli_openai_missing_key_errors_before_client_creation(monkeypatch):
    from scripts import data_cleanup
    monkeypatch.setattr(data_cleanup, 'load_secrets', lambda path: {})
    monkeypatch.setattr(openai_client, 'create_openai_client',
                        lambda key: pytest.fail('Client created without a key'))
    with pytest.raises(ValueError, match='EVAL_OPENAI_API_KEY.*absent'):
        cli._live_provider('fixture-secrets', configuration())


def test_cli_openai_rejects_unconfirmed_billing_before_secrets(monkeypatch):
    from scripts import data_cleanup
    monkeypatch.setattr(data_cleanup, 'load_secrets',
                        lambda path: pytest.fail('Unconfirmed secrets read'))
    config = configuration()
    config['billing']['confirmed'] = False
    with pytest.raises(ValueError, match='confirmed credit billing'):
        cli._live_provider('fixture-secrets', config)
