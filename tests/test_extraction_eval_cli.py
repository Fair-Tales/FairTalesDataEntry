"""End-to-end CLI boundaries using local snapshots and a fake paid facade."""
from __future__ import annotations

import hashlib
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


@pytest.mark.parametrize('provider', ['anthropic-messages', 'openai-responses'])
def test_dry_run_limit_twenty_no_credentials_no_paid_calls_no_isbn(offline_inputs, fake_core, tmp_path, monkeypatch, capsys, provider):
    import utilities
    from scripts.extraction_eval import openai_client
    monkeypatch.setattr(utilities, "lookup_isbn", forbidden)
    monkeypatch.setattr(openai_client, 'create_openai_client', forbidden)
    snapshot, config = offline_inputs
    atomic_json(config, {**read_json(config), 'provider': provider})
    out = tmp_path / "dry-run"
    assert cli.main(["extract", "--original-experiment", "--snapshot", str(snapshot), "--out", str(out), "--config", str(config),
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


def test_openai_cli_executes_and_replays_with_confirmed_key(offline_inputs, fake_core,
                                                          tmp_path, monkeypatch):
    import httpx
    from scripts import data_cleanup
    from scripts.extraction_eval import openai_client
    snapshot, config_path = offline_inputs
    config = read_json(config_path)
    config['provider'] = 'openai-responses'
    config['billing'].update(secret_name='EVAL_OPENAI_API_KEY', funding='prepaid_credits')
    atomic_json(config_path, config)
    requests = []
    clients = []

    def respond(request):
        assert str(request.url) == 'https://api.openai.com/v1/responses'
        requests.append(request)
        return httpx.Response(200, headers={'x-request-id': f'req-{len(requests)}'}, json={
            'id': f'resp-{len(requests)}', 'model': 'fixture', 'status': 'completed',
            'output': [{'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': '{}'}]}],
            'usage': {'input_tokens': 5, 'output_tokens': 3,
                      'input_tokens_details': {'cached_tokens': 2}},
        })

    def create_client(key):
        assert key == 'fake-never-used'
        client = httpx.Client(base_url='https://api.openai.com/v1/',
                              transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    monkeypatch.setattr('builtins.input', lambda prompt: 'AFFIRMATIVE')
    monkeypatch.setattr(data_cleanup, 'load_secrets',
                        lambda path: {'EVAL_OPENAI_API_KEY': 'fake-never-used'})
    monkeypatch.setattr(openai_client, 'create_openai_client', create_client)
    out = tmp_path / 'openai-paid-fixture'
    args = ['extract', '--original-experiment', '--snapshot', str(snapshot),
            '--out', str(out), '--config', str(config_path), '--books', 'book-02', '--execute']
    assert cli.main(args) == 0
    assert len(requests) == 3
    calls = read_json(out / 'calls.json')
    assert len(calls) == 3
    assert all(call['state'] == 'completed' and call['cost_usd'] == pytest.approx(11 / 1e6)
               for call in calls)
    assert cli.main(args) == 0
    assert len(requests) == 3
    assert all(client.is_closed for client in clients)


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
    args = ["extract", "--original-experiment", "--snapshot", str(snapshot), "--out", str(out), "--config", str(config),
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
    assert cli.main(["extract", "--original-experiment", "--snapshot", str(snapshot), "--out", str(out), "--config", str(config), "--execute"]) == 0
    assert len(provider.calls) == 6
    monkeypatch.setattr(cli, "_live_anthropic", forbidden)
    import utilities
    monkeypatch.setattr(utilities, "lookup_isbn", forbidden)
    assert cli.main(["score", "--original-experiment", "--snapshot", str(snapshot), "--run", str(out)]) == 0
    assert all((out / "reports" / name).is_file() for name in (
        "summary.json", "books.csv", "pages.csv", "calls.csv", "inspection.html"))
    summary = read_json(out / "reports" / "summary.json")
    assert summary.get("snapshot_id") == "offline-fixture"


def test_unconfirmed_paid_run_never_constructs_provider(offline_inputs, fake_core, tmp_path, monkeypatch):
    snapshot, config = offline_inputs
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert cli.main(["extract", "--original-experiment", "--snapshot", str(snapshot), "--out", str(tmp_path / "refused"),
                     "--config", str(config), "--limit", "20", "--execute"]) == 1
    assert fake_core == []


@pytest.fixture
def tuning_freeze(tmp_path):
    """A small seal with the same schema as the real freeze; no real IDs/files."""
    from scripts.extraction_eval.store import digest
    path = tmp_path / 'freeze'
    atomic_json(path / 'holdout_ids.json', ['book-00'])
    allowed = [f'book-{i:02}' for i in range(2, 25)]
    atomic_json(path / 'freeze.json', {
        'imported80_ids': ['book-00'], 'protected40_ids': ['book-01'],
        'tune41_ids': allowed, 'tune81_ids': ['book-01', *allowed],
        'imported80_ids_sha256': digest(['book-00']),
        'holdout_ids_file_sha256': hashlib.sha256((path / 'holdout_ids.json').read_bytes()).hexdigest(),
    })
    return path


def test_default_extract_and_score_use_tuning_gate(offline_inputs, fake_core, tuning_freeze,
                                                 tmp_path, monkeypatch):
    snapshot, config = offline_inputs
    out = tmp_path / 'tuning'
    provider = FakePaidProvider()
    monkeypatch.setattr('builtins.input', lambda prompt: 'AFFIRMATIVE')
    monkeypatch.setattr(cli, '_live_anthropic', lambda *args: provider)
    args = ['extract', '--snapshot', str(snapshot), '--out', str(out), '--config', str(config),
            '--freeze-dir', str(tuning_freeze), '--books', 'book-02', '--execute']
    assert cli.main(args) == 0
    record = read_json(out / 'run.json')['tuning']
    assert record['loaded_ids'] == ['book-02']
    assert record['refused_attempts'] == []
    assert len(provider.calls) == 3
    assert cli.main(args) == 0
    assert len(provider.calls) == 3
    monkeypatch.setattr(cli, '_live_anthropic', forbidden)
    assert cli.main(['score', '--snapshot', str(snapshot), '--run', str(out)]) == 0
    assert cli.main(['audit-tuning', '--run', str(out)]) == 0
    assert read_json(out / 'run.json')['tuning']['breaches'] == []
    with pytest.raises(SystemExit) as exc:
        cli.main(['extract', '--original-experiment', '--snapshot', str(snapshot), '--out', str(out)])
    assert exc.value.code == 2


def test_default_extract_refuses_before_image_or_paid_setup(offline_inputs, tuning_freeze,
                                                           tmp_path, monkeypatch):
    from scripts.extraction_eval import runner
    snapshot, config = offline_inputs
    out = tmp_path / 'denied-tuning'
    monkeypatch.setattr(runner, 'local_image', forbidden)
    monkeypatch.setattr('builtins.input', forbidden)
    with pytest.raises(SystemExit) as exc:
        cli.main(['extract', '--snapshot', str(snapshot), '--out', str(out), '--config', str(config),
                  '--freeze-dir', str(tuning_freeze), '--books', 'book-00', '--execute'])
    assert exc.value.code == 2
    record = read_json(out / 'run.json')['tuning']
    assert record['loaded_ids'] == []
    assert record['refused_attempts'][0]['source_ids'] == ['book-00']
    assert record['breaches'] == []


def test_default_extract_missing_freeze_stops_before_snapshot(offline_inputs, tmp_path, monkeypatch):
    snapshot, config = offline_inputs
    monkeypatch.setattr(cli, '_load_snapshot', forbidden)
    with pytest.raises(SystemExit) as exc:
        cli.main(['extract', '--snapshot', str(snapshot), '--out', str(tmp_path / 'missing-seal'),
                  '--config', str(config), '--freeze-dir', str(tmp_path / 'missing')])
    assert exc.value.code == 2


def test_challenge_selection_keeps_other_tuning_candidates(offline_inputs, fake_core, tuning_freeze,
                                                         tmp_path):
    snapshot, config = offline_inputs
    out = tmp_path / 'challenge-tuning'
    assert cli.main(['extract', '--snapshot', str(snapshot), '--out', str(out), '--config', str(config),
                     '--freeze-dir', str(tuning_freeze), '--challenge-books', 'book-02', '--limit', '3']) == 0
    assert len(read_json(out / 'selection.json')['book_ids']) == 3
