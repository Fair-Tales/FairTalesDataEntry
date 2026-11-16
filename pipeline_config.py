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
EXTRACTION_OCR_MAX_TOKENS = 2048
LOCATE_MAX_EDGE = 784
AI_MODEL_ALLOWLIST = (
    'claude-opus-4-8', 'claude-sonnet-5', 'claude-sonnet-4-6', 'claude-haiku-4-5',
)
AI_EDGE_MIN, AI_EDGE_MAX = 512, 4096
AI_TOKENS_MIN, AI_TOKENS_MAX = 256, 8192

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
PIPELINE_SETTINGS_DEFAULTS = {
    **AI_SETTINGS_DEFAULTS,
    'prompt_variant': 'rules-plus-icl',
    'page_schema_version': 'legacy-compatible-page-v1',
    'story_context_enabled': False,
    'story_context_model': EXTRACTION_MODEL,
    'story_context_max_tokens': AI_TOKENS_MAX,
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
        elif key.endswith('_max_edge') or key.endswith('_max_tokens'):
            low, high = ((AI_EDGE_MIN, AI_EDGE_MAX) if key.endswith('_max_edge')
                         else (AI_TOKENS_MIN, AI_TOKENS_MAX))
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
    return result
