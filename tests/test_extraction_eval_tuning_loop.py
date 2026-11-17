"""Noise, paired decisions and append-only attempts, entirely synthetic."""
from copy import deepcopy
import json
import math

import pytest

from scripts.extraction_eval import cli
from scripts.extraction_eval.store import atomic_json
from scripts.extraction_eval.tuning_loop import (
    MEASURES, SETS, append_attempt, noise_floors, t_quantile, tuning_scores, verdict,
)


def run(run_id='baseline', *, configuration='unchanged', replicate=None, replicate_stages=None):
    ids = ['iterate-a', 'iterate-b', 'check-a', 'check-b']
    selections = {'iterate55_ids': ids[:2], 'check20_ids': ids[2:]}
    scores = []
    for book_id in ids:
        ratio = {'numerator': 50, 'denominator': 100, 'recall': .5, 'coverage': .5, 'share': .5}
        scores.append({'id': book_id, 'accuracy': .5, 'reference_tokens': 100,
                       'insertions': 2, 'inserted_text_per_100_reference_tokens': 2,
                       'story_text': {'accuracy': .5, 'reference_tokens': 100, 'insertions': 2,
                                      'inserted_text_per_100_reference_tokens': 2},
                       'characters': {'extra_characters': 1, 'tolerant_recall': .5, 'tolerant_numerator': 50, 'denominator': 100},
                       'attributes': {name: {**ratio, 'matched_pair_agreement': {**ratio, 'agreement': .5}} for name in ('gender', 'human', 'protagonist')},
                       'aliases': {**{name: dict(ratio) for name in ('explicit_alias_coverage', 'groundedness')},
                                   'additional_aliases': {'duplicates': {'numerator': 1, 'denominator': 100, 'share': .01}}},
                       'page_failures': {'failed': 2, 'attempted': 100, 'failed_pages_per_100': 2}})
    return tuning_scores(scores, selections, run_id=run_id, configuration_id=configuration,
                         replicate=run_id if replicate is None else replicate, replicate_stages=replicate_stages,
                         measurement_id='synthetic-fixed-references',
                         reference_hashes={book_id: f'synthetic-reference-{book_id}' for book_id in ids},
                         cost={'settled_usd': 2, 'per_book_usd': .5, 'outstanding_reservations_usd': 0})


def shift(scored, group, metric, delta):
    for book in scored['sets'][group].values():
        book[metric]['value'] += delta


@pytest.fixture
def comparison():
    baseline = run()
    repeats = [run(f'cal-{i}', replicate=str(i)) for i in range(20)]
    for i, repeat in enumerate(repeats):
        for group in SETS:
            for metric in MEASURES:
                shift(repeat, group, metric, (.001 if group == 'iterate' else .002)
                      * math.sqrt(19 / 20) * (-1 if i < 10 else 1))
    floors = noise_floors(repeats)
    candidate = run('candidate', configuration='changed')
    shift(candidate, 'iterate', 'character_recall', .03)
    return baseline, candidate, floors


def test_noise_is_sd_of_set_means_and_has_separate_floors(comparison):
    baseline, _, floors = comparison
    for name in MEASURES:
        assert floors['sets']['iterate'][name]['floor'] == pytest.approx(.001)
        assert floors['sets']['check'][name]['floor'] == pytest.approx(.002)
        assert floors['sets']['check'][name]['replicates'] == 20
    # Large opposite book movements cancel in the set mean: floor zero, not book SD.
    repeat = run('repeat', replicate='2')
    repeat['sets']['iterate']['iterate-a']['gender']['value'] += .4
    repeat['sets']['iterate']['iterate-b']['gender']['value'] -= .4
    assert noise_floors([baseline, repeat])['sets']['iterate']['gender']['floor'] == 0


def test_refreshed_stages_are_sets_and_legacy_scores_mean_all_stages():
    stages = ['page_extraction', 'character_detection', 'page_extraction']
    first = run('one', replicate_stages=stages)
    assert first['replicate_stages'] == ['character_detection', 'page_extraction']
    second = run('two', replicate='2', replicate_stages=stages)
    second['replicate_stages'] = stages  # Readers also compare sets, not list order.
    assert noise_floors([first, second])['replicate_stages'] == first['replicate_stages']
    legacy = run('old', replicate='2')
    del legacy['replicate_stages']
    assert noise_floors([run(), legacy])['replicate_stages'] is None


@pytest.mark.parametrize('other', [None, [], ['page_extraction']])
def test_noise_command_refuses_different_refreshed_stages(tmp_path, other, capsys):
    paths = [tmp_path / 'one.json', tmp_path / 'two.json']
    atomic_json(paths[0], run('one', replicate_stages=['character_detection']))
    atomic_json(paths[1], run('two', replicate='2', replicate_stages=other))
    out = tmp_path / 'noise.json'
    with pytest.raises(SystemExit) as exc:
        cli.main(['tuning-noise', '--runs', *map(str, paths), '--out', str(out)])
    assert exc.value.code == 2
    assert 'same refreshed-stage set' in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize('different', ['baseline', 'candidate', 'floors', 'check'])
def test_attempt_command_refuses_different_refreshed_stages(tmp_path, comparison, different, capsys):
    baseline, candidate, floors = comparison
    records = {'baseline': baseline, 'candidate': candidate, 'floors': floors,
               'check': deepcopy(candidate)}
    records[different]['replicate_stages'] = ['character_detection']
    paths = {name: tmp_path / f'{name}.json' for name in records}
    for name, record in records.items():
        atomic_json(paths[name], record)
    log = tmp_path / 'attempts.jsonl'
    args = ['tuning-attempt', '--baseline', str(paths['baseline']),
            '--candidate', str(paths['candidate']), '--floors', str(paths['floors']),
            '--target', 'character_recall', '--hypothesis', 'Synthetic hypothesis',
            '--change', 'Synthetic change', '--log', str(log)]
    if different == 'check':
        args.extend(['--candidate-check', str(paths['check'])])
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2
    assert 'same refreshed-stage set' in capsys.readouterr().err
    assert not log.exists()


def test_iterate_gain_check_unchanged_accepts(comparison):
    result = verdict(*comparison, 'character_recall')
    assert result['verdict'] == 'accept'
    assert all(clause['passed'] for clause in result['clauses'])
    assert result['figures']['iterate']['character_recall']['improvement'] == pytest.approx(.03)


@pytest.mark.parametrize('group,metric,delta', [
    ('check', 'character_recall', -.021), ('check', 'gender', -.021),
    ('iterate', 'character_recall', -.029), ('iterate', 'gender', -.011),
    ('iterate', 'groundedness', -.011), ('iterate', 'story_inserted_text_per_100', .011),
    ('iterate', 'unsuccessful_pages_per_100', .011),
])
def test_each_regression_or_insufficient_gain_rejects(comparison, group, metric, delta):
    baseline, candidate, floors = comparison
    shift(candidate, group, metric, delta)
    result = verdict(baseline, candidate, floors, 'character_recall')
    assert result['verdict'] == 'reject'
    assert any(not clause['passed'] and clause['measure'] == metric for clause in result['clauses'])


@pytest.mark.parametrize('target,delta', [('alias_coverage', .03), ('groundedness', .03),
                                        ('story_inserted_text_per_100', -.03), ('unsuccessful_pages_per_100', -.03)])
def test_directions_and_all_targets(comparison, target, delta):
    baseline, candidate, floors = comparison
    shift(candidate, 'iterate', target, delta)
    assert verdict(baseline, candidate, floors, target)['verdict'] == 'accept'
    shift(candidate, 'check', target, -.03 if delta > 0 else .03)
    assert verdict(baseline, candidate, floors, target)['verdict'] == 'reject'


def test_exact_boundaries_and_no_extra_check_guardrail(comparison):
    baseline, candidate, floors = comparison
    result = verdict(baseline, candidate, floors, 'character_recall')
    gain = result['clauses'][0]['threshold']
    shift(candidate, 'iterate', 'character_recall', gain - .03)
    assert verdict(baseline, candidate, floors, 'character_recall')['verdict'] == 'reject'
    shift(candidate, 'iterate', 'character_recall', .001)
    for group in SETS:
        cutoff = next(c['threshold'] for c in result['clauses']
                      if c['set'] == group and c['measure'] == 'human')
        shift(candidate, group, 'human', cutoff)
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
    single = noise_floors([run('single-cal')])
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
    # Shared undefined values must also match the calibration eligibility.
    assert result['verdict'] is None
    from scripts.extraction_eval.store import digest
    floors['sets']['iterate']['gender']['panel_sha256'] = digest(['iterate-b'])
    assert verdict(baseline, candidate, floors, 'character_recall')['verdict'] == 'accept'
    assert result['figures']['iterate']['gender']['paired_books'] == 1
    candidate['sets']['iterate']['iterate-b']['gender']['value'] = None
    assert verdict(baseline, candidate, floors, 'character_recall')['verdict'] is None


def test_replicates_require_same_configuration_panel_and_distinct_runs():
    baseline = run()
    for repeat, message in [(run('other', configuration='changed', replicate='2'), 'unchanged'),
                            (run('other', replicate='baseline'), 'distinct'), (run(), 'distinct')]:
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
    with pytest.raises(ValueError, match='every frozen'):
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
        candidate['replicate'] = f'candidate-{index}'
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
    for name, value in [('baseline', baseline), ('candidate', candidate)]:
        atomic_json(tmp_path / f'{name}.json', value)
    repeats = [run(f'cal-{i}', replicate=str(i)) for i in range(20)]
    for i, repeat in enumerate(repeats):
        for group in SETS:
            for metric in MEASURES:
                shift(repeat, group, metric, .001 * (-1 if i < 10 else 1))
        atomic_json(tmp_path / f'cal-{i}.json', repeat)
    monkeypatch.setattr(cli, '_load_snapshot', lambda *args, **kwargs: pytest.fail('No source reads'))
    from scripts.extraction_eval import comparison as legacy
    monkeypatch.setattr(legacy, 'compare_metrics', lambda *args, **kwargs: pytest.fail('No legacy gates'))
    floors = tmp_path / 'floors.json'
    assert cli.main(['tuning-noise', '--runs',
                     *[str(tmp_path / f'cal-{i}.json') for i in range(20)], '--out', str(floors)]) == 0
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


@pytest.mark.parametrize('df', [4, 19])
@pytest.mark.parametrize('probability', [.975, .99375])
def test_student_t_against_independent_density_integration(df, probability):
    # Independent numerical method: composite Simpson integration of the t PDF,
    # not the cosine recurrence used in production. No external table/dependency.
    x = t_quantile(probability, df)
    steps = 16384
    h = x / steps
    coefficient = math.gamma((df + 1) / 2) / (math.sqrt(df * math.pi) * math.gamma(df / 2))

    def pdf(value):
        return coefficient * (1 + value * value / df) ** (-(df + 1) / 2)

    integral = h / 3 * (pdf(0) + pdf(x) +
                        4 * sum(pdf(i * h) for i in range(1, steps, 2)) +
                        2 * sum(pdf(i * h) for i in range(2, steps, 2)))
    assert .5 + integral == pytest.approx(probability, abs=1e-11)


def arm(scored, prefix, iterate=4, check=8):
    runs = []
    for i in range(max(iterate, check)):
        fresh = deepcopy(scored)
        fresh.update(run_id=f'{prefix}-{i}', replicate=f'{prefix}-{i}')
        runs.append(fresh)
    return {'iterate': runs[:iterate], 'check': runs[:check]}


@pytest.mark.parametrize('R,r', [(20, {'iterate': 4, 'check': 8}), (5, {'iterate': 1, 'check': 1})])
def test_difference_noise_and_t_thresholds(comparison, R, r):
    baseline, candidate, floors = comparison
    floors['R'] = R
    for group in floors['sets'].values():
        for noise in group.values():
            noise['replicates'] = R
    result = verdict(arm(baseline, 'b', **r), arm(candidate, 'c', **r), floors, 'character_recall')
    assert result['R'] == R and result['r'] == r
    assert result['m'] == 13  # No cached measures; redundant Iterate target excluded.
    for clause in result['clauses']:
        expected_d = clause['floor'] * math.sqrt(2 / r[clause['set']])
        gain = clause['rule'] == 'target_gain'
        expected_t = t_quantile(.975 if gain else 1 - .05 / 13, R - 1)
        assert clause['d'] == pytest.approx(expected_d)
        assert clause['quantile'] == pytest.approx(expected_t)
        assert clause['threshold'] == pytest.approx(expected_d * expected_t * (1 if gain else -1))


def test_averages_within_books_then_equal_book_means(comparison):
    baseline, candidate, floors = comparison
    before, after = arm(baseline, 'b'), arm(candidate, 'c')
    for i, scored in enumerate(after['iterate']):
        for book_id, base_value, denominator in [('iterate-a', .2, 10), ('iterate-b', .8, 1000)]:
            metric = scored['sets']['iterate'][book_id]['character_recall']
            metric.update(value=base_value + .02 * i, denominator=denominator,
                          numerator=(base_value + .02 * i) * denominator)
    result = verdict(before, after, floors, 'character_recall')
    figure = result['figures']['iterate']['character_recall']
    assert figure['after'] == pytest.approx((.23 + .83) / 2)
    assert figure['improvement'] == pytest.approx(.03)
    assert figure['counts']['after'] == {'numerator': pytest.approx(832.3), 'denominator': 1010}
    # An undefined value in just one replicate must not silently change weights.
    after['iterate'][1]['sets']['iterate']['iterate-a']['character_recall']['value'] = None
    assert verdict(before, after, floors, 'character_recall')['verdict'] is None


@pytest.mark.parametrize('target,m', [('character_recall', 9), ('alias_coverage', 11)])
def test_cached_measures_omitted_only_when_identical_in_all_runs(target, m):
    calibration = [run(f'cal-{i}', replicate=str(i)) for i in range(20)]
    cached = ('story_text_accuracy', 'story_inserted_text_per_100', 'unsuccessful_pages_per_100')
    for i, scored in enumerate(calibration):
        for name in SETS:
            for metric in MEASURES:
                if metric not in cached:
                    shift(scored, name, metric, .001 * i)
    floors = noise_floors(calibration)
    baseline, candidate = run('b'), run('c')
    shift(candidate, 'iterate', target, .03)
    result = verdict(baseline, candidate, floors, target)
    assert result['verdict'] == 'accept'
    assert result['omitted_measures'] == list(cached)
    assert result['m'] == m
    assert not any(c['measure'] in cached for c in result['clauses'])
    # Zero SD of means alone is insufficient: book values changed and cancel.
    calibration[1]['sets']['iterate']['iterate-a']['story_text_accuracy']['value'] += .1
    calibration[1]['sets']['iterate']['iterate-b']['story_text_accuracy']['value'] -= .1
    changed_floors = noise_floors(calibration)
    assert changed_floors['sets']['iterate']['story_text_accuracy']['floor'] == 0
    assert 'story_text_accuracy' not in verdict(baseline, candidate, changed_floors, target)['omitted_measures']
    # Nor can a calibration-only cache fingerprint hide a changed comparison.
    shift(candidate, 'check', 'story_text_accuracy', -.001)
    result = verdict(baseline, candidate, floors, target)
    assert 'story_text_accuracy' not in result['omitted_measures']
    assert result['m'] == m + 2 and result['verdict'] == 'reject'


def test_cached_target_cannot_vacuously_accept():
    floors = noise_floors([run('cal-0'), run('cal-1', replicate='2')])
    result = verdict(run('b'), run('c'), floors, 'character_recall')
    assert result['omitted_measures'] == list(MEASURES)
    assert result['m'] == 0 and result['verdict'] == 'reject'


def test_fresh_equal_arms_and_configuration_required(comparison):
    baseline, candidate, floors = comparison
    before, after = arm(baseline, 'b'), arm(candidate, 'c')
    after['check'].pop()
    with pytest.raises(ValueError, match='same number'):
        verdict(before, after, floors, 'character_recall')
    with pytest.raises(ValueError, match='fresh'):
        verdict(baseline, baseline, floors, 'character_recall')
    baseline['run_id'] = floors['run_ids'][0]
    with pytest.raises(ValueError, match='fresh'):
        verdict(baseline, candidate, floors, 'character_recall')
    after = arm(candidate, 'c')
    after['check'][-1]['configuration_id'] = 'different'
    with pytest.raises(ValueError, match='one configuration'):
        verdict(before, after, floors, 'character_recall')


def test_multi_run_cli_log_and_fresh_incumbent(tmp_path, comparison, capsys):
    baseline, candidate, floors = comparison
    atomic_json(tmp_path / 'floors.json', floors)
    args = ['tuning-attempt', '--floors', str(tmp_path / 'floors.json'),
            '--target', 'character_recall', '--hypothesis', 'Gain', '--change', 'Rule',
            '--log', str(tmp_path / 'attempts.jsonl')]
    for side, scored in [('baseline', baseline), ('candidate', candidate)]:
        runs = arm(scored, side)
        paths = []
        for item in runs['check']:
            path = tmp_path / (item['run_id'] + '.json')
            atomic_json(path, item)
            paths.append(str(path))
        args += [f'--{side}', *paths[:4], f'--{side}-check', *paths]
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    row = json.loads((tmp_path / 'attempts.jsonl').read_text())
    assert row['R'] == 20 and row['r'] == {'iterate': 4, 'check': 8}
    assert len(row['baseline_run_ids']['check']) == 8
    assert row['cost']['before']['settled_usd'] == 16  # Shared lists counted once.
    assert not any(book_id in output + json.dumps(row) for book_id in baseline['reference_hashes'])
    # Fresh runs of the accepted configuration preserve the incumbent chain.
    result = append_attempt(tmp_path / 'attempts.jsonl', arm(candidate, 'fresh-b'),
                            arm(candidate, 'fresh-c'), floors, 'character_recall', 'Try', 'Rule')
    assert result['verdict'] == 'reject' and result['attempt'] == 2


def test_design_simulation_row(capsys):
    """1,500 trials; independent normal errors at per-book SD .05.

    Draw sufficient statistics N(0, .05/sqrt(n)), n=55/20. Assigning a set
    mean to two synthetic book records preserves every floor and verdict;
    separate tests cover literal unequal-book and replicate averaging.
    Calibration and each of 4/8 fresh runs per arm are redrawn every trial.
    """
    import numpy as np

    rng = np.random.default_rng(161126)
    calibration = [run(f'cal-{i}', replicate=str(i)) for i in range(20)]
    baseline, candidate = arm(run(), 'b'), arm(run(configuration='candidate'), 'c')
    # Preserve the original random draws when report-only scores are added.
    simulated = ('story_text_accuracy', 'character_recall', 'gender', 'human', 'protagonist',
                 'alias_coverage', 'groundedness', 'story_inserted_text_per_100', 'unsuccessful_pages_per_100')
    moving = ('character_recall', 'gender', 'human', 'protagonist', 'alias_coverage', 'groundedness')
    scenarios = [(0, 0, 0, 0), (.01, .01, 0, 0), (.02, .02, 0, 0),
                 (.03, .03, 0, 0), (.03, .03, -.03, -.03), (.03, 0, 0, 0), (.03, .03, 0, -.03)]
    kept = [0] * len(scenarios)
    panel_sd = .05 / np.sqrt(np.array([55, 20]))[:, None]

    def assign(scored, errors, scenario=(0, 0, 0, 0)):
        for s, name in enumerate(SETS):
            for metric in MEASURES:
                delta = scenario[s] if metric == 'character_recall' else scenario[s + 2] if metric == 'gender' else 0
                value = float(.5 + (errors[s, simulated.index(metric)] if metric in moving else 0) + delta)
                for book in scored['sets'][name].values():
                    book[metric]['value'] = value

    for _ in range(1500):
        for scored, errors in zip(calibration, rng.normal(size=(20, 2, len(simulated))) * panel_sd):
            assign(scored, errors)
        floors = noise_floors(calibration)
        errors = rng.normal(size=(2, 8, 2, len(simulated))) * panel_sd
        for scored, error in zip(baseline['check'], errors[0]):
            assign(scored, error)
        for j, scenario in enumerate(scenarios):
            for scored, error in zip(candidate['check'], errors[1]):
                assign(scored, error, scenario)
            result = verdict(baseline, candidate, floors, 'character_recall')
            assert result['m'] == 8 and result['R'] == 20
            assert result['r'] == {'iterate': 4, 'check': 8}
            kept[j] += result['verdict'] == 'accept'
    percentages = [100 * count / 1500 for count in kept]
    expected = [2.8, 49.5, 93.6, 96.1, 0., 95.7, .6]
    assert percentages == pytest.approx(expected, abs=2.6)
    with capsys.disabled():
        print('\nSD .05, seed 161126, 1500 trials; keep rates (%): ' +
              ', '.join(f'{value:.2f}' for value in percentages))


@pytest.mark.parametrize('collision', ['arms', 'baseline-calibration', 'candidate-calibration'])
def test_attempt_refuses_reused_cache_tokens_in_new_directories(comparison, collision):
    baseline, candidate, floors = comparison
    if collision == 'arms':
        candidate['replicate'] = baseline['replicate']
    elif collision == 'baseline-calibration':
        baseline['replicate'] = floors['replicate_tokens'][0]
    else:
        candidate['replicate'] = floors['replicate_tokens'][0]
    with pytest.raises(ValueError, match='distinct replicate tokens'):
        verdict(baseline, candidate, floors, 'character_recall')


def test_noise_refuses_equivalent_string_and_numeric_tokens():
    with pytest.raises(ValueError, match='distinct'):
        noise_floors([run('a', replicate=1), run('b', replicate='1')])


def test_old_noise_without_tokens_requires_offline_regeneration(comparison):
    baseline, candidate, floors = comparison
    del floors['replicate_tokens']
    with pytest.raises(ValueError, match='regenerate'):
        verdict(baseline, candidate, floors, 'character_recall')


def test_duplicate_count_increase_blocks_even_when_share_falls(comparison):
    baseline, candidate, floors = comparison
    shift(candidate, 'iterate', 'duplicate_alias_count', 1)
    shift(candidate, 'iterate', 'duplicate_aliases', -.005)
    result = verdict(baseline, candidate, floors, 'character_recall')
    assert result['verdict'] == 'reject'
    clause, = [c for c in result['clauses'] if c['measure'] == 'duplicate_alias_count']
    assert clause['passed'] is False
