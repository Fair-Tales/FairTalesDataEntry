"""Offline account binding: no probing, credential discovery or fallback."""
import hashlib
import hmac

from text_content.pipeline_runtime import (
    BILLING_EXHAUSTED as BILLING_EXHAUSTED, BILLING_KEY_MISMATCH, BILLING_KEY_MISSING,
    BILLING_UNVERIFIED,
)

EVALUATION_KEY = 'EVAL_ANTHROPIC_API_KEY'


class BillingStopped(RuntimeError):
    """Fatal run-level failure, deliberately outside ordinary stage failures."""


def validate_billing(config, secrets=None):
    """Bind owner confirmation to one key without claiming remote verification.

    The organisation/project are the owner's recorded Console attestation. The
    fingerprint prevents accidentally dispatching with a different local key.
    No production secret, environment variable or SDK default is consulted.
    """
    billing = config.get('billing') or {}
    fingerprint = billing.get('key_sha256', '')
    if (billing.get('funding') != 'subscription_credits'
            or billing.get('confirmed') is not True
            or billing.get('secret_name') != EVALUATION_KEY
            or not all(isinstance(billing.get(field), str) and billing.get(field).strip()
                       for field in ('organisation', 'project', 'confirmed_by', 'confirmed_at'))
            or not isinstance(fingerprint, str) or len(fingerprint) != 64
            or any(char not in '0123456789abcdef' for char in fingerprint)):
        raise ValueError(BILLING_UNVERIFIED)
    if secrets is None:
        return dict(billing)
    key = secrets.get(EVALUATION_KEY)
    if not isinstance(key, str) or not key.strip():
        raise ValueError(BILLING_KEY_MISSING)
    if not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), fingerprint):
        raise ValueError(BILLING_KEY_MISMATCH)
    return key


def billing_rejection(exc):
    code = getattr(exc, 'status_code', None)
    if code in {401, 402, 403}:
        return True
    message = str(exc).casefold()
    return code in {400, 429} and any(word in message for word in (
        'credit balance', 'insufficient credit', 'credits exhausted',
        'billing', 'payment required', 'spend limit', 'usage limit',
    ))
