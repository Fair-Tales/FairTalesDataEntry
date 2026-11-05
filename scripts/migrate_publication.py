#!/usr/bin/env python3
"""Add event-scoped publication fields; dry-run default, no inference or S3.

Legacy fields are preserved in place as well as archived. Versioned adapters and
exports expose the new compatibility projections without overwriting student
entries. A single bounded read and update-time conditional writes prevent races.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import sys

from google.api_core.exceptions import GoogleAPIError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bibliography import (METADATA_SCHEMA_VERSION, PERSISTED_FIELDS,
                           legacy_publication, publication_projection, record_hash)
from scripts.backfill_alias_canonical import _json_value
from text_content.publication import PublicationText as T

MIGRATION_VERSION = "publication-migration-v1"
DEFAULT_MAX_BOOKS = 276


@dataclass(frozen=True)
class BookRecord:
    document_id: str
    data: dict
    update_time: object = None


@dataclass(frozen=True)
class PlannedUpdate:
    record: BookRecord
    updates: dict


def _json(value):
    return json.dumps(_json_value(value), sort_keys=True, ensure_ascii=False)


def plan_migration(records):
    planned, flagged, seen = [], [], set()
    for record in records:
        if (not isinstance(record.document_id, str) or not record.document_id
                or '/' in record.document_id or record.document_id in seen
                or not isinstance(record.data, dict)):
            raise ValueError(T.invalid_snapshot)
        seen.add(record.document_id)
        data = record.data
        version = data.get('metadata_schema_version')
        if version:
            flagged.append({'record_id': record.document_id,
                            'reason': 'existing_version_preserved' if version == METADATA_SCHEMA_VERSION else 'unknown_version_preserved'})
            continue
        # Never amend a partial manually populated target or existing archive.
        protected = [key for key in PERSISTED_FIELDS if data.get(key) not in (None, '', [], {})
                     and key != 'publication_events']
        if protected:
            flagged.append({'record_id': record.document_id, 'reason': 'existing_fields_preserved', 'fields': protected})
            continue
        legacy = legacy_publication(data, record_id=record.document_id)
        projected = publication_projection(data.get('publication_events') or [], legacy=legacy)
        updates = {key: deepcopy(projected[key]) for key in PERSISTED_FIELDS
                   if key in projected and (key not in data or data.get(key) in (None, '', [], {}))}
        # Existing publication_events, even unknown ones, are never rewritten.
        if updates:
            planned.append(PlannedUpdate(record, updates))
    return planned, flagged


def run_migration(records, *, execute=False, firestore_client=None,
                  checkpoint=None, source='offline', apply_update=None, log=print):
    records = list(records)
    planned, flags = plan_migration(records)
    # Budget is printed here too for programmatic callers, before any write.
    log(T.budget.format(expected_reads=0, read_cap=0,
                        write_cap=len(planned) if execute else 0))
    ledger = {'migration_version': MIGRATION_VERSION, 'source': source,
              'changes': [{'record_id': item.record.document_id,
                           'original_hash': record_hash(item.record.data),
                           'expected_update_time': item.record.update_time,
                           'diff': {key: {'before_present': key in item.record.data,
                                          'before': item.record.data.get(key), 'after': value}
                                    for key, value in item.updates.items()}}
                          for item in planned], 'flags': flags}
    log(_json(ledger))
    written = 0
    if execute:
        if checkpoint is None:
            raise ValueError(T.execute_source)
        checkpoint = Path(checkpoint)
        progress = (json.loads(checkpoint.read_text()) if checkpoint.exists() else
                    {'migration_version': MIGRATION_VERSION, 'source': source, 'completed': {}})
        if progress.get('migration_version') != MIGRATION_VERSION or progress.get('source') != source:
            raise ValueError(T.conflicting_source.format(record_id='checkpoint'))
        completed = progress.get('completed', {})
        for item in planned:
            prior = completed.get(item.record.document_id)
            if prior and prior.get('original_hash') != record_hash(item.record.data):
                raise ValueError(T.conflicting_source.format(record_id=item.record.document_id))
            if item.record.update_time is None:
                raise ValueError(T.missing_timestamp.format(record_id=item.record.document_id))
        for item in planned:
            if apply_update is not None:
                apply_update(item)
            else:
                from data_structures.book import Book
                # Stored ID is authoritative even for legacy title spelling.
                book = Book(db_object={**item.record.data, '_update_time': item.record.update_time})
                book._migration_document_id = item.record.document_id
                book.update_fields_conditionally(item.updates,
                    expected_update_time=item.record.update_time, firestore_client=firestore_client)
            written += 1
            completed[item.record.document_id] = {'original_hash': record_hash(item.record.data),
                                                   'applied_fields': item.updates}
            progress['completed'] = completed
            checkpoint.write_text(_json(progress), encoding='utf-8')
    return {'planned': len(planned), 'written': written, 'flags': flags, 'ledger': ledger}


def read_snapshot(path, *, max_books):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    rows = data.get('books', data) if isinstance(data, dict) else data
    if isinstance(rows, dict):
        records = [BookRecord(key, value) for key, value in rows.items()]
    elif isinstance(rows, list):
        records = [BookRecord(row.get('id'), row.get('data', {})) for row in rows]
    else:
        raise ValueError(T.invalid_snapshot)
    if len(records) > max_books:
        raise ValueError(T.overflow.format(limit=max_books))
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=T.migration_description)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--snapshot', type=Path, help=T.snapshot_help)
    sources.add_argument('--firestore', action='store_true', help=T.firestore_help)
    parser.add_argument('--execute', action='store_true', help=T.execute_help)
    parser.add_argument('--max-books', type=int, default=DEFAULT_MAX_BOOKS, help=T.limit_help)
    parser.add_argument('--checkpoint', type=Path, help=T.checkpoint_help)
    parser.add_argument('--secrets', default='.streamlit/secrets.toml', help=T.secrets_help)
    parser.add_argument('--project', default='sawdataentry', help=T.project_help)
    args = parser.parse_args(argv)
    if args.max_books < 1:
        parser.error(T.positive_limit)
    if args.execute and (not args.firestore or args.checkpoint is None):
        parser.error(T.execute_source)
    print(T.budget.format(expected_reads=min(DEFAULT_MAX_BOOKS, args.max_books) if args.firestore else 0,
                         read_cap=args.max_books + 1 if args.firestore else 0,
                         write_cap=args.max_books if args.firestore and args.execute else 0))
    db = None
    try:
        if args.firestore:
            from scripts.data_cleanup import build_firestore_client, load_secrets
            db = build_firestore_client(load_secrets(args.secrets), project=args.project)
            snapshots = list(db.collection('books').limit(args.max_books + 1).stream(retry=None))
            if len(snapshots) > args.max_books:
                raise ValueError(T.overflow.format(limit=args.max_books))
            records = [BookRecord(item.id, item.to_dict() or {}, item.update_time) for item in snapshots]
        else:
            records = read_snapshot(args.snapshot, max_books=args.max_books)
        run_migration(records, execute=args.execute, firestore_client=db, checkpoint=args.checkpoint,
                      source=f'firestore:{args.project}' if args.firestore else 'offline')
    except (GoogleAPIError, ValueError, TypeError, OSError, KeyError) as exc:
        print(T.failure.format(error=exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
