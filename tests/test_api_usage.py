"""Daily billing writes use fake storage and real atomic Increment sentinels."""

from types import SimpleNamespace

import pytest
from google.api_core.exceptions import GoogleAPIError
from google.cloud import firestore

import utilities


@pytest.fixture
def ledger(monkeypatch):
    writes = []

    class FakeFirestoreWrapper:
        def __init__(self, *, auth):
            assert auth is False

        def set_document(self, collection, document_id, data, *, merge):
            writes.append((collection, document_id, data, merge))

    monkeypatch.setattr(utilities, "FirestoreWrapper", FakeFirestoreWrapper)
    monkeypatch.setattr(utilities, "current_runtime", lambda: None)
    return writes


def recorded_values(ledger):
    """Check every numeric leaf is an atomic transform, then expose its value."""
    assert len(ledger) == 1
    collection, document_id, data, merge = ledger[0]
    assert collection == "api_usage"
    assert document_id == data["date"]
    assert merge is True

    def unwrap(value):
        if isinstance(value, dict):
            return {key: unwrap(item) for key, item in value.items()}
        if isinstance(value, str):
            assert value == document_id
            return value
        assert isinstance(value, firestore.Increment)
        return value.value

    return unwrap(data)


def assert_buckets(data, model, provider, flow, expected):
    for field, key in (("by_model", model), ("by_provider", provider),
                       ("by_flow", flow)):
        assert data[field][key] == pytest.approx(expected)
    for key, value in expected.items():
        assert data[f"total_{key}"] == pytest.approx(value)


def test_anthropic_ledger_figures_and_schema_are_preserved(ledger):
    response = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=1000, output_tokens=200,
        cache_read_input_tokens=500, cache_creation_input_tokens=100,
    ))
    utilities.record_api_usage(response, "claude-sonnet-4-6", "page.ocr")
    data = recorded_values(ledger)
    assert_buckets(data, "claude-sonnet-4-6", "anthropic", "page_ocr", {
        "calls": 1, "cost_usd": 0.006525, "input_tokens": 1000,
        "output_tokens": 200, "cache_read_tokens": 500, "cache_write_tokens": 100,
    })
    assert "total_unpriced_calls" not in data
    assert "total_unknown_usage_calls" not in data


@pytest.mark.parametrize("model, cost", [
    ("gpt-6-astra", 0.0132),
    ("gpt-6.1-sol", 0.00262),
    ("gpt-6.1-terra", 0.00131),
    ("gpt-6-luna", 0.000132),
    ("gpt-4.1", 0.0025),
    ("gpt-4.1-mini", 0.0005),
])
@pytest.mark.parametrize("response", [
    {"usage": {"input_tokens": 1000, "output_tokens": 100,
               "input_tokens_details": {"cached_tokens": 200}}},
    SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=1000, completion_tokens=100,
        prompt_tokens_details=SimpleNamespace(cached_tokens=200),
    )),
])
def test_openai_ledger_infers_provider_and_prices_cache(ledger, response, model, cost):
    utilities.record_api_usage(response, model, "relations")
    data = recorded_values(ledger)
    assert_buckets(data, model, "openai", "relations", {
        "calls": 1, "cost_usd": cost, "input_tokens": 800,
        "output_tokens": 100, "cache_read_tokens": 200, "cache_write_tokens": 0,
    })
    assert "total_unpriced_calls" not in data
    assert "total_unknown_usage_calls" not in data


def test_openai_cache_write_without_duration_keeps_counts_unpriced(ledger):
    utilities.record_api_usage({"usage": {
        "input_tokens": 1000, "output_tokens": 100,
        "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
    }}, "gpt-6.1-terra", "relations", provider="openai")
    data = recorded_values(ledger)
    assert_buckets(data, "gpt-6.1-terra", "openai", "relations", {
        "calls": 1, "input_tokens": 700, "output_tokens": 100,
        "cache_read_tokens": 200, "cache_write_tokens": 100, "unpriced_calls": 1,
    })
    assert "total_cost_usd" not in data
    assert "total_unknown_usage_calls" not in data


def test_unknown_model_keeps_tokens_and_marks_cost_unpriced(ledger, caplog):
    utilities.record_api_usage({"usage": {
        "input_tokens": 1000, "output_tokens": 100,
        "input_tokens_details": {"cached_tokens": 200},
    }}, "future-model", "relations", provider="openai")
    data = recorded_values(ledger)
    assert_buckets(data, "future-model", "openai", "relations", {
        "calls": 1, "input_tokens": 800, "output_tokens": 100,
        "cache_read_tokens": 200, "cache_write_tokens": 0, "unpriced_calls": 1,
    })
    assert "total_cost_usd" not in data
    assert "total_unknown_usage_calls" not in data
    assert "future-model" in caplog.text


@pytest.mark.parametrize("response", [{}, SimpleNamespace(usage=None)])
def test_missing_usage_is_recorded_as_unknown_instead_of_free(ledger, response):
    utilities.record_api_usage(response, "claude-sonnet-4-6")
    data = recorded_values(ledger)
    assert_buckets(data, "claude-sonnet-4-6", "anthropic", "unlabelled", {
        "calls": 1, "unpriced_calls": 1, "unknown_usage_calls": 1,
    })
    assert "total_cost_usd" not in data
    assert "total_input_tokens" not in data
    assert "total_output_tokens" not in data


def test_partial_usage_keeps_known_counts_and_marks_unknowns(ledger):
    utilities.record_api_usage({"usage": {"input_tokens": 12}}, "claude-sonnet-4-6")
    data = recorded_values(ledger)
    assert data["total_input_tokens"] == 12
    assert data["total_unpriced_calls"] == 1
    assert data["total_unknown_usage_calls"] == 1
    assert "total_output_tokens" not in data
    assert "total_cost_usd" not in data


@pytest.mark.parametrize("usage", ["broken", {"input_tokens": -1, "output_tokens": 3},
                                  {"input_tokens": "12", "output_tokens": 3}])
def test_malformed_usage_is_a_logged_noop(ledger, caplog, usage):
    utilities.record_api_usage(SimpleNamespace(usage=usage), "claude-sonnet-4-6", "ocr")
    assert ledger == []
    assert "record_api_usage" in caplog.text
    assert "ocr" in caplog.text


def test_firestore_failure_is_logged_without_breaking_request(ledger, monkeypatch, caplog):
    def unavailable(*args, **kwargs):
        raise GoogleAPIError("offline storage failure")

    monkeypatch.setattr(utilities.FirestoreWrapper, "set_document", unavailable)
    utilities.record_api_usage({"usage": {"input_tokens": 12, "output_tokens": 3}},
                               "claude-sonnet-4-6", "ocr")
    assert ledger == []
    assert "offline storage failure" in caplog.text


def test_live_shared_logging_records_missing_usage(ledger):
    utilities._log_usage(SimpleNamespace(usage=None), "claude-sonnet-4-6", "ocr")
    data = recorded_values(ledger)
    assert data["total_unknown_usage_calls"] == 1


def test_missing_firestore_configuration_is_logged(ledger, monkeypatch, caplog):
    def missing_configuration(*args, **kwargs):
        raise KeyError("firestore_key")

    monkeypatch.setattr(utilities, "FirestoreWrapper", missing_configuration)
    utilities.record_api_usage({"usage": {"input_tokens": 12, "output_tokens": 3}},
                               "claude-sonnet-4-6", "ocr")
    assert ledger == []
    assert "could not record usage" in caplog.text


def test_runtime_usage_sink_still_fails_closed(monkeypatch):
    def failed_sink(*args, **kwargs):
        raise ValueError("evaluation ledger unavailable")

    monkeypatch.setattr(utilities, "current_runtime",
                        lambda: SimpleNamespace(usage_sink=failed_sink))
    with pytest.raises(ValueError, match="evaluation ledger unavailable"):
        utilities.record_api_usage(SimpleNamespace(usage=None), "claude-sonnet-4-6")


def test_summary_combines_legacy_docs_with_provider_and_gap_counts(ledger, monkeypatch):
    recent = {"calls": 2, "cost_usd": 0.1, "input_tokens": 42,
              "unpriced_calls": 1, "unknown_usage_calls": 1}
    legacy = {"calls": 3, "cost_usd": 0.4, "input_tokens": 500}
    docs = iter([
        {**{f"total_{key}": value for key, value in recent.items()},
         "by_model": {"gpt-4.1": recent}, "by_flow": {"ocr": recent},
         "by_provider": {"openai": recent}},
        {**{f"total_{key}": value for key, value in legacy.items()},
         "by_model": {"claude-sonnet-4-6": legacy}, "by_flow": {"ocr": legacy}},
    ])

    def get_by_reference(self, collection, document_id):
        assert collection == "api_usage"
        doc = next(docs)
        return SimpleNamespace(exists=True, to_dict=lambda: doc)

    monkeypatch.setattr(utilities.FirestoreWrapper, "get_by_reference",
                        get_by_reference, raising=False)
    summary = utilities.get_api_usage_summary(days=2)
    totals = summary["window"]["totals"]
    assert totals["calls"] == 5
    assert totals["cost_usd"] == pytest.approx(0.5)
    assert totals["input_tokens"] == 542
    assert totals["unpriced_calls"] == 1
    assert totals["unknown_usage_calls"] == 1
    assert summary["today"]["unknown_usage_calls"] == 1
    assert summary["daily"][1]["unknown_usage_calls"] == 0
    assert summary["window"]["by_flow"]["ocr"] == totals
    assert summary["window"]["by_model"]["gpt-4.1"]["unpriced_calls"] == 1
    assert summary["window"]["by_provider"]["openai"]["unknown_usage_calls"] == 1
    assert list(summary["window"]["by_provider"]) == ["openai"]
    assert ledger == []


@pytest.fixture
def worker_database():
    writes = []

    class FakeDatabase:
        def collection(self, collection):
            def document(document_id):
                def set_document(data, *, merge):
                    writes.append((collection, document_id, data, merge))
                return SimpleNamespace(set=set_document)
            return SimpleNamespace(document=document)

    return FakeDatabase(), writes


@pytest.mark.parametrize("usage, unknown", [
    ({"input_tokens": 20, "output_tokens": 10,
      "input_tokens_details": {"cached_tokens": 5}}, False),
    (None, True),
])
def test_worker_records_unpriced_usage_in_daily_job_and_batch_ledgers(
        worker_database, ledger, usage, unknown):
    from pipeline_worker_runtime import usage_recorder

    database, writes = worker_database
    record = usage_recorder(database, "job-1", "batch-1")
    record({"usage": usage}, "future-model", "ocr", provider="openai")

    assert len(writes) == 3
    daily = recorded_values(writes[:1])
    assert daily["total_unpriced_calls"] == 1
    assert "total_cost_usd" not in daily
    assert daily["by_provider"]["openai"]["unpriced_calls"] == 1
    if unknown:
        assert daily["total_unknown_usage_calls"] == 1
        assert "total_input_tokens" not in daily
    else:
        assert daily["total_input_tokens"] == 15
        assert daily["total_cache_read_tokens"] == 5
        assert "total_unknown_usage_calls" not in daily
    for write, expected in zip(writes[1:], [("pipeline_jobs", "job-1"),
                                           ("photo_batches", "batch-1")]):
        collection, document_id, data, merge = write
        assert (collection, document_id) == expected
        assert merge is True
        assert data["usage"]["unpriced_calls"].value == 1
        assert "cost_usd" not in data["usage"]
    assert ledger == []


def test_worker_malformed_usage_is_a_logged_noop(worker_database, caplog):
    from pipeline_worker_runtime import usage_recorder

    database, writes = worker_database
    usage_recorder(database, "job-1")({"usage": "broken"}, "gpt-4.1", "ocr")
    assert writes == []
    assert "usage persistence failed" in caplog.text
    assert "job-1" in caplog.text
