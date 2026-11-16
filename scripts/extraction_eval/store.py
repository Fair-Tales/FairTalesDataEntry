"""Atomic local artifacts and a transactional, shared spend/request ledger."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from ai_pricing import usage_provider
from .openai_adapter import response_content
from text_content.pipeline_runtime import BUDGET_REFUSED, CYCLE_REQUIRED, CYCLE_DAY_IMMUTABLE


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def money(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Spend amounts must be finite and nonnegative")
    return int((amount * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))


class BudgetStopped(RuntimeError):
    pass


class OutcomeUnknown(RuntimeError):
    pass


class CachedFailure(RuntimeError):
    pass


@contextmanager
def run_lock(directory):
    """Prevent a concurrent runner from adopting another process's dispatch."""
    import fcntl
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("This run is already active in another process") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Ledger:
    """Reservations and responses live in the SAME SQLite transaction boundary.

    Currency uses integer nano-dollars. Unknown outcomes retain their reservation.
    All evaluator runs must use the same ledger for provider cycle caps to apply.
    """
    def __init__(self, path, run_id, cap, *, budgets=None, clock=None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.run_id = run_id
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.budgets = budgets or {}
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, cap INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS calls(
          key TEXT PRIMARY KEY, run_id TEXT, month TEXT, state TEXT,
          reservation INTEGER, cost INTEGER DEFAULT 0, request TEXT,
          response TEXT, error TEXT, label TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS billing_providers(
          provider TEXT PRIMARY KEY, start_day INTEGER NOT NULL, cap INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS billing_cycles(
          provider TEXT, start TEXT, cap INTEGER NOT NULL, PRIMARY KEY(provider,start));
        CREATE TABLE IF NOT EXISTS retry_events(
          key TEXT PRIMARY KEY, run_id TEXT NOT NULL, created TEXT NOT NULL);
        """)
        try:
            with self.transaction():
                if 'provider' not in {row['name'] for row in self.db.execute('PRAGMA table_info(calls)')}:
                    self.db.execute('ALTER TABLE calls ADD COLUMN provider TEXT')
                    # SQLite extracts only the provider tag, never returning book content.
                    self.db.execute("""UPDATE calls SET provider = CASE
                      WHEN COALESCE(json_extract(request, '$.provider'), 'anthropic-messages')
                        IN ('anthropic', 'anthropic-messages') THEN 'anthropic'
                      WHEN json_extract(request, '$.provider')
                        IN ('openai', 'openai-responses', 'openai-chat-completions') THEN 'openai'
                      ELSE 'unsupported' END""")
                amount = money(cap)
                row = self.db.execute('SELECT cap FROM runs WHERE id=?', (run_id,)).fetchone()
                if row and row['cap'] != amount:
                    raise ValueError('The existing runs spend cap is immutable')
                self.db.execute('INSERT OR IGNORE INTO runs(id,cap) VALUES (?,?)', (run_id, amount))
                for provider, policy in self.budgets.items():
                    row = self.db.execute('SELECT start_day FROM billing_providers WHERE provider=?', (provider,)).fetchone()
                    if row and row['start_day'] != policy['start_day']:
                        raise ValueError(CYCLE_DAY_IMMUTABLE.format(provider=provider))
                    self.db.execute("""INSERT INTO billing_providers VALUES (?,?,?)
                      ON CONFLICT(provider) DO UPDATE SET cap=excluded.cap""",
                                    (provider, policy['start_day'], money(policy['cap_usd'])))
                    self._cycle_cap(provider, self.clock())
        except BaseException:
            self.db.close()
            raise

    def _cycle_cap(self, provider, now):
        from .billing import cycle_bounds
        policy = self.budgets.get(provider)
        if policy is None:
            raise ValueError(CYCLE_REQUIRED.format(provider=provider))
        start, end = cycle_bounds(policy['start_day'], now)
        # A later run cannot restore headroom removed by an earlier lower cap.
        self.db.execute("""INSERT INTO billing_cycles VALUES (?,?,?)
          ON CONFLICT(provider,start) DO UPDATE SET cap=MIN(cap,excluded.cap)""",
                        (provider, start.isoformat(), money(policy['effective_cap_usd'])))
        cap = self.db.execute('SELECT cap FROM billing_cycles WHERE provider=? AND start=?',
                              (provider, start.isoformat())).fetchone()['cap']
        return start, end, cap

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def close(self):
        self.db.close()

    def recover(self):
        with self.transaction():
            self.db.execute("UPDATE calls SET state='outcome_unknown', error='Interrupted after reservation; reconcile manually' WHERE run_id=? AND state='dispatched'", (self.run_id,))

    def get(self, key):
        row = self.db.execute("SELECT * FROM calls WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def retry_authorized(self, key):
        return self.db.execute('SELECT 1 FROM retry_events WHERE key=?', (key,)).fetchone() is not None

    def _last_calls(self):
        # Context identifies the stage, not the paid request. Group inside SQLite
        # so recovery never loads book content, even for a mixed-cohort ledger.
        return self.db.execute("""SELECT key,run_id,state,error,cost,
          json_extract(request, '$.context.stage') AS stage FROM calls
          WHERE rowid IN (SELECT MAX(rowid) FROM calls
            GROUP BY json_remove(request, '$.context'))""")

    def retry_failed(self):
        """Authorize fresh attempt keys; preserve every response and reservation."""
        with self.transaction():
            created = self.clock().isoformat()
            keys = []
            for row in self._last_calls():
                if row['run_id'] != self.run_id or row['cost'] or self.retry_authorized(row['key']):
                    continue
                if (row['state'] == 'retryable' or row['state'] == 'failed'
                        and (row['error'] or '').startswith('billing_stopped:')):
                    self.db.execute('INSERT INTO retry_events VALUES (?,?,?)',
                                    (row['key'], self.run_id, created))
                    keys.append(row['key'])
            unknown = [row[0] for row in self.db.execute("""SELECT key FROM calls
              WHERE run_id=? AND state IN ('dispatched','outcome_unknown') ORDER BY key""", (self.run_id,))]
        return {'keys': keys, 'unknown': unknown, 'created': created}

    def stage_retry_authorized(self, stage):
        return any(row['stage'] == stage and self.retry_authorized(row['key'])
                   for row in self._last_calls())

    def reserve(self, key, request, ceiling):
        from .billing import billing_provider
        reservation = money(ceiling)
        provider = billing_provider(request.get('provider', 'anthropic-messages'))
        with self.transaction():
            now = self.clock().astimezone(timezone.utc)
            if self.get(key):
                raise OutcomeUnknown("Request already exists; reload its persisted state")
            start, end, cycle_cap = self._cycle_cap(provider, now)
            settled, outstanding = cycle_spend(self.db, provider, start, end)
            run_cap = self.db.execute('SELECT cap FROM runs WHERE id=?', (self.run_id,)).fetchone()['cap']
            run_used = self.db.execute('SELECT COALESCE(SUM(cost+reservation),0) FROM calls WHERE run_id=?',
                                       (self.run_id,)).fetchone()[0]
            for scope, cap, used in ((provider, cycle_cap, settled + outstanding), ('run', run_cap, run_used)):
                if used + reservation > cap:
                    raise BudgetStopped(BUDGET_REFUSED.format(scope=scope, cap=cap/1e9,
                        reservation=reservation/1e9, headroom=(cap-used)/1e9, used=used/1e9))
            self.db.execute("""INSERT INTO calls(key,run_id,month,provider,state,reservation,request,created)
              VALUES (?,?,?,?,'dispatched',?,?,?)""",
                            (key, self.run_id, now.strftime('%Y-%m'), provider,
                             reservation, canonical(request), now.isoformat()))

    def capture_response(self, key, response):
        """Keep the reservation until usage is settled; crashes stay unknown."""
        with self.transaction():
            self.db.execute("UPDATE calls SET response=? WHERE key=?", (canonical(response), key))

    def settle(self, key, response, cost):
        with self.transaction():
            row = self.get(key)
            if row is None:
                raise ValueError("Cannot settle an unreserved request")
            charge = money(cost)
            if charge > row["reservation"]:
                self.db.execute("UPDATE calls SET state='outcome_unknown',response=?,cost=?,reservation=0,error='Usage exceeded pinned ceiling' WHERE key=?", (canonical(response), charge, key))
            else:
                self.db.execute("UPDATE calls SET state='completed',response=?,cost=?,reservation=0 WHERE key=?", (canonical(response),charge,key))
        if charge > row["reservation"]:
            raise OutcomeUnknown("Returned usage exceeded the price ceiling; reconcile ledger before continuing")

    def fail(self, key, error, *, rejected=False, response=None, retryable=False):
        state = "retryable" if rejected and retryable else "failed" if rejected else "outcome_unknown"
        with self.transaction():
            self.db.execute("UPDATE calls SET state=?,error=?,response=? WHERE key=?", (state,str(error),canonical(response) if response is not None else None,key))

    def annotate(self, key, label):
        with self.transaction():
            self.db.execute("UPDATE calls SET label=? WHERE key=?", (label,key))

    def rows(self):
        return call_rows(self.db, self.run_id)


def cycle_spend(db, provider, start, end):
    """Sum every attempt, regardless of outcome, by its reservation timestamp."""
    return db.execute("""SELECT COALESCE(SUM(cost),0), COALESCE(SUM(reservation),0)
      FROM calls WHERE provider=? AND created>=? AND created<?""",
                      (provider, start.isoformat(), end.isoformat())).fetchone()


def read_headroom(path, *, now=None):
    """Read only accounting columns. Never migrate or decode request/response bodies."""
    from .billing import cycle_bounds
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')  # One consistent view while another run reserves.
        now = now or datetime.now(timezone.utc)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = []
        for provider in ('anthropic', 'openai'):
            policy = (db.execute('SELECT * FROM billing_providers WHERE provider=?', (provider,)).fetchone()
                      if 'billing_providers' in tables else None)
            if policy is None:
                rows.append({'provider': provider, 'configured': False})
                continue
            start, end = cycle_bounds(policy['start_day'], now)
            cycle = db.execute('SELECT cap FROM billing_cycles WHERE provider=? AND start=?',
                               (provider, start.isoformat())).fetchone()
            cap = cycle['cap'] if cycle else policy['cap']
            settled, outstanding = cycle_spend(db, provider, start, end)
            rows.append(dict(provider=provider, configured=True,
                             start=start.date().isoformat(), end=end.date().isoformat(),
                             cap=cap/1e9, settled=settled/1e9, outstanding=outstanding/1e9,
                             headroom=(cap-settled-outstanding)/1e9))
        return rows
    finally:
        db.close()


def read_calls(path, run_id):
    """Read current accounting for offline reports, including interrupted runs."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        return call_rows(db, run_id)
    finally:
        db.close()


def guard_cached_request(request, kind="response_cache"):
    """Admit a stored request's source before decoding its content or response."""
    from .tuning import current_loader
    loader = current_loader()
    if loader is None:
        return  # Low-level accounting use outside an evaluation run.
    from .json_sources import JsonSources
    with JsonSources.from_bytes((request or "{}").encode("utf-8")) as source:
        top = source.members(source.root)
        context = source.members(top["context"]) if "context" in top else {}
        book_id = source.decode(context["book_id"]) if "book_id" in context else None
        loader.load([book_id], kind, lambda: None)


def call_rows(db, run_id):
    rows = []
    for row in db.execute("SELECT key,run_id,month,state,reservation/1e9 AS reserved_usd,cost/1e9 AS cost_usd,label,error,created,request,response FROM calls WHERE run_id=? ORDER BY created,key", (run_id,)):
        result = dict(row)
        guard_cached_request(result["request"], "call_report")
        request = json.loads(result.pop("request") or "{}")
        response = json.loads(result.pop("response") or "{}")
        stop_reason = response.get("stop_reason")
        provider = request.get("provider", "anthropic-messages")
        if usage_provider(provider=provider) == "openai" and "usage" in response:
            try:
                _, stop_reason = response_content(response, provider)
            except (ValueError, TypeError):
                stop_reason = "invalid_response"
        result.update(model=request.get("request", {}).get("model"),
                      usage=response.get("usage"), request_id=response.get("_request_id") or response.get("id"),
                      stop_reason=stop_reason)
        result.update(request.get("context", {}))
        result['served_model'] = response.get('model')
        # Old rows have no settings sidecar. Preserve that absence: interpreting
        # historical provider defaults using today's table would invent evidence.
        result.setdefault('requested_settings', None)
        rows.append(result)
    return rows
