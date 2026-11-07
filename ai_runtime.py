"""Explicit, storage-free dependencies for shared AI extraction computations.

The application keeps its existing settings/telemetry defaults. A CLI evaluation
enters ``extraction_runtime`` so the *same* helpers instead receive immutable
settings and a local usage sink. Context variables isolate concurrent workers;
there is no global monkeypatch, Streamlit access, or client construction here.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ExtractionRuntime:
    client: Any
    settings: Mapping[str, Any]
    usage_sink: Callable[..., None]


_runtime: ContextVar[ExtractionRuntime | None] = ContextVar(
    "extraction_runtime", default=None
)


def current_runtime():
    """Return injected extraction dependencies, or ``None`` in the app."""
    return _runtime.get()


@contextmanager
def extraction_runtime(*, client, settings, usage_sink):
    """Scope shared calls to explicit settings and strictly local accounting.

    ``usage_sink(response, model, label)`` must persist locally. Exceptions from
    it propagate; an accounting failure must never fall back to Firestore.
    Provider-aware sinks also accept the optional ``provider=`` keyword.
    The caller validates and freezes settings before entering this boundary.
    """
    if client is None:
        raise ValueError("Extraction requires an explicit client")
    if not callable(usage_sink):
        raise TypeError("Extraction requires a local usage_sink")
    runtime = ExtractionRuntime(
        client=client,
        settings=MappingProxyType(dict(settings)),
        usage_sink=usage_sink,
    )
    token = _runtime.set(runtime)
    try:
        yield runtime
    finally:
        _runtime.reset(token)
