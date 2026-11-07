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
    All evaluator runs must use the same ledger for the monthly cap to apply.
    """
    def __init__(self, path, run_id, cap, monthly_cap=200, month=None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.run_id = run_id
        self.month = month or datetime.now(timezone.utc).strftime("%Y-%m")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, cap INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS months(id TEXT PRIMARY KEY, cap INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS calls(
          key TEXT PRIMARY KEY, run_id TEXT, month TEXT, state TEXT,
          reservation INTEGER, cost INTEGER DEFAULT 0, request TEXT,
          response TEXT, error TEXT, label TEXT, created TEXT);
        """)
        with self.transaction():
            for table, identifier, amount in (("runs", run_id, money(cap)), ("months", self.month, money(monthly_cap))):
                row = self.db.execute(f"SELECT cap FROM {table} WHERE id=?", (identifier,)).fetchone()
                if row and row["cap"] != amount:
                    raise ValueError(f"The existing {table} spend cap is immutable")
                self.db.execute(f"INSERT OR IGNORE INTO {table}(id,cap) VALUES (?,?)", (identifier, amount))

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

    def reserve(self, key, request, ceiling):
        reservation = money(ceiling)
        with self.transaction():
            if self.get(key):
                raise OutcomeUnknown("Request already exists; reload its persisted state")
            for table, field, identifier in (("runs", "run_id", self.run_id), ("months", "month", self.month)):
                cap = self.db.execute(f"SELECT cap FROM {table} WHERE id=?", (identifier,)).fetchone()["cap"]
                used = self.db.execute(f"SELECT COALESCE(SUM(cost+reservation),0) FROM calls WHERE {field}=?", (identifier,)).fetchone()[0]
                if used + reservation > cap:
                    raise BudgetStopped(f"{table} cap cannot admit ${float(ceiling):.4f} reservation; remaining ${(cap-used)/1e9:.4f}")
            self.db.execute("INSERT INTO calls(key,run_id,month,state,reservation,request,created) VALUES (?,?,?,'dispatched',?,?,?)", (key,self.run_id,self.month,reservation,canonical(request),datetime.now(timezone.utc).isoformat()))

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
            self.db.execute("UPDATE calls SET state=?,error=?,reservation=CASE WHEN ? THEN 0 ELSE reservation END,response=? WHERE key=?", (state,str(error),rejected,canonical(response) if response is not None else None,key))

    def annotate(self, key, label):
        with self.transaction():
            self.db.execute("UPDATE calls SET label=? WHERE key=?", (label,key))

    def rows(self):
        return call_rows(self.db, self.run_id)


def read_calls(path, run_id):
    """Read current accounting for offline reports, including interrupted runs."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        return call_rows(db, run_id)
    finally:
        db.close()


def call_rows(db, run_id):
    rows = []
    for row in db.execute("SELECT key,run_id,month,state,reservation/1e9 AS reserved_usd,cost/1e9 AS cost_usd,label,error,created,request,response FROM calls WHERE run_id=? ORDER BY created,key", (run_id,)):
        result = dict(row)
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
        rows.append(result)
    return rows
