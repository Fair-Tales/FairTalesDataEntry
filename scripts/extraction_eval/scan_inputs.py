"""Original scan JPEGs, admitted by book id before any PDF is opened."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from scripts.import_pilot_data import normalise_title
from .json_sources import JsonSources
from .tuning import current_loader
from text_content.tuning import TuningErrors

DEFAULT_SCAN_ROOT = Path("/media/storage/Gender in Children's Literature/Books")


class UnmappedScan(ValueError):
    def __init__(self, book_id, reason):
        self.reason = reason
        super().__init__(f"Unmapped scan: {book_id} ({reason})")


def _loader():
    loader = current_loader()
    if loader is None:
        raise ValueError(TuningErrors.missing_record)
    return loader


def _snapshot_titles(snapshot_dir, book_ids, kind):
    """Admit all ids before opening the manifest; decode only their titles."""
    loader = _loader()
    ids = sorted(set(book_ids))
    loader.load(ids, kind, lambda: None)
    titles = {}
    with JsonSources(Path(snapshot_dir) / 'manifest.json') as source:
        top = source.members(source.root)
        for span in source.items(top['books']):
            fields = source.members(span)
            book_id = source.decode(fields['id'])
            if book_id in ids:
                if book_id in titles:
                    raise ValueError(f"Duplicate snapshot book id: {book_id}")
                titles[book_id] = loader.load(
                    [book_id], kind + '_title',
                    lambda: source.decode(fields['title']) if 'title' in fields else None)
    return titles


def _poppler(book_id, kind, names):
    programs = {name: shutil.which(name) for name in names}
    missing = [name for name, executable in programs.items() if executable is None]
    if missing:
        raise ValueError(f"Poppler is required for {kind} inputs; missing: {', '.join(missing)}")

    def run(program, *arguments):
        result = subprocess.run([programs[program], *map(str, arguments)],
                                capture_output=True, check=False)
        if result.returncode:
            raise ValueError(f"{program} failed for {book_id}: {result.stderr.decode('utf-8').strip()}")
        return result.stdout.decode('utf-8')

    return run


def _pdf_page_count(run, path, book_id):
    count = re.search(r'^Pages:\s+(\d+)\s*$', run('pdfinfo', path), re.MULTILINE)
    if count is None or int(count[1]) < 1:
        raise ValueError(f"Cannot read PDF page count: {book_id}")
    return int(count[1])


def load_scan(book_id, title, scan_root=DEFAULT_SCAN_ROOT):
    """Return ordered embedded JPEG bytes and their public inventory metadata.

    Match the importer's normalized title to exactly one immediate folder, then
    require exactly one PDF in it (any basename, case-insensitive extension).
    Never prefer a same-named PDF over another candidate or fall back to renders.
    """
    def read():
        root = Path(scan_root)
        key = normalise_title(title)
        if not key:
            raise UnmappedScan(book_id, 'missing_title')
        folders = [path for path in root.iterdir()
                   if path.is_dir() and normalise_title(path.name) == key]
        if len(folders) != 1:
            raise UnmappedScan(book_id, 'missing_folder' if not folders else 'ambiguous_folder')
        pdfs = [path for path in folders[0].iterdir()
                if path.is_file() and path.suffix.lower() == '.pdf']
        if len(pdfs) != 1:
            raise UnmappedScan(book_id, 'missing_pdf' if not pdfs else 'ambiguous_pdf')
        path = pdfs[0]
        run = _poppler(book_id, 'scan', ('pdfinfo', 'pdfimages'))
        page_count = _pdf_page_count(run, path, book_id)
        rows = [line.split() for line in run('pdfimages', '-list', path).splitlines()
                if re.match(r'^\s*\d+\s', line)]
        if ([int(row[0]) for row in rows] != list(range(1, page_count + 1))
                or any(len(row) < 9 or row[2] != 'image' or row[8] != 'jpeg' for row in rows)):
            raise ValueError(f"Scan must contain exactly one embedded JPEG per page: {book_id}")
        with tempfile.TemporaryDirectory(prefix='fairtales-scan-') as temporary:
            prefix = Path(temporary) / 'page'
            run('pdfimages', '-j', path, prefix)
            files = sorted(Path(temporary).iterdir(), key=lambda item: int(item.stem.rsplit('-', 1)[1]))
            if (len(files) != page_count or any(item.suffix != '.jpg' for item in files)
                    or [int(item.stem.rsplit('-', 1)[1]) for item in files] != [int(row[1]) for row in rows]):
                raise ValueError(f"Extracted scan images do not match PDF page order: {book_id}")
            pages = [item.read_bytes() for item in files]
        return {'id': book_id, 'scan_file': str(path.relative_to(root)),
                'page_count': page_count,
                'page_sha256': [hashlib.sha256(data).hexdigest() for data in pages],
                'pages': pages}

    return _loader().load([book_id], 'scan', read)


def scan_metadata(scan):
    return {key: value for key, value in scan.items() if key != 'pages'}


def inventory(snapshot_dir, book_ids, scan_root=DEFAULT_SCAN_ROOT):
    """Hash one admitted cohort without decoding reference text or page records."""
    ids = sorted(set(book_ids))
    titles = _snapshot_titles(snapshot_dir, ids, 'scan_inventory')
    result = {'books': [], 'unmapped': []}
    for book_id in ids:
        try:
            if book_id not in titles:
                raise UnmappedScan(book_id, 'missing_snapshot_id')
            result['books'].append(scan_metadata(load_scan(book_id, titles[book_id], scan_root)))
        except UnmappedScan as exc:
            result['unmapped'].append({'id': book_id, 'reason': exc.reason})
    return result


def scan_manifest(manifest, scans):
    """Extraction page descriptors; deliberately no reference-page correspondence."""
    by_id = {scan['id']: scan for scan in scans}
    books = []
    for book in manifest.get('books', []):
        scan = by_id[book['id']]
        pages = [{'id': f'scan-{number}', 'page_number': number,
                  'image': {'sha256': sha256}}
                 for number, sha256 in enumerate(scan['page_sha256'], 1)]
        books.append({**book, 'pages': pages})
    return {**manifest, 'books': books}
