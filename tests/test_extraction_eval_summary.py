"""Frozen, whole-book summary inference using synthetic scored cohorts."""
from copy import deepcopy
import json
import re
from collections import Counter

import pytest

from scripts.extraction_eval.reporting import summarize_scan_scores, scan_summary_markdown
from scripts.extraction_eval.statistics import book_draws, bootstrap_books


def scored(book_id, value, *, denominator=100):
    ratio = {'numerator': int(value * denominator), 'denominator': denominator,
             'recall': value, 'strict_numerator': int(value * denominator), 'strict_recall': value}
    return {'id': book_id, 'status': 'ready', 'accuracy': value, 'reference_tokens': denominator,
            'insertions': 2, 'inserted_text_per_100_reference_tokens': 2,
            'story_text': {'accuracy': value, 'reference_tokens': denominator},
            'characters': {**ratio, 'tolerant_numerator': ratio['numerator'], 'tolerant_recall': value},
            'attributes': {field: dict(ratio) for field in ('gender', 'human', 'protagonist')},
            'aliases': {'alias_recall': dict(ratio)},
            'page_failures': {'failed': 1, 'attempted': 4, 'failed_pages_per_100': 25}}


def selection(scores):
    return {'book_strata': {row['id']: 'alias_positive' if index < 4 else 'alias_zero'
                           for index, row in enumerate(scores)},
            'imported80_ids': [row['id'] for row in scores],
            'heldout_author_shared_ids': ['0', '4'], 'heldout_alias_quoted_ids': ['1']}


@pytest.fixture
def cohort():
    return [scored(str(i), value) for i, value in enumerate([.11, .37, .62, .89, .18, .42, .71, .96, .54])]


def test_fixed_synthetic_ranges_are_exact_and_order_independent(cohort):
    selections = selection(cohort)
    summary = summarize_scan_scores(cohort, selections, cohort='tune')
    metrics = summary['primary']['metrics']
    for name in ('text_accuracy', 'character_recall', 'gender', 'human', 'protagonist', 'alias_recall'):
        assert metrics[name]['mean'] == 0.5333333333333333
        assert metrics[name]['range_95'] == [0.3522222222222222, 0.71]
        assert metrics[name]['books'] == 9
        assert metrics[name]['items'] == 900
    assert summarize_scan_scores(list(reversed(cohort)), selections, cohort='tune') == summary
    assert summary['sensitivities']['status'] == 'not_applicable'
    assert metrics['unsuccessful_pages_per_100']['pooled'] == 25
    assert metrics['inserted_text_per_100']['pooled'] == 2
    assert 'range_95' not in metrics['strict_gender']


def test_shared_draws_and_sensitivities_retain_books_and_stratum_counts(cohort, monkeypatch):
    from scripts.extraction_eval import statistics
    original_draws, original_bootstrap = statistics.book_draws, statistics.bootstrap_books
    batches, used = [], []

    def draws(records, **kwargs):
        result = original_draws(records, **kwargs)
        strata = {row['id']: row['stratum'] for row in records}
        counts = Counter(strata.values())
        for draw in result:
            assert Counter(strata[i] for i in draw) == counts
        batches.append((set(strata), result))
        return result

    def bootstrap(records, **kwargs):
        used.append(kwargs['draws'])
        return original_bootstrap(records, **kwargs)

    monkeypatch.setattr(statistics, 'book_draws', draws)
    monkeypatch.setattr(statistics, 'bootstrap_books', bootstrap)
    selections = selection(cohort)
    before = deepcopy(selections)
    summary = summarize_scan_scores(cohort, selections, cohort='proof')
    assert selections == before  # no new cohort selection, replacements or mutations
    assert len(batches) == 3
    for index in range(3):
        assert all(draw is batches[index][1] for draw in used[sum([7, 6, 6][:index]):sum([7, 6, 6][:index + 1])])
    for index, field in enumerate(('heldout_author_shared_ids', 'heldout_alias_quoted_ids'), 1):
        excluded = set(selections[field])
        assert batches[index][0] == batches[0][0] - excluded
        result = summary['sensitivities'][field]
        assert result['excluded_books'] == len(excluded)
        assert result['books'] == 9 - len(excluded)
        assert sum(result['strata'].values()) == result['books']
        for metric in result['metrics'].values():
            assert metric['items'] == 100 * result['books']
            assert metric['books'] == result['books']
            assert metric['range_95'] is not None
    assert summary['primary']['metrics']['gender']['items'] == 900
    assert summary['sensitivities']['heldout_author_shared_ids']['strata'] == {'alias_positive': 3, 'alias_zero': 4}


def test_no_cross_stratum_resampling_or_item_weighting():
    records = [{'id': 'one', 'stratum': 'a', 'value': 0},
               {'id': 'two', 'stratum': 'z', 'value': 1}]
    draws = book_draws(records)
    assert all(draw == ['one', 'two'] for draw in draws)
    result = bootstrap_books(records, repeats=20000, seed=20261110, draws=draws)
    assert result['interval_95'] == [0.5, 0.5]
    books = [scored('one', 0, denominator=1), scored('two', 1, denominator=999)]
    selected = {'book_strata': {'one': 'a', 'two': 'z'}}
    metric = summarize_scan_scores(books, selected, cohort='tune')['primary']['metrics']['gender']
    assert metric['mean'] == 0.5
    assert metric['pooled'] == .999


def test_undefined_is_omitted_but_unavailable_stays_zero(cohort):
    cohort[0]['attributes']['gender'].update(recall=None, numerator=0, denominator=0)
    cohort[1]['status'] = 'unavailable'
    cohort[1].update(accuracy=0, reference_tokens=None)
    summary = summarize_scan_scores(cohort, selection(cohort), cohort='tune')
    gender = summary['primary']['metrics']['gender']
    assert gender['books'] == 8
    assert gender['undefined_books'] == 1
    assert gender['items'] == 800
    assert gender['mean'] == sum([0, .62, .89, .18, .42, .71, .96, .54]) / 8
    text = summary['primary']['metrics']['text_accuracy']
    assert text['books'] == 9
    assert text['items'] == 800
    assert text['unknown_item_books'] == 1
    assert summary['primary']['unavailable_books'] == 1


def test_perfect_point_range_caveat_and_report_language(cohort):
    cohort = [scored(row['id'], 1) for row in cohort]
    summary = summarize_scan_scores(cohort, selection(cohort), cohort='proof')
    rendered = scan_summary_markdown(summary)
    for name in ('text_accuracy', 'character_recall', 'gender', 'human', 'protagonist'):
        metric = summary['primary']['metrics'][name]
        assert metric['range_95'] == [1, 1]
        assert 'cannot quantify unseen errors' in metric['qualification']
    assert 'mean 100.00%; 95% range [100.00%, 100.00%]; books 9; items 900' in rendered
    assert 'cannot quantify unseen errors' in rendered
    assert not re.search(r'pass|fail|gate|target', rendered.lower())
    assert not re.search(r'pass|fail|gate|target', json.dumps(summary).lower())


def test_all_undefined_has_no_estimate_or_range(cohort):
    for row in cohort:
        row['accuracy'] = None
        row['reference_tokens'] = None
    summary = summarize_scan_scores(cohort, selection(cohort), cohort='rehearsal')
    metric = summary['primary']['metrics']['text_accuracy']
    assert metric['mean'] is metric['range_95'] is metric['items'] is None
    assert metric['books'] == 0
    assert metric['undefined_books'] == 9
    assert summary['sensitivities']['status'] == 'not_applicable'


def test_legacy_bootstrap_output_is_unchanged():
    records = [{'id': 'z', 'stratum': 'z', 'value': .3, 'inclusion_probability': .5},
               {'id': 'a', 'stratum': 'a', 'value': .8}, {'id': 'b', 'stratum': 'a', 'value': .2},
               {'id': 'c', 'value': None}, {'id': 'd', 'value': 1, 'role': 'challenge'}]
    assert bootstrap_books(records, repeats=100) == {
        'estimate': .4, 'books': 3, 'interval_95': [.25, .55], 'repeats': 100,
        'seed': 20261104, 'resampling_unit': 'book', 'strata': {'z': 1, 'a': 2},
        'singleton_strata': ['z'],
        'qualification': 'Book sampling interval; does not establish reference correctness or future-book accuracy.'}


def test_missing_strata_duplicate_ids_and_partial_proof_are_rejected(cohort):
    selections = selection(cohort)
    with pytest.raises(ValueError, match='every frozen proof id'):
        summarize_scan_scores(cohort[:-1], selections, cohort='proof')
    with pytest.raises(ValueError, match='one score per book'):
        summarize_scan_scores(cohort + cohort[:1], selections, cohort='tune')
    selections['book_strata'].pop('0')
    with pytest.raises(ValueError, match='frozen alias stratum'):
        summarize_scan_scores(cohort, selections, cohort='tune')
