"""SDK-shaped, fully metered request cache. No clients are created at import."""
from __future__ import annotations

from decimal import Decimal
import json
import sqlite3
from types import SimpleNamespace

from ai_pricing import normalize_usage
from text_content.pipeline_runtime import EVALUATION_CLIENT_MISSING

from .store import CachedFailure, OutcomeUnknown, canonical, digest
from .billing import BILLING_EXHAUSTED, BillingStopped, billing_rejection
from .openai_client import OPENAI_PROVIDERS
from .openai_adapter import prepare_request, response_content


class RequestLimitError(ValueError):
    pass


class DryRunMiss(RuntimeError):
    pass


class ProviderHTTPError(RuntimeError):
    def __init__(self, status_code, body):
        self.status_code = status_code
        super().__init__(f"OpenAI HTTP {status_code}: {body}")


def _namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


def response_dict(response):
    if hasattr(response, "model_dump"):
        result = response.model_dump(mode="json")
    elif isinstance(response, dict):
        result = response.copy()
    else:
        def plain(value):
            if isinstance(value, SimpleNamespace):
                return {key: plain(item) for key, item in vars(value).items()}
            if isinstance(value, list):
                return [plain(item) for item in value]
            return value
        result = plain(response)
    if not isinstance(result, dict):
        raise TypeError("Provider response cannot be serialized")
    result["_request_id"] = getattr(response, "_request_id", result.get("_request_id"))
    return result


def validate_prices(config, *, executing=False):
    pricing = config.get("pricing", {})
    if executing and (pricing.get("verified") is not True or not pricing.get("verified_at") or not pricing.get("source")):
        raise ValueError("Verify current account rates and request limits, then pin pricing.verified=true, verified_at and source in your local config")
    models = {value for key, value in config.get("settings", {}).items() if key.endswith("_model") and key != "theme_model"}
    rates = pricing.get("models", {})
    for model in models:
        row = rates.get(model)
        if not isinstance(row, dict):
            raise ValueError(f"Unknown price for {model}; refusing a zero-price fallback")
        for field in ("input", "output", "cache_read", "cache_write", "max_input_tokens", "max_output_tokens", "max_images", "max_request_bytes"):
            value = row.get(field)
            if field == "cache_write" and value is None and config.get("provider") in OPENAI_PROVIDERS:
                continue  # No write tariff; explicit write usage remains unsettled.
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not Decimal(str(value)).is_finite() or value <= 0:
                raise ValueError(f"Invalid {field} price/limit for {model}")
    return rates


def request_ceiling(request, prices):
    """Use the pinned full input/context bound, never a token estimate as a cap."""
    rate = prices.get(request.get("model"))
    if rate is None:
        raise ValueError(f"Unknown model price: {request.get('model')}")
    # Baseline makes plain messages requests. New billable features need an
    # explicit pricing implementation, rather than accidental under-reservation.
    unsupported = set(request) - {"model", "max_tokens", "messages", "system", "temperature", "top_p", "top_k", "stop_sequences"}
    if unsupported:
        raise RequestLimitError(f"Unpriced request options: {sorted(unsupported)}")
    output = request.get("max_tokens")
    if not isinstance(output, int) or output <= 0 or output > rate.get("max_output_tokens", 0):
        raise RequestLimitError("Requested output exceeds pinned model limit")
    images = sum(1 for message in request.get("messages", []) for block in (message.get("content", []) if isinstance(message.get("content"), list) else []) if isinstance(block, dict) and block.get("type") == "image")
    if images > rate.get("max_images", 0):
        raise RequestLimitError(f"{images} images exceed pinned provider request limit; chunking requires a named pipeline variant")
    if len(json.dumps(request, ensure_ascii=False).encode()) > rate.get("max_request_bytes", 0):
        raise RequestLimitError("Request exceeds pinned byte limit; no automatic chunking")
    return (Decimal(str(rate.get("max_input_tokens"))) * max(Decimal(str(rate[key])) for key in ("input", "cache_read", "cache_write") if rate.get(key) is not None) + output * Decimal(str(rate.get("output")))) / 1_000_000


def usage_charge(response, rate, provider="anthropic-messages"):
    """Settle normalized tokens using the evaluation's pinned per-model rates."""
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise OutcomeUnknown("Missing usage; retaining the request reservation")
    try:
        counts = normalize_usage(usage, provider)
    except ValueError as exc:
        raise OutcomeUnknown(f"Invalid usage; retaining reservation: {exc}") from exc
    cost = Decimal(0)
    for field, count in counts.items():
        if count is None:
            raise OutcomeUnknown(f"Missing {field} usage; retaining reservation")
        if count:
            price = rate.get(field)
            if price is None:
                raise OutcomeUnknown(f"Missing {field} price; retaining reservation")
            cost += count * Decimal(str(price)) / 1_000_000
    # Any unexpected server-side billable tool use must be reconciled manually.
    if usage.get("server_tool_use"):
        raise OutcomeUnknown("Unexpected billable server tools; reconcile usage")
    return cost


class Gateway:
    """Pass as the client to shared production helpers (``messages.create``).

    Persist raw blocks, usage and request IDs BEFORE any parser runs. A crash or
    uncertain provider outcome is never replayed automatically. Only explicit
    429 rejections may retry; each attempt gets its own reservation and record.
    """
    def __init__(self, client, ledger, config, *, replicate="1", dry_run=False):
        self.provider = config.get("provider", "anthropic-messages")
        if self.provider not in ("anthropic", "anthropic-messages", *OPENAI_PROVIDERS):
            raise ValueError(f"Unsupported evaluation provider: {self.provider}")
        if client is None and not dry_run:
            provider_name = "OpenAI" if self.provider in OPENAI_PROVIDERS else "Anthropic"
            raise ValueError(EVALUATION_CLIENT_MISSING.format(provider=provider_name))
        self.client = client
        self.ledger = ledger
        self.config = config
        self.prices = validate_prices(config)
        self.replicate = str(replicate)
        self.messages = self
        self.cache_hits = 0
        self.paid_calls = 0
        self.dry_run = dry_run
        self.context = {}
        self.billing_stopped = False

    def create(self, **request):
        if self.billing_stopped:
            raise BillingStopped(BILLING_EXHAUSTED)
        ceiling = request_ceiling(request, self.prices)
        identity = {"request_version": self.config.get("request_version", "1"), "replicate": self.replicate, "provider": self.provider, "request": request}
        attempts = self.config.get("max_attempts", 1)
        if not isinstance(attempts, int) or not 1 <= attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        for attempt in range(attempts):
            key = digest({**identity, "attempt": attempt})
            cached = self.ledger.get(key)
            if cached:
                state = cached.get("state")
                if state == "completed":
                    self.cache_hits += 1
                    return self._response(key, json.loads(cached.get("response")), True)
                if state == "retryable" and attempt + 1 < attempts:
                    continue
                if state in {"dispatched", "outcome_unknown"}:
                    raise OutcomeUnknown(f"Request {key} has an unknown outcome; manual reconciliation required")
                if str(cached.get('error', '')).startswith('billing_stopped:'):
                    self.billing_stopped = True
                    raise BillingStopped(BILLING_EXHAUSTED)
                raise CachedFailure(f"Request {key} permanently failed: {cached.get('error')}")
            if self.dry_run:
                raise DryRunMiss(f"Uncached request for {request.get('model')}; no paid call dispatched")
            # Preserve the original request identity; conversion is only for
            # dispatch, and unsupported content fails before reserving money.
            try:
                endpoint, payload = prepare_request(request, self.provider) if self.provider in OPENAI_PROVIDERS else (None, request)
            except ValueError as exc:
                raise RequestLimitError(str(exc)) from exc
            if endpoint and len(json.dumps(payload, ensure_ascii=False).encode()) > self.prices[request["model"]]["max_request_bytes"]:
                raise RequestLimitError("Converted request exceeds pinned byte limit; no automatic chunking")
            self.ledger.reserve(key, {**identity, "context": self.context}, ceiling)
            raw = None
            try:
                if endpoint:
                    response = self.client.post(endpoint, json=payload)
                    raw = {"_request_id": response.headers.get("x-request-id"),
                           "_http_status": response.status_code, "_body": response.text}
                    self.ledger.capture_response(key, raw)
                    if not 200 <= response.status_code < 300:
                        raise ProviderHTTPError(response.status_code, response.text)
                else:
                    response = self.client.messages.create(**request)
            except BaseException as exc:
                # Always preserve a dispatch outcome, including cancellation.
                # No exception is swallowed: only definite 429 rejections retry.
                code = getattr(exc, "status_code", None)
                if billing_rejection(exc):
                    self.ledger.fail(key, 'billing_stopped: ' + BILLING_EXHAUSTED,
                                     rejected=True, retryable=False, response=raw)
                    self.billing_stopped = True
                    raise BillingStopped(BILLING_EXHAUSTED) from exc
                rejected = code in {400, 401, 403, 404, 413, 422, 429}
                self.ledger.fail(key, f"{type(exc).__name__}: {exc}", rejected=rejected, retryable=code == 429, response=raw)
                if code == 429 and attempt + 1 < attempts:
                    continue
                if not rejected:
                    raise OutcomeUnknown(f"Request {key} may have been charged; reservation retained") from exc
                raise CachedFailure(f"Request {key} rejected: {exc}") from exc
            self.paid_calls += 1
            try:
                parsed = response_dict(response.json() if endpoint else response)
                if endpoint:
                    parsed["_request_id"] = raw["_request_id"]
                canonical(parsed)
            except (TypeError, ValueError, AttributeError) as exc:
                self.ledger.fail(key, f"Response serialization failed after dispatch: {exc}", response=raw)
                raise OutcomeUnknown("Provider returned but its response could not be persisted; reservation retained") from exc
            raw = parsed
            try:
                self.ledger.capture_response(key, raw)
            except (sqlite3.Error, OSError) as exc:
                raise OutcomeUnknown("Provider returned but persistence failed; reservation retained") from exc
            try:
                charge = usage_charge(raw, self.prices.get(request.get("model"), {}), self.provider)
            except OutcomeUnknown as exc:
                self.ledger.fail(key, exc, response=raw)
                raise
            try:
                self.ledger.settle(key, raw, charge)
            except (sqlite3.Error, OSError) as exc:
                raise OutcomeUnknown("Provider returned but settlement failed; dispatched reservation requires reconciliation") from exc
            return self._response(key, raw, False)
        raise CachedFailure("Retry allowance exhausted")

    def _response(self, key, raw, cached):
        if self.provider in OPENAI_PROVIDERS:
            content, stop_reason = response_content(raw, self.provider)
            raw = {**raw, "content": content, "stop_reason": stop_reason}
        result = _namespace(raw)
        result._eval_key = key
        result._eval_cached = cached
        return result

    def record_usage(self, response, model, label="", *, provider=None):
        """Injected into utilities.record_api_usage; pricing settled on dispatch.

        Annotating a cache hit is idempotent and cannot charge a second time.
        """
        key = getattr(response, "_eval_key", None)
        if key is None:
            raise ValueError("Unmetered response reached record_api_usage")
        self.ledger.annotate(key, label or model)
