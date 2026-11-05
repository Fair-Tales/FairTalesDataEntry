#!/usr/bin/env python3
"""Add missing canonical projection fields; never replace recorded alias data.

The default is a dry run. A local ``--snapshot`` needs no credentials or network.
Future live execution explicitly requires ``--firestore --execute --checkpoint``.
The complete additive diff is printed before the first write. One bounded alias
scan supplies both the dry-run ledger and conditional-write preconditions; no
character, book, page or reference lookups are needed. Characters already provide
their stored identity and do not require a mass rewrite (§4.1.1).

Unknown, stale and manual canonical metadata is retained and flagged. Partial
known derived bundles are completed only when every existing component agrees
with the current rule. A conflict aborts; rerunning reads a fresh snapshot once
and safely completes any remaining entries. This script never invokes inference
or S3. Importing it does not initialize any service client.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable, Iterable

from google.api_core.exceptions import GoogleAPIError

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from alias_schema import canonical_bundle, raw_surface_hash  # noqa: E402
from text_content.backfill_alias import AliasBackfillText as T  # noqa: E402

SCHEMA_VERSION = "alias-canonical-backfill-v1"
DEFAULT_MAX_ALIASES = 200
CANONICAL_FIELDS = frozenset({
    "canonical_form", "canonical_source", "canonical_override",
    "canonical_rule_version", "canonical_input_hash", "alias_schema_version",
})
ADDITIVE_FIELDS = CANONICAL_FIELDS | {"storage_id"}
KNOWN_CANONICAL_METADATA = CANONICAL_FIELDS | {"canonical_history"}


@dataclass(frozen=True)
class AliasRecord:
    document_id: str
    data: dict[str, Any]
    update_time: Any = None


@dataclass(frozen=True)
class PlannedUpdate:
    record: AliasRecord
    updates: dict[str, Any]


def _json_value(value: Any) -> Any:
    """Serialize references locally without dereferencing any document."""
    if hasattr(value, "path"):
        return {"reference_path": value.path}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and math.isnan(value):
        return {"legacy_nan": True}
    return value


def _json(data: Any) -> str:
    return json.dumps(_json_value(data), ensure_ascii=False, sort_keys=True)


def raw_invariants(records: Iterable[AliasRecord]) -> dict[str, Any]:
    """Raw identity/surface/reference fingerprint also fixes matchability inputs."""
    rows = [
        {"id": record.document_id, **{
            key: record.data.get(key)
            for key in ("name", "book", "character", "datetime_created", "last_updated", "entered_by")
        }}
        for record in records
    ]
    rows.sort(key=lambda row: row.get("id", ""))
    return {"count": len(rows), "sha256": hashlib.sha256(_json(rows).encode("utf-8")).hexdigest()}


def _absent(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))


def _validate_records(records: list[AliasRecord]) -> None:
    seen = set()
    for record in records:
        if not isinstance(record.document_id, str) or not record.document_id or "/" in record.document_id:
            raise ValueError(T.snapshot_invalid)
        if record.document_id in seen:
            raise ValueError(T.duplicate_id.format(document_id=record.document_id))
        seen.add(record.document_id)
        if not isinstance(record.data, dict):
            raise ValueError(T.snapshot_invalid)


def plan_backfill(records: Iterable[AliasRecord]) -> tuple[list[PlannedUpdate], list[dict[str, str]]]:
    """Pure additive planning: no reads, writes, inference, or identity decisions."""
    records = list(records)
    _validate_records(records)
    planned = []
    flagged = []
    for record in sorted(records, key=lambda item: item.document_id):
        data = record.data
        name = data.get("name")
        reason = None
        if not isinstance(name, str) or not name.strip():
            reason = "missing_or_invalid_raw_surface"
        elif not _absent(data.get("storage_id")) and data.get("storage_id") != record.document_id:
            reason = "stored_id_conflicts_with_actual_document_id"
        elif data.get("canonical_override") is True or data.get("canonical_source") == "manual":
            reason = "human_override_preserved"
        elif any(key.startswith("canonical_") and key not in KNOWN_CANONICAL_METADATA for key in data):
            reason = "unknown_canonical_metadata_preserved"
        if reason:
            flagged.append({"document_id": record.document_id, "reason": reason})
            continue
        expected = canonical_bundle(name)
        expected["storage_id"] = record.document_id
        if set(expected) - ADDITIVE_FIELDS:
            raise ValueError(T.illegal_updates)
        # False and 0 compare equal in Python; a non-bool override flag is still
        # unknown metadata and must not be silently interpreted as a decision.
        override = data.get("canonical_override")
        if not _absent(override) and type(override) is not bool:
            flagged.append({"document_id": record.document_id, "reason": "invalid_override_state_preserved"})
            continue
        conflicting = [key for key, value in expected.items()
                       if not _absent(data.get(key)) and data.get(key) != value]
        if conflicting:
            flagged.append({"document_id": record.document_id,
                            "reason": "existing_metadata_preserved:" + ",".join(sorted(conflicting))})
            continue
        updates = {key: value for key, value in expected.items() if _absent(data.get(key))}
        if updates:
            planned.append(PlannedUpdate(record, updates))
    return planned, flagged


def _ledger(records, planned, flagged) -> dict[str, Any]:
    return {
        "backfill_version": SCHEMA_VERSION,
        "raw_invariants": raw_invariants(records),
        "character_migration": "not_required_legacy_identity_uses_stored_reference",
        "changes": [{
            "document_id": item.record.document_id,
            "original": {key: item.record.data.get(key) for key in
                         ("name", "book", "character", "datetime_created", "last_updated", "entered_by")},
            "expected_update_time": item.record.update_time,
            "raw_surface_hash": raw_surface_hash(item.record.data.get("name")),
            "diff": {key: {"before_present": key in item.record.data,
                           "before": item.record.data.get(key), "after": value}
                     for key, value in item.updates.items()},
        } for item in planned],
        "flags": flagged,
    }


def _apply_with_fields(item: PlannedUpdate, firestore_client) -> None:
    """All document writes pass through the shared Field/entity persistence API."""
    from data_structures.alias import Alias

    record = item.record
    alias = Alias(db_object={**record.data, "storage_id": record.document_id,
                             "_update_time": record.update_time})
    alias.update_fields_conditionally(
        item.updates,
        expected_update_time=record.update_time,
        expected_raw_hash=raw_surface_hash(record.data.get("name")),
        firestore_client=firestore_client,
    )


def _load_checkpoint(path: Path | None, source: str) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"backfill_version": SCHEMA_VERSION, "source": source, "completed": {}}
    result = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(result, dict) or result.get("backfill_version") != SCHEMA_VERSION
            or not isinstance(result.get("completed"), dict)):
        raise ValueError(T.malformed_checkpoint)
    if result.get("source") != source:
        raise ValueError(T.checkpoint_mismatch)
    return result


def run_backfill(records: Iterable[AliasRecord], *, execute: bool = False,
                 firestore_client=None, checkpoint: Path | None = None,
                 source: str = "offline", log: Callable[[str], None] = print,
                 apply_update: Callable[[PlannedUpdate], None] | None = None) -> dict[str, Any]:
    """Print full diff, then optionally apply its additions with safe replay.

    ``apply_update`` is an injected conditional entity writer for offline tests.
    The checkpoint is an audit of confirmed writes, never authority to skip a
    currently missing field. A successful write followed by a checkpoint failure
    is harmless: the next single scan observes the completed projection.
    """
    records = list(records)
    planned, flagged = plan_backfill(records)
    before = raw_invariants(records)
    by_id = {item.record.document_id: item.updates for item in planned}
    projected = [AliasRecord(record.document_id,
                            {**record.data, **by_id.get(record.document_id, {})}, record.update_time)
                 for record in records]
    if raw_invariants(projected) != before:
        raise ValueError(T.raw_changed)
    log(_json(_ledger(records, planned, flagged)))
    written = 0
    if execute:
        if checkpoint is None:
            raise ValueError(T.execute_source_error)
        for item in planned:
            if item.record.update_time is None:
                raise ValueError(T.missing_timestamp.format(document_id=item.record.document_id))
        progress = _load_checkpoint(checkpoint, source)
        for item in planned:
            if apply_update is not None:
                apply_update(item)
            else:
                _apply_with_fields(item, firestore_client)
            written += 1
            completed = progress.get("completed", {})
            completed[item.record.document_id] = {
                "raw_surface_hash": raw_surface_hash(item.record.data.get("name")),
                "applied_fields": item.updates,
                "source_update_time": _json_value(item.record.update_time),
            }
            progress["raw_invariants"] = before
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
            temporary.write_text(_json(progress) + "\n", encoding="utf-8")
            temporary.replace(checkpoint)
    result = {"planned": len(planned), "written": written, "flagged": len(flagged),
              "raw_invariants": before, "execute": execute}
    log(T.complete.format(mode=T.executed if execute else T.dry_run,
                          planned=len(planned), written=written, flagged=len(flagged),
                          raw_count=before.get("count"), raw_hash=before.get("sha256")))
    return result


def read_snapshot(path: Path, *, max_aliases: int) -> list[AliasRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if not isinstance(aliases, list):
        raise ValueError(T.snapshot_invalid)
    if len(aliases) > max_aliases:
        raise ValueError(T.overflow.format(limit=max_aliases))
    if any(not isinstance(row, dict) for row in aliases):
        raise ValueError(T.snapshot_invalid)
    records = [AliasRecord(row.get("id"), row.get("data"), row.get("update_time")) for row in aliases]
    _validate_records(records)
    return records


def read_firestore_once(db, *, max_aliases: int) -> list[AliasRecord]:
    """One bounded collection scan; no automatic retry or reference fetch."""
    snapshots = list(db.collection("aliases").limit(max_aliases + 1).stream(retry=None))
    if len(snapshots) > max_aliases:
        raise ValueError(T.overflow.format(limit=max_aliases))
    records = [AliasRecord(snapshot.id, snapshot.to_dict() or {}, snapshot.update_time)
               for snapshot in snapshots]
    _validate_records(records)
    return records


def print_budget(*, live: bool, execute: bool, max_aliases: int, log=print) -> None:
    read_cap = max_aliases + 1 if live else 0
    log(T.budget.format(expected_reads=min(DEFAULT_MAX_ALIASES, max_aliases) if live else 0,
                        read_cap=read_cap, write_cap=max_aliases if live and execute else 0,
                        share=read_cap / 50000 * 100))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=T.description)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--snapshot", type=Path, help=T.snapshot_help)
    sources.add_argument("--firestore", action="store_true", help=T.firestore_help)
    parser.add_argument("--execute", action="store_true", help=T.execute_help)
    parser.add_argument("--max-aliases", type=int, default=DEFAULT_MAX_ALIASES, help=T.max_aliases_help)
    parser.add_argument("--checkpoint", type=Path, help=T.checkpoint_help)
    parser.add_argument("--secrets", default=".streamlit/secrets.toml", help=T.secrets_help)
    parser.add_argument("--project", default="sawdataentry", help=T.project_help)
    args = parser.parse_args(argv)
    if args.max_aliases < 1:
        parser.error(T.positive_limit_error)
    if args.execute and (not args.firestore or args.checkpoint is None):
        parser.error(T.execute_source_error)
    print_budget(live=args.firestore, execute=args.execute, max_aliases=args.max_aliases)
    db = None
    try:
        if args.firestore:
            from scripts.data_cleanup import build_firestore_client, load_secrets
            db = build_firestore_client(load_secrets(args.secrets), project=args.project)
            records = read_firestore_once(db, max_aliases=args.max_aliases)
        else:
            records = read_snapshot(args.snapshot, max_aliases=args.max_aliases)
        run_backfill(records, execute=args.execute, firestore_client=db,
                     checkpoint=args.checkpoint, source=f"firestore:{args.project}" if args.firestore else "offline")
    except (GoogleAPIError, OSError, ValueError, TypeError, KeyError) as exc:
        print(T.failure.format(error=exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
