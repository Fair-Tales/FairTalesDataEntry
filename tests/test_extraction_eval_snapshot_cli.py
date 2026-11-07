"""Snapshot planning, authorization and quota errors with no live clients."""
import json

from google.api_core.exceptions import PermissionDenied, ResourceExhausted
import pytest

from scripts.extraction_eval import cli, snapshot
from scripts.extraction_eval.read_cost import estimate_snapshot_reads
from scripts.extraction_eval.store import atomic_json, digest, read_json


def forbidden(*args, **kwargs):
    raise AssertionError("No service client or read should be attempted")


@pytest.fixture
def fake_snapshot(monkeypatch):
    calls = []
    backend = object()
    monkeypatch.setattr(snapshot, "create_live_backend", lambda *args, **kwargs: backend)

    def capture(out, received_backend, *, entered_by, books, audit_source_changes, audit_membership):
        assert received_backend is backend
        calls.append({"source_changes": audit_source_changes, "membership": audit_membership})
        return {"snapshot_id": "fake", "books": [], "complete": True,
                "source_consistent": True if audit_source_changes and audit_membership else None,
                "audits": calls[-1]}

    monkeypatch.setattr(snapshot, "create_snapshot", capture)
    return calls


@pytest.mark.parametrize("options,expected", [
    ([], {"source_changes": False, "membership": False}),
    (["--audit-source-changes"], {"source_changes": True, "membership": False}),
    (["--audit-membership"], {"source_changes": False, "membership": True}),
    (["--audit"], {"source_changes": True, "membership": True}),
])
def test_snapshot_optional_audit_flags(tmp_path, fake_snapshot, options, expected, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", forbidden)
    assert cli.main(["snapshot", "--out", str(tmp_path), "--estimated-books", "1", *options]) == 0
    assert fake_snapshot == [expected]
    output = capsys.readouterr().out
    assert "Estimated Firestore document reads before any service access" in output
    if not options:
        assert "NOT AUDITED" in output


def test_snapshot_estimate_and_confirmation_precede_client(tmp_path, fake_snapshot, monkeypatch, capsys):
    confirmed = []

    def confirm(prompt):
        output = capsys.readouterr().out
        assert '"estimated_firestore_reads": 11507' in output
        assert "not a hard cap" in output
        assert not fake_snapshot
        confirmed.append(prompt)
        return "AFFIRMATIVE"

    original = snapshot.create_live_backend

    def backend(*args, **kwargs):
        assert confirmed
        return original(*args, **kwargs)

    monkeypatch.setattr("builtins.input", confirm)
    monkeypatch.setattr(snapshot, "create_live_backend", backend)
    assert cli.main(["snapshot", "--out", str(tmp_path)]) == 0
    assert len(confirmed) == 1


@pytest.mark.parametrize("answer", ["", "yes", "affirmative", " AFFIRMATIVE", None])
def test_snapshot_refused_or_missing_confirmation_never_constructs_client(tmp_path, monkeypatch, answer, capsys):
    monkeypatch.setattr(snapshot, "create_live_backend", forbidden)
    monkeypatch.setattr(snapshot, "create_snapshot", forbidden)

    def decline(prompt):
        if answer is None:
            raise EOFError
        return answer

    monkeypatch.setattr("builtins.input", decline)
    assert cli.main(["snapshot", "--out", str(tmp_path)]) == 1
    assert "No Firestore reads authorized" in capsys.readouterr().out


def test_snapshot_threshold_is_strictly_exceeded(tmp_path, fake_snapshot, monkeypatch):
    monkeypatch.setattr("builtins.input", forbidden)
    assert cli.main(["snapshot", "--out", str(tmp_path), "--read-confirmation-threshold", "11507"]) == 0
    assert len(fake_snapshot) == 1


@pytest.mark.parametrize("options", [
    ["--estimated-books", "0"],
    ["--estimated-documents-per-book", "-1"],
    ["--read-confirmation-threshold", "-1"],
])
def test_invalid_read_planning_options_fail_before_client(tmp_path, monkeypatch, options):
    monkeypatch.setattr(snapshot, "create_live_backend", forbidden)
    with pytest.raises(SystemExit) as error:
        cli.main(["snapshot", "--out", str(tmp_path), *options])
    assert error.value.code == 2


def test_estimates_include_whole_discovery_and_requested_audits(tmp_path):
    subset = estimate_snapshot_reads(tmp_path, books=[f"book-{number}" for number in range(20)])
    full = estimate_snapshot_reads(tmp_path)
    audited = estimate_snapshot_reads(tmp_path, audit_source_changes=True, audit_membership=True)
    assert subset.get("estimated_firestore_reads") == 1357
    assert full.get("estimated_firestore_reads") == 11507
    assert audited.get("remaining_reads_by_pass") == {
        "capture": 11507, "source_change_audit": 9750, "membership_audit": 11310}
    assert full.get("remaining_reads_by_pass", {}).get("source_change_audit") == 0


def test_partial_inventory_estimate_credits_saved_reads(tmp_path):
    atomic_json(tmp_path / "inventory-progress.json", {
        "raw_documents": {f"books/{number}": {} for number in range(195)},
        "queries": {json.dumps(["books", "pilot_import"]): {"done": True, "paths": []}},
    })
    plan = estimate_snapshot_reads(tmp_path)
    assert plan.get("captured_documents_reused") == 195
    assert plan.get("estimated_firestore_reads") == 11507 - 195 - 2


def test_frozen_inventory_estimate_has_no_capture_reads_and_reuses_audit_progress(tmp_path):
    atomic_json(tmp_path / "inventory.json", {
        "raw_documents": {"books/a": {}, "pages/p1": {}, "pages/p2": {}},
        "missing_documents": ["authors/missing"],
        "records": [{"book": {"id": "a"}, "related": {"pages": [{}, {}], "characters": [], "aliases": []}}],
    })
    atomic_json(tmp_path / "audit-progress.json", {
        "checked_documents": ["books/a"], "checked_missing_documents": ["authors/missing"],
        "queries": {json.dumps(["pages", "a"]): {"done": True, "paths": ["pages/p1", "pages/p2"]}},
    })
    assert estimate_snapshot_reads(tmp_path).get("estimated_firestore_reads") == 0
    audited = estimate_snapshot_reads(tmp_path, audit_source_changes=True, audit_membership=True)
    assert audited.get("remaining_reads_by_pass") == {
        "capture": 0, "source_change_audit": 2, "membership_audit": 6}


def test_estimate_credits_completed_variants_of_interrupted_queries(tmp_path):
    atomic_json(tmp_path / "inventory-progress.json", {
        "queries": {
            json.dumps(["books", "pilot_import"]): {"done": False, "cursor": {"value_index": 1}},
            json.dumps(["pages", "a"]): {"done": False, "cursor": {"value_index": 2}},
        },
    })
    assert estimate_snapshot_reads(tmp_path).get("estimated_firestore_reads") == 11507 - 3


def test_empty_membership_queries_have_three_read_allowance_each(tmp_path):
    atomic_json(tmp_path / "inventory.json", {"records": [{"book": {"id": "a"}, "related": {}}]})
    plan = estimate_snapshot_reads(tmp_path, audit_membership=True)
    assert plan.get("remaining_reads_by_pass", {}).get("membership_audit") == 9


def test_all_completed_memberships_have_zero_remaining_reads_even_after_deletion(tmp_path):
    atomic_json(tmp_path / "inventory.json", {
        "records": [{"book": {"id": "a"}, "related": {"pages": [{}, {}]}}],
    })
    atomic_json(tmp_path / "audit-progress.json", {
        "queries": {json.dumps([collection, "a"]): {"done": True, "paths": []}
                    for collection in ("pages", "characters", "aliases")},
    })
    assert estimate_snapshot_reads(tmp_path, audit_membership=True).get("estimated_firestore_reads") == 0


@pytest.mark.parametrize("state_name,fields", [
    ("inventory-progress.json", {"entered_by": "different"}),
    ("inventory.json", {"requested_books": ["different"]}),
    ("manifest.json", {"audits": {"source_changes": True, "membership": True}}),
    ("audit-progress.json", {"audits": {"source_changes": True, "membership": False}}),
])
def test_resume_contract_checked_before_clients_or_confirmation(tmp_path, monkeypatch, state_name, fields):
    atomic_json(tmp_path / state_name, {
        "entered_by": "pilot_import", "requested_books": [],
        "audits": {"source_changes": False, "membership": False}, **fields,
    })
    monkeypatch.setattr(snapshot, "create_live_backend", forbidden)
    monkeypatch.setattr("builtins.input", forbidden)
    with pytest.raises(SystemExit) as error:
        cli.main(["snapshot", "--out", str(tmp_path)])
    assert error.value.code == 2


def test_completed_snapshot_estimate_is_zero(tmp_path):
    atomic_json(tmp_path / "manifest.json", {"complete": True})
    assert estimate_snapshot_reads(tmp_path, audit_source_changes=True, audit_membership=True).get("estimated_firestore_reads") == 0


def test_quota_exhaustion_returns_clear_resume_message_without_retry(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(snapshot, "create_live_backend", lambda *args, **kwargs: object())

    def interrupted(out, backend, **kwargs):
        calls.append(backend)
        atomic_json(out / "inventory-progress.json", {"raw_documents": {"books/saved": {}}})
        raise ResourceExhausted("Quota exceeded")

    monkeypatch.setattr(snapshot, "create_snapshot", interrupted)
    assert cli.main(["snapshot", "--out", str(tmp_path), "--estimated-books", "1"]) == 2
    assert len(calls) == 1
    assert (tmp_path / "inventory-progress.json").exists()
    error = capsys.readouterr().err
    assert "Firestore quota is exhausted" in error
    assert "midnight US/Pacific" in error
    assert "same snapshot command" in error
    assert str(tmp_path) in error
    assert "Traceback" not in error


def test_other_service_errors_are_not_misreported_as_quota(tmp_path, monkeypatch, capsys):
    def denied(*args, **kwargs):
        raise PermissionDenied("Not allowed")

    monkeypatch.setattr(snapshot, "create_live_backend", denied)
    with pytest.raises(PermissionDenied):
        cli.main(["snapshot", "--out", str(tmp_path), "--estimated-books", "1"])
    assert "quota is exhausted" not in capsys.readouterr().err


@pytest.mark.parametrize("audits", [
    {"source_changes": False, "membership": False},
    {"source_changes": True, "membership": False},
    {"source_changes": False, "membership": True},
])
def test_unaudited_and_partially_audited_snapshots_load_with_explicit_provenance(tmp_path, audits):
    manifest = {"complete": True, "source_consistent": None, "audits": audits}
    atomic_json(tmp_path / "manifest.json", manifest)
    assert cli._load_snapshot(tmp_path) == manifest


@pytest.mark.parametrize("fields", [
    {}, {"source_consistent": None}, {"source_consistent": False},
    {"audits": {"source_changes": False, "membership": False}},
    {"source_consistent": None, "audits": {"source_changes": True, "membership": True}},
    {"source_consistent": None, "audits": {"source_changes": "false", "membership": False}},
])
def test_unknown_or_failed_consistency_is_rejected(tmp_path, fields):
    atomic_json(tmp_path / "manifest.json", {"complete": True, **fields})
    with pytest.raises(ValueError):
        cli._load_snapshot(tmp_path)


def test_default_snapshot_then_score_retains_explicit_unaudited_provenance(tmp_path, monkeypatch, capsys):
    class FakeBackend:
        def iter_books(self, entered_by):
            return [{"id": "a", "path": "books/a", "update_time": "v1",
                     "data": {"title": "Story", "entered_by": entered_by}}]

        def iter_related(self, collection, book_id):
            return []

        def list_images(self, folder):
            return []

        get_document = staticmethod(forbidden)
        image_info = staticmethod(forbidden)

    output = tmp_path / "snapshot"
    run = tmp_path / "run"
    monkeypatch.setattr(snapshot, "create_live_backend", lambda *args, **kwargs: FakeBackend())
    monkeypatch.setattr("builtins.input", forbidden)
    assert cli.main(["snapshot", "--out", str(output), "--estimated-books", "1"]) == 0
    manifest = read_json(output / "manifest.json")
    assert manifest.get("audits") == {"source_changes": False, "membership": False}
    assert manifest.get("source_consistent") is None
    selection = {"book_ids": ["a"]}
    atomic_json(run / "run.json", {"contract": {
        "snapshot_id": manifest.get("snapshot_id"), "snapshot_hash": digest(manifest),
        "selection": selection,
    }})
    atomic_json(run / "selection.json", selection)
    atomic_json(run / "predictions.json", {"snapshot_id": manifest.get("snapshot_id"), "books": {}})
    monkeypatch.setattr(snapshot, "create_live_backend", forbidden)
    monkeypatch.setattr(cli, "_live_anthropic", forbidden)
    assert cli.main(["score", "--original-experiment", "--snapshot", str(output), "--run", str(run)]) == 0
    summary = read_json(run / "reports" / "summary.json")
    assert summary.get("snapshot_audit_status") == "not_audited"
    assert summary.get("snapshot_source_consistent") is None
    assert summary.get("snapshot_audits") == {"source_changes": False, "membership": False}
    assert "NOT AUDITED" in (run / "reports" / "inspection.html").read_text()
    assert "NOT AUDITED" in capsys.readouterr().out
