"""Offline admission/accounting; preserved evidence is tested only on a copy."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

from scripts.extraction_eval.billing import cycle_budgets, cycle_bounds
from scripts.extraction_eval.cli import main
from scripts.extraction_eval.evidence import evidence_dir
from scripts.extraction_eval.gateway import Gateway
from scripts.extraction_eval.store import BudgetStopped, Ledger, read_headroom


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def config(day=1):
    return {'billing_cycles': {'anthropic': {'start_day': day}, 'openai': {'start_day': 10}}}


@pytest.fixture
def accounting(tmp_path):
    ledgers = []
    now = [utc('2026-11-16')]
    path = tmp_path / 'spend.sqlite'

    def create(configuration=None, cap_override=None, run='run'):
        ledger = Ledger(path, run, 1000, budgets=cycle_budgets(configuration or config(), cap_override),
                        clock=lambda: now[0])
        ledgers.append(ledger)
        return ledger

    yield path, now, create
    for ledger in ledgers:
        ledger.close()


def test_providers_and_endpoint_aliases_have_separate_headroom(accounting):
    path, now, create = accounting
    ledger = create()
    ledger.reserve('anthropic', {'provider': 'anthropic-messages'}, 150)
    ledger.reserve('openai', {'provider': 'openai-responses'}, 200)
    for provider in ('anthropic', 'anthropic-messages', 'openai', 'openai-chat-completions'):
        with pytest.raises(BudgetStopped, match=r'headroom \$0.000000000'):
            ledger.reserve('excess', {'provider': provider}, Decimal('.000000001'))
    rows = read_headroom(path, now=now[0])
    assert [(row['cap'], row['outstanding'], row['headroom']) for row in rows] == [(150, 150, 0), (200, 200, 0)]
    assert [row['start'] for row in rows] == ['2026-11-01', '2026-11-10']


@pytest.mark.parametrize('day,before,after,next_end', [
    (15, '2026-11-14T23:59:59', '2026-11-15', '2026-12-15'),
    (29, '2026-02-27T23:59:59', '2026-02-28', '2026-03-29'),
    (30, '2026-02-27T23:59:59', '2026-02-28', '2026-03-30'),
    (31, '2026-02-27T23:59:59', '2026-02-28', '2026-03-31'),
    (31, '2028-02-28T23:59:59', '2028-02-29', '2028-03-31'),
    (31, '2026-04-29T23:59:59', '2026-04-30', '2026-05-31'),
    (1, '2026-12-31T23:59:59', '2027-01-01', '2027-02-01'),
])
def test_cycle_reset_during_running_process(accounting, day, before, after, next_end):
    path, now, create = accounting
    now[0] = utc(before)
    ledger = create(config(day), 1)
    ledger.reserve('old', {}, 1)
    with pytest.raises(BudgetStopped):
        ledger.reserve('blocked', {}, .01)
    now[0] = utc(after)
    ledger.reserve('new', {}, 1)
    # Settlement after renewal still belongs to the reservation's original cycle.
    ledger.settle('old', {}, .4)
    current = read_headroom(path, now=now[0])[0]
    assert (current['start'], current['end']) == (after, next_end)
    assert (current['settled'], current['outstanding'], current['headroom']) == (0, 1, 0)
    previous = read_headroom(path, now=utc(before))[0]
    assert (previous['settled'], previous['outstanding'], previous['headroom']) == (.4, 0, .6)


def test_calendar_month_does_not_reset_noncalendar_cycle(accounting):
    path, now, create = accounting
    now[0] = utc('2026-01-31')
    ledger = create(config(31), 1)
    ledger.reserve('old', {}, 1)
    now[0] = utc('2026-02-01')
    with pytest.raises(BudgetStopped):
        ledger.reserve('new', {}, .01)
    assert cycle_bounds(31, now[0]) == (utc('2026-01-31'), utc('2026-02-28'))


def test_failed_retried_unknown_and_settled_attempts_all_count(accounting):
    path, now, create = accounting
    ledger = create(cap_override=1)
    for key in ('failed', 'retry', 'unknown', 'settled'):
        ledger.reserve(key, {}, .25)
    ledger.fail('failed', 'failure', rejected=True)
    ledger.fail('retry', 'rate limit', rejected=True, retryable=True)
    ledger.fail('unknown', 'timeout')
    ledger.settle('settled', {}, .1)
    row = read_headroom(path, now=now[0])[0]
    assert (row['settled'], row['outstanding'], row['headroom']) == (.1, .75, .15)
    # A second connection/run sees all attempts in the same admission transaction.
    other = create(cap_override=1, run='other')
    with pytest.raises(BudgetStopped, match=r'headroom \$0.150000000'):
        other.reserve('excess', {}, .16)
    assert other.get('excess') is None
    other.reserve('fits', {}, .15)


def test_lower_cap_survives_later_default_run_but_expires_next_cycle(accounting):
    path, now, create = accounting
    create(cap_override=20)
    create(run='later')
    assert read_headroom(path, now=now[0])[0]['cap'] == 20
    now[0] = utc('2026-12-01')
    assert read_headroom(path, now=now[0])[0]['cap'] == 150
    with pytest.raises(ValueError, match='immutable'):
        create(config(2), run='changed-day')


def test_option_can_only_lower_configured_maximum():
    assert cycle_budgets(config())['anthropic']['cap_usd'] == 150
    assert cycle_budgets(config())['openai']['cap_usd'] == 200
    with pytest.raises(ValueError, match='cannot exceed'):
        cycle_budgets(config(), 151)
    changed = config()
    changed['billing_cycles']['anthropic']['cap_usd'] = 160
    assert cycle_budgets(changed, 155)['anthropic']['effective_cap_usd'] == 155


@pytest.mark.parametrize('day', [None, 0, 32, True, 1.5, '1'])
def test_paid_gateway_refuses_missing_or_invalid_cycle_start(day, tmp_path):
    configuration = config(day)
    ledger = Ledger(tmp_path / 'spend.sqlite', 'run', 10)
    try:
        with pytest.raises(ValueError, match='start_day'):
            Gateway(object(), ledger, configuration)
        # Direct admission cannot bypass the required configured cycle either.
        with pytest.raises(ValueError, match='start_day'):
            ledger.reserve('no-cycle', {}, 1)
    finally:
        ledger.close()


def test_over_cap_retry_is_refused_before_second_dispatch(accounting):
    _, _, create = accounting
    configuration = config()
    configuration.update(max_attempts=2, pricing={'models': {'fake': {
        'input': 1, 'output': 1, 'cache_read': 1, 'cache_write': 1,
        'max_input_tokens': 100, 'max_output_tokens': 10,
        'max_images': 1, 'max_request_bytes': 1000}}})
    calls = []

    class RateLimit(RuntimeError):
        status_code = 429

    def dispatch(**kwargs):
        calls.append(kwargs)
        raise RateLimit('synthetic rate limit')

    ledger = create(configuration, .00011)
    gateway = Gateway(SimpleNamespace(messages=SimpleNamespace(create=dispatch)), ledger, configuration)
    with pytest.raises(BudgetStopped, match=r'cap \$0.000110000.*\$0.000110000 reservation.*headroom \$0.000000000'):
        gateway.create(model='fake', max_tokens=10, messages=[])
    assert len(calls) == 1
    assert ledger.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 1


def test_headroom_command_is_read_only(accounting, capsys):
    path, now, create = accounting
    ledger = create()
    ledger.reserve('settled', {}, 1)
    ledger.settle('settled', {}, .25)
    # Force the command's clock to the test time without touching the database.
    from unittest.mock import patch
    with patch('scripts.extraction_eval.store.read_headroom', side_effect=lambda p: read_headroom(p, now=now[0])):
        before = ledger.db.total_changes
        assert main(['headroom', '--ledger', str(path)]) == 0
        assert ledger.db.total_changes == before
    output = capsys.readouterr().out
    assert 'anthropic 2026-11-01 to 2026-12-01' in output
    assert 'settled $0.250000000; outstanding $0.000000000; headroom $149.750000000' in output
    assert 'openai 2026-11-10 to 2026-12-10' in output


def test_preserved_ledger_copy_reports_exact_spend():
    source = evidence_dir() / 'spend.sqlite'
    if not source.exists():
        pytest.skip('Preserved evidence is not available on this machine')
    cache = Path(__file__).resolve().parents[1] / '.cache'
    cache.mkdir(exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix='a1b-ledger-', dir=cache)) / 'spend.sqlite'
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    shutil.copy2(source, destination)
    wal = source.with_name(source.name + '-wal')
    if wal.exists():
        shutil.copy2(wal, destination.with_name(destination.name + '-wal'))
    # Synthetic start days, solely for this compatibility check, NOT account settings.
    ledger = Ledger(destination, 'a1b-offline-check', 0, budgets=cycle_budgets(config()),
                    clock=lambda: utc('2026-11-16'))
    try:
        count, spend = ledger.db.execute("SELECT count(*), sum(cost) FROM calls WHERE provider='anthropic'").fetchone()
        assert count == 1669
        assert spend == 15_045_192_000
        row = read_headroom(destination, now=utc('2026-11-16'))[0]
        assert row['settled'] == 15.045192
        assert row['outstanding'] == 0
    finally:
        ledger.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    # A report neither mutates nor migrates the migrated copy.
    before_report = destination.read_bytes()
    assert main(['headroom', '--ledger', str(destination)]) == 0
    assert destination.read_bytes() == before_report


def test_legacy_report_does_not_silently_assume_calendar_cycle(tmp_path):
    path = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE months(id TEXT PRIMARY KEY, cap INTEGER)')
    before = path.read_bytes()
    assert all(not row['configured'] for row in read_headroom(path))
    assert path.read_bytes() == before
