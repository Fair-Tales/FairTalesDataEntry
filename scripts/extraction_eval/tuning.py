"""One pre-decode ID gate for tune, rehearsal and proof, including cache replay."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from text_content.tuning import TuningErrors as E
from .store import atomic_json, digest, read_json

SELECTIONS_PATH = Path(__file__).with_name("selections.json")
DEFAULT_FREEZE_DIR = Path('/home/expert/Documents/uob/fair_tales/autonomy-freeze')
_active = ContextVar('tuning_loader', default=None)


class TuningRefused(RuntimeError):
    """A pre-load refusal, never an extraction failure or proof exposure."""


class TuningExposure(RuntimeError):
    """A recorded forbidden load invalidates untouched-proof status."""


def load_selections(path=SELECTIONS_PATH):
    # App prompt imports must not pull in the offline freeze machinery.
    from .freeze import load_selections as load
    return load(path)


def current_loader():
    return _active.get()


def _ids(value):
    if (not isinstance(value, list) or not value
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(E.inconsistent_freeze)
    return set(value)


class TuningLoader:
    def __init__(self, out, freeze_dir=None, *, cohort='tune', rehearsal_lock=None,
                 selections_path=None):
        if cohort not in {'tune', 'rehearsal', 'proof'}:
            raise ValueError(E.invalid_cohort)
        self.cohort = cohort
        self.path = Path(out) / 'run.json'
        self.selections_path = Path(selections_path or SELECTIONS_PATH)
        selections = load_selections(self.selections_path)
        self.iterate = set(selections['iterate55_ids'])
        self.check = set(selections['check20_ids'])
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
        if any(set(selections[key]) != expected for key, expected in (
                ('imported80_ids', self.holdout), ('protected40_ids', self.protected),
                ('tune41_ids', self.tune41))):
            raise ValueError(E.inconsistent_freeze)
        self.known = self.holdout | self.protected | self.iterate | self.check
        if not self.path.exists() and any((self.path.parent / name).exists() for name in (
                'eval.sqlite', 'predictions.json', 'dry_run_predictions.json', 'selection.json')):
            raise ValueError(E.missing_record)
        self.record = {'tuning': self.run_state(self.path)} if self.path.exists() else {}
        saved = self.record.get('tuning')
        if self.record and not saved:
            raise ValueError(E.missing_record)
        lock = rehearsal_lock if rehearsal_lock is not None else (saved or {}).get('rehearsal_lock')
        identity = {'cohort': cohort, 'freeze_sha256': digest(freeze),
                    'selections_sha256': digest(selections),
                    'holdout_ids_file_sha256': file_hash,
                    'imported80_ids_sha256': freeze['imported80_ids_sha256'],
                    'rehearsal_lock': lock}
        self.lock = lock
        self.allowed = ({'tune': self.iterate | self.check,
                         'rehearsal': self.protected, 'proof': self.holdout}[cohort]
                        | (self.protected if cohort == 'tune' and lock else set()))
        if saved:
            if any(saved.get(key) != value for key, value in identity.items()):
                raise ValueError(E.changed_run)
            if not all(isinstance(saved.get(key), list) for key in ('loaded_ids', 'refused_attempts')):
                raise ValueError(E.missing_record)
            self.state = saved
        else:
            self.state = {**identity, 'started_at': datetime.now(timezone.utc).isoformat(),
                          'freeze_dir': str(self.freeze_dir.resolve()),
                          'selections_path': str(self.selections_path.resolve()),
                          'loaded_ids': [], 'refused_attempts': [], 'breaches': []}
            self.save()
        if lock is not None:
            try:
                self._validate_lock(lock)
            except (OSError, ValueError, KeyError, TypeError):
                self.refuse(sorted(self.protected), 'rehearsal_lock', E.invalid_lock)
        self.audit()

    @staticmethod
    def run_state(path):
        """Read cohort identity without opening a previous run's book inventory."""
        from .json_sources import JsonSources
        with JsonSources(path) as source:
            top = source.members(source.root)
            return source.decode(top['tuning']) if 'tuning' in top else None

    def _selection_sources(self, source, span):
        fields = source.members(span)
        ids = source.decode(fields['book_ids']) if 'book_ids' in fields else []
        self.load(ids, 'selection', lambda: None)
        for key in ('selected', 'books', 'inventory', 'excluded', 'held_out_exclusions'):
            for row in source.items(fields[key]) if key in fields else []:
                item = source.members(row)
                work_id = source.decode(item['id']) if 'id' in item else None
                self.load([work_id], 'selection', lambda: None)

    def selection(self, path):
        """Saved inventories carry titles as well as their selected IDs."""
        from .json_sources import JsonSources
        with JsonSources(path) as source:
            self._selection_sources(source, source.root)
            return source.decode(source.root)

    def run_record(self, path):
        """Admit the contract's inventory before opening its book descriptions."""
        from .json_sources import JsonSources
        with JsonSources(path) as source:
            top = source.members(source.root)
            if 'contract' in top:
                contract = source.members(top['contract'])
                self._selection_sources(source, contract['selection'])
            return source.decode(source.root)

    def _validate_lock(self, lock):
        from .json_sources import JsonSources
        if (not isinstance(lock, dict)
                or not all(isinstance(lock.get(key), str) and lock[key].strip()
                           for key in ('run_record', 'run_id', 'locked_at'))
                or not all(isinstance(lock.get(key), str) and re.fullmatch(r'[0-9a-f]{64}', lock[key])
                           for key in ('results_sha256', 'configuration_sha256'))):
            raise ValueError(E.invalid_lock)
        locked_at = datetime.fromisoformat(lock['locked_at'].replace('Z', '+00:00'))
        if locked_at.tzinfo is None or locked_at > datetime.now(timezone.utc):
            raise ValueError(E.invalid_lock)
        path = Path(lock['run_record'])
        if not path.is_absolute() or path.resolve() == self.path.resolve():
            raise ValueError(E.invalid_lock)
        # The linked contract includes titles. Inspect only IDs and hashes.
        with JsonSources(path) as source:
            top = source.members(source.root)
            state = source.members(top['tuning'])
            contract = source.members(top['contract'])
            selection = source.members(contract['selection'])
            completion = source.decode(top['completion'])
            for key in ('freeze_sha256', 'selections_sha256', 'holdout_ids_file_sha256'):
                if source.decode(state[key]) != self.state[key]:
                    raise ValueError(E.invalid_lock)
            if (source.decode(state['cohort']) != 'rehearsal'
                    or source.decode(top['run_id']) != lock['run_id']
                    or set(source.decode(selection['book_ids'])) != self.protected
                    or source.decode(state['breaches'])
                    or set(completion['book_ids']) != self.protected
                    or completion['results_sha256'] != lock['results_sha256']
                    or completion['configuration_sha256'] != lock['configuration_sha256']
                    or source.sha256(top['contract']) != lock['configuration_sha256']):
                raise ValueError(E.invalid_lock)
            completed_at = datetime.fromisoformat(completion['completed_at'].replace('Z', '+00:00'))
            if completed_at.tzinfo is None or completed_at > locked_at:
                raise ValueError(E.invalid_lock)
        with JsonSources(path.with_name('predictions.json')) as source:
            top = source.members(source.root)
            if (source.sha256(source.root) != lock['results_sha256']
                    or source.decode(top['run_id']) != lock['run_id']
                    or source.decode(top['incomplete']) is not False
                    or source.decode(top['dry_run']) is not False
                    or set(source.members(top['books'])) != self.protected):
                raise ValueError(E.invalid_lock)

    def save(self):
        # Preserve contract inventories without opening their book content,
        # including when recording a refusal or a failed resume audit.
        if not self.path.exists():
            atomic_json(self.path, {**self.record, 'tuning': self.state})
            return
        from .json_sources import JsonSources
        with JsonSources(self.path) as source:
            top = source.members(source.root)
            if 'tuning' not in top:
                raise ValueError(E.missing_record)
            state = json.dumps(self.state, ensure_ascii=False, sort_keys=True,
                               indent=2, allow_nan=False).encode('utf-8')
            updated = source.replacing(top['tuning'], state)
        with tempfile.NamedTemporaryFile(mode='wb', dir=self.path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    @contextmanager
    def activate(self):
        token = _active.set(self)
        try:
            yield self
        finally:
            _active.reset(token)

    def reason(self, work_id, *, examples=False):
        if not isinstance(work_id, str) or work_id not in self.known:
            return E.missing_source
        allowed = self.iterate if examples else self.allowed
        if work_id not in allowed:
            return E.excluded.format(group='iterate examples' if examples else self.cohort)
        return None

    def resolve(self, source_ids, *, examples=False):
        if not isinstance(source_ids, (tuple, list)) or not source_ids:
            return [], E.missing_source
        if any(not isinstance(source, str) for source in source_ids):
            return [], E.missing_source
        resolved = sorted(set(source_ids))
        for work_id in resolved:
            reason = self.reason(work_id, examples=examples)
            if reason:
                return resolved, reason
        return resolved, None

    def load(self, source_ids, kind, callback):
        """Check before invoking any reader, including a cache hit.

        Persist admitted IDs before the read so interruption cannot hide a load.
        A reader that fails may therefore be conservatively counted as loaded.
        """
        examples = kind == 'prompt_example'
        resolved, reason = self.resolve(source_ids, examples=examples)
        if reason:
            self.refuse(source_ids, kind, reason, resolved)
        field = 'example_ids' if examples else 'loaded_ids'
        loaded = sorted(set(self.state.get(field, [])) | set(resolved))
        if loaded != self.state.get(field, []):
            self.state[field] = loaded
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
        saved = self.run_state(self.path) or {}
        if not isinstance(saved.get('loaded_ids'), list):
            raise ValueError(E.missing_record)
        self.state = saved
        if saved.get('breaches'):
            raise TuningExposure(E.breach.format(ids=[row['work_id'] for row in saved['breaches']]))
        breaches = []
        for field in ('loaded_ids', 'example_ids'):
            for work_id in saved.get(field, []):
                resolved, reason = self.resolve([work_id], examples=field == 'example_ids')
                if reason:
                    breaches.append({'work_id': work_id, 'resolved_ids': resolved,
                                     'reason': reason, 'proof_status': 'invalidated'})
        if breaches:
            self.state['breaches'] = breaches
            self.save()
            raise TuningExposure(E.breach.format(ids=[row['work_id'] for row in breaches]))
        self.state['last_audit_at'] = datetime.now(timezone.utc).isoformat()
        self.save()

    def snapshot(self, path, book_ids=None, *, additional_ids=None):
        """Read exact IDs first; titles, page metadata and references stay sealed.

        Frozen selections already identify the permitted representatives. Runtime
        title/image matching would open excluded works before admitting them.
        """
        from .json_sources import JsonSources
        with JsonSources(Path(path) / 'manifest.json') as source:
            top = source.members(source.root)
            spans = {}
            for span in source.items(top['books']):
                fields = source.members(span)
                work_id = source.decode(fields['id']) if 'id' in fields else None
                if not isinstance(work_id, str):
                    self.load([work_id], 'snapshot_source', lambda: None)
                if work_id in spans:
                    raise ValueError(E.invalid_source_file.format(path=path))
                spans[work_id] = span
            selected = (sorted(self.allowed & spans.keys()) if book_ids is None else list(book_ids))
            selected.extend(additional_ids or [])
            # Check the entire request before decoding even its first book.
            for work_id in selected:
                reason = self.reason(work_id)
                if reason or work_id not in spans:
                    self.refuse([work_id], 'snapshot_source', reason or E.selection_absent)
            manifest = {key: source.decode(top[key]) for key in (
                'version', 'snapshot_id', 'complete', 'source_consistent', 'audits',
                'created_at', 'completed_at', 'entered_by', 'reference_eligibility_version') if key in top}
            manifest['audit'] = []
            for span in source.items(top['audit']) if 'audit' in top else []:
                fields = source.members(span)
                manifest['audit'].append({key: source.decode(fields[key]) for key in (
                    'issue', 'book_id', 'book_ids') if key in fields})
            manifest['excluded_books'] = []
            for span in source.items(top['excluded_books']) if 'excluded_books' in top else []:
                fields = source.members(span)
                manifest['excluded_books'].append({key: source.decode(fields[key]) for key in (
                    'id', 'reason') if key in fields})
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
            records = []
            for span in source.items(top['examples']):
                fields = source.members(span)
                work_id = source.decode(fields['book_id']) if 'book_id' in fields else None
                self.load([work_id], 'prompt_example', lambda: None)
                records.append((work_id, span))
            examples = [self.load([work_id], 'prompt_example', lambda span=span: source.decode(span))
                        for work_id, span in records]
            return {**{key: source.decode(top[key]) for key in (
                'pack_version', 'development_book_ids') if key in top},
                'development_holdout_groups': sorted({row['holdout_group'] for row in examples
                                                     if row.get('holdout_group')}),
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
