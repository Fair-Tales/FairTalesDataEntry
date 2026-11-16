"""Offline tuning decisions over numeric scores; no source or prediction reads."""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from statistics import mean, stdev

from text_content.evaluation import TuningLoopText as T
from .reporting import _SCAN_PRIMARIES, scan_metric
from .store import digest, run_lock

PRIMARIES = _SCAN_PRIMARIES
GUARDRAILS = ('groundedness', 'inserted_text_per_100', 'unsuccessful_pages_per_100')
MEASURES = (*PRIMARIES, 'alias_coverage', *GUARDRAILS)
DIRECTION = {name: (-1 if name in GUARDRAILS[1:] else 1) for name in MEASURES}
SETS = ('iterate', 'check')


def tuning_scores(scores, selections, *, run_id, configuration_id, replicate,
                  measurement_id, reference_hashes, cost=None):
    """Project scored books to numbers and ids for machine pairing, never names/text."""
    groups = {name: selections[key] for name, key in
              (('iterate', 'iterate55_ids'), ('check', 'check20_ids'))}
    ids = [row['id'] for row in scores]
    if len(set(ids)) != len(ids) or not set(ids) <= set(groups['iterate']) | set(groups['check']):
        raise ValueError(T.invalid_books)
    return {'version': 'tuning-scores-v1', 'run_id': run_id,
            'configuration_id': configuration_id, 'replicate': replicate,
            'measurement_id': measurement_id,
            'reference_hashes': reference_hashes,
            'cohorts': {name: {'books': len(group), 'ids_sha256': digest(sorted(group))}
                        for name, group in groups.items()},
            'sets': {name: {row['id']: {metric: scan_metric(row, metric) for metric in MEASURES}
                            for row in scores if row['id'] in group}
                     for name, group in groups.items()},
            'cost': cost}


def _validate(run):
    if run.get('version') != 'tuning-scores-v1' or any(not run.get(key) for key in
            ('run_id', 'configuration_id', 'replicate', 'measurement_id', 'cohorts')):
        raise ValueError(T.invalid_run)
    for name in SETS:
        if any(book_id not in run['reference_hashes'] for book_id in run['sets'][name]):
            raise ValueError(T.incompatible)
        for metrics in run['sets'][name].values():
            if set(metrics) != set(MEASURES):
                raise ValueError(T.invalid_metrics)
            for measure in metrics.values():
                for value in measure.values():
                    if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                        raise ValueError(T.invalid_metrics)


def _compatible(left, right):
    for key in ('measurement_id', 'cohorts', 'reference_hashes'):
        if left[key] != right[key]:
            raise ValueError(T.incompatible)
    for name in SETS:
        if left['sets'][name].keys() != right['sets'][name].keys():
            raise ValueError(T.unpaired)


@lru_cache(maxsize=128)
def t_quantile(probability, df):
    """Invert Student-t for integer df via the cosine-integral recurrence.

    With x = sqrt(df)*tan(theta), the density integral is proportional
    to integral(cos(theta)**(df-1)). Integration by parts reduces its power
    by two, ending at theta or sin(theta); bisection then finds x.
    """
    if not 0.5 <= probability < 1 or not isinstance(df, int) or df < 1:
        raise ValueError(T.invalid_quantile)
    coefficient = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(math.pi)

    def cdf(x):
        theta = math.atan(x / math.sqrt(df))
        sine, cosine = math.sin(theta), math.cos(theta)
        integrals = [theta, sine]
        for power in range(2, df):
            integrals.append((sine * cosine ** (power - 1) +
                              (power - 1) * integrals[power - 2]) / power)
        return .5 + coefficient * integrals[df - 1]

    low, high = 0., 1.
    while cdf(high) < probability:
        high *= 2
    for _ in range(60):
        middle = (low + high) / 2
        if cdf(middle) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _values(run, name, metric):
    return {book_id: float(scores[metric]['value']) if scores[metric]['value'] is not None else None
            for book_id, scores in run['sets'][name].items()}


def _complete(run, name):
    if digest(sorted(run['sets'][name])) != run['cohorts'][name]['ids_sha256']:
        raise ValueError(T.incomplete)


def noise_floors(runs):
    """Sample SD of R unchanged full-set means, with fingerprints for caching."""
    if not runs:
        raise ValueError(T.no_runs)
    for run in runs:
        _validate(run)
        _compatible(runs[0], run)
        for name in SETS:
            _complete(run, name)
    if len({run['configuration_id'] for run in runs}) != 1:
        raise ValueError(T.configuration_changed)
    if any(len({run[key] for run in runs}) != len(runs) for key in ('run_id', 'replicate')):
        raise ValueError(T.duplicate_replicates)
    result = {key: runs[0][key] for key in ('configuration_id', 'measurement_id', 'cohorts')}
    result.update(version='tuning-noise-v2', run_ids=[run['run_id'] for run in runs],
                  reference_sha256=digest(runs[0]['reference_hashes']),
                  R=len(runs), replicates=len(runs), sets={})
    for name in SETS:
        result['sets'][name] = {}
        for metric in MEASURES:
            values = [_values(run, name, metric) for run in runs]
            panels = [{i: v for i, v in value.items() if v is not None} for value in values]
            stable = all(panel.keys() == panels[0].keys() for panel in panels)
            means = [mean(panel.values()) for panel in panels if panel]
            available = stable and len(means) == len(runs) and len(runs) >= 2
            result['sets'][name][metric] = {
                'floor': stdev(means) if available else None, 'replicates': len(means),
                'books': len(panels[0]), 'panel_sha256': digest(sorted(panels[0])),
                'identical_sha256': digest(values[0]) if all(v == values[0] for v in values) else None,
                'reason': T.measured if available else T.no_floor}
    return result


def _arms(arm):
    """Accept one run, a run list, or separate Iterate/Check run lists."""
    if isinstance(arm, dict) and 'version' in arm:
        arm = [arm]
    return {name: arm[name] if isinstance(arm, dict) else arm for name in SETS}


def _average(runs, name):
    """Average within each book; never pool the numerator/denominator ratios."""
    books = {}
    for book_id in runs[0]['sets'][name]:
        books[book_id] = {}
        for metric in MEASURES:
            books[book_id][metric] = {}
            for key in ('value', 'numerator', 'denominator'):
                values = [run['sets'][name][book_id][metric][key] for run in runs]
                books[book_id][metric][key] = mean(values) if all(v is not None for v in values) else None
    return books


def _sum_known(values):
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _paired(before, after, metric):
    left = {i: row[metric]['value'] for i, row in before.items()}
    right = {i: row[metric]['value'] for i, row in after.items()}
    paired = [i for i in left if left[i] is not None and right[i] is not None]
    changed_eligibility = any((left[i] is None) != (right[i] is None) for i in left)
    counts = {side: {key: _sum_known(row[metric][key] for row in books.values())
                     for key in ('numerator', 'denominator')}
              for side, books in (('before', before), ('after', after))}
    return {'before': mean(left[i] for i in paired) if paired else None,
            'after': mean(right[i] for i in paired) if paired else None,
            'improvement': DIRECTION[metric] * mean(right[i] - left[i] for i in paired) if paired else None,
            'paired_books': len(paired), 'undefined_books': len(left) - len(paired),
            'changed_eligibility': changed_eligibility, 'counts': counts}


def verdict(baseline, candidate, floors, target):
    """Compare fresh arms using estimated difference noise and Student-t cutoffs."""
    if target not in MEASURES:
        raise ValueError(T.invalid_target)
    baseline, candidate = _arms(baseline), _arms(candidate)
    if any(not arm[name] for arm in (baseline, candidate) for name in SETS):
        raise ValueError(T.empty_arm)
    first = baseline['iterate'][0]
    for arm in (baseline, candidate):
        if len({run['configuration_id'] for runs in arm.values() for run in runs}) != 1:
            raise ValueError(T.arm_configuration)
        for name, runs in arm.items():
            if any(len({run[key] for run in runs}) != len(runs) for key in ('run_id', 'replicate')):
                raise ValueError(T.duplicate_replicates)
            for run in runs:
                _validate(run)
                if any(run[key] != first[key] for key in ('measurement_id', 'cohorts', 'reference_hashes')):
                    raise ValueError(T.incompatible)
                if run['sets'][name].keys() != arm[name][0]['sets'][name].keys():
                    raise ValueError(T.unpaired)
                _complete(run, name)
    r = {name: len(baseline[name]) for name in SETS}
    if any(len(candidate[name]) != r[name] for name in SETS):
        raise ValueError(T.unequal_arms)
    run_ids = [{run['run_id'] for runs in arm.values() for run in runs} for arm in (baseline, candidate)]
    if run_ids[0] & run_ids[1] or (run_ids[0] | run_ids[1]) & set((floors or {}).get('run_ids', [])):
        raise ValueError(T.fresh_runs)
    if floors and (any(floors.get(key) != first[key] for key in ('measurement_id', 'cohorts')) or
                   floors.get('reference_sha256') != digest(first['reference_hashes'])):
        raise ValueError(T.incompatible)
    R = (floors or {}).get('R', 0)
    averages = [{name: _average(arm[name], name) for name in SETS} for arm in (baseline, candidate)]
    figures = {name: {metric: _paired(averages[0][name], averages[1][name], metric)
                      for metric in MEASURES} for name in SETS}
    omitted = []
    for metric in MEASURES:
        identical = True
        for name in SETS:
            noise = (floors or {}).get('sets', {}).get(name, {}).get(metric, {})
            panels = [_values(run, name, metric) for run in baseline[name] + candidate[name]]
            eligible = [[i for i, value in panel.items() if value is not None] for panel in panels]
            figure = figures[name][metric]
            figure['changed_eligibility'] = (any(set(ids) != set(eligible[0]) for ids in eligible) or
                                             noise.get('panel_sha256') != digest(sorted(eligible[0])))
            identical &= (R >= 2 and noise.get('floor') is not None and
                          all(digest(panel) == noise.get('identical_sha256') for panel in panels))
        if identical:
            omitted.append(metric)
    rules = [('iterate', target, 'target_gain')]
    for name in SETS:
        rules.extend((name, metric, 'no_regression') for metric in
                     dict.fromkeys((*PRIMARIES, *(GUARDRAILS if name == 'iterate' else (target,))))
                     if (name, metric) != ('iterate', target))
    rules = [rule for rule in rules if rule[1] not in omitted]
    m = sum(rule == 'no_regression' for _, _, rule in rules)
    clauses = []
    for name, metric, rule in rules:
        figure = figures[name][metric]
        noise = (floors or {}).get('sets', {}).get(name, {}).get(metric, {})
        floor = noise.get('floor')
        passed, d, quantile, threshold = None, None, None, None
        reason = T.no_floor
        if (R >= 2 and floor is not None and isinstance(floor, (int, float)) and math.isfinite(floor)
                and floor >= 0 and noise.get('replicates') == R):
            d = floor * math.sqrt(2 / r[name])
            quantile = t_quantile(.975 if rule == 'target_gain' else 1 - .05 / m, R - 1)
            threshold = (1 if rule == 'target_gain' else -1) * quantile * d
            delta = figure['improvement']
            if delta is None or figure['changed_eligibility']:
                reason = T.undefined_measure
            else:
                # Decimal input scores often land one ulp across a boundary.
                equal = math.isclose(delta, threshold, rel_tol=1e-12, abs_tol=0)
                passed = (delta > threshold and not equal) if rule == 'target_gain' else (delta >= threshold or equal)
                reason = T.clause.format(improvement=delta, operator='>' if rule == 'target_gain' else '>=',
                                         threshold=threshold, result=T.passed if passed else T.failed)
        clauses.append({'set': name, 'measure': metric, 'rule': rule,
                        'floor': floor, 'd': d, 'quantile': quantile, 'threshold': threshold,
                        'passed': passed, 'reason': reason})
    outcome = (None if R < 2 or any(row['passed'] is None for row in clauses)
               else 'accept' if target not in omitted and all(row['passed'] for row in clauses) else 'reject')
    return {'verdict': outcome, 'target': target, 'R': R, 'r': r, 'm': m,
            'omitted_measures': omitted, 'clauses': clauses, 'figures': figures}


def _arm_cost(arm):
    runs = {run['run_id']: run for group in arm.values() for run in group}
    costs = [run.get('cost') for run in runs.values()]
    if any(cost is None for cost in costs):
        return None
    return {key: (mean(values) if key == 'per_book_usd' else sum(values))
            if all(v is not None for v in values) else None
            for key in costs[0] for values in [[cost.get(key) for cost in costs]]}


def append_attempt(path, baseline, candidate, floors, target, hypothesis, change):
    """One append per evaluated attempt, preserving rejected and unscorable rows."""
    if not hypothesis.strip() or not change.strip():
        raise ValueError(T.description_required)
    baseline, candidate = _arms(baseline), _arms(candidate)
    path = Path(path)
    with run_lock(path.parent):
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        accepted = sum(row['verdict'] == 'accept' for row in rows)
        if accepted >= 15 or len(rows) >= 30:
            raise ValueError(T.limit.format(accepted=accepted, attempts=len(rows)))
        result = verdict(baseline, candidate, floors, target)
        baseline_config = baseline['iterate'][0]['configuration_id']
        candidate_config = candidate['iterate'][0]['configuration_id']
        if rows:
            previous = rows[-1]
            side = 'candidate' if previous['verdict'] == 'accept' else 'baseline'
            incumbent = previous.get(f'{side}_configuration_id')
            if incumbent is None or baseline_config != incumbent:
                raise ValueError(T.incumbent)
        row = {'attempt': len(rows) + 1, 'accepted_changes': accepted + (result['verdict'] == 'accept'),
               'hypothesis': hypothesis, 'change': change, 'target': target,
               'baseline_configuration_id': baseline_config, 'candidate_configuration_id': candidate_config,
               'baseline_run_ids': {name: [run['run_id'] for run in runs] for name, runs in baseline.items()},
               'candidate_run_ids': {name: [run['run_id'] for run in runs] for name, runs in candidate.items()},
               **{key: result[key] for key in ('R', 'r', 'm', 'omitted_measures')},
               'noise_run_ids': (floors or {}).get('run_ids', []),
               'verdict': result['verdict'], 'clauses': result['clauses'],
               'counts': {}, 'primaries': {}, 'guardrails': {}, 'alias_coverage': {},
               'cost': {'before': _arm_cost(baseline), 'after': _arm_cost(candidate), 'judgement': T.cost}}
        for name, figures in result['figures'].items():
            row['counts'][name] = {metric: figure['counts'] for metric, figure in figures.items()}
            for section, metrics in (('primaries', PRIMARIES), ('guardrails', GUARDRAILS),
                                     ('alias_coverage', ('alias_coverage',))):
                row[section][name] = {metric: {key: value for key, value in figures[metric].items() if key != 'counts'}
                                      for metric in metrics}
        encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n'
        with path.open('a', encoding='utf-8') as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    return row


def format_verdict(result):
    lines = [T.verdict.format(verdict=result['verdict'] or T.no_verdict, target=result['target'])
             + T.repetitions.format(**result),
             T.omitted.format(measures=', '.join(result['omitted_measures']) or T.none)]
    for clause in result['clauses']:
        lines.append(T.clause_line.format(**clause))
    return '\n'.join(lines)
