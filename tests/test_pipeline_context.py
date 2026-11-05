"""Offline context/archival regressions: fakes only, no provider calls."""
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

import extraction_core as core
import utilities
from ai_runtime import extraction_runtime
from conventions import project_analysis_text, project_story


class Client:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.messages = self

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps(self.reply))],
                               stop_reason='end_turn')


@pytest.fixture
def photo():
    output = io.BytesIO()
    Image.new('RGB', (30, 50), 'white').save(output, format='JPEG')
    return output.getvalue()


@pytest.fixture
def settings():
    return {**utilities.AI_SETTINGS_DEFAULTS,
            'story_context_model': 'fixture',
            'story_context_limits': {'max_images': 100, 'max_request_bytes': 32000000,
                                     'max_input_tokens': 200000, 'max_output_tokens': 8192}}


def decision(number=3, story=True):
    return {'page_number': number, 'contains_story': story,
            'page_type': 'narrative' if story else 'uncertain',
            'decision': 'review_required' if story is None else 'resolved',
            'evidence': 'Sequence evidence'}


def test_context_receives_actual_numbers_all_ocr_and_preserves_null(photo, settings):
    pages = [{'page_number': 3, 'text': 'Archived lettering', 'contains_story': True,
              'page_type': 'narrative', 'status': 'completed'}]
    client = Client({'pages': [decision(story=None)]})
    result = core.classify_book_context(pages, [(3, photo)], client, settings, lambda *a: None)
    assert result['status'] == 'completed'
    assert result['unresolved_pages'] == 1
    final = core.apply_context_classification(pages, result)
    assert final[0]['contains_story'] is None
    assert final[0]['text'] == 'Archived lettering'
    assert final[0]['provisional_classification']['contains_story'] is True
    assert pages[0]['contains_story'] is True
    prompt = client.requests[0]['messages'][0]['content'][-1]['text']
    assert '"page_number": 3' in prompt and 'Archived lettering' in prompt
    assert core.extract_characters(final, client, settings, lambda *a: None) == []
    assert len(client.requests) == 1


@pytest.mark.parametrize('reply', [[], {'pages': []}, {'pages': [decision(), decision()]},
                                  {'pages': [decision(2)]}, {'pages': [decision(story='yes')]},
                                  {'pages': [{**decision(), 'page_type': []}]}])
def test_invalid_context_keeps_explicit_provisional_fallback(reply, photo, settings):
    pages = [{'page_number': 3, 'text': 'Keep text', 'contains_story': True}]
    result = core.classify_book_context(pages, [(3, photo)], Client(reply), settings, lambda *a: None)
    assert result['status'] == 'failed'
    assert result['fallback'] is True
    final = core.apply_context_classification(pages, result)
    assert final[0]['contains_story'] is True and final[0]['text'] == 'Keep text'
    assert final[0]['classification_source'] == 'provisional_fallback'


@pytest.mark.parametrize('limit,value', [('max_images', 1), ('max_input_tokens', 10),
                                       ('max_request_bytes', 10), ('max_output_tokens', 16)])
def test_context_limits_fail_before_dispatch(limit, value, photo, settings):
    pages = [{'page_number': n, 'text': 'Text', 'contains_story': True} for n in (3, 8)]
    settings['story_context_limits'][limit] = value
    client = Client({})
    result = core.classify_book_context(pages, [(3, photo), (8, photo)], client, settings, lambda *a: None)
    assert result['error_type'] == 'context_request_limit'
    assert result['fallback'] is True
    assert client.requests == []


def test_page_archival_text_survives_conflicting_flag_and_can_be_reprojected(settings):
    data = {'has_text': False, 'text': 'Sound!\n\nNarration', 'is_story_page': True,
            'page_type': 'narrative', 'regions': [
                {'kind': 'illustration_lettering', 'text': 'Sound!'},
                {'kind': 'narrative', 'text': 'Narration'}]}
    outcome, payload = core.attempt_page_extraction(b'fake', object(), settings, label='fixture',
        strict=True, vision_json_call=lambda *a, **k: (data, json.dumps(data)))
    assert outcome == 'ok' and payload[0] == data['text']
    assert payload.details['has_text'] is True
    assert 'has_text_conflict' in payload.details['validation_flags']
    assert payload.details['raw_page_output'] == data
    projected = project_analysis_text({'text': payload[0], **payload.details}, include_illustration_lettering=False)
    assert projected['analysis_text'] == 'Narration'
    assert project_story('supplemental_activity') is False
    assert project_story('supplemental_activity', include_supplemental=True) is True


def test_durable_payload_round_trip_retains_regions(settings):
    from background_pipeline import _result_to_fields, _fields_to_result
    from extraction_core import PageExtractionPayload, ProcessedPagePayload
    evidence = {'regions': [{'kind': 'narrative', 'text': 'Printed'}],
                'convention_version': 'fixture'}
    payload = PageExtractionPayload('Printed', True, 'narrative', evidence)
    fields = _result_to_fields('ok', payload, None, False)
    recovered = _fields_to_result(fields)[1]
    assert recovered == ('Printed', True, 'narrative')
    assert recovered.details == evidence
    assert ProcessedPagePayload(recovered, False, False).details == evidence


def test_runner_applies_context_before_characters_and_reconciles_after_ocr(tmp_path):
    import hashlib
    from scripts.extraction_eval.runner import run_extract
    from alias_schema import RecordedSuggestions
    data = b'fixture'
    (tmp_path / 'page.jpg').write_bytes(data)
    manifest = {'snapshot_id': 'fixture', 'books': [{'id': 'book', 'pages': [
        {'id': f'p{number}', 'page_number': number,
         'image': {'path': 'page.jpg', 'sha256': hashlib.sha256(data).hexdigest()}}
        for number in (3, 8)]}]}
    events = []
    def metadata(*args):
        events.append('metadata')
        return {'title': 'initial'}
    def page(*args):
        events.append('page')
        return {'status': 'completed', 'text': 'Archive\n\nPublisher', 'contains_story': True,
                'page_type': 'narrative', 'regions': [
                    {'kind': 'narrative', 'text': 'Archive'},
                    {'kind': 'paratext', 'text': 'Publisher'}]}
    def context(pages, images, *args):
        assert [number for number, _ in images] == [3, 8]
        events.append('context')
        return {'status': 'completed', 'fallback': False,
                'pages': [decision(3, None), decision(8, True)]}
    def reconcile(metadata, pages, *args):
        assert all(row['status'] == 'completed' for row in pages)
        events.append('reconcile')
        return {**metadata, 'title': 'reconciled'}
    def characters(pages, *args):
        events.append('characters')
        assert [row['contains_story'] for row in pages] == [None, True]
        return RecordedSuggestions([], raw_response={'characters': [],
            'unresolved_mentions': [{'surface': 'unresolved'}]})
    fake = SimpleNamespace(extract_metadata=metadata, extract_page=page,
        classify_book_context=context, reconcile_metadata=reconcile, extract_characters=characters)
    gateway = SimpleNamespace(record_usage=lambda *a: None, context={})
    config = {'settings': {'story_context_enabled': True, 'metadata_reconciliation_enabled': True}}
    result = run_extract(tmp_path, manifest, {'book_ids': ['book']}, tmp_path,
                         gateway, config, core=fake, isbn_lookup=lambda _: None, progress=lambda _: None)
    assert events == ['metadata', 'page', 'page', 'context', 'reconcile', 'characters']
    book = result['books']['book']
    assert book['metadata']['value']['title'] == 'reconciled'
    assert book['metadata_initial']['value']['title'] == 'initial'
    assert book['characters']['input_pages'] == [{'page_id': 'p8', 'page_number': 8, 'text': 'Archive'}]
    assert book['characters']['raw_response']['unresolved_mentions']
    assert book['pages']['p3']['text'] == 'Archive\n\nPublisher'
    saved = json.loads((tmp_path / 'predictions.json').read_text())
    assert saved['books']['book']['characters']['raw_response']['unresolved_mentions']


def test_truncated_page_and_context_do_not_become_completed_success(photo, settings):
    class Truncated(Client):
        def create(self, **request):
            response = super().create(**request)
            response.stop_reason = 'max_tokens'
            return response
    data = {'has_text': True, 'is_story_page': True, 'text': 'Partial'}
    with extraction_runtime(client=Truncated(data), settings=settings, usage_sink=lambda *a: None):
        outcome, payload = core.attempt_page_extraction(photo, Truncated(data), settings, label='fixture', strict=True)
    assert outcome == 'error'
    assert payload.details['failure_kind'] == 'truncated_response'
    result = core.classify_book_context([{'page_number': 3, 'text': 'archive'}],
        [(3, photo)], Truncated({'pages': [decision()]}), settings, lambda *a: None)
    assert result['status'] == 'failed' and result['fallback'] is True
    assert 'raw_response' in result


@pytest.mark.parametrize('changes', [{'regions': None}, {'regions': [{'kind': 'unknown', 'text': 'Text'}]},
                                     {'regions': [{'kind': [], 'text': 'Text'}]},
                                     {'story_needs_context': None}, {'page_type': 'made_up'},
                                     {'page_type': {}}])
def test_candidate_page_schema_rejects_invalid_structure_without_losing_raw(settings, changes):
    settings['page_schema_version'] = 'page-regions-v1'
    data = {'has_text': True, 'is_story_page': True, 'text': 'Text',
            'page_type': 'narrative', 'story_needs_context': False,
            'regions': [{'kind': 'narrative', 'text': 'Text'}], **changes}
    outcome, payload = core.attempt_page_extraction(b'fake', object(), settings, label='fixture',
        strict=True, vision_json_call=lambda *a, **k: (data, json.dumps(data)))
    assert outcome == 'error'
    assert payload.details['raw_page_output']['text'] == 'Text'
