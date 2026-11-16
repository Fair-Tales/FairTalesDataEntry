"""Stable evaluation failure kinds, including older prediction records."""

FAILURE_KINDS = ('provider_error', 'output_blocked', 'refusal', 'truncated',
                 'invalid_response', 'outcome_unknown', 'missing_input', 'request_limit')


class EvaluationFailure(ValueError):
    def __init__(self, kind, message, *, raw_response=None):
        super().__init__(message)
        self.failure_kind = kind
        self.raw_response = raw_response


def failure_kind(record):
    """Use explicit evidence; never infer refusal/truncation from empty text."""
    kind = record.get('failure_kind')
    if kind in FAILURE_KINDS:
        return kind
    raw = record.get('raw_response')
    stop = raw.get('stop_reason') if isinstance(raw, dict) else None
    if stop == 'refusal':
        return 'refusal'
    if stop == 'max_tokens' or kind == 'truncated_response':
        return 'truncated'
    error = str(record.get('error', '')).lower()
    if 'output blocked' in error or 'content filtering policy' in error:
        return 'output_blocked'
    if record.get('status') in {'outcome_unknown', 'unknown_outcome'}:
        return 'outcome_unknown'
    if 'filenotfounderror' in error:
        return 'missing_input'
    if 'requestlimiterror' in error:
        return 'request_limit'
    if (kind in {'invalid_json_response', 'salvaged_partial_response', 'invalid_character_structure'}
            or record.get('error_type') == 'parse_error'
            or error.startswith(('jsondecodeerror:', 'valueerror:', 'typeerror:'))):
        return 'invalid_response'
    return 'provider_error'


def normalize_outcome(record):
    """Copy failure metadata without recovering or rewriting cached evidence."""
    if record.get('status') == 'billing_stopped':
        return {**record, 'status': 'failed', 'failure_kind': 'provider_error',
                'halt_reason': 'billing_stopped'}
    if record.get('status') in {'failed', 'outcome_unknown', 'unknown_outcome'}:
        return {**record, 'status': 'failed', 'failure_kind': failure_kind(record)}
    return record
