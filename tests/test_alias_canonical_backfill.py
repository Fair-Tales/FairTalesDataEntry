"""Offline migration safety: raw records, budgets, preconditions and replay."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import FailedPrecondition

from alias_schema import canonical_bundle
from scripts import backfill_alias_canonical as backfill


def record(document_id="old_id", name="The Brown Bear", **extra):
    return backfill.AliasRecord(document_id, {
        "name": name, "book": "books/book", "character": "characters/bear",
        "provenance": [{"source": "student"}], "last_updated": "original-time", **extra,
    }, "version-1")


def test_plan_is_additive_and_keeps_actual_id_and_raw_bytes():
    original = record("arbitrary_legacy_id", "  THE Brown—Bear  ")
    before = deepcopy(original.data)
    planned, flagged = backfill.plan_backfill([original])
    assert not flagged
    assert original.data == before
    assert planned[0].updates == {
        **canonical_bundle("  THE Brown—Bear  "), "storage_id": "arbitrary_legacy_id",
    }
    assert set(planned[0].updates) <= backfill.ADDITIVE_FIELDS


@pytest.mark.parametrize("metadata", [
    {"canonical_form": "Special Bear", "canonical_override": True},
    {"canonical_form": "Special Bear", "canonical_source": "manual"},
    {"canonical_source": "unrecognized-old-tool"},
    {"canonical_form": "Different existing canonical"},
    {"canonical_extra": {"legacy": "do not lose"}},
    {"canonical_override": "false"},
    {"canonical_override": 0},
    {"canonical_input_hash": "stale"},
    {"storage_id": "another_id"},
])
def test_unknown_stale_and_manual_metadata_is_preserved_and_flagged(metadata):
    original = record(**metadata)
    before = deepcopy(original.data)
    planned, flags = backfill.plan_backfill([original])
    assert planned == []
    assert len(flags) == 1
    assert original.data == before


def test_consistent_partial_bundle_can_be_completed_but_not_rewritten():
    original = record(canonical_form="the brown bear", canonical_source="derived")
    planned, flags = backfill.plan_backfill([original])
    assert not flags
    assert "canonical_form" not in planned[0].updates
    assert "canonical_source" not in planned[0].updates
    completed = backfill.AliasRecord(original.document_id, {**original.data, **planned[0].updates})
    assert backfill.plan_backfill([completed]) == ([], [])


def test_known_canonical_history_is_retained_without_being_rewritten():
    history = [{"canonical_form": "Previous human choice", "canonical_override": True}]
    original = record(canonical_history=history)
    planned, flagged = backfill.plan_backfill([original])
    assert not flagged
    assert "canonical_history" not in planned[0].updates
    assert original.data["canonical_history"] == history


def test_dry_run_never_calls_writer_or_creates_checkpoint(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    lines = []
    result = backfill.run_backfill(
        [record()], checkpoint=checkpoint, log=lines.append,
        apply_update=lambda item: pytest.fail("A dry run must never write"),
    )
    assert result["written"] == 0
    assert result["planned"] == 1
    assert not checkpoint.exists()
    ledger = json.loads(lines[0])
    change = ledger["changes"][0]
    assert change["document_id"] == "old_id"
    assert change["original"]["name"] == "The Brown Bear"
    assert change["diff"]["canonical_form"] == {
        "before_present": False, "before": None, "after": "the brown bear",
    }
    assert ledger["raw_invariants"] == backfill.raw_invariants([record()])


def test_execution_prints_entire_diff_before_first_write_and_is_idempotent(tmp_path):
    originals = [record("one"), record("two", "Her daughter")]
    storage = {item.document_id: deepcopy(item.data) for item in originals}
    lines = []

    def apply(item):
        ledger = json.loads(lines[0])
        assert len(ledger["changes"]) == 2
        storage[item.record.document_id].update(item.updates)

    checkpoint = tmp_path / "progress.json"
    first = backfill.run_backfill(originals, execute=True, checkpoint=checkpoint,
                                 apply_update=apply, log=lines.append)
    assert first["written"] == 2
    assert len(json.loads(checkpoint.read_text())["completed"]) == 2
    after = [backfill.AliasRecord(key, data, "version-2") for key, data in storage.items()]
    assert backfill.raw_invariants(originals) == backfill.raw_invariants(after)
    second = backfill.run_backfill(after, execute=True, checkpoint=checkpoint,
                                  apply_update=lambda item: pytest.fail("Replay wrote twice"),
                                  log=lambda line: None)
    assert second["planned"] == second["written"] == 0


def test_partial_failure_checkpoints_only_success_and_resume_finishes(tmp_path):
    originals = [record("one"), record("two")]
    stored = {item.document_id: deepcopy(item.data) for item in originals}
    checkpoint = tmp_path / "progress.json"

    def apply(item):
        if item.record.document_id == "two":
            raise FailedPrecondition("Concurrent human edit")
        stored[item.record.document_id].update(item.updates)

    with pytest.raises(FailedPrecondition):
        backfill.run_backfill(originals, execute=True, checkpoint=checkpoint,
                              apply_update=apply, log=lambda line: None)
    assert set(json.loads(checkpoint.read_text())["completed"]) == {"one"}
    assert "canonical_form" not in stored["two"]
    fresh = [backfill.AliasRecord(key, data, "fresh-version") for key, data in stored.items()]
    written = []
    result = backfill.run_backfill(fresh, execute=True, checkpoint=checkpoint,
                                  apply_update=lambda item: written.append(item.record.document_id),
                                  log=lambda line: None)
    assert written == ["two"]
    assert result["written"] == 1


def test_missing_update_time_aborts_before_any_write(tmp_path):
    originals = [record("one"), backfill.AliasRecord("two", {"name": "Mum"})]
    with pytest.raises(ValueError, match="update_time"):
        backfill.run_backfill(originals, execute=True, checkpoint=tmp_path / "progress.json",
                              apply_update=lambda item: pytest.fail("Preflight should reject all writes"),
                              log=lambda line: None)


class FakeQuery:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def collection(self, name):
        self.calls.append(("collection", name))
        assert name == "aliases"  # No character/reference/corpus reread.
        return self

    def limit(self, limit):
        self.calls.append(("limit", limit))
        self.read_limit = limit
        return self

    def stream(self, *, retry):
        assert retry is None
        self.calls.append(("stream",))
        return iter(self.docs[:self.read_limit])


def test_single_bounded_scan_uses_actual_snapshot_id():
    db = FakeQuery([SimpleNamespace(id="actual", update_time="v", to_dict=lambda: {"name": "Mum"})])
    records = backfill.read_firestore_once(db, max_aliases=200)
    assert records[0].document_id == "actual"
    assert db.calls == [("collection", "aliases"), ("limit", 201), ("stream",)]


def test_over_budget_aborts_without_more_reads_or_writes():
    db = FakeQuery([object()] * 202)
    with pytest.raises(ValueError, match="exceeds"):
        backfill.read_firestore_once(db, max_aliases=200)
    assert db.calls == [("collection", "aliases"), ("limit", 201), ("stream",)]


def test_budget_is_printed_before_even_reading_local_input(monkeypatch, capsys, tmp_path):
    observed = []

    def read(path, *, max_aliases):
        observed.append(capsys.readouterr().out)
        return [record()]

    monkeypatch.setattr(backfill, "read_snapshot", read)
    assert backfill.main(["--snapshot", str(tmp_path / "unused.json")]) == 0
    assert observed[0].startswith("BUDGET BEFORE INPUT:")
    assert "expected Firestore alias reads 0" in observed[0]
    assert "character reads/writes 0" in observed[0]


def test_live_budget_counts_one_alias_scan_and_no_character_rewrite():
    lines = []
    backfill.print_budget(live=True, execute=True, max_aliases=200, log=lines.append)
    assert "expected Firestore alias reads 200" in lines[0]
    assert "hard read cap 201" in lines[0]
    assert "writes at most 200" in lines[0]
    assert "0.402%" in lines[0]


def test_offline_cli_preserves_snapshot_and_never_initializes_clients(tmp_path, monkeypatch, capsys):
    from scripts import data_cleanup
    monkeypatch.setattr(data_cleanup, "build_firestore_client",
                        lambda *a, **kw: pytest.fail("Offline mode initialized Firestore"))
    snapshot = tmp_path / "snapshot.json"
    original = json.dumps({"aliases": [{"id": "legacy", "data": {"name": "The bear"}}]})
    snapshot.write_text(original)
    assert backfill.main(["--snapshot", str(snapshot)]) == 0
    assert snapshot.read_text() == original
    assert {path.name for path in tmp_path.iterdir()} == {"snapshot.json"}
    assert "dry-run (no writes)" in capsys.readouterr().out


@pytest.mark.parametrize("args", [[], ["--snapshot", "unused", "--execute"],
                                  ["--firestore", "--execute"],
                                  ["--firestore", "--max-aliases", "0"]])
def test_cli_requires_explicit_valid_source_and_execute_arguments(args):
    with pytest.raises(SystemExit) as exc:
        backfill.main(args)
    assert exc.value.code == 2


def test_duplicate_snapshot_ids_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        backfill.plan_backfill([record(), record()])


def test_actual_entity_write_uses_precondition_and_preserves_concurrent_override(tmp_path):
    """Exercise the real Alias/Field persistence layer against a tiny fake DB."""
    created = datetime(2026, 11, 5, tzinfo=timezone.utc)
    changed = datetime(2026, 11, 6, tzinfo=timezone.utc)
    data = {
        "name": "The Bear", "book": SimpleNamespace(path="books/book", id="book"),
        "character": SimpleNamespace(path="characters/bear", id="bear"),
        "unknown_student_metadata": {"keep": "forever"},
    }
    original = backfill.AliasRecord("actual-legacy-id", data, created)
    stored = dict(data)
    writes = []

    class FakeRef:
        def update(self, updates, *, option):
            assert option._last_update_time == created
            writes.append(dict(updates))
            stored.update(updates)
            return SimpleNamespace(update_time=changed)

    class FakeDB:
        def collection(self, collection):
            assert collection == "aliases"
            return self

        def document(self, document_id):
            assert document_id == "actual-legacy-id"
            return FakeRef()

    result = backfill.run_backfill([original], execute=True, firestore_client=FakeDB(),
                                  checkpoint=tmp_path / "checkpoint.json", log=lambda line: None)
    assert result["written"] == 1
    assert len(writes) == 1
    assert stored["unknown_student_metadata"] == data["unknown_student_metadata"]
    assert stored["name"] == "The Bear"
    assert stored["canonical_form"] == "the bear"

    # A human override arriving between scan and apply rejects the entire bundle.
    stored.update(canonical_bundle(data["name"], override="Human choice"))

    def reject(self, updates, *, option):
        assert option._last_update_time == created
        raise FailedPrecondition("Newer human edit")

    FakeRef.update = reject
    with pytest.raises(FailedPrecondition):
        backfill.run_backfill([original], execute=True, firestore_client=FakeDB(),
                              checkpoint=tmp_path / "checkpoint.json", log=lambda line: None)
    assert stored["canonical_form"] == "Human choice"
    assert stored["canonical_override"] is True
