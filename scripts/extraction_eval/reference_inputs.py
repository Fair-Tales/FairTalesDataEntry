"""Unaltered Word-export page text, admitted before any reference file is read."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from .json_sources import JsonSources
from .scan_inputs import _loader, _pdf_page_count, _poppler, _snapshot_titles, normalise_title
from .store import atomic_json
from .tuning import TuningLoader

DEFAULT_REFERENCE_ROOT = Path(
    '/home/expert/Documents/uob/fair_tales/fair-tales-language-analysis/text_pdfs')
PDFTOTEXT_ARGS = ('-raw', '-enc', 'UTF-8', '-eol', 'unix')


class UnmappedReference(ValueError):
    def __init__(self, book_id, reason):
        self.reason = reason
        super().__init__(f"Unmapped reference: {book_id} ({reason})")


def _text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _published_text(book_id, key, path):
    """Read only the matching Text cell, never the complete published dataframe."""
    path = Path(path)
    with JsonSources(path) as source:
        fields = source.members(source.root)
        titles = source.members(fields['Title'])
        matches = [row for row, span in titles.items()
                   if normalise_title(source.decode(span)) == key]
        if len(matches) != 1:
            raise UnmappedReference(book_id, 'missing_published_title' if not matches
                                    else 'ambiguous_published_title')
        row = matches[0]
        texts = source.members(fields['Text'])
        text = source.decode(texts[row]) if row in texts else None
        if not isinstance(text, str):
            raise UnmappedReference(book_id, 'missing_published_text')
    # Published text has no page boundaries: do not invent a one-page reference.
    return {'id': book_id, 'source': 'published_text', 'reference_file': path.name,
            'published_row': row, 'fallback_reason': 'missing_pdf',
            'page_count': None, 'page_sha256': [], 'page_char_counts': [],
            'text': text, 'text_sha256': _text_hash(text), 'text_char_count': len(text)}


def _load_reference(book_id, title, reference_root, published_path):
    def read():
        key = normalise_title(title)
        if not key:
            raise UnmappedReference(book_id, 'missing_title')
        root = Path(reference_root)
        matches = []
        for path in root.iterdir():
            if path.is_file() and path.suffix.lower() == '.pdf':
                # The importer also treats .doc.pdf as a Word export of this
                # title, but silently prefers .pdf. Here duplicates stay unmapped.
                stem = path.stem[:-4] if path.stem.lower().endswith('.doc') else path.stem
                if normalise_title(stem) == key:
                    matches.append(path)
        if len(matches) > 1:
            raise UnmappedReference(book_id, 'ambiguous_pdf')
        if not matches:
            if published_path is not None:
                return _published_text(book_id, key, published_path)
            raise UnmappedReference(book_id, 'missing_pdf')
        path = matches[0]
        run = _poppler(book_id, 'reference', ('pdfinfo', 'pdftotext'))
        count = _pdf_page_count(run, path, book_id)
        text = run('pdftotext', *PDFTOTEXT_ARGS, path, '-')
        pages = text.split('\f')
        # Poppler terminates every page with FF, including empty/final pages.
        # Remove only that delimiter, retaining every other character verbatim.
        if len(pages) != count + 1 or pages[-1] != '':
            raise ValueError(f"Reference text boundaries disagree with PDF page count: {book_id}")
        pages.pop()
        return {'id': book_id, 'source': 'word_export',
                'reference_file': str(path.relative_to(root)), 'page_count': count,
                'page_sha256': [_text_hash(page) for page in pages],
                'page_char_counts': [len(page) for page in pages], 'pages': pages}

    return _loader().load([book_id], 'reference', read)


def load_reference(book_id, snapshot_dir, reference_root=DEFAULT_REFERENCE_ROOT, *,
                   published_path=None):
    """Resolve the admitted id's snapshot title; fallback requires an explicit path.

    Word results contain ordered ``pages`` exactly as emitted by pdftotext -raw
    (UTF-8, Unix newlines, excluding page delimiters). Published fallback results
    contain one unaltered ``text`` string and page_count=None. Ambiguity refuses
    both extraction and fallback. No predictions influence either choice.
    """
    titles = _snapshot_titles(snapshot_dir, [book_id], 'reference')
    if book_id not in titles:
        raise UnmappedReference(book_id, 'missing_snapshot_id')
    return _load_reference(book_id, titles[book_id], reference_root, published_path)


def reference_metadata(reference):
    return {key: value for key, value in reference.items() if key not in {'pages', 'text'}}


def inventory(snapshot_dir, book_ids, reference_root=DEFAULT_REFERENCE_ROOT, *,
              published_path=None):
    """Record source choices, ordered text hashes/counts and unmapped ids; no text."""
    ids = sorted(set(book_ids))
    titles = _snapshot_titles(snapshot_dir, ids, 'reference_inventory')
    result = {'pdftotext_args': list(PDFTOTEXT_ARGS), 'books': [], 'unmapped': []}
    for book_id in ids:
        try:
            if book_id not in titles:
                raise UnmappedReference(book_id, 'missing_snapshot_id')
            reference = _load_reference(book_id, titles[book_id], reference_root, published_path)
            result['books'].append(reference_metadata(reference))
        except UnmappedReference as exc:
            result['unmapped'].append({'id': book_id, 'reason': exc.reason})
    return result


def main():
    parser = argparse.ArgumentParser(description='Offline reference inventory (no text output)')
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--run', required=True, type=Path, help='Cohort admission record directory')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--reference-root', type=Path, default=DEFAULT_REFERENCE_ROOT)
    parser.add_argument('--published-text', type=Path, help='Explicit whole-book fallback')
    parser.add_argument('--cohort', choices=('tune', 'rehearsal', 'proof'), default='tune')
    args = parser.parse_args()
    loader = TuningLoader(args.run, cohort=args.cohort)
    with loader.activate():
        result = inventory(args.snapshot, loader.allowed, args.reference_root,
                           published_path=args.published_text)
    atomic_json(args.out, result)
    word = [book for book in result['books'] if book['source'] == 'word_export']
    counts = [count for book in word for count in book['page_char_counts']]
    print(f"Word exports: {len(word)}; published fallback: {len(result['books']) - len(word)}; "
          f"unmapped: {len(result['unmapped'])}")
    print(f"Pages: {len(counts)}; empty: {counts.count(0)}; "
          f"under 20 characters (including empty): {sum(count < 20 for count in counts)}")


if __name__ == '__main__':
    main()
