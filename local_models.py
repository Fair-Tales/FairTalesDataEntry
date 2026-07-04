"""Streamlit-free thin client for a local Ollama (OpenAI-compatible) endpoint.

This is a **Streamlit-free**, dependency-light module (standard library +
``requests`` only; it imports NOTHING from Streamlit, ``anthropic`` or
``google`` and makes NO network call at import time) so it can be reused by
BOTH:

* the standalone pilot importer ``scripts/import_pilot_data.py`` (which is
  deliberately decoupled from the Streamlit ``data_structures`` package), and
* the live Streamlit app later (rotation / crop / wordless vision helpers).

Why local models
----------------
The importer's clean + coherence-judge pass is a pure TEXT task (strip OCR junk
while preserving verbatim/odd spelling, then judge ``makes_sense`` /
``fits_context``) and is ~67% of the run's API cost. A local **Gemma 3 27B** on
a consumer GPU (via Ollama) can do it for free. This module is the ONE shared
client for talking to that endpoint (#129: no inline HTTP calls elsewhere); the
importer keeps Claude for the actual vision OCR.

Design contract
---------------
* Every failure — connection refused, timeout, HTTP error, non-JSON reply —
  raises :class:`LocalModelError`. Callers ALWAYS catch it and fall back to the
  Claude path, so a local hiccup never fails a page. The helpers never silently
  return a default (mirrors the narrow-except / no-silent-pass convention).
* The Ollama **OpenAI-compatible** endpoint is used (default base URL
  ``http://localhost:11434/v1``, overridable — the GPU host may be remote). A
  supplied JSON ``schema`` is sent as ``response_format`` ``json_schema`` where
  the server honours it, and the reply is ALSO parsed defensively (fence-strip +
  ``json.loads`` + first-object salvage) because that endpoint's ``json_schema``
  support is still partial across Ollama versions; a plain ``json_object`` mode
  is the fallback and the prompts already instruct "reply with ONLY JSON".
* No model is ever downloaded here — the user runs ``ollama pull`` themselves;
  :func:`ping` lets callers (the eval) skip gracefully when the endpoint or the
  model is absent.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import requests

#: Default Ollama OpenAI-compatible base URL. Overridable (the GPU host may be a
#: different machine, e.g. ``http://192.168.1.50:11434/v1``).
DEFAULT_OLLAMA_URL = "http://localhost:11434/v1"

#: Generous default timeout (seconds): a 27B model on a consumer GPU is far
#: slower to first token than a hosted API, but we must never hang the pipeline.
DEFAULT_TIMEOUT = 180

#: Short timeout for the reachability probe (:func:`ping`).
PING_TIMEOUT = 5

#: Default output-token cap for a chat call (maps to Ollama ``num_predict``).
DEFAULT_MAX_TOKENS = 2048


class LocalModelError(RuntimeError):
    """Any local-model failure: connection, timeout, HTTP error or bad JSON.

    Callers catch this to fall back to the Claude path — a local model hiccup
    must never fail a page.
    """


# ---------------------------------------------------------------------------
# JSON salvage helpers (no I/O — unit-testable).
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def strip_json_fence(text: str) -> str:
    """Return ``text`` with a surrounding ```` ```json … ``` ```` code fence removed."""
    if not text:
        return ""
    match = _FENCE_RE.match(text)
    return match.group(1) if match else text


def _first_json_object(text: str) -> Optional[dict]:
    """Salvage the first ``{...}`` JSON object from a noisy reply, or None."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0), strict=False)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_json_object(content: str) -> dict:
    """Parse a model reply into a JSON object dict, or raise :class:`LocalModelError`.

    Tolerant of a stray code fence and of trailing prose around the JSON, and of
    a literal newline inside a string value (``strict=False``) — the prompts ask
    for single-line JSON but a local model is less reliable than the API.
    """
    if not content or not content.strip():
        raise LocalModelError("local model returned empty content")
    stripped = strip_json_fence(content)
    try:
        parsed = json.loads(stripped, strict=False)
    except json.JSONDecodeError:
        parsed = _first_json_object(stripped)
        if parsed is None:
            raise LocalModelError(
                f"local model reply was not valid JSON: {content[:200]!r}"
            ) from None
    if not isinstance(parsed, dict):
        raise LocalModelError("local model JSON reply was not an object")
    return parsed


# ---------------------------------------------------------------------------
# Endpoint helpers.
# ---------------------------------------------------------------------------

def _chat_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def _models_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/models"


def _response_format(schema: Optional[dict]) -> dict:
    """OpenAI-compatible ``response_format`` for a JSON reply.

    Prefer a ``json_schema`` constraint when a schema is supplied (honoured by
    newer Ollama); the caller retries in plain ``json_object`` mode if the
    server rejects it.
    """
    if schema is not None:
        return {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": schema},
        }
    return {"type": "json_object"}


def _post_chat(
    base_url: str,
    model: str,
    messages: list,
    *,
    timeout: float,
    schema: Optional[dict],
    max_tokens: int,
) -> str:
    """POST a chat completion and return the assistant message content string.

    Raises :class:`LocalModelError` on connection failure, timeout, a non-200
    status or a malformed response envelope. When a ``schema`` was requested and
    the server rejects the ``json_schema`` response_format (some Ollama versions
    return 400/500), retries ONCE in plain ``json_object`` mode.
    """
    base_payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0,
        "max_tokens": max_tokens,
    }

    def _send(response_format: dict) -> requests.Response:
        payload = dict(base_payload, response_format=response_format)
        try:
            return requests.post(_chat_url(base_url), json=payload, timeout=timeout)
        except requests.Timeout as exc:
            raise LocalModelError(
                f"local model timed out after {timeout}s ({model} @ {base_url})"
            ) from exc
        except requests.RequestException as exc:
            raise LocalModelError(
                f"could not reach local model at {base_url}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    resp = _send(_response_format(schema))
    if resp.status_code != 200 and schema is not None:
        # Some Ollama versions reject the json_schema response_format outright;
        # retry in plain json_object mode (the prompts already demand JSON).
        resp = _send({"type": "json_object"})
    if resp.status_code != 200:
        raise LocalModelError(
            f"local model HTTP {resp.status_code} from {base_url}: {resp.text[:200]}"
        )
    try:
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LocalModelError(
            f"malformed response from local model: {type(exc).__name__}: {exc}"
        ) from exc
    return content or ""


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------

def chat_json(
    base_url: str,
    model: str,
    system: str,
    user: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    schema: Optional[dict] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict:
    """Text chat call returning a parsed JSON object.

    ``system`` / ``user`` map onto OpenAI-style roles (the importer passes the
    same static instruction prompt as ``system`` and the variable context as
    ``user``, so the local path reuses the Claude prompt wording verbatim).
    Raises :class:`LocalModelError` on any failure so the caller can fall back to
    Claude.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    content = _post_chat(
        base_url, model, messages, timeout=timeout, schema=schema, max_tokens=max_tokens
    )
    return _parse_json_object(content)


def vision_json(
    base_url: str,
    model: str,
    images_b64: list,
    system: str,
    user: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    schema: Optional[dict] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    media_type: str = "image/jpeg",
) -> dict:
    """Vision chat call returning a parsed JSON object.

    ``images_b64`` is a list of base64-encoded image payloads, attached to the
    user turn as OpenAI ``image_url`` data-URI blocks (Ollama's OpenAI-compatible
    endpoint accepts these for multimodal models such as Qwen3-VL). Same error
    contract as :func:`chat_json`. Provided for later app use (page rotation /
    crop / wordless detection); the importer keeps Claude for OCR.
    """
    parts: list = [{"type": "text", "text": user}]
    for b64 in images_b64:
        parts.append(
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}}
        )
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": parts})
    content = _post_chat(
        base_url, model, messages, timeout=timeout, schema=schema, max_tokens=max_tokens
    )
    return _parse_json_object(content)


def ping(base_url: str, model: Optional[str] = None, *, timeout: float = PING_TIMEOUT) -> bool:
    """Return True if the endpoint is reachable (and, if given, ``model`` exists).

    Never raises — a probe failure is a plain ``False`` so the eval can skip
    gracefully with a "start Ollama / pull the model" message. Queries the
    OpenAI-compatible ``/models`` list; when ``model`` is supplied, accepts an
    exact id match or a base-name match (Ollama ids look like ``gemma3:27b``).
    """
    try:
        resp = requests.get(_models_url(base_url), timeout=timeout)
    except requests.RequestException:
        return False
    if resp.status_code != 200:
        return False
    if model is None:
        return True
    try:
        ids = [str(m.get("id", "")) for m in resp.json().get("data", [])]
    except (ValueError, AttributeError, TypeError):
        return False
    base = model.split(":")[0]
    return any(mid == model or mid.split(":")[0] == base for mid in ids)
