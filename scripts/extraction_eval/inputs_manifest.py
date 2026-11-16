"""Frozen file pairs for whole-book scoring; no page-to-page correspondence."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from .json_sources import JsonSources
from .reference_inputs import (DEFAULT_REFERENCE_ROOT, PDFTOTEXT_ARGS, UnmappedReference,
                               _load_reference, _text_hash, load_reference_file,
                               reference_metadata)
from .scan_inputs import (DEFAULT_SCAN_ROOT, UnmappedScan, _loader, _snapshot_titles,
                          load_scan, load_scan_file, scan_manifest, scan_metadata)
from .store import atomic_json, read_json
from .tuning import TuningLoader


def build_manifest(snapshot_dir, book_ids, scan_root=DEFAULT_SCAN_ROOT,
                   reference_root=DEFAULT_REFERENCE_ROOT, *, published_path=None,
                   overrides=None):
    ids = sorted(set(book_ids))
    titles = _snapshot_titles(snapshot_dir, ids, 'inputs_manifest')
    books = []
    for book_id in ids:
        scan = reference = None
        reasons = []
        if book_id not in titles:
            reasons.append('missing_snapshot_id')
        else:
            try:
                scan = scan_metadata(load_scan(book_id, titles[book_id], scan_root,
                                               overrides=overrides))
            except UnmappedScan as exc:
                reasons.append(f'scan:{exc.reason}')
            try:
                reference = reference_metadata(_load_reference(
                    book_id, titles[book_id], reference_root, published_path, overrides))
            except UnmappedReference as exc:
                reasons.append(f'reference:{exc.reason}')
        pairing_reason = 'whole_book_text'
        if reasons:
            pairing_reason = 'unavailable'
        elif reference['page_count'] is None:
            pairing_reason = 'published_text'
        elif scan['page_count'] != reference['page_count']:
            pairing_reason = 'unequal_page_counts'
        books.append({'id': book_id, 'scan': scan, 'reference': reference,
                      'status': 'unavailable' if reasons else 'ready',
                      'reason': '; '.join(reasons) if reasons else None,
                      'pairing': 'book_level_only', 'pairing_reason': pairing_reason})
    return {'version': 1, 'cohort': _loader().cohort,
            'pdftotext_args': list(PDFTOTEXT_ARGS), 'books': books}


def _rows(inputs):
    books = inputs['books']
    ids = [row['id'] for row in books]
    _loader().load(ids, 'inputs_manifest', lambda: None)
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate inputs manifest book id')
    if inputs.get('version') != 1 or inputs.get('cohort') != _loader().cohort:
        raise ValueError('Inputs manifest version or cohort differs')
    if inputs.get('pdftotext_args') != list(PDFTOTEXT_ARGS):
        raise ValueError('Inputs manifest reference extraction settings differ')
    for row in books:
        if row['status'] not in {'ready', 'unavailable'} or row['pairing'] != 'book_level_only':
            raise ValueError(f"Invalid input status/pairing: {row['id']}")
        if row['status'] == 'ready' and (not row['scan'] or not row['reference']):
            raise ValueError(f"Ready book lacks scan/reference: {row['id']}")
        if row['status'] == 'unavailable' and not row.get('reason'):
            raise ValueError(f"Unavailable book lacks reason: {row['id']}")
        for kind in ('scan', 'reference'):
            if row[kind] and row[kind]['id'] != row['id']:
                raise ValueError(f"Input file belongs to another id: {row['id']}")
    return {row['id']: row for row in books}


def load_manifest(path, book_ids=None):
    """Admit requested ids before decoding their rows; never silently drop one."""
    ids = sorted(_loader().allowed if book_ids is None else set(book_ids))
    _loader().load(ids, 'inputs_manifest', lambda: None)
    with JsonSources(path) as source:
        top = source.members(source.root)
        result = {key: source.decode(top[key]) for key in ('version', 'cohort', 'pdftotext_args')}
        result['books'] = []
        for span in source.items(top['books']):
            fields = source.members(span)
            book_id = source.decode(fields['id'])
            if book_id in ids:
                result['books'].append(_loader().load(
                    [book_id], 'inputs_manifest', lambda: source.decode(span)))
    present = {row['id'] for row in result['books']}
    if present != set(ids):
        raise ValueError(f'Inputs manifest missing requested ids: {sorted(set(ids) - present)}')
    _rows(result)
    return result


def extraction_manifest(snapshot, inputs):
    rows = _rows(inputs)
    by_id = {book['id']: book for book in snapshot['books']}
    books = []
    for book_id, row in rows.items():
        book = by_id.get(book_id, {'id': book_id})
        if row['status'] == 'ready':
            book = scan_manifest({'books': [book]}, [row['scan']])['books'][0]
        else:
            book = {**book, 'pages': []}
        books.append({**book, 'input_status': row['status'], 'input_reason': row['reason']})
    return {**snapshot, 'books': books}


def _verify_metadata(actual, expected):
    frozen = {key: value for key, value in expected.items()
              if key not in {'resolved_by', 'resolution_reason'}}
    if actual != frozen:
        raise ValueError(f"Input file changed since manifest: {expected['id']}")


def load_manifest_scan(row, scan_root=DEFAULT_SCAN_ROOT):
    if row['status'] != 'ready':
        raise ValueError(f"Unavailable scan input: {row['id']}")
    scan = load_scan_file(row['id'], row['scan']['scan_file'], scan_root)
    _verify_metadata(scan_metadata(scan), row['scan'])
    return scan


def _manifest_reference(row, reference_root, published_path):
    expected = row['reference']
    if expected['source'] == 'word_export':
        reference = load_reference_file(row['id'], expected['reference_file'], reference_root)
        _verify_metadata(reference_metadata(reference), expected)
        return '\n'.join(reference['pages'])
    if published_path is None or Path(published_path).name != expected['reference_file']:
        raise ValueError('Frozen published reference requires its recorded file')

    def read():
        with JsonSources(published_path) as source:
            top = source.members(source.root)
            texts = source.members(top['Text'])
            text = source.decode(texts[expected['published_row']])
        if (not isinstance(text, str) or _text_hash(text) != expected['text_sha256']
                or len(text) != expected['text_char_count']):
            raise ValueError(f"Published reference changed since manifest: {row['id']}")
        return text
    return _loader().load([row['id']], 'reference', read)


def scoring_inputs(inputs, predictions, reference_root=DEFAULT_REFERENCE_ROOT, *,
                   published_path=None, text_output='stored'):
    """A5a's input contract, NOT a metric. One row per book, even without output.

    Join all ordered pages with newlines (no story/wordless filtering). Failed
    pages contribute empty text and their numbers; A5a measures the omissions.
    Unavailable books must use forced_score=0 and remain in the denominator.
    """
    from .scoring import TEXT_OUTPUTS, prediction_page_text, score_page_failures
    from .outcomes import normalize_outcome
    if text_output not in TEXT_OUTPUTS:
        raise ValueError(f'Unknown text output: {text_output}')
    rows = _rows(inputs)
    result = []
    for book_id, row in rows.items():
        item = {'id': book_id, 'status': row['status'], 'reason': row['reason'],
                'reference_text': None, 'prediction_text': '', 'failed_pages': [],
                'page_failures': score_page_failures([]),
                'unrun_pages': [],
                'forced_score': 0 if row['status'] == 'unavailable' else None}
        if row['status'] == 'ready':
            item['reference_text'] = _manifest_reference(row, reference_root, published_path)
            pages = predictions.get('books', {}).get(book_id, {}).get('pages', {})
            texts = []
            outcomes = []
            for number in range(1, row['scan']['page_count'] + 1):
                page = normalize_outcome(pages.get(f'scan-{number}', {}))
                outcomes.append(page)
                if page.get('status') == 'completed' and isinstance(page.get('text'), str):
                    texts.append(prediction_page_text(page, text_output))
                else:
                    texts.append('')
                    if page.get('status') == 'failed':
                        item['failed_pages'].append(number)
                    else:
                        item['unrun_pages'].append(number)
            item['prediction_text'] = '\n'.join(texts)
            item['page_failures'] = score_page_failures(outcomes)
        result.append(item)
    return result


def main():
    parser = argparse.ArgumentParser(description='Offline cohort inputs manifest (no text output)')
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True, help='Cohort admission record directory')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cohort', choices=('tune', 'rehearsal', 'proof'), default='tune')
    parser.add_argument('--scan-root', type=Path, default=DEFAULT_SCAN_ROOT)
    parser.add_argument('--reference-root', type=Path, default=DEFAULT_REFERENCE_ROOT)
    parser.add_argument('--published-text', type=Path)
    parser.add_argument('--overrides', type=Path, help='Defaults to input_overrides.json')
    args = parser.parse_args()
    loader = TuningLoader(args.run, cohort=args.cohort)
    with loader.activate():
        result = build_manifest(args.snapshot, loader.allowed, args.scan_root, args.reference_root,
                                published_path=args.published_text,
                                overrides=read_json(args.overrides) if args.overrides else None)
    atomic_json(args.out, result)
    books = result['books']
    unavailable = [row for row in books if row['status'] == 'unavailable']
    print(f'Ready: {len(books) - len(unavailable)}; unavailable: {len(unavailable)}')
    for row in unavailable:
        print(f"{row['id']}: {row['reason']}")
    for kind in ('scan', 'reference'):
        counts = Counter(row[kind]['resolved_by'] for row in books if row[kind])
        pages = sum(row[kind]['page_count'] or 0 for row in books if row[kind])
        print(f"{kind}: exact {counts['exact']}; override {counts['override']}; pages {pages}")
    unequal = [row['id'] for row in books if row['pairing_reason'] == 'unequal_page_counts']
    print(f'Book-level only: {len(books)}; unequal page counts: {len(unequal)}')
    for book_id in unequal:
        print(book_id)


if __name__ == '__main__':
    main()
