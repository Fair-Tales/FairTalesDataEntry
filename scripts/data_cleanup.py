#!/usr/bin/env python3
"""Standalone data-cleanup CLI for FairTalesDataEntry (issue #120).

Trawls the production Firestore database **and** the ``sawimages`` S3 bucket for
junk / test / incomplete data ahead of going to production, in two clearly
separated phases:

    PHASE 1 - AUDIT  (default; READ ONLY; deletes nothing)
        Scans and writes a timestamped human-readable markdown report plus a
        machine-readable JSON candidate list, grouped by category, each entry
        carrying an id/path + reason. Phase 2 consumes a reviewed subset of the
        JSON.

    PHASE 2 - DELETE (guarded; dry-run by default; never auto-deletes)
        Operates ONLY on a reviewed input list. Prints exactly what *would* be
        deleted. Real deletion requires BOTH the ``--execute`` flag AND an
        interactive typed ``DELETE`` confirmation. Every deletion is logged.

This script runs OUTSIDE Streamlit, so ``st.secrets`` is unavailable: it loads
``.streamlit/secrets.toml`` directly and builds its own ``firestore.Client`` and
``s3fs.S3FileSystem`` mirroring the app's ``FirestoreWrapper`` / uploader config.

It deliberately does NOT import the Streamlit-coupled app helpers.

Usage
-----
    # Phase 1 - audit (read only):
    python scripts/data_cleanup.py --audit --report-dir cleanup_reports

    # Phase 2 - review then delete:
    #   1. open cleanup_reports/cleanup_candidates_<ts>.json
    #   2. DELETE the entries you do NOT want removed (keep only real junk)
    #   3. dry-run (default - deletes nothing):
    python scripts/data_cleanup.py --delete --ids cleanup_reports/cleanup_candidates_<ts>.json
    #   4. for real (asks you to type DELETE):
    python scripts/data_cleanup.py --delete --execute --ids <curated>.json

Safety
------
* Audit is read-only.
* Delete is dry-run unless ``--execute`` AND a typed ``DELETE`` confirmation.
* ``users``, ``edit_log``, ``extraction_errors`` and ``collections`` are never touched.
* Exceptions are narrow and surfaced, never silently swallowed.
* Idempotent / re-runnable: deleting already-absent data is a no-op.

The live audit/delete must be run by Chris (or in the main working tree) where
the real secrets exist - it cannot connect from an isolated worktree.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

# Make the repo root importable so the shared pure S3 helpers (``s3_constants``)
# resolve whether this runs as a standalone CLI (``python scripts/data_cleanup.py``,
# where ``sys.path[0]`` is ``scripts/``) or under pytest. ``s3_constants`` has NO
# Streamlit dependency, so it is safe to import in this non-Streamlit tool (#129).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from s3_constants import (  # noqa: E402
    S3_BUCKET,
    NON_BOOK_S3_PREFIXES,
    is_page_image,
    book_folder_name,
)

# ---------------------------------------------------------------------------
# Configuration (module-level so it is easy to tune / extend).
# ---------------------------------------------------------------------------

#: S3 bucket holding book page images (first path segment, app-wide). Shared with
#: the live app via ``s3_constants`` so the CLI and app agree on the bucket name.
DEFAULT_BUCKET = S3_BUCKET

#: Default path to the Streamlit secrets file.
DEFAULT_SECRETS = ".streamlit/secrets.toml"

#: Collections that are explicitly OUT OF SCOPE - never audited, never deleted.
PROTECTED_COLLECTIONS = ("users", "edit_log", "extraction_errors", "collections")

#: A book is flagged as "too few images" when its page-image count is at most
#: this threshold (0, 1 or 2 -> likely an incomplete or test entry).
TOO_FEW_IMAGES_THRESHOLD = 2

# --- Junk-name heuristics (configurable) -----------------------------------

#: Names (case-insensitive, after normalisation) that are real and must NOT be
#: flagged as junk even though a heuristic might otherwise match (e.g. genuine
#: short surnames). Extend this as false positives are found.
JUNK_NAME_ALLOWLIST = {
    "li", "wu", "xu", "yu", "lu", "ng", "an", "bo", "ed", "jo", "al", "mo",
    "j k", "jk", "aa milne", "ee cummings",
}

#: Known test / placeholder tokens. A name whose normalised form equals one of
#: these (or whose every whitespace token is one of these) is junk.
JUNK_NAME_TOKENS = {
    "test", "testing", "tester", "testbook", "testing123",
    "bla", "blah", "blabla", "blahblah", "blablabla",
    "asdf", "asdfasdf", "asdfg", "asdfgh", "asdfghjkl",
    "qwerty", "qwertyuiop", "qwe", "qwer", "wer", "wert",
    "zxc", "zxcv", "zxcvbn", "zxcvbnm",
    "xxx", "xxxx", "yyy", "zzz", "aaa", "aaaa",
    "foo", "bar", "baz", "foobar", "qux",
    "lorem", "ipsum", "dummy", "sample", "example", "examples",
    "abc", "abcd", "abcde", "abcdef",
    "temp", "tmp", "delete", "deleteme", "remove", "removeme",
    "ignore", "ignoreme", "junk", "rubbish", "nonsense",
    "none", "null", "nil", "na", "nan", "todo", "tbd", "tba",
    "untitled", "noname", "no name", "placeholder", "new book", "newbook",
    "book", "mybook", "my book", "title", "name",
}

#: Keyboard rows used to detect "keyboard walk" gibberish (e.g. ``asdfgh``).
_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890")

#: Minimum length of a contiguous keyboard run to count as gibberish.
_KEYBOARD_RUN_LEN = 4


# ---------------------------------------------------------------------------
# Pure helpers - junk-name detection and image-count classification.
# These have NO external dependencies and are unit-tested without a network.
# ---------------------------------------------------------------------------

def _normalise_name(name: object) -> str:
    """Lower-case, collapse internal whitespace, strip. ``None`` -> ``""``."""
    if name is None:
        return ""
    return re.sub(r"\s+", " ", str(name).strip().lower())


def _has_keyboard_walk(token: str) -> bool:
    """True if ``token`` contains a forward/backward keyboard run >= the limit."""
    if len(token) < _KEYBOARD_RUN_LEN:
        return False
    for row in _KEYBOARD_ROWS:
        rev = row[::-1]
        for i in range(len(token) - _KEYBOARD_RUN_LEN + 1):
            window = token[i:i + _KEYBOARD_RUN_LEN]
            if window in row or window in rev:
                return True
    return False


def junk_name_reason(
    name: object,
    *,
    allowlist: Iterable[str] = JUNK_NAME_ALLOWLIST,
    tokens: Iterable[str] = JUNK_NAME_TOKENS,
) -> Optional[str]:
    """Return a human-readable reason if ``name`` looks like junk, else ``None``.

    Heuristics (first match wins), applied to the normalised name:
      * empty / whitespace only
      * <= 2 characters (after removing spaces)
      * exactly a known test token, or every word is a known test token
      * all the same character (e.g. ``aaaa``, ``....``)
      * numeric only
      * keyboard-walk gibberish (e.g. ``asdfgh``)

    The allowlist is consulted first so genuine short names are never flagged.
    """
    norm = _normalise_name(name)
    allow = {_normalise_name(a) for a in allowlist}
    token_set = {_normalise_name(t) for t in tokens}

    if norm in allow:
        return None

    if not norm:
        return "empty name"

    condensed = norm.replace(" ", "")

    if len(condensed) <= 2:
        return f"very short name (<= 2 chars): {norm!r}"

    if norm in token_set:
        return f"known test/placeholder string: {norm!r}"

    words = norm.split(" ")
    if words and all(w in token_set for w in words):
        return f"all words are test/placeholder strings: {norm!r}"

    if len(set(condensed)) == 1:
        return f"all-same-character name: {norm!r}"

    if condensed.isdigit():
        return f"numeric-only name: {norm!r}"

    if _has_keyboard_walk(condensed):
        return f"keyboard-walk gibberish: {norm!r}"

    return None


def image_count_reason(count: int, *, threshold: int = TOO_FEW_IMAGES_THRESHOLD) -> Optional[str]:
    """Return a reason if a book's page-image ``count`` is too low, else ``None``."""
    if count <= threshold:
        noun = "image" if count == 1 else "images"
        return f"only {count} page {noun} in S3 (<= {threshold})"
    return None


# ---------------------------------------------------------------------------
# Candidate record + report rendering (pure, testable).
# ---------------------------------------------------------------------------

# Audit category keys.
CAT_TOO_FEW_IMAGES = "too_few_images"
CAT_ORPHANED_IMAGES = "orphaned_images"
CAT_JUNK_NAMES = "junk_names"
CAT_DANGLING_REFS = "dangling_refs"

CATEGORY_ORDER = (
    CAT_TOO_FEW_IMAGES,
    CAT_ORPHANED_IMAGES,
    CAT_JUNK_NAMES,
    CAT_DANGLING_REFS,
)

CATEGORY_TITLES = {
    CAT_TOO_FEW_IMAGES: "Books with no / too few images",
    CAT_ORPHANED_IMAGES: "Orphaned S3 images (no matching book)",
    CAT_JUNK_NAMES: "Junk / test names",
    CAT_DANGLING_REFS: "Dangling references",
}

# delete_kind values describing how Phase 2 should act on a record.
KIND_BOOK = "book"            # book doc + pages/characters/aliases + S3 folder
KIND_S3_PREFIX = "s3_prefix"  # delete all S3 objects under a prefix
KIND_PERSON_DOC = "person_doc"  # author/illustrator/publisher (ref-guarded)
KIND_CHARACTER = "character"  # character doc + its aliases
KIND_DOC = "doc"              # a single plain document (page / alias)


def make_record(
    *,
    category: str,
    delete_kind: str,
    reason: str,
    collection: str = "",
    doc_id: str = "",
    name: str = "",
    title: str = "",
    s3_path: str = "",
    image_count: Optional[int] = None,
) -> dict:
    """Build one normalised candidate record shared by both phases."""
    rec = {
        "category": category,
        "delete_kind": delete_kind,
        "collection": collection,
        "id": doc_id,
        "name": name,
        "title": title,
        "s3_path": s3_path,
        "reason": reason,
    }
    if image_count is not None:
        rec["image_count"] = image_count
    return rec


def record_label(rec: dict) -> str:
    """Short human label for a record (for reports and dry-run output)."""
    if rec.get("delete_kind") == KIND_S3_PREFIX:
        return rec.get("s3_path", "")
    coll = rec.get("collection", "")
    ident = rec.get("id") or rec.get("name") or rec.get("title") or "?"
    return f"{coll}/{ident}" if coll else ident


def build_report_markdown(report: dict) -> str:
    """Render the audit ``report`` dict as a human-readable markdown document."""
    lines = []
    lines.append("# Data cleanup audit report")
    lines.append("")
    lines.append(f"- Generated: {report.get('generated_at', '?')}")
    lines.append(f"- Bucket: `{report.get('bucket', '?')}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Category | Candidates |")
    lines.append("| --- | ---: |")
    categories = report.get("categories", {})
    total = 0
    for cat in CATEGORY_ORDER:
        n = len(categories.get(cat, []))
        total += n
        lines.append(f"| {CATEGORY_TITLES[cat]} | {n} |")
    lines.append(f"| **Total** | **{total}** |")
    lines.append("")
    lines.append(
        "> NOTHING has been deleted. Review the entries below, curate the "
        "companion JSON file, then run Phase 2 (`--delete`)."
    )
    lines.append("")

    for cat in CATEGORY_ORDER:
        entries = categories.get(cat, [])
        lines.append(f"## {CATEGORY_TITLES[cat]} ({len(entries)})")
        lines.append("")
        if not entries:
            lines.append("_None found._")
            lines.append("")
            continue
        lines.append("| Target | Reason |")
        lines.append("| --- | --- |")
        for rec in entries:
            lines.append(f"| `{record_label(rec)}` | {rec.get('reason', '')} |")
        lines.append("")

    return "\n".join(lines)


def iter_all_records(report: dict) -> Iterator[dict]:
    """Yield every candidate record across all categories of a report."""
    for cat in CATEGORY_ORDER:
        for rec in report.get("categories", {}).get(cat, []):
            yield rec


def write_report_files(report: dict, report_dir: str) -> dict:
    """Write markdown + JSON + CSV report files. Returns the written paths."""
    os.makedirs(report_dir, exist_ok=True)
    stamp = re.sub(r"[^0-9TZ]", "", report["generated_at"])

    md_path = os.path.join(report_dir, f"cleanup_report_{stamp}.md")
    json_path = os.path.join(report_dir, f"cleanup_candidates_{stamp}.json")
    csv_path = os.path.join(report_dir, f"cleanup_candidates_{stamp}.csv")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(build_report_markdown(report))

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    fieldnames = [
        "category", "delete_kind", "collection", "id", "name", "title",
        "s3_path", "image_count", "reason",
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for rec in iter_all_records(report):
            writer.writerow(rec)

    return {"markdown": md_path, "json": json_path, "csv": csv_path}


# ---------------------------------------------------------------------------
# Backend abstraction.
#
# All data access (Firestore + S3) goes through a CleanupBackend. The audit and
# delete logic are written purely against this interface, so an in-memory fake
# backend drives the unit tests with no network access.
#
# Reference fields are normalised to "collection/id" strings everywhere so the
# real and fake backends behave identically.
# ---------------------------------------------------------------------------


class CleanupBackend:
    """Abstract data-access interface. Implemented by the real + fake backends."""

    # --- Firestore ---
    def iter_docs(self, collection: str) -> Iterator[tuple]:
        """Yield ``(doc_id, data_dict)`` for every doc; refs as "collection/id"."""
        raise NotImplementedError

    def doc_exists(self, collection: str, doc_id: str) -> bool:
        raise NotImplementedError

    def delete_doc(self, collection: str, doc_id: str) -> None:
        raise NotImplementedError

    def query_referencing_ids(self, collection: str, field: str, target: str) -> list:
        """Ids of docs in ``collection`` whose ref ``field`` == ``target`` ("c/id")."""
        raise NotImplementedError

    # --- S3 ---
    def list_book_folders(self, bucket: str) -> list:
        """Immediate child *folder names* directly under the bucket."""
        raise NotImplementedError

    def count_page_images(self, bucket: str, folder: str) -> int:
        """Number of ``page_N.jpg`` (non-cropped) objects under the folder."""
        raise NotImplementedError

    def list_objects(self, bucket: str, folder: str) -> list:
        """All object paths under ``{bucket}/{folder}/``."""
        raise NotImplementedError

    def delete_prefix(self, bucket: str, folder: str) -> list:
        """Delete every object under ``{bucket}/{folder}/``; return deleted paths."""
        raise NotImplementedError


# --- Reference-field map: collection -> role field name on a referencing doc ---
PERSON_REF_FIELD = {
    "authors": "author",
    "illustrators": "illustrator",
    "publishers": "publisher",
}


class FirestoreS3Backend(CleanupBackend):
    """Live backend: real ``firestore.Client`` + ``s3fs.S3FileSystem``.

    Heavy third-party imports are deferred to construction time so the module
    (and its pure helpers/tests) import cleanly without google/s3fs present.
    """

    def __init__(self, secrets: dict, *, project: str = "sawdataentry"):
        # Lazy, narrow imports.
        import json as _json

        from google.cloud import firestore
        from google.oauth2 import service_account
        import s3fs

        firestore_key = secrets.get("firestore_key")
        if not firestore_key:
            raise KeyError("secrets is missing required 'firestore_key'")
        key_info = _json.loads(firestore_key) if isinstance(firestore_key, str) else firestore_key
        creds = service_account.Credentials.from_service_account_info(key_info)
        self._db = firestore.Client(credentials=creds, project=project)

        for required in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            if required not in secrets:
                raise KeyError(f"secrets is missing required '{required}'")
        self._fs = s3fs.S3FileSystem(
            anon=False,
            key=secrets["AWS_ACCESS_KEY_ID"],
            secret=secrets["AWS_SECRET_ACCESS_KEY"],
        )

    # --- Firestore ---
    @staticmethod
    def _normalise_value(value):
        """Convert a Firestore ``DocumentReference`` to "collection/id"."""
        path = getattr(value, "path", None)
        if path is not None and hasattr(value, "id"):
            # DocumentReference.path is e.g. "books/some_id".
            return path
        return value

    def iter_docs(self, collection):
        for snap in self._db.collection(collection).stream():
            data = snap.to_dict() or {}
            data = {k: self._normalise_value(v) for k, v in data.items()}
            yield snap.id, data

    def doc_exists(self, collection, doc_id):
        return self._db.collection(collection).document(doc_id).get().exists

    def delete_doc(self, collection, doc_id):
        self._db.collection(collection).document(doc_id).delete()

    def query_referencing_ids(self, collection, field, target):
        from google.cloud.firestore_v1.base_query import FieldFilter

        target_coll, _, target_id = target.partition("/")
        ref = self._db.collection(target_coll).document(target_id)
        stream = (
            self._db.collection(collection)
            .where(filter=FieldFilter(field, "==", ref))
            .stream()
        )
        return [snap.id for snap in stream]

    # --- S3 ---
    def list_book_folders(self, bucket):
        entries = self._fs.ls(bucket, detail=True)
        folders = []
        for entry in entries:
            if entry.get("type") == "directory":
                folders.append(entry["name"].rstrip("/").split("/")[-1])
        return folders

    def count_page_images(self, bucket, folder):
        return sum(1 for p in self.list_objects(bucket, folder) if is_page_image(p))

    def list_objects(self, bucket, folder):
        prefix = f"{bucket}/{folder}"
        if not self._fs.exists(prefix):
            return []
        return list(self._fs.find(prefix))

    def delete_prefix(self, bucket, folder):
        prefix = f"{bucket}/{folder}"
        paths = self.list_objects(bucket, folder)
        if paths:
            self._fs.rm(prefix, recursive=True)
        return paths


# ---------------------------------------------------------------------------
# PHASE 1 - AUDIT (read-only against a backend).
# ---------------------------------------------------------------------------

def _person_name(data: dict) -> str:
    # Illustrators now store a single ``name`` field (#156); authors and legacy
    # illustrator records use forename/surname. Prefer ``name`` when present.
    name = (data.get("name") or "").strip()
    if name:
        return name
    return " ".join(p for p in (data.get("forename", ""), data.get("surname", "")) if p).strip()


# Collections whose name fields make them junk-name candidates, with the
# function that derives a display name and the delete_kind to act with.
JUNK_NAME_SOURCES = {
    "books": (lambda d: d.get("title", ""), KIND_BOOK),
    "authors": (_person_name, KIND_PERSON_DOC),
    "illustrators": (_person_name, KIND_PERSON_DOC),
    "publishers": (lambda d: d.get("name", ""), KIND_PERSON_DOC),
}


def run_audit(backend: CleanupBackend, *, bucket: str = DEFAULT_BUCKET) -> dict:
    """Run the full read-only audit and return a structured report dict."""
    categories = {cat: [] for cat in CATEGORY_ORDER}

    # Load all books once: needed for image checks, orphan matching, junk names.
    books = list(backend.iter_docs("books"))
    book_ids = {doc_id for doc_id, _ in books}
    expected_folders = set()

    # 1. Books with no / too few images.
    for doc_id, data in books:
        title = data.get("title", "")
        folder = book_folder_name(title, data.get("photos_url", ""))
        expected_folders.add(folder)
        count = backend.count_page_images(bucket, folder)
        reason = image_count_reason(count)
        if reason is not None:
            categories[CAT_TOO_FEW_IMAGES].append(
                make_record(
                    category=CAT_TOO_FEW_IMAGES,
                    delete_kind=KIND_BOOK,
                    collection="books",
                    doc_id=doc_id,
                    title=title,
                    s3_path=f"{bucket}/{folder}",
                    reason=reason,
                    image_count=count,
                )
            )

    # 2. Orphaned S3 image folders (no matching book).
    for folder in backend.list_book_folders(bucket):
        if folder in NON_BOOK_S3_PREFIXES:
            continue
        if folder in expected_folders:
            continue
        categories[CAT_ORPHANED_IMAGES].append(
            make_record(
                category=CAT_ORPHANED_IMAGES,
                delete_kind=KIND_S3_PREFIX,
                s3_path=f"{bucket}/{folder}",
                reason="S3 image folder matches no existing book title",
            )
        )

    # 3. Junk / test names across books + people.
    for collection, (name_fn, kind) in JUNK_NAME_SOURCES.items():
        docs = books if collection == "books" else backend.iter_docs(collection)
        for doc_id, data in docs:
            name = name_fn(data)
            reason = junk_name_reason(name)
            if reason is not None:
                categories[CAT_JUNK_NAMES].append(
                    make_record(
                        category=CAT_JUNK_NAMES,
                        delete_kind=kind,
                        collection=collection,
                        doc_id=doc_id,
                        name=name,
                        title=data.get("title", "") if collection == "books" else "",
                        reason=f"junk name ({reason})",
                    )
                )

    # 4. Dangling references.
    #    pages.book -> books, characters.book -> books, aliases.character -> characters
    dangling_specs = (
        ("pages", "book", "books", KIND_DOC),
        ("characters", "book", "books", KIND_CHARACTER),
        ("aliases", "character", "characters", KIND_DOC),
    )
    existing_cache = {"books": book_ids}
    for collection, ref_field, target_coll, kind in dangling_specs:
        if target_coll not in existing_cache:
            existing_cache[target_coll] = {
                doc_id for doc_id, _ in backend.iter_docs(target_coll)
            }
        targets = existing_cache[target_coll]
        for doc_id, data in backend.iter_docs(collection):
            ref = data.get(ref_field)
            if ref is None:
                categories[CAT_DANGLING_REFS].append(
                    make_record(
                        category=CAT_DANGLING_REFS,
                        delete_kind=kind,
                        collection=collection,
                        doc_id=doc_id,
                        reason=f"missing '{ref_field}' reference",
                    )
                )
                continue
            # ref is "target_coll/target_id".
            ref_coll, _, ref_id = str(ref).partition("/")
            if ref_coll != target_coll or ref_id not in targets:
                categories[CAT_DANGLING_REFS].append(
                    make_record(
                        category=CAT_DANGLING_REFS,
                        delete_kind=kind,
                        collection=collection,
                        doc_id=doc_id,
                        reason=f"'{ref_field}' -> missing {target_coll} doc: {ref}",
                    )
                )

    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "bucket": bucket,
        "categories": categories,
    }


# ---------------------------------------------------------------------------
# PHASE 2 - DELETE planning + execution (guarded).
# ---------------------------------------------------------------------------

@dataclass
class DeletionPlan:
    """The outcome of executing (or dry-running) a set of reviewed records."""
    firestore_deletes: list = field(default_factory=list)   # "collection/id"
    s3_deletes: list = field(default_factory=list)          # object paths
    skipped: list = field(default_factory=list)             # (label, reason)
    actions: list = field(default_factory=list)             # human-readable log


def load_reviewed_records(path: str) -> list:
    """Load the reviewed candidate records from a curated file.

    Accepts either:
      * the Phase-1 JSON report (an object with a ``categories`` map), or
      * a JSON list of record dicts, or
      * a plain-text ids file with one ``collection/id`` or ``s3:bucket/path``
        entry per line (``#`` comments allowed).
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        if isinstance(data, dict) and "categories" in data:
            return list(iter_all_records(data))
        if isinstance(data, list):
            return data
        raise ValueError("JSON must be a report object or a list of records")

    # Plain-text ids file.
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("s3:"):
            records.append(make_record(
                category=CAT_ORPHANED_IMAGES, delete_kind=KIND_S3_PREFIX,
                s3_path=line[3:].strip(), reason="(from ids file)",
            ))
        elif "/" in line:
            coll, _, doc_id = line.partition("/")
            kind = KIND_PERSON_DOC if coll in PERSON_REF_FIELD else (
                KIND_BOOK if coll == "books" else
                KIND_CHARACTER if coll == "characters" else KIND_DOC
            )
            records.append(make_record(
                category="(ids file)", delete_kind=kind,
                collection=coll, doc_id=doc_id, reason="(from ids file)",
            ))
        else:
            raise ValueError(f"unrecognised ids-file line: {line!r}")
    return records


def _guard_protected(collection: str) -> None:
    if collection in PROTECTED_COLLECTIONS:
        raise ValueError(
            f"refusing to operate on protected collection {collection!r}"
        )


def execute_deletions(
    records: Iterable[dict],
    backend: CleanupBackend,
    *,
    bucket: str = DEFAULT_BUCKET,
    dry_run: bool = True,
    logger=None,
) -> DeletionPlan:
    """Delete (or, when ``dry_run``, only plan) the reviewed ``records``.

    Returns a :class:`DeletionPlan` describing every Firestore doc / S3 object
    that was (or would be) removed and everything skipped. With ``dry_run`` the
    backend's mutating methods are never called.
    """
    plan = DeletionPlan()

    def log(msg: str) -> None:
        plan.actions.append(msg)
        if logger is not None:
            logger(msg)

    def fs_delete(collection: str, doc_id: str) -> None:
        _guard_protected(collection)
        label = f"{collection}/{doc_id}"
        plan.firestore_deletes.append(label)
        if dry_run:
            log(f"[DRY-RUN] would delete firestore doc {label}")
        else:
            backend.delete_doc(collection, doc_id)
            log(f"DELETED firestore doc {label}")

    def s3_delete(folder: str) -> None:
        prefix = f"{bucket}/{folder}"
        if dry_run:
            paths = backend.list_objects(bucket, folder)
            plan.s3_deletes.extend(paths)
            log(f"[DRY-RUN] would delete {len(paths)} S3 object(s) under {prefix}/")
        else:
            paths = backend.delete_prefix(bucket, folder)
            plan.s3_deletes.extend(paths)
            log(f"DELETED {len(paths)} S3 object(s) under {prefix}/")

    def delete_character(char_id: str) -> None:
        # Delete the character's aliases first, then the character doc.
        for alias_id in backend.query_referencing_ids(
            "aliases", "character", f"characters/{char_id}"
        ):
            fs_delete("aliases", alias_id)
        fs_delete("characters", char_id)

    for rec in records:
        kind = rec.get("delete_kind")
        label = record_label(rec)

        if kind == KIND_BOOK:
            book_id = rec.get("id")
            if not book_id:
                plan.skipped.append((label, "book record has no id"))
                continue
            book_ref = f"books/{book_id}"
            # Dependent pages + aliases that point at the book directly.
            for page_id in backend.query_referencing_ids("pages", "book", book_ref):
                fs_delete("pages", page_id)
            for alias_id in backend.query_referencing_ids("aliases", "book", book_ref):
                fs_delete("aliases", alias_id)
            # Characters (each takes its own aliases with it).
            for char_id in backend.query_referencing_ids("characters", "book", book_ref):
                for alias_id in backend.query_referencing_ids(
                    "aliases", "character", f"characters/{char_id}"
                ):
                    fs_delete("aliases", alias_id)
                fs_delete("characters", char_id)
            # The book document itself.
            fs_delete("books", book_id)
            # Its S3 image folder.
            folder = book_folder_name(rec.get("title", ""), rec.get("s3_path", ""))
            if folder:
                s3_delete(folder)

        elif kind == KIND_S3_PREFIX:
            s3_path = rec.get("s3_path", "")
            folder = s3_path.split("/", 1)[1] if "/" in s3_path else s3_path
            if not folder:
                plan.skipped.append((label, "orphan record has no s3_path"))
                continue
            s3_delete(folder)

        elif kind == KIND_PERSON_DOC:
            collection = rec.get("collection")
            doc_id = rec.get("id")
            if collection not in PERSON_REF_FIELD or not doc_id:
                plan.skipped.append((label, "invalid author/illustrator/publisher record"))
                continue
            ref_field = PERSON_REF_FIELD[collection]
            referencing = backend.query_referencing_ids(
                "books", ref_field, f"{collection}/{doc_id}"
            )
            if referencing:
                reason = (
                    f"still referenced by {len(referencing)} book(s): "
                    + ", ".join(referencing[:5])
                )
                plan.skipped.append((label, reason))
                log(f"SKIP {collection}/{doc_id}: {reason}")
                continue
            fs_delete(collection, doc_id)

        elif kind == KIND_CHARACTER:
            char_id = rec.get("id")
            if not char_id:
                plan.skipped.append((label, "character record has no id"))
                continue
            delete_character(char_id)

        elif kind == KIND_DOC:
            collection = rec.get("collection")
            doc_id = rec.get("id")
            if not collection or not doc_id:
                plan.skipped.append((label, "doc record missing collection/id"))
                continue
            fs_delete(collection, doc_id)

        else:
            plan.skipped.append((label, f"unknown delete_kind {kind!r}"))

    return plan


# ---------------------------------------------------------------------------
# Secrets loading + CLI.
# ---------------------------------------------------------------------------

def load_secrets(path: str) -> dict:
    """Load ``.streamlit/secrets.toml`` directly (no Streamlit dependency)."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"secrets file not found: {path} (run from the project root, or pass "
            "--secrets). This tool must run where the real secrets exist."
        )
    # Prefer stdlib tomllib (3.11+), fall back to tomli, then toml.
    try:
        import tomllib  # type: ignore
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ModuleNotFoundError:
        pass
    try:
        import tomli  # type: ignore
        with open(path, "rb") as f:
            return tomli.load(f)
    except ModuleNotFoundError:
        pass
    import toml  # type: ignore
    with open(path, "r", encoding="utf-8") as f:
        return toml.load(f)


def _make_logger(log_path: str):
    """Return a logger callable that timestamps and appends lines to a file."""
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

    def logger(msg: str) -> None:
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts}\t{msg}\n")

    return logger


def _build_backend(args) -> CleanupBackend:
    secrets = load_secrets(args.secrets)
    return FirestoreS3Backend(secrets)


def cmd_audit(args) -> int:
    backend = _build_backend(args)
    print("Running read-only audit (nothing will be deleted)...")
    report = run_audit(backend, bucket=args.bucket)
    paths = write_report_files(report, args.report_dir)

    print("\nAudit summary:")
    for cat in CATEGORY_ORDER:
        n = len(report["categories"][cat])
        print(f"  {CATEGORY_TITLES[cat]:<45} {n}")
    print("\nReports written:")
    for kind, p in paths.items():
        print(f"  {kind:<9} {p}")
    print(
        "\nNext: review the markdown report, prune the JSON to only the entries "
        "you want removed, then run:\n"
        f"  python {sys.argv[0]} --delete --ids {paths['json']}            # dry-run\n"
        f"  python {sys.argv[0]} --delete --execute --ids <curated>.json   # real"
    )
    return 0


def cmd_delete(args) -> int:
    if not args.ids:
        print("ERROR: --delete requires --ids <reviewed file>", file=sys.stderr)
        return 2

    records = load_reviewed_records(args.ids)
    if not records:
        print("No records found in the reviewed file; nothing to do.")
        return 0

    dry_run = not args.execute
    logger = None

    if not dry_run:
        # Real deletion: require an interactive typed confirmation.
        print(
            f"\nYou are about to PERMANENTLY DELETE data for {len(records)} reviewed "
            f"record(s) from PRODUCTION Firestore + S3.\n"
            "This cannot be undone. Type DELETE (all caps) to proceed, or anything "
            "else to abort."
        )
        try:
            answer = input("Confirm: ")
        except EOFError:
            answer = ""
        if answer.strip() != "DELETE":
            print("Aborted - confirmation not given. Nothing was deleted.")
            return 1
        logger = _make_logger(args.log_file)
        logger(f"=== delete run start: {len(records)} reviewed record(s) ===")

    backend = _build_backend(args)
    mode = "DRY-RUN (no deletions)" if dry_run else "EXECUTE (real deletions)"
    print(f"\nPhase 2 delete - {mode}\n")

    plan = execute_deletions(
        records, backend, bucket=args.bucket, dry_run=dry_run, logger=logger
    )

    for line in plan.actions:
        print(f"  {line}")
    print(
        f"\n{'Would delete' if dry_run else 'Deleted'}: "
        f"{len(plan.firestore_deletes)} Firestore doc(s), "
        f"{len(plan.s3_deletes)} S3 object(s). Skipped: {len(plan.skipped)}."
    )
    for label, reason in plan.skipped:
        print(f"  SKIPPED {label}: {reason}")
    if dry_run:
        print("\nDry-run only. Re-run with --execute to perform these deletions.")
    elif logger is not None:
        logger("=== delete run end ===")
        print(f"\nDeletion log appended to: {args.log_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit / clean up junk Firestore + S3 data (issue #120).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--audit", action="store_true",
        help="Phase 1: read-only scan, write a report (default).",
    )
    mode.add_argument(
        "--delete", action="store_true",
        help="Phase 2: delete a reviewed list (dry-run unless --execute).",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="With --delete: actually delete (also needs a typed DELETE).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="With --delete: force dry-run (the default; deletes nothing).",
    )
    parser.add_argument(
        "--ids", metavar="FILE",
        help="With --delete: the curated Phase-1 JSON, or a plain ids file.",
    )
    parser.add_argument(
        "--report-dir", default="cleanup_reports",
        help="Directory for audit reports (default: ./cleanup_reports).",
    )
    parser.add_argument(
        "--log-file", default=None,
        help="Deletion log file (default: <report-dir>/deletions.log).",
    )
    parser.add_argument(
        "--secrets", default=DEFAULT_SECRETS,
        help=f"Path to secrets.toml (default: {DEFAULT_SECRETS}).",
    )
    parser.add_argument(
        "--bucket", default=DEFAULT_BUCKET,
        help=f"S3 bucket name (default: {DEFAULT_BUCKET}).",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.log_file is None:
        args.log_file = os.path.join(args.report_dir, "deletions.log")
    # --dry-run only reinforces the default; --execute is what flips it.
    if args.dry_run:
        args.execute = False

    try:
        if args.delete:
            return cmd_delete(args)
        # Default action is the read-only audit.
        return cmd_audit(args)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
