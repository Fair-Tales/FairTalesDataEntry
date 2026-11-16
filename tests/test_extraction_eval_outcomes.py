"""Failure figures are independent of reference text and page word counts."""
import json
import sqlite3

import pytest

from scripts.extraction_eval.outcomes import FAILURE_KINDS, normalize_outcome
from scripts.extraction_eval.scoring import score_book_text, score_page_failures


def test_failure_denominator_includes_wordless_and_text_successes_only():
    pages = [{'status': 'failed', 'failure_kind': kind, 'text': 'untrusted'}
             for kind in FAILURE_KINDS]
    pages += [{'status': 'completed', 'text': ''}, {'status': 'completed', 'text': 'kept'},
              {'status': 'pending'}, {'status': 'budget_stopped'}, {}]
    figures = score_page_failures(pages)
    assert figures['by_kind'] == dict.fromkeys(FAILURE_KINDS, 1)
    assert figures['failed'] == 8
    assert figures['completed'] == 2
    assert figures['attempted'] == 10
    assert figures['unrun'] == 3
    assert figures['failed_pages_per_100'] == 80
    assert score_book_text(['kept'], pages)['accuracy'] == 1
    assert score_page_failures([])['failed_pages_per_100'] is None


@pytest.mark.parametrize('kind, expected', [
    ('truncated_response', 'truncated'),
    ('invalid_json_response', 'invalid_response'),
    ('salvaged_partial_response', 'invalid_response'),
    ('invalid_character_structure', 'invalid_response'),
])
def test_legacy_failure_names(kind, expected):
    assert normalize_outcome({'status': 'failed', 'failure_kind': kind})['failure_kind'] == expected


def test_dev20_failure_figures_from_predictions_and_read_only_ledger():
    from conftest import load_dev20_predictions
    from scripts.extraction_eval.evidence import evidence_dir

    predictions = load_dev20_predictions()
    path = evidence_dir() / 'spend.sqlite'
    if not path.exists():
        pytest.skip('Preserved dev20 ledger is absent')
    # Old predictions did not retain stop reasons. Enrich only in memory, using
    # the transcription call, never a short orientation check on the same page.
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        for book_id, book in predictions['books'].items():
            for page_id, page in book['pages'].items():
                if page.get('status') != 'failed':
                    continue
                stage = f'{book_id}:page:{page_id}'
                rows = db.execute(
                    "SELECT response FROM calls WHERE json_extract(request, '$.context.stage')=? "
                    "AND label='page_extraction' AND response IS NOT NULL", (stage,))
                for (response,) in rows:
                    page['raw_response'] = json.loads(response)
    finally:
        db.close()
    figures = score_page_failures([page for book in predictions['books'].values()
                                  for page in book['pages'].values()])
    assert figures['attempted'] == 772
    assert figures['failed'] == 5
    assert figures['by_kind'] == {**dict.fromkeys(FAILURE_KINDS, 0),
                                 'output_blocked': 3, 'refusal': 1, 'truncated': 1}
    assert figures['failed_pages_per_100'] == pytest.approx(500 / 772)
