"""Offline tuning decisions over numeric scores; no source or prediction reads."""
from __future__ import annotations

import json
import math
import os
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


def noise_floors(runs):
    """Sample SD of unchanged replicate set means, not per-book SD or SE."""
    if not runs:
        raise ValueError(T.no_runs)
    for run in runs:
        _validate(run)
        _compatible(runs[0], run)
    if len({run['configuration_id'] for run in runs}) != 1:
        raise ValueError(T.configuration_changed)
    if any(len({run[key] for run in runs}) != len(runs) for key in ('run_id', 'replicate')):
        raise ValueError(T.duplicate_replicates)
    result = {key: runs[0][key] for key in ('configuration_id', 'measurement_id', 'cohorts')}
    result.update(version='tuning-noise-v1', run_ids=[run['run_id'] for run in runs],
                  replicates=len(runs), sets={})
    for name in SETS:
        result['sets'][name] = {}
        for metric in MEASURES:
            panels = [{book_id: scores[metric]['value'] for book_id, scores in run['sets'][name].items()
                       if scores[metric]['value'] is not None} for run in runs]
            stable = all(panel.keys() == panels[0].keys() for panel in panels)
            means = [mean(panel.values()) for panel in panels if panel]
            available = stable and len(means) == len(runs) and len(runs) >= 2
            result['sets'][name][metric] = {
                'floor': stdev(means) if available else None, 'replicates': len(means),
                'books': len(panels[0]), 'panel_sha256': digest(sorted(panels[0])),
                'reason': T.measured if available else T.no_floor}
    return result


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
    """Evaluate every clause; missing evidence returns verdict=None, never a pass."""
    if target not in MEASURES:
        raise ValueError(T.invalid_target)
    for run in (baseline, candidate):
        _validate(run)
    _compatible(baseline, candidate)
    for name in SETS:
        if digest(sorted(baseline['sets'][name])) != baseline['cohorts'][name]['ids_sha256']:
            raise ValueError(T.incomplete)
    if floors and any(floors.get(key) != baseline[key] for key in ('measurement_id', 'cohorts')):
        raise ValueError(T.incompatible)
    figures = {name: {metric: _paired(baseline['sets'][name], candidate['sets'][name], metric)
                      for metric in MEASURES} for name in SETS}
    clauses = []
    for name in SETS:
        rules = [(metric, 'no_regression') for metric in
                 dict.fromkeys((*PRIMARIES, *(GUARDRAILS if name == 'iterate' else (target,))))]
        if name == 'iterate':
            rules.insert(0, (target, 'target_gain'))
        for metric, rule in rules:
            figure = figures[name][metric]
            noise = (floors or {}).get('sets', {}).get(name, {}).get(metric, {})
            floor = noise.get('floor')
            passed = None
            reason = T.no_floor
            if (floor is not None and isinstance(floor, (int, float)) and math.isfinite(floor)
                    and floor >= 0 and noise.get('replicates', 0) >= 2):
                delta = figure['improvement']
                if delta is None or figure['changed_eligibility']:
                    reason = T.undefined_measure
                else:
                    threshold = 2 * floor if rule == 'target_gain' else -floor
                    # Decimal input scores often land one ulp across a boundary.
                    equal = math.isclose(delta, threshold, rel_tol=1e-12, abs_tol=0)
                    passed = (delta > threshold and not equal) if rule == 'target_gain' else (delta >= threshold or equal)
                    reason = T.clause.format(improvement=delta, operator='>' if rule == 'target_gain' else '>=',
                                             threshold=threshold, result=T.passed if passed else T.failed)
            clauses.append({'set': name, 'measure': metric, 'rule': rule,
                            'floor': floor, 'passed': passed, 'reason': reason})
    outcome = (None if any(row['passed'] is None for row in clauses)
               else 'accept' if all(row['passed'] for row in clauses) else 'reject')
    return {'verdict': outcome, 'target': target, 'clauses': clauses, 'figures': figures}


def append_attempt(path, baseline, candidate, floors, target, hypothesis, change):
    """One append per evaluated attempt, preserving rejected and unscorable rows."""
    if not hypothesis.strip() or not change.strip():
        raise ValueError(T.description_required)
    path = Path(path)
    with run_lock(path.parent):
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        accepted = sum(row['verdict'] == 'accept' for row in rows)
        if accepted >= 15 or len(rows) >= 30:
            raise ValueError(T.limit.format(accepted=accepted, attempts=len(rows)))
        if rows:
            previous = rows[-1]
            incumbent = previous['candidate_run_id'] if previous['verdict'] == 'accept' else previous['baseline_run_id']
            if baseline['run_id'] != incumbent:
                raise ValueError(T.incumbent)
        result = verdict(baseline, candidate, floors, target)
        row = {'attempt': len(rows) + 1, 'accepted_changes': accepted + (result['verdict'] == 'accept'),
               'hypothesis': hypothesis, 'change': change, 'target': target,
               'baseline_run_id': baseline['run_id'], 'candidate_run_id': candidate['run_id'],
               'noise_run_ids': (floors or {}).get('run_ids', []),
               'verdict': result['verdict'], 'clauses': result['clauses'],
               'counts': {}, 'primaries': {}, 'guardrails': {}, 'alias_coverage': {},
               'cost': {'before': baseline['cost'], 'after': candidate['cost'], 'judgement': T.cost}}
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
    lines = [T.verdict.format(verdict=result['verdict'] or T.no_verdict, target=result['target'])]
    for clause in result['clauses']:
        lines.append(T.clause_line.format(**clause))
    return '\n'.join(lines)
