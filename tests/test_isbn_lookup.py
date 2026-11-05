"""ISBN network outcomes and enrichment, using only fake provider responses."""

import io
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

import utilities


ISBN = '9780306406157'
FOUND = {'totalItems': 1, 'items': [{'volumeInfo': {
    'title': 'Lookup title', 'authors': ['Writer'], 'publisher': 'Publisher',
}}]}


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


@pytest.fixture
def lookup_fakes(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(utilities, 'time', clock)
    monkeypatch.setattr(utilities.random, 'uniform', lambda low, high: 0.1)
    monkeypatch.setattr(utilities.st, 'secrets', {})
    calls = []

    def install(*responses):
        pending = iter(responses)

        def urlopen(url, timeout):
            calls.append((url, timeout))
            response = next(pending)
            if isinstance(response, BaseException):
                raise response
            return io.BytesIO(response if isinstance(response, bytes)
                              else json.dumps(response).encode())

        monkeypatch.setattr(utilities.urllib.request, 'urlopen', urlopen)

    return install, calls, clock


def http_error(code, headers=None, reason='fixture failure'):
    return HTTPError('https://provider.invalid', code, reason, headers or {}, None)


def test_429_then_success_retries_and_reports_success(lookup_fakes):
    install, calls, clock = lookup_fakes
    install(http_error(429), FOUND)
    outcomes, responses = [], []
    result = utilities.lookup_isbn(ISBN, outcome_sink=outcomes.append,
                                   response_sink=responses.append)
    assert result['title'] == 'Lookup title'
    assert len(calls) == 2
    assert clock.sleeps == [pytest.approx(0.35)]
    assert outcomes == [{'status': 'found', 'attempts': 2}]
    assert responses == [FOUND]


def test_retry_exhaustion_reports_failure_with_bounded_backoff(lookup_fakes):
    install, calls, clock = lookup_fakes
    install(*(http_error(429) for _ in range(3)))
    outcomes = []
    assert utilities.lookup_isbn(ISBN, outcome_sink=outcomes.append) is None
    assert len(calls) == 3
    assert clock.sleeps == pytest.approx([0.35, 0.6])
    assert outcomes == [{'status': 'failed', 'attempts': 3,
                         'reason': 'http_error', 'http_status': 429}]


@pytest.mark.parametrize('failure', [http_error(503), TimeoutError('timed out'),
                                   URLError(TimeoutError('timed out'))])
def test_retryable_failures_retry(lookup_fakes, failure):
    install, calls, _clock = lookup_fakes
    install(failure, FOUND)
    assert utilities.lookup_isbn(ISBN)['title'] == 'Lookup title'
    assert len(calls) == 2


@pytest.mark.parametrize('failure', [http_error(400), http_error(404),
                                   URLError('connection refused')])
def test_permanent_failures_do_not_retry(lookup_fakes, failure):
    install, calls, clock = lookup_fakes
    install(failure)
    outcomes = []
    assert utilities.lookup_isbn(ISBN, outcome_sink=outcomes.append) is None
    assert len(calls) == 1
    assert clock.sleeps == []
    assert outcomes[0]['status'] == 'failed'


def test_retry_after_seconds_is_respected(lookup_fakes):
    install, calls, clock = lookup_fakes
    install(http_error(429, {'Retry-After': '2'}), FOUND)
    assert utilities.lookup_isbn(ISBN)['title'] == 'Lookup title'
    assert clock.sleeps == [2]
    assert len(calls) == 2


def test_retry_after_http_date_is_respected(lookup_fakes, monkeypatch):
    install, _calls, clock = lookup_fakes
    now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(utilities, 'datetime', FixedDatetime)
    install(http_error(503, {'Retry-After': format_datetime(now + timedelta(seconds=3))}),
            FOUND)
    assert utilities.lookup_isbn(ISBN)['title'] == 'Lookup title'
    assert clock.sleeps == [3]


def test_retry_after_beyond_budget_stops_without_early_retry(lookup_fakes):
    install, calls, clock = lookup_fakes
    install(http_error(429, {'Retry-After': '60'}))
    outcomes = []
    assert utilities.lookup_isbn(ISBN, outcome_sink=outcomes.append) is None
    assert len(calls) == 1
    assert clock.sleeps == []
    assert outcomes[0]['status'] == 'failed'


def test_timeouts_share_total_interactive_budget(lookup_fakes, monkeypatch):
    _install, calls, clock = lookup_fakes

    def timeout(url, timeout):
        calls.append((url, timeout))
        clock.now += timeout
        raise TimeoutError('fixture timeout')

    monkeypatch.setattr(utilities.urllib.request, 'urlopen', timeout)
    assert utilities.lookup_isbn(ISBN) is None
    assert len(calls) == 2
    assert calls[0][1] == 5
    assert calls[1][1] < 3
    assert clock.now == pytest.approx(utilities.ISBN_LOOKUP_TOTAL_TIMEOUT)


def test_genuine_absence_reports_not_found_without_retry(lookup_fakes):
    install, calls, clock = lookup_fakes
    install({'totalItems': 0})
    outcomes, responses = [], []
    assert utilities.lookup_isbn(ISBN, outcome_sink=outcomes.append,
                                response_sink=responses.append) is None
    assert outcomes == [{'status': 'not_found', 'attempts': 1}]
    assert responses == [{'totalItems': 0}]
    assert len(calls) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize('response', [None, [], {}, b'not JSON',
                                     {'totalItems': 1, 'items': []},
                                     {'totalItems': 1, 'items': [None]},
                                     {'totalItems': 1, 'items': [{'volumeInfo': None}]}])
def test_malformed_provider_response_is_failure(lookup_fakes, response):
    install, calls, clock = lookup_fakes
    install(response)
    outcomes = []
    assert utilities.lookup_isbn(ISBN, outcome_sink=outcomes.append) is None
    assert outcomes == [{'status': 'failed', 'attempts': 1,
                         'reason': 'invalid_response'}]
    assert len(calls) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize('secret', [None, 'test-only-fake-key'])
def test_api_key_is_optional_query_parameter(lookup_fakes, monkeypatch, secret):
    install, calls, _clock = lookup_fakes
    monkeypatch.setattr(utilities.st, 'secrets',
                        {'google_books_api_key': secret} if secret else {})
    install(FOUND)
    assert utilities.lookup_isbn(ISBN)['title'] == 'Lookup title'
    query = parse_qs(urlsplit(calls[0][0]).query)
    assert query['q'] == [f'isbn:{ISBN}']
    assert query.get('key') == ([secret] if secret else None)


def test_absent_secrets_file_is_quiet(lookup_fakes, monkeypatch, caplog):
    install, _calls, _clock = lookup_fakes

    class MissingSecrets:
        def get(self, name):
            raise FileNotFoundError('No secrets file')

    monkeypatch.setattr(utilities.st, 'secrets', MissingSecrets())
    install(FOUND)
    assert utilities.lookup_isbn(ISBN)['title'] == 'Lookup title'
    assert 'No secrets file' not in caplog.text


def test_errors_never_expose_key_in_logs_or_outcome(lookup_fakes, monkeypatch, caplog):
    install, _calls, _clock = lookup_fakes
    secret = 'test-only-fake-private-key'
    monkeypatch.setattr(utilities.st, 'secrets', {'google_books_api_key': secret})
    install(http_error(400, reason=f'URL contained key={secret}'))
    outcomes = []
    assert utilities.lookup_isbn(ISBN, outcome_sink=outcomes.append) is None
    assert secret not in caplog.text
    assert secret not in json.dumps(outcomes)


def test_failed_enrichment_marks_only_missing_backfillable_fields():
    def lookup(isbn, *, outcome_sink):
        outcome_sink({'status': 'failed', 'attempts': 3, 'http_status': 429})

    metadata = {'isbn': ISBN, 'title': 'Vision title', 'authors': [],
                'publisher': None, 'illustrators': [], 'published_year': None}
    assert utilities.apply_isbn_metadata(metadata, isbn_lookup=lookup) is metadata
    assert metadata['title'] == 'Vision title'
    assert metadata['isbn_lookup']['status'] == 'failed'
    assert metadata['lookup_failed_fields'] == ['authors', 'publisher']


def test_enrichment_can_retry_failure_without_repeating_vision():
    metadata = {'isbn': ISBN, 'title': 'Vision title', 'authors': [],
                'publisher': None, 'lookup_failed_fields': ['authors', 'publisher']}
    utilities.apply_isbn_metadata(metadata, isbn_lookup=lambda isbn: {
        'title': 'Other edition', 'authors': ['Writer'], 'publisher': 'Publisher'})
    assert metadata['title'] == 'Vision title'
    assert metadata['authors'] == ['Writer']
    assert metadata['isbn_lookup']['status'] == 'found'
    assert metadata['lookup_failed_fields'] == []


def test_no_isbn_does_not_attempt_or_exclude_enrichment():
    metadata = {'title': None}
    utilities.apply_isbn_metadata(metadata,
                                 isbn_lookup=lambda isbn: pytest.fail('no ISBN'))
    assert metadata['isbn_lookup']['status'] == 'not_attempted'
    assert metadata['lookup_failed_fields'] == []
