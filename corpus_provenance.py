"""Small, storage-free provenance records shared by corpus writers."""
from datetime import datetime, timezone

# Keep in step with scripts/extraction_eval/outcomes.py; the app never imports it.
FAILURE_KINDS = ('provider_error', 'output_blocked', 'refusal', 'truncated',
                 'invalid_response', 'outcome_unknown', 'missing_input', 'request_limit')


def human_origin(account):
    return {'source': 'human', 'account': account or 'unknown',
            'timestamp': datetime.now(timezone.utc).isoformat()}


def pipeline_origin(manifest, *, requested_model=None, served_model=None):
    return {'source': 'ai_pipeline', 'model': served_model or 'unknown',
            'requested_model': requested_model or 'unknown',
            'prompt_variant': manifest.get('prompt_variant', 'unknown'),
            'prompt_version': manifest.get('prompt_version', 'unknown'),
            'prompt_sha256': manifest.get('rendered_prompt_sha256'),
            'timestamp': datetime.now(timezone.utc).isoformat()}


def page_failure_kind(payload):
    details = getattr(payload, 'details', {})
    kind = details.get('failure_kind')
    if kind in FAILURE_KINDS:
        return kind
    if kind == 'truncated_response':
        return 'truncated'
    if kind == 'invalid_json_response' or payload[0] in ('parse_error', 'ValueError', 'TypeError'):
        return 'invalid_response'
    return 'provider_error'


def page_extraction_fields(outcome, payload):
    """Persistent status and call evidence for a terminal OCR result."""
    details = getattr(payload, 'details', {})
    origin = details.get('value_origin', {'source': 'ai_pipeline', 'model': 'unknown',
                                         'prompt_variant': 'unknown', 'prompt_version': 'unknown',
                                         'timestamp': datetime.now(timezone.utc).isoformat()})
    return {
        'extraction_status': 'succeeded' if outcome == 'ok' else 'failed',
        'extraction_failure_kind': None if outcome == 'ok' else page_failure_kind(payload),
        'extraction_evidence': details or None,
        'value_provenance': {field: dict(origin) for field in ('text', 'contains_story')},
    }


def apply_page_extraction(page, outcome, payload):
    """Apply one terminal OCR result before initial registration, including failure."""
    for field, value in page_extraction_fields(outcome, payload).items():
        setattr(page, field, value)
    if outcome == 'ok':
        page.text, page.contains_story = payload[:2]


def character_attribute_values(suggestion):
    """Only actual model codes are data; absent or invalid codes stay null."""
    from text_content import CharacterForm

    gender = suggestion.get('gender')
    return {
        'gender': gender if gender in CharacterForm.gender_options else None,
        **{field: suggestion.get(field) if type(suggestion.get(field)) is bool else None
           for field in ('human', 'protagonist', 'plural')},
    }


def apply_detection_origin(entity, suggestion, *, reviewed=False, account=None):
    """Retain the producing call even when a reviewer changes individual values."""
    origin = suggestion.get('value_origin') or {
        'source': 'ai_pipeline', 'model': 'unknown', 'prompt_variant': 'unknown',
        'prompt_version': 'unknown', 'timestamp': datetime.now(timezone.utc).isoformat()}
    entity.source = 'ai_pipeline'
    if entity.belongs_to_collection == 'characters':
        suggestion = {**suggestion, **character_attribute_values(suggestion)}
    values = {}
    for field in entity.provenance_fields:
        if field not in suggestion:
            continue
        entry = dict(origin)
        if suggestion[field] is None:
            entry['status'] = 'not_supplied'
        if reviewed:
            entry['reviewed_by'] = account or 'unknown'
            entry['reviewed_at'] = datetime.now(timezone.utc).isoformat()
            if getattr(entity, field) != suggestion[field]:
                entry = {**human_origin(account), 'generated_from': entry}
        values[field] = entry
    entity.value_provenance = values
