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

pytestmark = pytest.mark.usefixtures("synthetic_cohort")


def forbidden(*args, **kwargs):
    raise AssertionError("This fixture must never contact a live service or read secrets")


def test_paid_run_requires_cycle_before_confirmation_or_clients(offline_inputs, tmp_path, monkeypatch, capsys):
    snapshot, config_path = offline_inputs
    config = read_json(config_path)
    del config['billing_cycles']
    atomic_json(config_path, config)
    monkeypatch.setattr('builtins.input', forbidden)
    monkeypatch.setattr(cli, '_live_provider', forbidden)
    with pytest.raises(SystemExit) as exc:
        cli.main(['extract', '--snapshot', str(snapshot), '--out', str(tmp_path / 'run'),
                  '--config', str(config_path), '--execute'])
    assert exc.value.code == 2
    assert 'billing_cycles.anthropic.start_day' in capsys.readouterr().err


@pytest.fixture
def system_temporary_dir(tmp_path, monkeypatch):
    """Model temporary and durable storage locally; all paid providers are fake."""
    path = tmp_path / 'temporary'
    path.mkdir()
    monkeypatch.setattr(cli.tempfile, 'gettempdir', lambda: str(path))
    return path


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
    config['billing_cycles'] = {'anthropic': {'start_day': 1}, 'openai': {'start_day': 10}}
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
        model = settings['extraction_model']
        response = gateway.messages.create(model=model, max_tokens=10,
                    messages=[{"role": "user", "content": f"{stage}:{content}"}])
        with extraction_runtime(client=gateway, settings=settings, usage_sink=sink):
            utilities.record_api_usage(response, model, label=stage)
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


@pytest.mark.parametrize("variant", [None, "", "unknown-variant", 1])
def test_extract_refuses_invalid_prompt_variant(
        offline_inputs, tmp_path, monkeypatch, capsys, variant):
    from scripts.extraction_eval import runner

    snapshot, config_path = offline_inputs
    config = read_json(config_path)
    config["settings"]["prompt_variant"] = variant
    atomic_json(config_path, config)
    monkeypatch.setattr(runner, "local_image", forbidden)
    monkeypatch.setattr("builtins.input", forbidden)
    out = tmp_path / "invalid-variant"
    with pytest.raises(SystemExit) as exc:
        cli.main(["extract", "--snapshot", str(snapshot), "--out", str(out),
                  "--config", str(config_path), "--books", "book-02", "--execute"])
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "Invalid pipeline setting: prompt_variant" in error
    assert not (snapshot.parent / "spend.sqlite").exists()


def test_checked_in_configs_name_their_prompt_variants():
    assert read_json(cli.PACKAGE / "baseline.json")["settings"]["prompt_variant"] == "legacy-v1"
    assert read_json(cli.PACKAGE / "candidate_pipeline_v2.json")["settings"]["prompt_variant"] == "rules-plus-icl"


def test_scan_cli_uses_scan_pages_without_stored_renders(
        offline_inputs, fake_core, tmp_path, capsys):
    from PIL import Image
    from test_extraction_eval_reference_inputs import make_pdf
    from scripts.extraction_eval import inputs_manifest
    from scripts.extraction_eval.store import digest

    snapshot, config = offline_inputs
    source = read_json(snapshot / 'manifest.json')
    source['books'][2]['pages'] = []
    source['books'][2]['reference']['characters'] = {
        'status': 'observed', 'value': [
            {'name': name, 'raw': {'gender': 'Female', 'human': False, 'protagonist': True}}
            for name in ('cat', 'Rabbit')]}
    atomic_json(snapshot / 'manifest.json', source)
    scan_root = tmp_path / 'scans'
    folder = scan_root / 'Book 2'
    folder.mkdir(parents=True)
    with Image.new('RGB', (8, 10), 'red') as first, Image.new('RGB', (8, 10), 'blue') as second:
        first.save(folder / 'Book 2.pdf', save_all=True, append_images=[second])
    reference_root = tmp_path / 'references'
    make_pdf(reference_root, 'Book 2.pdf', pages=('A synthetic reference',))
    paired_path = tmp_path / 'inputs.json'
    paired = inputs_manifest.build_manifest(snapshot, ['book-02'], scan_root, reference_root)
    atomic_json(paired_path, paired)
    out = tmp_path / 'scan-run'
    args = ['extract', '--snapshot', str(snapshot), '--out', str(out), '--config', str(config),
            '--books', 'book-02', '--inputs', 'scans', '--scan-root', str(scan_root),
            '--reference-root', str(reference_root), '--inputs-manifest', str(paired_path), '--dry-run']
    assert cli.main(args) == 0
    record = read_json(out / 'run.json')
    contract = record['contract']
    assert contract['config']['inputs'] == 'scans'
    assert contract['config']['inputs_manifest'] == paired
    assert paired['books'][0]['scan']['page_count'] == 2
    assert len(paired['books'][0]['scan']['page_sha256']) == 2
    assert paired['books'][0]['pairing_reason'] == 'unequal_page_counts'
    assert contract['selection']['page_count'] == 2
    predictions = read_json(out / 'dry_run_predictions.json')
    assert list(predictions['books']['book-02']['pages']) == ['scan-1', 'scan-2']
    assert predictions['inputs'] == 'scans'
    assert fake_core == []
    # The frozen snapshot still describes the original reference, untouched.
    loader_state = record['tuning']
    loader = cli.TuningLoader(out, loader_state['freeze_dir'])
    assert contract['snapshot_hash'] == digest(loader.snapshot(snapshot, ['book-02']))
    assert cli.main(args) == 0
    assert '0 paid calls' in capsys.readouterr().out
    predictions['books']['book-02']['pages'] = {
        'scan-2': {'status': 'completed', 'text': 'Added lettering', 'regions': [
            {'kind': 'illustration_lettering', 'text': 'Added lettering'}]},
        'scan-1': {'status': 'completed', 'text': 'A synthetic reference'},
    }
    predictions['books']['book-02']['characters'] = {
        'status': 'completed', 'value': [
            {'name': name, 'gender': 'Female', 'human': False, 'protagonist': True}
            for name in ('The Old Cat', 'Rabbit')]}
    atomic_json(out / 'predictions.json', predictions)
    score_args = ['score', '--snapshot', str(snapshot), '--run', str(out)]
    assert cli.main(score_args) == 0
    summary = read_json(out / 'reports' / 'summary.json')
    assert summary['book_denominator'] == 1
    assert summary['text_output'] == 'stored'
    primary = summary['cohort_summary']['primary']['metrics']
    assert primary['text_accuracy']['books'] == 1
    assert primary['text_accuracy']['items'] == 3
    assert primary['text_accuracy']['range_95'] == pytest.approx([1 / 3, 1 / 3])
    score = summary['book_scores'][0]
    assert score['reference_tokens'] == 3
    assert score['insertions'] == 2
    assert score['accuracy'] == pytest.approx(1 / 3)
    assert score['inserted_text_per_100_reference_tokens'] == pytest.approx(200 / 3)
    assert score['characters']['strict_numerator'] == 1
    assert score['characters']['tolerant_numerator'] == score['characters']['denominator'] == 2
    assert score['characters']['tolerant_recall'] == 1
    assert score['aliases']['explicit_alias_coverage']['numerator'] == 0
    assert score['aliases']['explicit_alias_coverage']['denominator'] == 1
    assert score['aliases']['alias_recall']['recall'] is None
    assert score['aliases']['additional_aliases']['numerator'] == 1
    assert score['aliases']['groundedness']['denominator'] == 2
    for result in score['attributes'].values():
        assert result['denominator'] == 2
        assert result['recall'] == 1
        assert result['strict_recall'] == 0.5
        assert result['matched_pair_agreement']['agreement'] == 1
    projected = tmp_path / 'projected-report'
    assert cli.main([*score_args, '--out', str(projected), '--text-output',
                     'analysis_without_illustration_lettering']) == 0
    summary = read_json(projected / 'summary.json')
    assert summary['text_output'] == 'analysis_without_illustration_lettering'
    assert summary['book_scores'][0]['accuracy'] == 1
    assert summary['book_scores'][0]['insertions'] == 0
    assert summary['book_scores'][0]['characters'] == score['characters']
    assert summary['book_scores'][0]['attributes'] == score['attributes']
    assert summary['book_scores'][0]['aliases'] == score['aliases']
    assert read_json(projected / 'book_scores.json') == summary['book_scores']
    assert set(path.name for path in projected.iterdir()) == {
        'summary.json', 'book_scores.json', 'summary.md', 'tuning_scores.json'}
    numeric = read_json(projected / 'tuning_scores.json')
    assert numeric['run_id'] == record['run_id']
    assert numeric['sets']['iterate']['book-02']['character_recall']['value'] == 1
    assert numeric['cost']['settled_usd'] == 0
    assert numeric['measurement_id'] != read_json(out / 'reports' / 'tuning_scores.json')['measurement_id']
    with pytest.raises(SystemExit):
        cli.main([*score_args, '--compare-run', str(out)])
    assert 'per-book text and character recall' in capsys.readouterr().err
    with Image.new('RGB', (8, 10), 'green') as changed:
        changed.save(folder / 'Book 2.pdf')
    with pytest.raises(SystemExit):
        cli.main(args)
    assert 'changed' in capsys.readouterr().err.lower()
    # Regenerating after a file change cannot silently change an existing run.
    changed = inputs_manifest.build_manifest(snapshot, ['book-02'], scan_root, reference_root)
    atomic_json(paired_path, changed)
    with pytest.raises(SystemExit):
        cli.main(args)
    assert 'Run inputs/settings/code/caps changed' in capsys.readouterr().err


@pytest.mark.parametrize('select_explicitly', [False, True])
@pytest.mark.parametrize('present_in_snapshot', [False, True])
def test_scan_cli_keeps_unavailable_book_without_dispatch(
        offline_inputs, fake_core, tmp_path, monkeypatch, capsys, select_explicitly,
        present_in_snapshot, cohort_loader_factory):
    from scripts.extraction_eval import inputs_manifest

    cohort_loader_factory(iterate=['book-02'], check=[])
    snapshot, config = offline_inputs
    source = read_json(snapshot / 'manifest.json')
    source['books'][2]['reference']['characters'] = {
        'status': 'observed', 'value': [{'name': 'cat'}]}
    source['books'] = [source['books'][2]] if present_in_snapshot else []
    atomic_json(snapshot / 'manifest.json', source)
    scan_root, reference_root = tmp_path / 'scans', tmp_path / 'references'
    scan_root.mkdir()
    reference_root.mkdir()
    paired = inputs_manifest.build_manifest(snapshot, ['book-02'], scan_root, reference_root)
    assert paired['books'][0]['status'] == 'unavailable'
    paired_path = tmp_path / 'inputs.json'
    atomic_json(paired_path, paired)
    monkeypatch.setattr(inputs_manifest, 'load_manifest_scan', forbidden)
    out = tmp_path / 'unavailable-run'
    selected = ['--books', 'book-02'] if select_explicitly else []
    assert cli.main(['extract', '--snapshot', str(snapshot), '--out', str(out), *selected,
                     '--config', str(config), '--inputs', 'scans',
                     '--scan-root', str(scan_root), '--reference-root', str(reference_root),
                     '--inputs-manifest', str(paired_path), '--dry-run']) == 0
    assert read_json(out / 'selection.json')['book_ids'] == ['book-02']
    result = read_json(out / 'dry_run_predictions.json')
    assert result['books']['book-02'] == {
        'status': 'unavailable', 'reason': paired['books'][0]['reason'], 'pages': {},
    }
    assert result['unavailable_books'] == 1
    assert result['incomplete'] is False
    # An implicit resume also keeps ids absent from the source snapshot.
    assert cli.main(['extract', '--snapshot', str(snapshot), '--out', str(out),
                     '--config', str(config), '--inputs', 'scans', '--scan-root', str(scan_root),
                     '--reference-root', str(reference_root), '--inputs-manifest', str(paired_path),
                     '--dry-run']) == 0
    assert read_json(out / 'selection.json')['book_ids'] == ['book-02']
    assert fake_core == []
    output = capsys.readouterr().out
    assert '"unavailable_books": 1' in output
    assert '"estimated_usd": 0.0' in output
    atomic_json(out / 'predictions.json', result)
    assert cli.main(['score', '--snapshot', str(snapshot), '--run', str(out)]) == 0
    summary = read_json(out / 'reports' / 'summary.json')
    assert summary['book_denominator'] == 1
    assert summary['book_scores'][0]['id'] == 'book-02'
    assert summary['book_scores'][0]['accuracy'] == 0
    assert summary['book_scores'][0]['forced_score'] == 0
    characters = summary['book_scores'][0]['characters']
    assert characters['denominator'] == int(present_in_snapshot)
    assert characters['tolerant_numerator'] == characters['strict_numerator'] == 0
    assert characters['tolerant_recall'] == (0 if present_in_snapshot else None)


@pytest.mark.parametrize('prediction', [
    None, {'status': 'failed', 'value': [{'name': 'cat'}]},
    {'status': 'abstained', 'value': [{'name': 'cat'}]},
    {'status': 'pending'}, {'status': 'completed', 'value': None},
])
@pytest.mark.parametrize('empty_reference', [False, True])
def test_scan_character_failure_retains_recorded_denominator(tmp_path, monkeypatch, prediction, empty_reference):
    from scripts.extraction_eval import inputs_manifest

    monkeypatch.setattr(inputs_manifest, 'scoring_inputs', lambda *args, **kwargs: [{
        'id': 'book', 'reference_text': '', 'prediction_text': '', 'forced_score': None,
        'status': 'ready', 'reason': None, 'failed_pages': [],
        'page_failures': {}, 'unrun_pages': [],
    }])
    args = SimpleNamespace(out=tmp_path, text_output='stored')
    contract = {'snapshot_id': 'fixture', 'config': {
        'inputs_manifest': {'books': [{'id': 'book'}]}, 'reference_root': tmp_path}}
    manifest = {'books': [{'id': 'book', 'reference': {'characters': {
        'status': 'observed', 'value': [] if empty_reference else [
            {'name': 'cat', 'aliases': ['kitten'], 'gender': 'Female',
             'human': False, 'protagonist': True}]}}}]}
    predictions = {'books': {'book': {'characters': prediction}}}
    assert cli._score_scans(args, contract, predictions, {'book_ids': ['book']}, manifest) == 0
    book = read_json(tmp_path / 'book_scores.json')[0]
    characters = book['characters']
    assert characters['denominator'] == (0 if empty_reference else 1)
    expected = None if empty_reference else 0
    assert characters['strict_recall'] == characters['tolerant_recall'] == expected
    assert characters['unmatched_reference'] == ([] if empty_reference else [0])
    if prediction and prediction['status'] == 'failed':
        assert characters['prediction_status'] == 'failed'
        assert characters['failure_kind'] == 'provider_error'
    assert book['aliases']['alias_recall']['recall'] == expected
    assert book['aliases']['groundedness']['denominator'] == 0
    assert book['aliases']['groundedness']['share'] is None
    for result in book['attributes'].values():
        assert result['denominator'] == (0 if empty_reference else 1)
        assert result['recall'] == result['strict_recall'] == expected


def test_inventory_cli_reports_counts_only(tmp_path, cohort_loader_factory, capsys):
    from PIL import Image

    loader = cohort_loader_factory(iterate=['book'], check=['check'])
    snapshot = tmp_path / 'snapshot'
    atomic_json(snapshot / 'manifest.json', {'books': [
        {'id': 'book', 'title': 'Matched'}, {'id': 'check', 'title': 'Absent'},
        {'id': 'heldout', 'title': 'Must never resolve'},
    ]})
    root = tmp_path / 'scans'
    folder = root / 'Matched'
    folder.mkdir(parents=True)
    with Image.new('RGB', (8, 10), 'red') as page:
        page.save(folder / 'Matched.pdf')
    out = tmp_path / 'inventory.json'
    assert cli.main(['inventory', '--snapshot', str(snapshot), '--out', str(out),
                     '--run', str(tmp_path / 'inventory-run'), '--scan-root', str(root),
                     '--freeze-dir', str(loader.freeze_dir), '--cohort', 'tune']) == 0
    import json
    counts = json.loads(capsys.readouterr().out)
    assert counts['iterate'] == {'mapped': 1, 'unmapped': 0, 'pages': 1, 'unmapped_reasons': {}}
    assert counts['check'] == {'mapped': 0, 'unmapped': 1, 'pages': 0,
                               'unmapped_reasons': {'missing_folder': 1}}
    assert counts['total_pages'] == 1
    assert read_json(out)['unmapped'] == [{'id': 'check', 'reason': 'missing_folder'}]


def openai_fixture_config(config):
    from ai_pricing import PRICING_PER_MTOK
    config['provider'] = 'openai-responses'
    config['settings'] = {key: 'gpt-6.1-sol' if key.endswith('_model') else value
                          for key, value in config['settings'].items()}
    config['pricing']['models'] = {'gpt-6.1-sol': {
        **config['pricing']['models']['fixture'], **PRICING_PER_MTOK['gpt-6.1-sol'],
        'cache_ttl': '30m', 'max_output_tokens': 128000}}
    return config


@pytest.mark.parametrize('provider', ['anthropic-messages', 'openai-responses'])
def test_dry_run_limit_twenty_no_credentials_no_paid_calls_no_isbn(offline_inputs, fake_core, tmp_path, monkeypatch, capsys, provider):
    import utilities
    from scripts.extraction_eval import openai_client
    monkeypatch.setattr(utilities, "lookup_isbn", forbidden)
    monkeypatch.setattr(openai_client, 'create_openai_client', forbidden)
    snapshot, config = offline_inputs
    values = read_json(config)
    atomic_json(config, openai_fixture_config(values) if provider == 'openai-responses' else values)
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


def test_openai_cli_executes_and_replays_without_attestation(offline_inputs, fake_core,
                                                          tmp_path, monkeypatch, system_temporary_dir):
    import httpx
    from scripts import data_cleanup
    from scripts.extraction_eval import openai_client
    snapshot, config_path = offline_inputs
    config = openai_fixture_config(read_json(config_path))
    atomic_json(config_path, config)
    requests = []
    clients = []

    def respond(request):
        assert str(request.url) == 'https://api.openai.com/v1/responses'
        requests.append(request)
        return httpx.Response(200, headers={'x-request-id': f'req-{len(requests)}'}, json={
            'id': f'resp-{len(requests)}', 'model': 'gpt-6.1-sol', 'status': 'completed',
            'output': [{'type': 'message', 'status': 'completed', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': '{}'}]}],
            'usage': {'input_tokens': 5, 'output_tokens': 3,
                      'input_tokens_details': {'cached_tokens': 2, 'cache_write_tokens': 0,
                          'cache_write_tokens_details': {'ttl_30m_tokens': 0, 'ttl_12h_tokens': 0}}},
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
    args = ['extract', '--snapshot', str(snapshot),
            '--out', str(out), '--config', str(config_path), '--books', 'book-02', '--execute']
    assert cli.main(args) == 0
    assert len(requests) == 3
    calls = read_json(out / 'calls.json')
    assert len(calls) == 3
    assert all(call['state'] == 'completed' and call['cost_usd'] == pytest.approx(36.2 / 1e6)
               for call in calls)
    assert cli.main(args) == 0
    assert len(requests) == 3
    assert all(client.is_closed for client in clients)


def test_paid_fake_exact_books_confirmation_cost_resume_and_offline_score(offline_inputs, fake_core, tmp_path, monkeypatch, capsys, system_temporary_dir):
    snapshot, config = offline_inputs
    out = tmp_path / "paid-fixture"
    from scripts import data_cleanup
    monkeypatch.setattr(data_cleanup, "load_secrets",
                        lambda path: {"ANTHROPIC_API_KEY": "fake-never-used"})
    provider = FakePaidProvider()
    confirmed = []

    def approve(prompt):
        # The estimate must already be visible before asking to spend.
        assert '"estimated_usd":' in capsys.readouterr().out
        confirmed.append(prompt)
        return "AFFIRMATIVE"

    def create_provider(path, config, credentials):
        assert confirmed
        assert credentials == ("fake-never-used", "ANTHROPIC_API_KEY")
        return provider

    monkeypatch.setattr("builtins.input", approve)
    monkeypatch.setattr(cli, "_live_anthropic", create_provider)
    args = ["extract", "--snapshot", str(snapshot), "--out", str(out), "--config", str(config),
            "--books", "book-02", "book-07", "--execute"]
    assert cli.main(args) == 0
    assert read_json(out / "selection.json").get("book_ids") == ["book-02", "book-07"]
    assert set(read_json(out / "predictions.json").get("books")) == {"book-02", "book-07"}
    assert read_json(out / "run.json")["secret_name"] == "ANTHROPIC_API_KEY"
    for path in out.rglob("*.json"):
        assert "fake-never-used" not in path.read_text()
    captured = capsys.readouterr()
    assert "fake-never-used" not in captured.out + captured.err
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


@pytest.fixture
def tuning_freeze(cohort_loader_factory):
    loader = cohort_loader_factory(iterate=[f'book-{i:02}' for i in range(2, 25)],
                                   holdout=['book-00'], protected=['book-01'])
    return loader.freeze_dir


def test_default_extract_and_score_use_tuning_gate(offline_inputs, fake_core, tuning_freeze,
                                                 tmp_path, monkeypatch, system_temporary_dir):
    snapshot, config = offline_inputs
    out = tmp_path / 'tuning'
    from scripts import data_cleanup
    monkeypatch.setattr(data_cleanup, "load_secrets",
                        lambda path: {"ANTHROPIC_API_KEY": "fake-never-used"})
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
        cli.main(['extract', '--snapshot', str(snapshot), '--out', str(out), '--cohort', 'proof'])
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


@pytest.mark.parametrize('execute', [False, True])
@pytest.mark.parametrize('target', ['ledger', 'default-ledger', 'output'])
@pytest.mark.parametrize('path_form', ['absolute', 'relative', 'symlink'])
def test_temporary_paid_artifacts_refused_dry_runs_allowed(
        offline_inputs, fake_core, tmp_path, monkeypatch, capsys,
        system_temporary_dir, execute, target, path_form):
    snapshot, config = offline_inputs
    out = tmp_path / 'temporary-sibling'  # A shared name prefix is not containment.
    ledger = tmp_path / 'spend.sqlite'
    if target == 'default-ledger':
        snapshot = snapshot.rename(system_temporary_dir / 'snapshot')
        path = snapshot
    elif target == 'ledger':
        path = system_temporary_dir / 'spend.sqlite'
    else:
        path = system_temporary_dir  # The temporary directory itself is unsafe too.
    if path_form == 'relative':
        monkeypatch.chdir(tmp_path)
        path = path.relative_to(tmp_path)
    elif path_form == 'symlink':
        link = tmp_path / 'linked-artifact'
        if target == 'default-ledger':
            link.symlink_to(path.parent, target_is_directory=True)
            path = link / path.name
        else:
            link.symlink_to(path, target_is_directory=target != 'ledger')
            path = link
    if target == 'default-ledger':
        snapshot = path
    elif target == 'ledger':
        ledger = path
    else:
        out = path
    evidence = tmp_path / 'preserved-evidence'
    monkeypatch.setenv('FAIRTALES_EVIDENCE_DIR', str(evidence))
    monkeypatch.setattr('builtins.input', lambda prompt: 'AFFIRMATIVE')
    monkeypatch.setattr(cli, '_live_provider', forbidden)
    args = ['extract', '--snapshot', str(snapshot),
            '--out', str(out), '--config', str(config), '--books', 'book-02',
            '--execute' if execute else '--dry-run']
    if target != 'default-ledger':
        args.extend(['--ledger', str(ledger)])
    ledger_path = ledger if target != 'default-ledger' else snapshot.parent / 'spend.sqlite'
    if execute:
        with pytest.raises(SystemExit) as exc:
            cli.main(args)
        assert exc.value.code == 2
        message = capsys.readouterr().err
        refused = out if target == 'output' else ledger_path
        assert str(refused.resolve()) in message
        assert 'Paid run refused' in message and str(evidence) in message
        assert not ledger_path.exists()
        assert read_json(out / 'run.json')['tuning']['loaded_ids']
    else:
        assert cli.main(args) == 0
        assert ledger_path.is_file()
        assert (out / 'dry_run_predictions.json').is_file()
    assert fake_core == []


def test_evidence_directory_default_and_override(tmp_path, monkeypatch):
    from conftest import load_dev20_predictions
    from scripts.extraction_eval.evidence import evidence_dir
    from scripts.extraction_eval.tuning import DEFAULT_FREEZE_DIR

    monkeypatch.delenv('FAIRTALES_EVIDENCE_DIR', raising=False)
    assert evidence_dir() == DEFAULT_FREEZE_DIR / 'evidence'
    monkeypatch.setenv('FAIRTALES_EVIDENCE_DIR', str(tmp_path))
    assert evidence_dir() == tmp_path
    with pytest.raises(pytest.skip.Exception, match=str(tmp_path / 'dev20-run' / 'predictions.json')):
        load_dev20_predictions()


@pytest.mark.parametrize('cohort,expected', [
    ('tune', {f'book-{i:02}' for i in range(2, 25)}),
    ('rehearsal', {'book-01'}), ('proof', {'book-00'}),
])
@pytest.mark.parametrize('selection_flag', ['limit', 'default', 'books'])
def test_each_cli_cohort_filters_before_content_decode(
        offline_inputs, fake_core, tuning_freeze, tmp_path, monkeypatch,
        cohort, expected, selection_flag):
    from scripts.extraction_eval.json_sources import JsonSources

    snapshot, config = offline_inputs
    forbidden_titles = {f'Book {i}' for i in range(25) if f'book-{i:02}' not in expected}
    original = JsonSources.decode
    reads = []

    def checked(source, span):
        raw = source._data[span[0]:span[1]]
        for title in forbidden_titles:
            assert ('"title": "' + title + '"').encode() not in raw
        reads.append(span)
        return original(source, span)

    monkeypatch.setattr(JsonSources, 'decode', checked)
    out = tmp_path / 'cohort-run'
    args = ['extract', '--cohort', cohort, '--snapshot', str(snapshot),
            '--out', str(out), '--config', str(config), '--freeze-dir', str(tuning_freeze)]
    if selection_flag == 'limit':
        args.extend(['--limit', '40'])
    elif selection_flag == 'books':
        args.extend(['--books', *sorted(expected)])
    assert cli.main(args) == 0
    assert set(read_json(out / 'selection.json')['book_ids']) == expected
    assert reads


@pytest.mark.parametrize('cohort,book_id', [
    ('tune', 'book-00'), ('tune', 'book-01'), ('tune', 'unknown'),
    ('rehearsal', 'book-00'), ('rehearsal', 'book-02'), ('proof', 'book-02'),
])
def test_cli_foreign_book_refused_before_image_read(
        offline_inputs, tuning_freeze, tmp_path, monkeypatch, cohort, book_id):
    from scripts.extraction_eval import runner

    snapshot, config = offline_inputs
    readers = []
    monkeypatch.setattr(runner, 'local_image', lambda *a, **k: readers.append(True))
    out = tmp_path / 'refused-cohort'
    with pytest.raises(SystemExit) as exc:
        cli.main(['extract', '--cohort', cohort, '--snapshot', str(snapshot),
                  '--out', str(out), '--config', str(config), '--freeze-dir', str(tuning_freeze),
                  '--books', book_id])
    assert exc.value.code == 2
    assert readers == []
    assert read_json(out / 'run.json')['tuning']['refused_attempts'][0]['source_ids'] == [book_id]


def test_original_experiment_flag_is_removed():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(['extract', '--snapshot', '.', '--out', '.',
                                       '--original-experiment'])


def test_openai_missing_key_refused_before_ledger(offline_inputs, tmp_path, monkeypatch,
                                                system_temporary_dir, capsys, caplog):
    from scripts import data_cleanup
    from scripts.extraction_eval import store
    snapshot, config_path = offline_inputs
    atomic_json(config_path, openai_fixture_config(read_json(config_path)))
    monkeypatch.setattr('builtins.input', lambda prompt: 'AFFIRMATIVE')
    monkeypatch.setattr(data_cleanup, 'load_secrets', lambda path: {
        'ANTHROPIC_API_KEY': 'unused-app-fixture', 'OPENAI_API_KEY': 'unused-openai-fixture'})
    monkeypatch.setattr(store, 'Ledger', lambda *a, **kw: pytest.fail('Ledger opened without key'))
    out = tmp_path / 'missing-openai-key'
    with pytest.raises(SystemExit) as error:
        cli.main(['extract', '--snapshot', str(snapshot), '--out', str(out),
                  '--config', str(config_path), '--books', 'book-02', '--execute'])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert 'EVAL_OPENAI_API_KEY is absent or blank' in captured.err
    output = captured.out + captured.err + caplog.text + (out / 'run.json').read_text()
    assert 'unused-app-fixture' not in output
    assert 'unused-openai-fixture' not in output
