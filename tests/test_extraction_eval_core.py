"""Fixture-only parity and storage-isolation checks for shared extraction."""

import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

import image_processing
import utilities
from ai_runtime import current_runtime, extraction_runtime
from extraction_core import (
    attempt_page_extraction, extract_characters, extract_metadata, extract_page,
)


class FakeClient:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []
        self.messages = self

    def create(self, **request):
        self.requests.append(request)
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        raw = reply if isinstance(reply, str) else json.dumps(reply)
        return SimpleNamespace(
            id=f'fake-{len(self.requests)}', stop_reason='end_turn',
            content=[SimpleNamespace(type='text', text=raw)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0),
        )


@pytest.fixture
def image_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (40, 60), 'white').save(buffer, format='JPEG')
    return buffer.getvalue()


@pytest.fixture
def isolated(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Extraction attempted application storage/network access')

    monkeypatch.setattr(utilities, 'FirestoreWrapper', forbidden)
    monkeypatch.setattr(utilities, '_read_ai_settings_doc', forbidden)
    monkeypatch.setattr(utilities, 'get_s3_filesystem', forbidden)
    monkeypatch.setattr(utilities, 'lookup_isbn', forbidden)
    monkeypatch.setattr(image_processing, 'correct_book_page',
                        lambda image: (None, False, False))
    settings = dict(utilities.AI_SETTINGS_DEFAULTS)
    settings['enable_rotation_correction'] = False
    return settings


def test_page_uses_shared_vision_and_local_usage(image_bytes, isolated):
    client = FakeClient({'has_text': True, 'text': ' Once upon a time ',
                         'is_story_page': True, 'page_type': 'story'})
    usage = []
    result = extract_page(image_bytes, client, isolated,
                          lambda *args: usage.append(args))
    assert result['status'] == 'completed'
    assert result['text'] == 'Once upon a time'
    assert result['contains_story'] is True
    assert len(usage) == 1
    assert usage[0][1:] == (isolated['extraction_model'], 'page_extraction')
    assert client.requests[0]['model'] == isolated['extraction_model']
    assert current_runtime() is None


def test_rotation_and_ocr_both_use_metered_shared_path(image_bytes, isolated):
    isolated['enable_rotation_correction'] = True
    client = FakeClient('UPRIGHT', {'has_text': True, 'text': 'hello', 'is_story_page': True})
    usage = []
    result = extract_page(image_bytes, client, isolated,
                          lambda *args: usage.append(args))
    assert result['rotation_uncertain'] is False
    assert [call[1] for call in usage] == [isolated['rotation_model'],
                                         isolated['extraction_model']]


@pytest.mark.parametrize('reply', ['not valid JSON', [], {},
                                  {'has_text': 'false', 'is_story_page': True, 'text': ''},
                                  {'has_text': True, 'is_story_page': True, 'text': 123}])
def test_bad_ocr_is_failure_and_not_wordless_agreement(reply, image_bytes, isolated):
    client = FakeClient(reply)
    usage = []
    result = extract_page(image_bytes, client, isolated,
                          lambda *args: usage.append(args))
    assert result['status'] == 'failed'
    assert result['text'] is None
    assert result['contains_story'] is None
    assert result['error_type'] == 'parse_error'
    assert len(usage) == 1


@pytest.mark.parametrize('reply, strict', [(None, False), ({}, True)],
                         ids=['unusable-json', 'invalid-strict-schema'])
def test_parse_failures_use_shared_error_constant(
        reply, strict, image_bytes, isolated, monkeypatch):
    from data_structures.extraction_error_log import ExtractionErrorLog

    monkeypatch.setattr(ExtractionErrorLog, 'ERROR_PARSE', 'changed-parse-error')
    outcome, payload = attempt_page_extraction(
        image_bytes, FakeClient(), isolated, label='page_extraction', strict=strict,
        vision_json_call=lambda *args, **kwargs: (reply, 'invalid response'),
    )

    assert outcome == 'error'
    assert payload[0] == 'changed-parse-error'


@pytest.mark.parametrize('missing_key', [
    'extraction_model', 'extraction_max_edge', 'extraction_max_tokens',
])
@pytest.mark.parametrize('options', [{}, {'strict': True}],
                         ids=['application-default', 'strict-evaluation'])
def test_required_page_settings_fail_before_vision_call(
        missing_key, options, image_bytes, isolated):
    del isolated[missing_key]
    calls = []

    def vision_json_call(*args, **kwargs):
        calls.append((args, kwargs))
        return {'has_text': True, 'is_story_page': True, 'text': 'Hello'}, ''

    with pytest.raises(KeyError) as exc_info:
        attempt_page_extraction(
            image_bytes, FakeClient(), isolated, label='page_extraction',
            vision_json_call=vision_json_call, **options,
        )

    assert exc_info.value.args == (missing_key,)
    assert calls == []


def test_wordless_guard_matches_application_wrapper(image_bytes, isolated):
    from pages.uploader import attempt_page_extraction

    reply = {'has_text': False, 'text': 'invented description',
             'is_story_page': True, 'page_type': 'story'}
    client = FakeClient(reply, reply)
    usage = []
    sink = lambda *args: usage.append(args)
    with extraction_runtime(client=client, settings=isolated, usage_sink=sink):
        outcome, app_payload = attempt_page_extraction(
            image_bytes, client, isolated, label='page_extraction',
        )
    result = extract_page(image_bytes, client, isolated, sink)
    assert outcome == 'ok'
    assert app_payload == (result['text'], result['contains_story'], result['page_type'])
    assert result['text'] == ''
    assert client.requests[0] == client.requests[1]


def test_metadata_injects_settings_lookup_and_value_sources(image_bytes, isolated):
    client = FakeClient(
        {'title_page': 1, 'copyright_page': 2},
        {'title': 'A story', 'authors': [], 'illustrators': ['Artist']},
        {'publisher': 'Imprint', 'first_published_year': 2001,
         'isbn': '9780306406157'},
    )
    looked_up = []
    usage = []

    def lookup(isbn):
        looked_up.append(isbn)
        return {'title': 'Different edition', 'authors': ['Writer'],
                'publisher': 'Another imprint'}

    result = extract_metadata([(7, image_bytes), (3, image_bytes)], client,
                              isolated, lambda *args: usage.append(args), lookup)
    assert looked_up == ['9780306406157']
    assert result['title'] == 'A story'
    assert result['authors'] == ['Writer']
    assert result['publisher'] == 'Imprint'
    assert result['published_year'] == 2001
    assert result['sources']['title']['page_number'] == 3
    assert result['sources']['publisher']['page_number'] == 7
    assert result['sources']['authors']['source'] == 'isbn_lookup'
    assert len(usage) == 3
    assert [request['model'] for request in client.requests] == [
        isolated['locate_model'], isolated['metadata_model'], isolated['metadata_model'],
    ]


def test_characters_consume_only_successful_predicted_story_text(isolated):
    client = FakeClient({'characters': [{'name': 'Fox', 'aliases': ['the fox']}]})
    pages = [
        {'page_number': 3, 'contains_story': True, 'text': 'Fox ran.'},
        {'page_number': 1, 'contains_story': False, 'text': 'Publisher'},
        {'page_number': 2, 'contains_story': True, 'text': 'FAILED TEXT', 'status': 'failed'},
        {'page_number': 4, 'contains_story': True, 'text': ''},
    ]
    usage = []
    result = extract_characters(pages, client, isolated, lambda *args: usage.append(args))
    assert result[0]['name'] == 'Fox'
    prompt = client.requests[0]['messages'][0]['content']
    assert 'Fox ran.' in prompt
    assert 'FAILED TEXT' not in prompt
    assert 'Publisher' not in prompt
    assert len(usage) == 1


def test_no_predicted_story_text_avoids_paid_character_call(isolated):
    client = FakeClient()
    assert extract_characters([], client, isolated, lambda *args: None) == []
    assert client.requests == []


def test_runtime_restores_dependencies_and_records_unknown_usage(isolated):
    outer, inner = FakeClient(), FakeClient()
    usage = []
    with extraction_runtime(client=outer, settings=isolated,
                            usage_sink=lambda *args: usage.append(args)):
        assert utilities.get_anthropic_client() is outer
        assert utilities.get_ai_settings() == isolated
        with extraction_runtime(client=inner, settings={'frozen': True},
                                usage_sink=lambda *args: None):
            assert utilities.get_anthropic_client() is inner
            assert utilities.get_ai_settings() == {'frozen': True}
        assert utilities.get_anthropic_client() is outer
        utilities._log_usage(SimpleNamespace(), 'frozen-model', 'unknown-usage')
    assert len(usage) == 1
    assert current_runtime() is None


def test_accounting_or_gateway_control_failures_propagate(image_bytes, isolated):
    class BudgetStopped(RuntimeError):
        pass

    client = FakeClient(BudgetStopped('cap reached'))
    with pytest.raises(BudgetStopped, match='cap reached'):
        extract_page(image_bytes, client, isolated, lambda *args: None)
    assert current_runtime() is None

    def failed_sink(*args):
        raise OSError('local ledger unavailable')

    client = FakeClient({'has_text': True, 'text': 'Hello', 'is_story_page': True})
    with pytest.raises(OSError, match='ledger unavailable'):
        extract_page(image_bytes, client, isolated, failed_sink)


def test_shared_isbn_lookup_exposes_raw_provider_response(monkeypatch):
    raw = {'totalItems': 1, 'items': [{'id': 'provider-volume', 'volumeInfo': {
        'title': 'A story', 'authors': ['Writer'], 'publisher': 'Imprint',
        'publishedDate': '2001-04-05', 'language': 'en',
    }}]}
    calls = []

    def fixture_urlopen(url, timeout):
        calls.append((url, timeout))
        return io.BytesIO(json.dumps(raw).encode())

    monkeypatch.setattr(utilities.urllib.request, 'urlopen', fixture_urlopen)
    responses = []
    result = utilities.lookup_isbn('9780306406157', response_sink=responses.append)
    assert result == {'title': 'A story', 'authors': ['Writer'],
                      'publisher': 'Imprint', 'published_date': '2001-04-05'}
    assert responses == [raw]
    assert responses[0]['items'][0]['volumeInfo']['language'] == 'en'
    assert len(calls) == 1
    # Existing application callers keep the same return contract.
    assert utilities.lookup_isbn('9780306406157') == result


def test_isbn_raw_response_survives_metadata_parse_failure(monkeypatch, caplog):
    raw = {'totalItems': 1, 'items': []}
    monkeypatch.setattr(utilities.urllib.request, 'urlopen',
                        lambda *args, **kwargs: io.BytesIO(json.dumps(raw).encode()))
    responses = []
    assert utilities.lookup_isbn('9780306406157', response_sink=responses.append) is None
    assert responses == [raw]
    assert 'lookup_isbn failed' in caplog.text
