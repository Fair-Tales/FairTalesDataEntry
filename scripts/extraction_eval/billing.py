"""Offline account binding: no probing, credential discovery or fallback."""
import hashlib
import hmac
from calendar import monthrange
from datetime import datetime, timezone
from decimal import Decimal

from text_content.pipeline_runtime import (
    BILLING_EXHAUSTED as BILLING_EXHAUSTED, BILLING_KEY_MISMATCH, BILLING_KEY_MISSING,
    BILLING_UNVERIFIED, EVALUATION_PROVIDER_UNSUPPORTED,
    CYCLE_REQUIRED, CYCLE_CAP_INVALID, CYCLE_CAP_TOO_HIGH,
)
from .openai_client import OPENAI_PROVIDERS

EVALUATION_KEY = 'EVAL_ANTHROPIC_API_KEY'
DEFAULT_CYCLE_CAPS = {'anthropic': 150, 'openai': 200}


def billing_provider(provider):
    if provider in OPENAI_PROVIDERS:
        return 'openai'
    if provider in {'anthropic', 'anthropic-messages'}:
        return 'anthropic'
    raise ValueError(EVALUATION_PROVIDER_UNSUPPORTED.format(provider=provider))


def cycle_budgets(config, cap_override=None, *, required=False):
    """Validate local cycle settings; no balance or billing API is consulted."""
    selected = billing_provider(config.get('provider', 'anthropic-messages'))
    budgets = {}
    configured = config.get('billing_cycles') or {}
    if not isinstance(configured, dict):
        raise ValueError(CYCLE_REQUIRED.format(provider=selected))
    for provider, settings in configured.items():
        if provider not in DEFAULT_CYCLE_CAPS:
            raise ValueError(EVALUATION_PROVIDER_UNSUPPORTED.format(provider=provider))
        day = settings.get('start_day') if isinstance(settings, dict) else None
        if type(day) is not int or not 1 <= day <= 31:
            raise ValueError(CYCLE_REQUIRED.format(provider=provider))
        maximum = settings.get('cap_usd', DEFAULT_CYCLE_CAPS[provider])
        amount = cap_override if provider == selected and cap_override is not None else maximum
        for value in (maximum, amount):
            if (isinstance(value, bool) or not isinstance(value, (int, float, Decimal))
                    or not Decimal(str(value)).is_finite() or value < 0):
                raise ValueError(CYCLE_CAP_INVALID)
        if amount > maximum:
            raise ValueError(CYCLE_CAP_TOO_HIGH.format(provider=provider, cap=maximum))
        budgets[provider] = {'start_day': day, 'cap_usd': maximum, 'effective_cap_usd': amount}
    if (required or cap_override is not None) and selected not in budgets:
        raise ValueError(CYCLE_REQUIRED.format(provider=selected))
    return budgets


def cycle_bounds(start_day, now=None):
    """UTC half-open cycle, clamping the configured day separately each month."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    def boundary(year, month):
        return datetime(year, month, min(start_day, monthrange(year, month)[1]), tzinfo=timezone.utc)
    start = boundary(now.year, now.month)
    if now < start:
        start = boundary(now.year - (now.month == 1), (now.month - 2) % 12 + 1)
    end = boundary(start.year + (start.month == 12), start.month % 12 + 1)
    return start, end


class BillingStopped(RuntimeError):
    """Fatal run-level failure, deliberately outside ordinary stage failures."""


def validate_billing(config, secrets=None):
    """Bind owner confirmation to one key without claiming remote verification.

    The organisation/project are the owner's recorded Console attestation. The
    fingerprint prevents accidentally dispatching with a different local key.
    No production secret, environment variable or SDK default is consulted.
    """
    cycle_budgets(config, required=True)
    provider = config.get('provider', 'anthropic-messages')
    if provider in OPENAI_PROVIDERS:
        evaluation_key = 'EVAL_OPENAI_API_KEY'
        funding = ('subscription_credits', 'prepaid_credits')
    elif provider in {'anthropic', 'anthropic-messages'}:
        evaluation_key = EVALUATION_KEY
        funding = ('subscription_credits',)
    else:
        raise ValueError(EVALUATION_PROVIDER_UNSUPPORTED.format(provider=provider))
    billing = config.get('billing') or {}
    fingerprint = billing.get('key_sha256', '')
    if (billing.get('funding') not in funding
            or billing.get('confirmed') is not True
            or billing.get('secret_name') != evaluation_key
            or not all(isinstance(billing.get(field), str) and billing.get(field).strip()
                       for field in ('organisation', 'project', 'confirmed_by', 'confirmed_at'))
            or not isinstance(fingerprint, str) or len(fingerprint) != 64
            or any(char not in '0123456789abcdef' for char in fingerprint)):
        raise ValueError(BILLING_UNVERIFIED)
    if secrets is None:
        return dict(billing)
    key = secrets.get(evaluation_key)
    if not isinstance(key, str) or not key.strip():
        raise ValueError(BILLING_KEY_MISSING.format(secret_name=evaluation_key))
    if not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), fingerprint):
        raise ValueError(BILLING_KEY_MISMATCH)
    return key


def billing_rejection(exc):
    code = getattr(exc, 'status_code', None)
    if code in {401, 402, 403}:
        return True
    message = f"{exc} {getattr(exc, 'body', '')}".casefold()
    return code in {400, 429} and any(word in message for word in (
        'credit balance', 'insufficient credit', 'credits exhausted',
        'billing', 'payment required', 'spend limit', 'usage limit', 'insufficient_quota',
        'credit_balance_exhausted', 'enforced_spend_limit_reached',
    ))
