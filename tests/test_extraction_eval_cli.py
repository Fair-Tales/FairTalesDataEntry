"""End-to-end CLI boundaries using local snapshots and a fake paid facade."""
from __future__ import annotations

import hashlib
import json
import socket
import sys
from types import SimpleNamespace
import urllib.request

import pytest

from scripts.extraction_eval import cli
from scripts.extraction_eval.store import atomic_json, read_json


def forbidden(*args, **kwargs):
    raise AssertionError("This fixture must never contact a live service or read secrets")


@pytest.fixture
def offline_inputs(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    books = []
    for index in range(25):
        book_id = f"book-{index:02}"
        image = f"fixture-original-{index}".encode()
        path = f"image-{index}.jpg"
        (snapshot / path).write_bytes(image)
        books.append({"id": book_id, "title": f"Book {index}", "pages": [{
            "id": f"{book_id}_1", "page_number": 1,
            "image": {"path": path, "sha256": hashlib.sha256(image).hexdigest()},
            "reference": {
                "text": {"value": f"Reference {index}", "status": "observed", "kind": "text", "provenance": "fixture"},
                "contains_story": {"value": True, "status": "observed", "provenance": "fixture"}},
        }], "reference": {"metadata": {}, "characters": {"value": [], "status": "missing", "complete": False}}})
    manifest = {"version": "fixture-v1", "snapshot_id": "offline-fixture", "complete": True,
                "source_consistent": True, "entered_by": "pilot_import", "requested_books": [],
                "books": books, "excluded_books": [{"id": "seasons", "title": "Seasons", "reason": "metadata_only"}], "audit": []}
    atomic_json(snapshot / "manifest.json", manifest)
    config = {"request_version": "fixture-v1", "parser_version": "fixture-v1", "max_attempts": 1,
              "settings": {"extraction_model": "fixture", "metadata_model": "fixture", "character_detection_model": "fixture"},
              "pricing": {"verified": True, "verified_at": "2026-11-04", "source": "test fixture, not real rates",
                          "models": {"fixture": {"input": 1, "output": 2, "cache_read": 1, "cache_write": 1,
                          "max_input_tokens": 1000, "max_output_tokens": 100, "max_images": 100, "max_request_bytes": 100000}}}}
    frozen_settings = read_json(cli.PACKAGE / "baseline.json").get("settings", {})
    config["settings"] = {key: "fixture" if key.endswith("_model") else value
                          for key, value in frozen_settings.items()}
    config['billing'] = {'funding': 'subscription_credits', 'confirmed': True,
                         'organisation': 'fixture', 'project': 'fixture',
                         'confirmed_by': 'fixture', 'confirmed_at': '2026-11-05',
                         'secret_name': 'EVAL_ANTHROPIC_API_KEY',
                         'key_sha256': hashlib.sha256(b'fake-never-used').hexdigest()}
    config_path = tmp_path / "config.json"
    atomic_json(config_path, config)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    from scripts import data_cleanup
    monkeypatch.setattr(data_cleanup, "load_secrets", forbidden)
    from scripts.extraction_eval import snapshot as snapshot_module
    monkeypatch.setattr(snapshot_module, "create_live_backend", forbidden)
    monkeypatch.setattr(cli, "_live_anthropic", forbidden)
    return snapshot, config_path


@pytest.fixture
def fake_core(monkeypatch):
    """Run real runner/cache/ledger plumbing; only extraction/provider responses fake."""
    import utilities
    from ai_runtime import extraction_runtime
    events = []

    def call(stage, content, gateway, settings, sink):
        response = gateway.messages.create(model="fixture", max_tokens=10,
                    messages=[{"role": "user", "content": f"{stage}:{content}"}])
        with extraction_runtime(client=gateway, settings=settings, usage_sink=sink):
            utilities.record_api_usage(response, "fixture", label=stage)
        events.append((stage, content))

    def metadata(images, gateway, settings, sink, lookup):
        content = repr(images)
        call("metadata", content, gateway, settings, sink)
        lookup("fixture-isbn")
        return {"title": "Predicted metadata", "authors": ["Predicted Author"]}

    def page(image, gateway, settings, sink):
        call("page", repr(image), gateway, settings, sink)
        return {"status": "completed", "text": image.decode(), "contains_story": True}

    def characters(pages, gateway, settings, sink):
        assert all("Reference" not in (page.get("text") or "") for page in pages)
        call("characters", repr(pages), gateway, settings, sink)
        return [{"name": "Predicted Fox", "aliases": ["Freddy"]}]

    monkeypatch.setitem(sys.modules, "extraction_core", SimpleNamespace(
        extract_metadata=metadata, extract_page=page, extract_characters=characters))
    monkeypatch.setattr(utilities, "lookup_isbn", lambda isbn: {"title": "Fixture ISBN response"})
    return events


class FakePaidProvider:
    def __init__(self):
        self.messages = self
        self.calls = []
        self.closed = False

    def create(self, **request):
        self.calls.append(request)
        return SimpleNamespace(id=f"fake-{len(self.calls)}", content=[SimpleNamespace(type="text", text="{}")],
                               usage=SimpleNamespace(input_tokens=5, output_tokens=3), stop_reason="end_turn")

    def close(self):
        self.closed = True


def test_snapshot_completed_resume_constructs_no_clients(offline_inputs, capsys):
    snapshot, _ = offline_inputs
    assert cli.main(["snapshot", "--out", str(snapshot)]) == 0
    assert "25 books" in capsys.readouterr().out


def test_dry_run_limit_twenty_no_credentials_no_paid_calls_no_isbn(offline_inputs, fake_core, tmp_path, monkeypatch, capsys):
    import utilities
    monkeypatch.setattr(utilities, "lookup_isbn", forbidden)
    snapshot, config = offline_inputs
    out = tmp_path / "dry-run"
    assert cli.main(["extract", "--snapshot", str(snapshot), "--out", str(out), "--config", str(config),
                     "--limit", "20", "--dry-run"]) == 0
    assert len(read_json(out / "selection.json").get("book_ids")) == 20
    predicted = read_json(out / "dry_run_predictions.json")
    assert len(predicted.get("books")) == 20
    assert predicted.get("incomplete") is True
    assert not (out / "predictions.json").exists()
    assert not list((out / "lookups").glob("*.json"))
    assert fake_core == []
    output = capsys.readouterr().out
    assert '"books": 20' in output and '"estimated_usd":' in output
    assert "0 paid calls" in output


def test_paid_fake_exact_books_confirmation_cost_resume_and_offline_score(offline_inputs, fake_core, tmp_path, monkeypatch, capsys):
    snapshot, config = offline_inputs
    out = tmp_path / "paid-fixture"
    provider = FakePaidProvider()
    confirmed = []

    def approve(prompt):
        # The estimate must already be visible before asking to spend.
        assert '"estimated_usd":' in capsys.readouterr().out
        confirmed.append(prompt)
        return "AFFIRMATIVE"

    def create_provider(path, config):
        assert confirmed
        return provider

    monkeypatch.setattr("builtins.input", approve)
    monkeypatch.setattr(cli, "_live_anthropic", create_provider)
    args = ["extract", "--snapshot", str(snapshot), "--out", str(out), "--config", str(config),
            "--books", "book-02", "Book 7", "--execute"]
    assert cli.main(args) == 0
    assert read_json(out / "selection.json").get("book_ids") == ["book-02", "book-07"]
    assert set(read_json(out / "predictions.json").get("books")) == {"book-02", "book-07"}
    assert provider.closed and len(provider.calls) == 6
    calls = read_json(out / "calls.json")
    assert len(calls) == 6
    assert all(call.get("state") == "completed" and call.get("cost_usd", 0) > 0 for call in calls)
    assert all(call.get("label") for call in calls)
    # Resume without subset flags restores the exact saved selection and is free.
    assert cli.main(["extract", "--snapshot", str(snapshot), "--out", str(out), "--config", str(config), "--execute"]) == 0
    assert len(provider.calls) == 6
    monkeypatch.setattr(cli, "_live_anthropic", forbidden)
    import utilities
    monkeypatch.setattr(utilities, "lookup_isbn", forbidden)
    assert cli.main(["score", "--snapshot", str(snapshot), "--run", str(out)]) == 0
    assert all((out / "reports" / name).is_file() for name in (
        "summary.json", "books.csv", "pages.csv", "calls.csv", "inspection.html"))
    summary = read_json(out / "reports" / "summary.json")
    assert summary.get("snapshot_id") == "offline-fixture"


def test_unconfirmed_paid_run_never_constructs_provider(offline_inputs, fake_core, tmp_path, monkeypatch):
    snapshot, config = offline_inputs
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert cli.main(["extract", "--snapshot", str(snapshot), "--out", str(tmp_path / "refused"),
                     "--config", str(config), "--limit", "20", "--execute"]) == 1
    assert fake_core == []
