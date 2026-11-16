"""Portable local reports from cached artifacts; importing this module is offline."""

from __future__ import annotations

import csv
import difflib
import html
import json
import os
import tempfile
from pathlib import Path

from text_content.alias_metrics import AliasMetrics
from text_content.evaluation import EvaluationErrors

from .scoring import aggregate, score_book
from .runner import metadata_with_lookup_outcome


def _atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = handle.name
    os.replace(temporary, path)


def _csv(path, records, fields):
    path = Path(path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                             if isinstance(value, (dict, list)) else value
                             for key, value in record.items()})
        temporary = handle.name
    os.replace(temporary, path)


def _pretty(value):
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _image(path, base, output, label):
    if not path or "://" in str(path):
        return ""
    image_path = Path(path)
    if not image_path.is_absolute():
        image_path = base / image_path
    source = os.path.relpath(image_path.resolve(), output.resolve())
    return f'<figure><img loading="lazy" src="{html.escape(source, quote=True)}" alt="{html.escape(label)}"><figcaption>{html.escape(label)}</figcaption></figure>'


def snapshot_audit_provenance(manifest):
    """Keep missing legacy audit provenance unknown, even if consistency was asserted."""
    recorded = manifest.get("audits")
    recorded = recorded if isinstance(recorded, dict) else {}
    audits = {name: value if isinstance(value, bool) else None
              for name in ("source_changes", "membership")
              for value in (recorded.get(name),)}
    if any(value is None for value in audits.values()):
        status = "unknown"
    elif all(audits.values()):
        status = "audited"
    elif any(audits.values()):
        status = "partially_audited"
    else:
        status = "not_audited"
    consistent = manifest.get("source_consistent")
    if consistent is not False and not (status == "audited" and consistent is True):
        consistent = None
    return {"snapshot_audits": audits, "snapshot_audit_status": status,
            "snapshot_source_consistent": consistent}


def snapshot_audit_description(manifest):
    """Describe the audit coverage and consistency without claiming unrun checks passed."""
    provenance = snapshot_audit_provenance(manifest)
    audits = provenance.get("snapshot_audits", {})
    states = {True: "ran", False: "not run", None: "unknown"}
    consistency = {True: "verified", False: "changes detected", None: "not verified"}
    status = str(provenance.get("snapshot_audit_status", "unknown")).replace("_", " ").upper()
    return (f"Snapshot audits: {status}. "
            f"Source-change audit: {states.get(audits.get('source_changes'), 'unknown')}; "
            f"membership audit: {states.get(audits.get('membership'), 'unknown')}. "
            f"Source consistency: {consistency.get(provenance.get('snapshot_source_consistent'), 'not verified')}.")


def report_calls(calls, loader):
    """Check-book calls stay in the ledger, never in readable tune reports."""
    calls = list(calls)
    if loader.cohort != "tune":
        return calls
    visible = loader.iterate | loader.protected
    rows = [call for call in calls if call.get("book_id") in visible]
    omitted = [call for call in calls if call.get("book_id") not in visible]
    if omitted:
        # Keep totals when a portable calls.json is scored without its ledger.
        # This row can safely pass through the same filter again on replay.
        rows.append({"aggregate_only": True,
                     "call_count": sum(call.get("call_count", 1) for call in omitted),
                     "cost_usd": sum(call.get("cost_usd") or 0 for call in omitted),
                     "reserved_usd": sum(call.get("reserved_usd") or 0 for call in omitted
                                         if call.get("state") not in {"completed", "settled", "failed"}),
                     "state": "aggregate"})
    return rows


def _figures(scores, protocol):
    """Aggregate without the diagnostic IDs or source descriptions."""
    result = aggregate(scores, protocol)
    for section, key in (("wordless", "books"), ("characters", "ambiguous_books"),
                         ("challenge", "books")):
        result[section][key + "_count"] = len(result[section].pop(key))
    result["metadata_lookup_failures"].pop("book_ids")
    result["ocr"].pop("provenance_counts")
    return result


def write_reports(manifest, predictions, out, protocol=None, selection=None, calls=None, *, loader=None):
    """Write summary.json, books.csv, pages.csv, calls.csv and inspection.html.

    ``manifest['_snapshot_dir']`` and ``predictions['_run_dir']`` resolve local
    image paths. The original manifests need not be changed on disk. Selection
    rows use selected=[{id,role,stratum,inclusion_probability}] and book_ids.
    All filtering uses the frozen selection, never prediction availability.
    Tune check books contribute figures only; their rows remain in scoring
    memory and are never copied to reports, including the HTML summary.
    """
    from .tuning import current_loader
    from text_content.tuning import TuningErrors
    loader = loader or current_loader()
    if loader is None:
        raise ValueError(TuningErrors.missing_record)
    protocol = protocol or {}
    selection = selection or {}
    calls = list(calls or [])
    output = Path(out)
    output.mkdir(parents=True, exist_ok=True)
    selected_rows = selection.get("selected", selection.get("books", []))
    sampling = {record.get("id"): record for record in selected_rows if isinstance(record, dict)}
    ids = selection.get("book_ids")
    if ids is None and sampling:
        ids = list(sampling)
    ids = set(ids) if ids is not None else None
    books = [book for book in manifest.get("books", []) if ids is None or book.get("id") in ids]
    if ids is not None and ids - {book.get("id") for book in books}:
        raise ValueError("Selection contains books absent from the snapshot")
    predicted_books = predictions.get("books", {})
    scores = []
    for book in books:
        loader.load([book.get("id")], "report", lambda: None)
        prediction = predicted_books.get(book.get("id")) or {}
        stage = prediction.get("metadata") or {}
        if stage.get("status") == "completed":
            with loader.activate():
                prediction = {**prediction, "metadata": {**stage, "value": metadata_with_lookup_outcome(
                    stage.get("value") or {}, predictions.get("_run_dir"))}}
        score = score_book(book, prediction, protocol)
        sample = sampling.get(book.get("id"), {})
        for key in ("role", "stratum", "inclusion_probability", "holdout_group"):
            if key in sample:
                score[key] = sample[key]
        scores.append(score)
    tune = loader.cohort == "tune"
    hidden = loader.check if tune else set()
    visible_scores = [score for score in scores if score["id"] not in hidden]
    summary = _figures(scores, protocol) if tune else aggregate(scores, protocol)
    if tune:
        summary["cohort_aggregates"] = {
            name: _figures([score for score in scores if score["id"] in group], protocol)
            for name, group in (("iterate", loader.iterate), ("check", loader.check))}
        released = [score for score in scores if score["id"] in loader.protected]
        if released:
            summary["cohort_aggregates"]["released_protected"] = _figures(released, protocol)
    report_selection = selection
    exclusions = manifest.get("excluded_books", manifest.get("exclusions", []))
    if tune:
        # Selection inventories include titles and unselected books too. Keep
        # the original selection in run.json, and report only its counts.
        report_selection = {key: selection.get(key) for key in (
            "version", "snapshot_id", "seed", "requested_limit", "method") if key in selection}
        report_selection["selected_books"] = len(books)
        exclusions = {"count": len(exclusions)}
    summary.update(snapshot_audit_provenance(manifest))
    summary.update({"snapshot_id": manifest.get("snapshot_id"), "run_id": predictions.get("run_id"),
                    "cohort": loader.cohort,
                    "selection": report_selection, "snapshot_exclusions": exclusions,
                    "adjudication": manifest.get("adjudication"),
                    "equivalence_version": protocol.get("equivalence_version"),
                    "settled_cost_usd": sum(call.get("cost_usd") or 0 for call in calls),
                    "outstanding_reservations_usd": sum(call.get("reserved_usd") or 0 for call in calls
                                                        if call.get("state") not in {"completed", "settled", "failed"})})
    from .comparison import book_comparison_record, comparison_protocol
    identity = comparison_protocol(protocol, manifest)
    summary["comparison_protocol"] = identity
    summary["book_scores"] = [book_comparison_record(score, protocol_identity=identity) for score in visible_scores]
    cohort_path = Path(__file__).parent / "evidence" / "dev20-v1" / "empty_reference_cohort.json"
    cohort = (loader.source_json(cohort_path, 'historical_labels', snapshot_id=manifest['snapshot_id'])
              if manifest.get('snapshot_id') and cohort_path.exists() else None)
    if cohort and cohort.get("snapshot_id") == manifest.get("snapshot_id"):
        from .monitoring import monitor_empty_reference
        summary["empty_reference_monitoring"] = monitor_empty_reference(manifest, predictions, cohort, selected_ids=ids)
        if tune:
            monitoring = summary["empty_reference_monitoring"]
            monitoring["records"] = [record for record in monitoring["records"] if record["book_id"] not in hidden]
    _atomic_text(output / "summary.json", json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    _atomic_text(output / "book_scores.json", json.dumps(visible_scores, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    report_protocol = protocol
    if tune:
        from .store import digest
        report_protocol = dict(protocol)
        for key in ("reviewed_equivalences", "publication_equivalences"):
            if key in report_protocol:
                report_protocol[key + "_sha256"] = digest(report_protocol.pop(key))
    _atomic_text(output / "protocol.json", json.dumps(report_protocol, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    book_rows, page_rows, sections = [], [], []
    snapshot_root = Path(manifest.get("_snapshot_dir", "."))
    run_root = Path(predictions.get("_run_dir", "."))
    for book, score in zip(books, scores):
        book_id = book.get("id")
        if book_id in hidden:
            continue
        actual_book = predicted_books.get(book_id, {})
        book_cost = sum(call.get("cost_usd") or 0 for call in calls
                        if call.get("book_id") == book_id or str(call.get("label", "")).startswith(f"{book_id}/"))
        book_rows.append({"book_id": book_id, "title": book.get("title"), "role": score.get("role", "descriptive"),
                          "pages": len(score["pages"]), "ocr_f1": score["ocr"]["f1"], "ocr": score["ocr"],
                          "wordless": score["wordless"], "metadata": score["metadata"], "characters": score["characters"],
                          "alias_foundation": score["alias_foundation"],
                          "optional_stages": score.get("optional_stages"), "story_context_fallback": score.get("story_context_fallback"),
                          "reference_hash": score["reference_hash"], "cost_usd": book_cost})
        parts = [f'<section><h2>{html.escape(str(book.get("title", book_id)))}</h2>',
                 f'<p>Book: {html.escape(str(book_id))}; settled cost: ${book_cost:.4f}</p>',
                 f'<details><summary>Book scores, metadata and character attachment</summary><pre>{_pretty({key: value for key, value in score.items() if key != "pages"})}</pre></details>']
        for page, page_score in zip(book.get("pages", []), score["pages"]):
            actual = actual_book.get("pages", {}).get(page.get("id"), {})
            expected = page.get("reference", {}).get("text", {}).get("value", "")
            actual_text = actual.get("text", "")
            page_rows.append({"book_id": book_id, "page_id": page.get("id"), "page_number": page.get("page_number"),
                              "reference_status": page_score["ocr"]["reference_status"],
                              "prediction_status": page_score["ocr"]["prediction_status"],
                              "subgroup": page_score["ocr"]["subgroup"], "f1": page_score["ocr"]["f1"],
                              "classification_source": page_score.get("classification_source"),
                              "cer": page_score["ocr"]["cer"], "wer": page_score["ocr"]["wer"],
                              "reference_text": expected, "predicted_text": actual_text,
                              "ocr": page_score["ocr"], "story": page_score["story"], "error": actual.get("error")})
            difference = "\n".join(difflib.unified_diff(str(expected or "").splitlines(), str(actual_text or "").splitlines(),
                                                       fromfile="reference", tofile="prediction", lineterm=""))
            image = page.get("image", {}) or {}
            image_path = image.get("path") if isinstance(image, dict) else image
            parts.extend([f'<article><h3>Page {html.escape(str(page.get("page_number")))}</h3>',
                          f'<p>{html.escape(page_score["ocr"]["subgroup"])}; {html.escape(page_score["ocr"]["prediction_status"])}</p>',
                          '<div class="images">', _image(image_path, snapshot_root, output, "Original image"),
                          _image(actual.get("corrected_path"), run_root, output, "Corrected image"), '</div>',
                          '<div class="texts"><div><h4>Reference</h4>', f'<pre>{html.escape(str(expected or ""))}</pre></div>',
                          f'<div><h4>Prediction</h4><pre>{html.escape(str(actual_text or ""))}</pre></div></div>',
                          f'<details><summary>Token omissions/extras, ordered differences, story and eligibility</summary><pre>{_pretty(page_score)}</pre><pre>{html.escape(difference)}</pre></details>',
                          f'<pre>{html.escape(str(actual.get("error") or ""))}</pre></article>'])
        parts.append("</section>")
        sections.append("\n".join(parts))
    _csv(output / "books.csv", book_rows, ["book_id", "title", "role", "pages", "ocr_f1", "ocr", "wordless", "metadata", "characters", "reference_hash", "cost_usd", "alias_foundation", "optional_stages", "story_context_fallback"])
    _csv(output / "pages.csv", page_rows, ["book_id", "page_id", "page_number", "reference_status", "prediction_status", "subgroup", "classification_source", "f1", "cer", "wer", "reference_text", "predicted_text", "ocr", "story", "error"])
    visible_calls = report_calls(calls, loader)
    call_fields = sorted({key for call in visible_calls for key in call}) or ["key", "run_id", "state", "reserved_usd", "cost_usd", "label", "error"]
    _csv(output / "calls.csv", visible_calls, call_fields)
    document = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\' file: data:; style-src \'unsafe-inline\'">'
                '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pilot reference agreement</title>'
                '<style>body{font:16px system-ui;max-width:1200px;margin:auto;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
                'article{border-top:1px solid #bbb;padding:16px 0}.images,.texts{display:flex;gap:20px;flex-wrap:wrap}'
                '.texts>div{flex:1;min-width:260px}figure{margin:0;max-width:46%}img{max-width:100%;max-height:550px}details{padding:12px 0}</style></head><body>'
                '<h1>Pilot reference-agreement inspection</h1>'
                '<p>Imported OCR, cleaning and some metadata are AI-derived. These results measure agreement, not independently verified accuracy. '
                'Wordless agreement has a separate denominator. Disputed wordless pages require human adjudication. '
                'Failed or unrun eligible text recovers zero tokens; incomplete work remains visible.</p>'
                f'<p>{html.escape(AliasMetrics.report_intro)}</p>'
                f'<p><strong>{html.escape(snapshot_audit_description(manifest))}</strong></p>'
                f'<details open><summary>Run summary and denominators</summary><pre>{_pretty(summary)}</pre></details>'
                + "\n".join(sections) + '</body></html>')
    _atomic_text(output / "inspection.html", document)
    return summary


# (path, score, numerator, denominator, scale). Text accuracy is an edit score,
# so its pooled edit components are reported separately, never as token recall.
_SCAN_METRICS = {
    'text_accuracy': ((), 'accuracy', None, 'reference_tokens', 1),
    'character_recall': (('characters',), 'tolerant_recall', 'tolerant_numerator', 'denominator', 1),
    **{field: (('attributes', field), 'recall', 'numerator', 'denominator', 1)
       for field in ('gender', 'human', 'protagonist')},
    'strict_character_recall': (('characters',), 'strict_recall', 'strict_numerator', 'denominator', 1),
    **{'strict_' + field: (('attributes', field), 'strict_recall', 'strict_numerator', 'denominator', 1)
       for field in ('gender', 'human', 'protagonist')},
    **{name: (('aliases', section), score, 'numerator', 'denominator', 1)
       for name, section, score in (
           ('alias_coverage', 'explicit_alias_coverage', 'coverage'),
           ('alias_recall', 'alias_recall', 'recall'),
           ('additional_aliases', 'additional_aliases', 'share'),
           ('groundedness', 'groundedness', 'share'))},
    'additional_groundedness': (('aliases', 'additional_aliases', 'grounding'), 'share', 'numerator', 'denominator', 1),
    'duplicate_aliases': (('aliases', 'additional_aliases', 'duplicates'), 'share', 'numerator', 'denominator', 1),
    'inserted_text_per_100': ((), 'inserted_text_per_100_reference_tokens', 'insertions', 'reference_tokens', 100),
    'unsuccessful_pages_per_100': (('page_failures',), 'failed_pages_per_100', 'failed', 'attempted', 100),
}
_SCAN_PRIMARIES = ('text_accuracy', 'character_recall', 'gender', 'human', 'protagonist')


def scan_metric(book, name):
    """Numeric projection shared by cohort summaries and tuning comparisons."""
    path, value_key, numerator_key, denominator_key, _ = _SCAN_METRICS[name]
    measure = book
    for key in path:
        measure = measure.get(key, {})
    value = measure.get(value_key)
    if book.get('status') == 'unavailable' and (name in _SCAN_PRIMARIES or name.startswith('strict_')):
        value = 0.0
    return {'value': value, 'numerator': measure.get(numerator_key),
            'denominator': measure.get(denominator_key)}


def summarize_scan_scores(scores, selections, *, cohort):
    """Count-only summary of already scored books; never opens source material."""
    from collections import Counter
    from .statistics import book_draws, bootstrap_books
    from text_content.evaluation import ScanSummaryText as T

    scores = sorted(scores, key=lambda row: row['id'])
    ids = [row['id'] for row in scores]
    if len(set(ids)) != len(ids):
        raise ValueError(EvaluationErrors.summary_duplicate_books)
    strata = selections['book_strata']
    if any(i not in strata for i in ids):
        raise ValueError(EvaluationErrors.summary_missing_strata)
    if cohort == 'proof' and set(ids) != set(selections['imported80_ids']):
        raise ValueError(EvaluationErrors.summary_partial_proof)

    def analysis(books, names, ranged):
        records = [{'id': book['id'], 'stratum': strata[book['id']]} for book in books]
        draws = book_draws(records)
        result = {'books': len(books), 'unavailable_books': sum(book.get('status') == 'unavailable' for book in books),
                  'strata': dict(sorted(Counter(row['stratum'] for row in records).items())),
                  'metrics': {}}
        for name in names:
            _, _, numerator_key, _, scale = _SCAN_METRICS[name]
            values, denominators, numerators = [], [], []
            for book, record in zip(books, records):
                measure = scan_metric(book, name)
                values.append({**record, 'value': measure['value']})
                denominators.append(measure['denominator'])
                if numerator_key:
                    numerators.append(measure['numerator'])
            usable = [row['value'] for row in values if row['value'] is not None]
            known_items = [n for n in denominators if n is not None]
            known_numerators = [n for n in numerators if n is not None]
            items = sum(known_items) if known_items else None
            numerator = sum(known_numerators) if known_numerators else None
            metric = {'mean': sum(usable) / len(usable) if usable else None,
                      'books': len(usable), 'undefined_books': len(books) - len(usable),
                      'items': items, 'unknown_item_books': denominators.count(None),
                      'numerator': numerator,
                      'pooled': scale * numerator / items if numerator is not None and items else None}
            if name in ranged:
                interval = bootstrap_books(values, repeats=20000, seed=20261110, draws=draws)
                metric['range_95'] = interval['interval_95']
                if usable and all(value == 1 for value in usable):
                    metric['qualification'] = T.perfect
            result['metrics'][name] = metric
        return result

    result = {'version': 'scan-cohort-summary-v1', 'cohort': cohort,
              'method': {'repeats': 20000, 'seed': 20261110, 'resampling_unit': 'book',
                         'shared_draws': True, 'weighting': 'equal_book', 'percentiles': [0.025, 0.975],
                         'interpolation': 'linear'},
              'qualification': T.scope, 'undefined_policy': T.undefined,
              'primary': analysis(scores, _SCAN_METRICS, (*_SCAN_PRIMARIES, 'alias_recall'))}
    if cohort == 'proof':
        result['sensitivities'] = {}
        for field in ('heldout_author_shared_ids', 'heldout_alias_quoted_ids'):
            excluded = set(selections[field])
            remaining = [book for book in scores if book['id'] not in excluded]
            sensitivity = analysis(remaining, (*_SCAN_PRIMARIES, 'alias_recall'), (*_SCAN_PRIMARIES, 'alias_recall'))
            sensitivity.update(excluded_books=len(scores) - len(remaining), qualification=T.remaining)
            result['sensitivities'][field] = sensitivity
    else:
        result['sensitivities'] = {'status': 'not_applicable', 'qualification': T.not_applicable}
    return result


def scan_summary_markdown(summary):
    """Five plain primary lines, followed by strict/descriptive and sensitivities."""
    from text_content.evaluation import ScanSummaryText as T

    def number(value, scale=1):
        if value is None:
            return T.unavailable
        return f'{value * 100:.2f}%' if scale == 1 else f'{value:.4f}'

    def lines(analysis, names):
        output = [T.counts.format(books=analysis['books'], unavailable=analysis['unavailable_books'],
                                 strata=analysis['strata'])]
        for name in names:
            metric = analysis['metrics'][name]
            scale = _SCAN_METRICS[name][-1]
            interval = metric.get('range_95')
            values = dict(label=T.labels[name], mean=number(metric['mean'], scale),
                          interval=f'[{number(interval[0])}, {number(interval[1])}]' if interval else T.no_range,
                          books=metric['books'], items=metric['items'] if metric['items'] is not None else T.unavailable,
                          undefined=metric['undefined_books'], unknown=metric['unknown_item_books'],
                          numerator=metric['numerator'] if metric['numerator'] is not None else T.unavailable)
            output.append((T.line if name in _SCAN_PRIMARIES else T.descriptive_line).format(**values))
            if name not in _SCAN_PRIMARIES and 'range_95' in metric:
                output[-1] += ' ' + T.range_clause.format(**values)
            if metric.get('qualification'):
                output.append(metric['qualification'])
        return output

    output = [T.title, summary['qualification'], summary['undefined_policy'], T.primary,
              *lines(summary['primary'], _SCAN_PRIMARIES), T.descriptive,
              *lines(summary['primary'], [name for name in _SCAN_METRICS if name not in _SCAN_PRIMARIES])]
    if summary['sensitivities'].get('status') == 'not_applicable':
        output.append(T.not_applicable)
    else:
        for name, analysis in summary['sensitivities'].items():
            output.extend([T.sensitivity.format(name=T.sensitivity_names[name], excluded=analysis['excluded_books']),
                           analysis['qualification'], *lines(analysis, analysis['metrics'])])
    return '\n\n'.join(output) + '\n'
