"""Noise, paired decisions and append-only attempts, entirely synthetic."""
from copy import deepcopy
import json
import math

import pytest

from scripts.extraction_eval import cli
from scripts.extraction_eval.store import atomic_json
from scripts.extraction_eval.tuning_loop import (
    MEASURES, append_attempt, noise_floors, tuning_scores, verdict,
)


def run(run_id='baseline', *, configuration='unchanged', replicate='1'):
    ids = ['iterate-a', 'iterate-b', 'check-a', 'check-b']
    selections = {'iterate55_ids': ids[:2], 'check20_ids': ids[2:]}
    scores = []
    for book_id in ids:
        ratio = {'numerator': 50, 'denominator': 100, 'recall': .5, 'coverage': .5, 'share': .5}
        scores.append({'id': book_id, 'accuracy': .5, 'reference_tokens': 100,
                       'insertions': 2, 'inserted_text_per_100_reference_tokens': 2,
                       'characters': {'tolerant_recall': .5, 'tolerant_numerator': 50, 'denominator': 100},
                       'attributes': {name: dict(ratio) for name in ('gender', 'human', 'protagonist')},
                       'aliases': {name: dict(ratio) for name in ('explicit_alias_coverage', 'groundedness')},
                       'page_failures': {'failed': 2, 'attempted': 100, 'failed_pages_per_100': 2}})
    return tuning_scores(scores, selections, run_id=run_id, configuration_id=configuration,
                         replicate=replicate, measurement_id='synthetic-fixed-references',
                         reference_hashes={book_id: f'synthetic-reference-{book_id}' for book_id in ids},
                         cost={'settled_usd': 2, 'per_book_usd': .5, 'outstanding_reservations_usd': 0})


def shift(scored, group, metric, delta):
    for book in scored['sets'][group].values():
        book[metric]['value'] += delta


@pytest.fixture
def comparison():
    baseline = run()
    repeats = [deepcopy(baseline), run('repeat-2', replicate='2')]
    for group in ('iterate', 'check'):
        for metric in MEASURES:
            shift(repeats[1], group, metric, (.01 if group == 'iterate' else .02) * math.sqrt(2))
    floors = noise_floors(repeats)
    candidate = run('candidate', configuration='changed')
    shift(candidate, 'iterate', 'character_recall', .03)
    return baseline, candidate, floors


def test_noise_is_sd_of_set_means_and_has_separate_floors(comparison):
    baseline, _, floors = comparison
    for name in MEASURES:
        assert floors['sets']['iterate'][name]['floor'] == pytest.approx(.01)
        assert floors['sets']['check'][name]['floor'] == pytest.approx(.02)
        assert floors['sets']['check'][name]['replicates'] == 2
    # Large opposite book movements cancel in the set mean: floor zero, not book SD.
    repeat = run('repeat', replicate='2')
    repeat['sets']['iterate']['iterate-a']['gender']['value'] += .4
    repeat['sets']['iterate']['iterate-b']['gender']['value'] -= .4
    assert noise_floors([baseline, repeat])['sets']['iterate']['gender']['floor'] == 0


def test_iterate_gain_check_unchanged_accepts(comparison):
    result = verdict(*comparison, 'character_recall')
    assert result['verdict'] == 'accept'
    assert all(clause['passed'] for clause in result['clauses'])
    assert result['figures']['iterate']['character_recall']['improvement'] == pytest.approx(.03)


@pytest.mark.parametrize('group,metric,delta', [
    ('check', 'character_recall', -.021), ('check', 'gender', -.021),
    ('iterate', 'character_recall', -.011), ('iterate', 'gender', -.011),
    ('iterate', 'groundedness', -.011), ('iterate', 'inserted_text_per_100', .011),
    ('iterate', 'unsuccessful_pages_per_100', .011),
])
def test_each_regression_or_insufficient_gain_rejects(comparison, group, metric, delta):
    baseline, candidate, floors = comparison
    shift(candidate, group, metric, delta)
    result = verdict(baseline, candidate, floors, 'character_recall')
    assert result['verdict'] == 'reject'
    assert any(not clause['passed'] and clause['measure'] == metric for clause in result['clauses'])


@pytest.mark.parametrize('target,delta', [('alias_coverage', .03), ('groundedness', .03),
                                        ('inserted_text_per_100', -.03), ('unsuccessful_pages_per_100', -.03)])
def test_directions_and_all_targets(comparison, target, delta):
    baseline, candidate, floors = comparison
    shift(candidate, 'iterate', target, delta)
    assert verdict(baseline, candidate, floors, target)['verdict'] == 'accept'
    shift(candidate, 'check', target, -.03 if delta > 0 else .03)
    assert verdict(baseline, candidate, floors, target)['verdict'] == 'reject'


def test_exact_boundaries_and_no_extra_check_guardrail(comparison):
    baseline, candidate, floors = comparison
    # Exact >2 floor is required, but equality to -floor is allowed.
    for group in floors['sets'].values():
        for item in group.values():
            item['floor'] = .01
    shift(candidate, 'iterate', 'character_recall', -.01)
    assert verdict(baseline, candidate, floors, 'character_recall')['verdict'] == 'reject'
    shift(candidate, 'iterate', 'character_recall', .001)
    shift(candidate, 'iterate', 'human', -.01)
    shift(candidate, 'check', 'human', -.01)
    shift(candidate, 'check', 'groundedness', -.2)
    assert verdict(baseline, candidate, floors, 'character_recall')['verdict'] == 'accept'


def test_counts_and_cost_do_not_decide_acceptance(comparison):
    baseline, candidate, floors = comparison
    for row in candidate['sets']['iterate'].values():
        row['character_recall']['numerator'] = 1
    candidate['cost']['per_book_usd'] = 100
    assert verdict(baseline, candidate, floors, 'character_recall')['verdict'] == 'accept'
    candidate = run('candidate')
    for row in candidate['sets']['iterate'].values():
        row['character_recall']['numerator'] = 100
    assert verdict(baseline, candidate, floors, 'character_recall')['verdict'] == 'reject'


def test_missing_floor_and_undefined_measure_give_no_verdict(comparison):
    baseline, candidate, floors = comparison
    assert verdict(baseline, candidate, None, 'character_recall')['verdict'] is None
    single = noise_floors([baseline])
    assert single['sets']['iterate']['character_recall']['floor'] is None
    assert single['sets']['iterate']['character_recall']['replicates'] == 1
    assert verdict(baseline, candidate, single, 'character_recall')['verdict'] is None
    for row in candidate['sets']['iterate'].values():
        row['character_recall']['value'] = None
    assert verdict(baseline, candidate, floors, 'character_recall')['verdict'] is None
    assert 'undefined' in verdict(baseline, candidate, floors, 'character_recall')['clauses'][0]['reason']


def test_paired_means_drop_only_shared_undefined_books(comparison):
    baseline, candidate, floors = comparison
    for scored in (baseline, candidate):
        scored['sets']['iterate']['iterate-a']['gender']['value'] = None
    result = verdict(baseline, candidate, floors, 'character_recall')
    assert result['verdict'] == 'accept'
    assert result['figures']['iterate']['gender']['paired_books'] == 1
    candidate['sets']['iterate']['iterate-b']['gender']['value'] = None
    assert verdict(baseline, candidate, floors, 'character_recall')['verdict'] is None


def test_replicates_require_same_configuration_panel_and_distinct_runs():
    baseline = run()
    for repeat, message in [(run('other', configuration='changed', replicate='2'), 'unchanged'),
                            (run('other'), 'distinct'), (run(), 'distinct')]:
        with pytest.raises(ValueError, match=message):
            noise_floors([baseline, repeat])
    repeat = run('other', replicate='2')
    repeat['sets']['check'].pop('check-a')
    with pytest.raises(ValueError, match='identical books'):
        noise_floors([baseline, repeat])
    repeat = run('other', replicate='2')
    repeat['sets']['check']['check-a']['gender']['value'] = None
    assert noise_floors([baseline, repeat])['sets']['check']['gender']['floor'] is None


def test_pairing_and_full_cohorts_are_required(comparison):
    baseline, candidate, floors = comparison
    candidate['sets']['check'].pop('check-a')
    with pytest.raises(ValueError, match='identical books'):
        verdict(baseline, candidate, floors, 'character_recall')
    baseline['sets']['check'].pop('check-a')
    with pytest.raises(ValueError, match='every frozen'):
        verdict(baseline, candidate, floors, 'character_recall')


def test_log_is_append_only_and_rejected_and_unscorable_attempts_remain(tmp_path, comparison):
    baseline, candidate, floors = comparison
    path = tmp_path / 'attempts.jsonl'
    rejected = run('rejected')
    row = append_attempt(path, baseline, rejected, floors, 'character_recall', 'a hypothesis', 'a change')
    assert row['verdict'] == 'reject'
    before = path.read_bytes()
    row = append_attempt(path, baseline, candidate, floors, 'character_recall', 'gain', 'one change')
    assert path.read_bytes().startswith(before)
    assert row['accepted_changes'] == 1 and row['attempt'] == 2
    before = path.read_bytes()
    with pytest.raises(ValueError, match='last accepted'):
        append_attempt(path, baseline, candidate, floors, 'character_recall', 'gain', 'one change')
    assert path.read_bytes() == before
    row = append_attempt(path, candidate, run('unscorable'), None, 'character_recall', 'gain', 'change')
    assert row['verdict'] is None and row['attempt'] == 3 and row['accepted_changes'] == 1
    assert [json.loads(line)['verdict'] for line in path.read_text().splitlines()] == ['reject', 'accept', None]
    for book_id in ('check-a', 'check-b', 'iterate-a', 'iterate-b'):
        assert book_id not in path.read_text()


@pytest.mark.parametrize('limit,accept', [(15, True), (30, False)])
def test_both_attempt_limits(tmp_path, comparison, limit, accept):
    baseline, _, floors = comparison
    path = tmp_path / 'attempts.jsonl'
    for index in range(limit):
        candidate = deepcopy(baseline)
        candidate['run_id'] = f'candidate-{index}'
        if accept:
            shift(candidate, 'iterate', 'character_recall', .03)
        row = append_attempt(path, baseline, candidate, floors, 'character_recall', 'gain', 'change')
        if accept:
            baseline = candidate
    before = path.read_bytes()
    with pytest.raises(ValueError, match=f'{limit}/' + ('15 accepted' if accept else '30 attempts')):
        append_attempt(path, baseline, candidate, floors, 'character_recall', 'gain', 'change')
    assert path.read_bytes() == before
    assert row['attempt'] == limit


def test_commands_are_numeric_only_and_check_output_is_aggregate(tmp_path, comparison, capsys, monkeypatch):
    baseline, candidate, _ = comparison
    repeat = run('repeat', replicate='2')
    for group in ('iterate', 'check'):
        for metric in MEASURES:
            shift(repeat, group, metric, .01 * math.sqrt(2))
    for name, value in [('baseline', baseline), ('candidate', candidate), ('repeat', repeat)]:
        atomic_json(tmp_path / f'{name}.json', value)
    monkeypatch.setattr(cli, '_load_snapshot', lambda *args, **kwargs: pytest.fail('No source reads'))
    from scripts.extraction_eval import comparison as legacy
    monkeypatch.setattr(legacy, 'compare_metrics', lambda *args, **kwargs: pytest.fail('No legacy gates'))
    floors = tmp_path / 'floors.json'
    assert cli.main(['tuning-noise', '--runs', str(tmp_path / 'baseline.json'),
                     str(tmp_path / 'repeat.json'), '--out', str(floors)]) == 0
    args = ['tuning-attempt', '--baseline', str(tmp_path / 'baseline.json'),
            '--candidate', str(tmp_path / 'candidate.json'), '--floors', str(floors),
            '--target', 'character_recall', '--hypothesis', 'Recover missed characters',
            '--change', 'One synthetic rule', '--log', str(tmp_path / 'attempts.jsonl')]
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    assert 'accept: target character_recall' in output
    assert 'check character_recall' in output
    assert not any(book_id in output for book_id in baseline['sets']['check'])
    row = json.loads((tmp_path / 'attempts.jsonl').read_text())
    assert row['counts']['iterate']['character_recall']['before'] == {'numerator': 100, 'denominator': 200}
    assert row['guardrails']['iterate']['groundedness']['before'] == .5
