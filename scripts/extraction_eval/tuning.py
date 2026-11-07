"""One source-aware load gate for offline tuning, including cache replay.

The current seal has a flat imported80 exclusion file and three further ID lists
in freeze.json. Resolve only these native IDs and the existing exposed fixtures;
unknown works fail closed, including pilot/unfinished/abandoned sources absent
from that seal. No draw or exclusion file is ever changed here.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from text_content.tuning import TuningErrors as E
from .exposure import EXPOSED_IDS
from .store import atomic_json, digest, read_json

DEFAULT_FREEZE_DIR = Path('/home/expert/Documents/uob/fair_tales/autonomy-freeze')
_active = ContextVar('tuning_loader', default=None)


class TuningRefused(RuntimeError):
    """A pre-load refusal, never an extraction failure or proof exposure."""


class TuningExposure(RuntimeError):
    """A recorded forbidden load invalidates untouched-proof status."""


def current_loader():
    return _active.get()


def _ids(value):
    if (not isinstance(value, list) or not value
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(E.inconsistent_freeze)
    return set(value)


class TuningLoader:
    def __init__(self, out, freeze_dir=None, *, paired_experiment_lock=None):
        self.path = Path(out) / 'run.json'
        self.freeze_dir = Path(freeze_dir or DEFAULT_FREEZE_DIR)
        try:
            freeze = read_json(self.freeze_dir / 'freeze.json')
            data = (self.freeze_dir / 'holdout_ids.json').read_bytes()
            if not isinstance(freeze, dict):
                raise ValueError(E.inconsistent_freeze)
            file_hash = hashlib.sha256(data).hexdigest()
            if file_hash != freeze.get('holdout_ids_file_sha256'):
                raise ValueError(E.changed_exclusions)
            self.holdout = _ids(json.loads(data))
            self.protected = _ids(freeze.get('protected40_ids'))
            self.tune41 = _ids(freeze.get('tune41_ids'))
            tune81 = _ids(freeze.get('tune81_ids'))
            if (self.holdout != _ids(freeze.get('imported80_ids'))
                    or digest(sorted(self.holdout)) != freeze.get('imported80_ids_sha256')
                    or self.holdout & (self.protected | self.tune41)
                    or self.protected & self.tune41
                    or tune81 != self.protected | self.tune41):
                raise ValueError(E.inconsistent_freeze)
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError(E.invalid_freeze.format(detail=exc)) from exc
        self.known = self.holdout | tune81 | EXPOSED_IDS
        self.equivalents = {}
        if not self.path.exists() and any((self.path.parent / name).exists() for name in (
                'eval.sqlite', 'predictions.json', 'dry_run_predictions.json', 'selection.json')):
            raise ValueError(E.missing_record)
        self.record = read_json(self.path) if self.path.exists() else {}
        saved = self.record.get('tuning')
        if self.record and not saved:
            raise ValueError(E.missing_record)
        lock = paired_experiment_lock if paired_experiment_lock is not None else (saved or {}).get('paired_experiment_lock')
        if lock is not None:
            if (not isinstance(lock, dict)
                    or not all(isinstance(lock.get(key), str) and lock[key].strip()
                               for key in ('run_id', 'locked_at'))
                    or not all(isinstance(lock.get(key), str) and re.fullmatch(r'[0-9a-f]{64}', lock[key])
                               for key in ('results_sha256', 'configuration_sha256'))):
                raise ValueError(E.invalid_lock)
            try:
                locked_at = datetime.fromisoformat(lock['locked_at'].replace('Z', '+00:00'))
                if locked_at.tzinfo is None or locked_at > datetime.now(timezone.utc):
                    raise ValueError(E.invalid_lock)
            except ValueError as exc:
                raise ValueError(E.invalid_lock) from exc
        self.lock = lock
        identity = {'freeze_sha256': digest(freeze),
                    'holdout_ids_file_sha256': file_hash,
                    'imported80_ids_sha256': freeze['imported80_ids_sha256'],
                    'paired_experiment_lock': lock}
        if saved:
            if any(saved.get(key) != value for key, value in identity.items()):
                raise ValueError(E.changed_run)
            if not all(isinstance(saved.get(key), list) for key in ('loaded_ids', 'refused_attempts')):
                raise ValueError(E.missing_record)
            self.state = saved
            self.equivalents = saved.get('equivalent_ids', {})
        else:
            self.state = {**identity, 'started_at': datetime.now(timezone.utc).isoformat(),
                          'freeze_dir': str(self.freeze_dir.resolve()),
                          'loaded_ids': [], 'refused_attempts': [], 'breaches': []}
            self.save()
        self.audit()

    def save(self):
        # freeze_run adds the ordinary run contract after preflight. Retain it.
        record = read_json(self.path) if self.path.exists() else self.record
        record['tuning'] = self.state
        atomic_json(self.path, record)

    @contextmanager
    def activate(self):
        token = _active.set(self)
        try:
            yield self
        finally:
            _active.reset(token)

    def reason(self, work_id):
        if not isinstance(work_id, str) or work_id not in self.known:
            return E.missing_source
        if work_id in self.holdout:
            return E.excluded.format(group='imported80')
        if work_id in self.protected and self.lock is None:
            return E.excluded.format(group='protected40')
        return None

    def resolve(self, source_ids):
        if not isinstance(source_ids, (tuple, list)) or not source_ids:
            return [], E.missing_source
        resolved = set()
        for source in source_ids:
            if not isinstance(source, str):
                return [], E.missing_source
            resolved.add(source)
            resolved.update(self.equivalents.get(source, []))
        for work_id in sorted(resolved):
            reason = self.reason(work_id)
            if reason:
                return sorted(resolved), reason
        return sorted(resolved), None

    def load(self, source_ids, kind, callback):
        """Check before invoking any reader, including a cache hit.

        Persist admitted IDs before the read so interruption cannot hide a load.
        A reader that fails may therefore be conservatively counted as loaded.
        """
        resolved, reason = self.resolve(source_ids)
        if reason:
            self.refuse(source_ids, kind, reason, resolved)
        loaded = sorted(set(self.state['loaded_ids']) | set(resolved))
        if loaded != self.state['loaded_ids']:
            self.state['loaded_ids'] = loaded
            self.save()
        return callback()

    def refuse(self, source_ids, kind, reason, resolved=()):
        self.state['refused_attempts'].append({
            'source_ids': source_ids, 'resolved_ids': list(resolved), 'kind': kind,
            'reason': reason, 'at': datetime.now(timezone.utc).isoformat()})
        self.save()
        raise TuningRefused(E.refused.format(source_ids=source_ids, reason=reason))

    def audit(self):
        """Compare the saved load record, also callable before final policy lock."""
        if not self.path.exists():
            raise ValueError(E.missing_record)
        saved = read_json(self.path).get('tuning', {})
        if not isinstance(saved.get('loaded_ids'), list):
            raise ValueError(E.missing_record)
        self.state = saved
        if saved.get('breaches'):
            raise TuningExposure(E.breach.format(ids=[row['work_id'] for row in saved['breaches']]))
        breaches = []
        for work_id in saved['loaded_ids']:
            resolved, reason = self.resolve([work_id])
            if reason:
                breaches.append({'work_id': work_id, 'equivalent_ids': resolved,
                                 'reason': reason, 'proof_status': 'invalidated'})
        if breaches:
            self.state['breaches'] = breaches
            self.save()
            raise TuningExposure(E.breach.format(ids=[row['work_id'] for row in breaches]))
        self.state['last_audit_at'] = datetime.now(timezone.utc).isoformat()
        self.save()

    def snapshot(self, path, book_ids=None, *, additional_ids=None):
        """Read identity metadata first; decode only permitted book records.

        The legacy raw_documents map duplicates every book's text/labels. It is
        deliberately not included in the tuning view; per-book references are.
        """
        from .json_sources import JsonSources
        from .sampling import _holdout_groups, _resolve
        with JsonSources(Path(path) / 'manifest.json') as source:
            top = source.members(source.root)
            spans, metadata = {}, []
            for span in source.items(top['books']):
                fields = source.members(span)
                work_id = source.decode(fields['id']) if 'id' in fields else None
                if not isinstance(work_id, str):
                    self.load([work_id], 'snapshot_source', lambda: None)
                if work_id in spans:
                    raise ValueError(E.invalid_source_file.format(path=path))
                spans[work_id] = span
                row = {'id': work_id}
                for key in ('title', 'holdout_group'):
                    if key in fields:
                        row[key] = source.decode(fields[key])
                row['pages'] = []
                for page in source.items(fields['pages']) if 'pages' in fields else []:
                    page_fields = source.members(page)
                    image = source.decode(page_fields['image']) if 'image' in page_fields else None
                    row['pages'].append({'image': image})
                metadata.append(row)
            groups = _holdout_groups([{'id': row['id'], 'title': row.get('title')} for row in metadata],
                                     {'books': metadata})
            for group in groups:
                for work_id in group['book_ids']:
                    self.equivalents[work_id] = group['book_ids']
            self.state['equivalent_ids'] = self.equivalents
            self.save()
            # An explicit unresolved ID must leave a refusal in the run record.
            selected = [] if book_ids else sorted(
                (self.tune41 | (self.protected if self.lock else set())) & spans.keys())
            if book_ids or additional_ids:
                for value in (book_ids or []) + (additional_ids or []):
                    try:
                        selected.extend(_resolve([value], metadata))
                    except ValueError:
                        self.refuse([value], 'snapshot_source', E.missing_source)
            manifest = {key: source.decode(top[key]) for key in (
                'version', 'snapshot_id', 'complete', 'source_consistent', 'audits',
                'created_at', 'completed_at', 'entered_by', 'reference_eligibility_version',
                'excluded_books') if key in top}
            # Audit findings can embed whole invalid page documents. Retain only
            # identity/structural fields, including the existing folder guard.
            manifest['audit'] = []
            for span in source.items(top['audit']) if 'audit' in top else []:
                fields = source.members(span)
                manifest['audit'].append({key: source.decode(fields[key]) for key in (
                    'issue', 'book_id', 'book_ids', 'folder', 'path', 'paths', 'sha256') if key in fields})
            manifest['books'] = [self.load([work_id], 'snapshot', lambda work_id=work_id: source.decode(spans[work_id]))
                                 for work_id in sorted(set(selected))]
            self.state['snapshot_book_ids'] = sorted(set(selected))
            self.save()
            return manifest

    def predictions(self, path):
        from .json_sources import JsonSources
        with JsonSources(path) as source:
            top = source.members(source.root)
            books = source.members(top['books'])
            # Every cached source is checked before any prediction is decoded.
            return self.load(list(books), 'predictions', lambda: source.decode(source.root))

    def examples(self, path):
        from .json_sources import JsonSources
        with JsonSources(path) as source:
            top = source.members(source.root)
            examples = []
            for span in source.items(top['examples']):
                fields = source.members(span)
                work_id = source.decode(fields['book_id']) if 'book_id' in fields else None
                examples.append(self.load([work_id], 'prompt_example', lambda span=span: source.decode(span)))
            return {**{key: source.decode(top[key]) for key in (
                'pack_version', 'development_book_ids', 'development_holdout_groups') if key in top},
                'examples': examples}

    def source_json(self, path, kind, *, snapshot_id=None):
        """Other inputs declare source_work_ids or records with native book_id."""
        from .json_sources import JsonSources
        with JsonSources(path) as source:
            top = source.members(source.root)
            if snapshot_id is not None and ('snapshot_id' not in top
                    or source.decode(top['snapshot_id']) != snapshot_id):
                return None
            ids = source.decode(top['source_work_ids']) if 'source_work_ids' in top else []
            if not isinstance(ids, list):
                return self.load(ids, kind, lambda: source.decode(source.root))
            if 'records' in top:
                for span in source.items(top['records']):
                    fields = source.members(span)
                    ids.append(source.decode(fields['book_id']) if 'book_id' in fields else None)
            return self.load(ids, kind, lambda: source.decode(source.root))
