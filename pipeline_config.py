"""Shared pipeline settings; no application, storage or provider dependencies.

The twelve admin defaults and the previously implicit stage defaults are kept
separate only to preserve the admin page's existing fields. Evaluation may
add model IDs from its pinned price/limit table; production keeps its allow-list.
Stage availability is an orchestration concern, not a setting's validity:
production has no whole-book context stage and its existing late metadata pass
does not consult metadata_reconciliation_enabled. Both switches below retain
the harness's existing default of False; production scheduling is unchanged.
"""
from collections.abc import Mapping
from copy import deepcopy

from text_content.pipeline_prompts import PROMPT_VARIANTS
from text_content.pipeline_runtime import (
    PIPELINE_SETTINGS_OBJECT, PIPELINE_SETTING_UNKNOWN, PIPELINE_SETTING_INVALID,
)

EXTRACTION_MODEL = 'claude-sonnet-5'
EXTRACTION_OCR_MAX_TOKENS = 8192
LOCATE_MAX_EDGE = 784
AI_MODEL_ALLOWLIST = (
    'claude-opus-4-8', 'claude-sonnet-5', 'claude-sonnet-4-6', 'claude-haiku-4-5',
)
AI_EDGE_MIN, AI_EDGE_MAX = 512, 4096
# Stay below the SDK's default-timeout non-streaming limit: 128000 * 600 / 3600 ≈ 21333.
AI_TOKENS_MIN, AI_TOKENS_MAX = 256, 20000

# Existing public admin defaults, re-exported by utilities for its callers.
AI_SETTINGS_DEFAULTS = {
    'extraction_model': EXTRACTION_MODEL,
    'metadata_model': EXTRACTION_MODEL,
    'character_detection_model': EXTRACTION_MODEL,
    'locate_model': 'claude-haiku-4-5',
    'rotation_model': 'claude-sonnet-4-6',
    'crop_quality_model': 'claude-haiku-4-5',
    'theme_model': 'claude-sonnet-4-6',
    'extraction_max_edge': 2000,
    'extraction_max_tokens': EXTRACTION_OCR_MAX_TOKENS,
    'locate_max_edge': LOCATE_MAX_EDGE,
    'enable_rotation_correction': True,
    'enable_crop_quality_gate': True,
}
# One group per pipeline stage. Rotation's three probes share one group.
STAGE_MAX_TOKENS = {
    'extraction': EXTRACTION_OCR_MAX_TOKENS, 'metadata': 20000, 'character_detection': 20000,
    'locate': 128, 'rotation': 16, 'crop_quality': 16, 'theme': 512,
    'story_context': 20000,
}
OPENAI_OUTPUT_FLOOR = 25000
GENERATION_DEFAULTS = {
    f'{stage}_{field}': value
    for stage, tokens in STAGE_MAX_TOKENS.items()
    for field, value in (('max_tokens', tokens), ('thinking', 'default'), ('effort', 'default'),
                         ('openai_reasoning_effort', 'low'), ('openai_output_floor', OPENAI_OUTPUT_FLOOR))
}

# Official model pages, checked 2026-11-16; citations in extraction_eval/README.
OPENAI_REASONING_EFFORTS = {
    'gpt-6-astra': ('low', 'medium', 'high', 'xhigh', 'max'),
    'gpt-6.1-sol': ('low', 'medium', 'high', 'xhigh', 'max'),
    'gpt-6.1-terra': ('none', 'low', 'medium', 'high', 'xhigh', 'max'),
    'gpt-6-luna': ('none', 'low', 'medium', 'high', 'xhigh', 'max'),
}


def validate_openai_generation(model, effort, floor):
    if effort not in OPENAI_REASONING_EFFORTS.get(model, ()):
        raise ValueError(PIPELINE_SETTING_INVALID.format(
            key=f'{model}.openai_reasoning_effort', expected='documented model effort'))
    if type(floor) is not int or not 256 <= floor <= 128000:
        raise ValueError(PIPELINE_SETTING_INVALID.format(
            key='openai_output_floor', expected='integer from 256 to 128000'))

# Offline source: bundled claude-api reference 2.1.322, shared/models.md and
# shared/model-migration.md (Sonnet 5, Sonnet 4.6, Opus 4.7/4.8 sections).
# See the extraction_eval README for source paths and documented limitations.
# These are supported overrides, not an exhaustive provider capability registry.
ANTHROPIC_GENERATION = {
    'claude-sonnet-5': (('adaptive', 'disabled'), ('low', 'medium', 'high', 'xhigh', 'max'), 'adaptive', 'high'),
    'claude-sonnet-4-6': (('adaptive', 'disabled'), ('low', 'medium', 'high', 'max'), 'disabled', 'high'),
    'claude-opus-4-8': (('adaptive', 'disabled'), ('low', 'medium', 'high', 'xhigh', 'max'), 'disabled', 'high'),
    'claude-haiku-4-5': (('disabled',), (), 'disabled', None),
}


def validate_generation(model, thinking='default', effort='default'):
    """Refuse unsupported overrides; unknown models may retain omission only.

    Manual thinking needs a budget and is outside this setting surface. OpenAI
    reasoning belongs in its own provider mapping (A12a), not this one.
    """
    modes, efforts, _, _ = ANTHROPIC_GENERATION.get(model, ((), (), None, None))
    for field, value, allowed in (('thinking', thinking, modes), ('effort', effort, efforts)):
        if value != 'default' and value not in allowed:
            raise ValueError(PIPELINE_SETTING_INVALID.format(
                key=f'{model}.{field}', expected=', '.join(('default', *allowed))))


def generation_request(settings, stage, *, model=None):
    """Provider kwargs; Anthropic defaults retain their previous request body."""
    model = model or settings.get(f'{stage}_model', PIPELINE_SETTINGS_DEFAULTS[f'{stage}_model'])
    thinking = settings.get(f'{stage}_thinking', 'default')
    effort = settings.get(f'{stage}_effort', 'default')
    validate_generation(model, thinking, effort)
    request = dict(model=model, max_tokens=settings.get(f'{stage}_max_tokens', STAGE_MAX_TOKENS[stage]))
    if model in OPENAI_REASONING_EFFORTS:
        reasoning = settings.get(f'{stage}_openai_reasoning_effort', 'low')
        floor = settings.get(f'{stage}_openai_output_floor', OPENAI_OUTPUT_FLOOR)
        validate_openai_generation(model, reasoning, floor)
        request.update(reasoning={'effort': reasoning}, openai_output_floor=floor)
        request['max_tokens'] = max(request['max_tokens'], floor)
    if thinking != 'default':
        request['thinking'] = {'type': thinking}
    if effort != 'default':
        request['output_config'] = {'effort': effort}
    return request


def requested_generation(request):
    """Record omission honestly, plus the locally documented default meaning."""
    model = request.get('model')
    if model in OPENAI_REASONING_EFFORTS:
        return dict(model=model, max_tokens=request.get('max_tokens'),
                    reasoning=request.get('reasoning'),
                    openai_output_floor=request.get('openai_output_floor'),
                    prompt_cache_options=request.get('prompt_cache_options'))
    _, _, thinking_default, effort_default = ANTHROPIC_GENERATION.get(model, ((), (), None, None))
    return dict(model=model, max_tokens=request.get('max_tokens'),
                thinking=request.get('thinking', {}).get('type', 'default'),
                effort=request.get('output_config', {}).get('effort', 'default'),
                documented_defaults=dict(thinking=thinking_default, effort=effort_default))


def validate_generation_request(request):
    """Validate the actual Anthropic body before a gateway reserves anything."""
    thinking = request.get('thinking', {})
    output = request.get('output_config', {})
    if (not isinstance(thinking, dict) or set(thinking) - {'type'}
            or ('thinking' in request and 'type' not in thinking)
            or thinking.get('type') == 'default'
            or not isinstance(output, dict) or set(output) - {'effort'}
            or output.get('effort') == 'default'
            or ('output_config' in request and 'effort' not in output)):
        raise ValueError(PIPELINE_SETTING_INVALID.format(
            key='generation', expected='thinking.type and output_config.effort only'))
    validate_generation(request.get('model'), thinking.get('type', 'default'), output.get('effort', 'default'))
    if any(key in request for key in ('temperature', 'top_p', 'top_k')) and request.get('model') in {'claude-sonnet-5', 'claude-opus-4-8'}:
        raise ValueError(PIPELINE_SETTING_INVALID.format(key='sampling', expected='omitted for this model'))


PIPELINE_SETTINGS_DEFAULTS = {
    **AI_SETTINGS_DEFAULTS,
    **GENERATION_DEFAULTS,
    'prompt_variant': 'legacy-v1',
    'page_schema_version': 'legacy-compatible-page-v1',
    'story_context_enabled': False,
    'story_context_model': EXTRACTION_MODEL,
    'story_context_max_tokens': STAGE_MAX_TOKENS['story_context'],
    'story_context_limits': None,
    'metadata_reconciliation_enabled': False,
}
AI_SETTINGS_MODEL_KEYS = tuple(
    key for key in PIPELINE_SETTINGS_DEFAULTS if key.endswith('_model')
)
_CHOICES = {
    'prompt_variant': PROMPT_VARIANTS,
    'page_schema_version': ('legacy-compatible-page-v1', 'page-regions-v1'),
}
_CONTEXT_LIMITS = ('max_images', 'max_request_bytes', 'max_input_tokens', 'max_output_tokens')


def validate_settings(values, *, model_ids=()):
    """Return defaults plus explicit overrides, refusing every unknown/bad key.

    Integers must be actual integers (not booleans, floats or numeric strings).
    Optional context limits must contain exactly four positive integer limits.
    ``model_ids`` extends the production allow-list with evaluation's pinned
    model registry, so provider experiments need no production UI changes.
    """
    if not isinstance(values, Mapping):
        raise ValueError(PIPELINE_SETTINGS_OBJECT)
    result = deepcopy(PIPELINE_SETTINGS_DEFAULTS)
    models = (*AI_MODEL_ALLOWLIST, *model_ids)
    for key, value in values.items():
        if key not in result:
            raise ValueError(PIPELINE_SETTING_UNKNOWN.format(key=key))
        if key in AI_SETTINGS_MODEL_KEYS:
            valid = isinstance(value, str) and value in models
            expected = ', '.join(models)
        elif key in _CHOICES:
            valid = isinstance(value, str) and value in _CHOICES[key]
            expected = ', '.join(_CHOICES[key])
        elif key.endswith('_openai_reasoning_effort'):
            valid = isinstance(value, str) and value in ('none', 'low', 'medium', 'high', 'xhigh', 'max')
            expected = 'none, low, medium, high, xhigh, max'
        elif key.endswith('_openai_output_floor'):
            valid = type(value) is int and 256 <= value <= 128000
            expected = 'integer from 256 to 128000'
        elif key.endswith('_thinking') or key.endswith('_effort'):
            choices = (('default', 'adaptive', 'disabled') if key.endswith('_thinking')
                       else ('default', 'low', 'medium', 'high', 'xhigh', 'max'))
            valid = isinstance(value, str) and value in choices
            expected = ', '.join(choices)
        elif key.endswith('_max_edge') or key.endswith('_max_tokens'):
            low, high = ((AI_EDGE_MIN, AI_EDGE_MAX) if key.endswith('_max_edge')
                         else (AI_TOKENS_MIN, AI_TOKENS_MAX))
            if key in {'rotation_max_tokens', 'crop_quality_max_tokens', 'locate_max_tokens'}:
                low = 1
            valid = type(value) is int and low <= value <= high
            expected = f'integer from {low} to {high}'
        elif key == 'story_context_limits':
            valid = value is None or (
                isinstance(value, dict) and set(value) == set(_CONTEXT_LIMITS)
                and all(type(value[name]) is int and value[name] > 0 for name in _CONTEXT_LIMITS)
            )
            expected = 'null or positive integer limits: ' + ', '.join(_CONTEXT_LIMITS)
        else:
            valid = type(value) is bool
            expected = 'boolean'
        if not valid:
            raise ValueError(PIPELINE_SETTING_INVALID.format(key=key, expected=expected))
        result[key] = deepcopy(value)
    for stage in STAGE_MAX_TOKENS:
        validate_generation(result[f'{stage}_model'], result[f'{stage}_thinking'], result[f'{stage}_effort'])
        if result[f'{stage}_model'] in OPENAI_REASONING_EFFORTS:
            validate_openai_generation(result[f'{stage}_model'],
                                       result[f'{stage}_openai_reasoning_effort'],
                                       result[f'{stage}_openai_output_floor'])
    return result
