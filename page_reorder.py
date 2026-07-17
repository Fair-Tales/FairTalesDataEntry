"""Transactional page reordering for a book's photos + Page docs (#148/#204).

A book's pages are stored as ``sawimages/{title}/page_N(.jpg|_cropped.jpg|
_display.jpg)`` files plus Firestore ``pages`` docs whose DOCUMENT ID embeds the
page number (``{book_id}_{N}``) — so a true reorder is an id-changing migration
across both stores, and naive in-place renames collide (moving 2→3 while 3→4
still exists). This module implements it safely, mirroring the
create-new-before-delete-old discipline of ``scripts/rename_book.py`` and
``Character.rename``:

  Phase A  copy every affected page file into a ``_reorder_tmp/`` staging area
           inside the book folder, ALREADY under its NEW page number, then write
           a ``manifest.json`` (token + tmp→final map) LAST — so the manifest's
           presence guarantees the staging area is complete.
  Commit   ONE atomic Firestore batch rewrites the affected page docs (same id
           set — a permutation — with each doc's content moved to its new id and
           ``page_number`` corrected) AND writes a ``page_reorders/{book_id}``
           sentinel carrying the manifest token. Atomicity means the docs and
           the sentinel flip together.
  Phase B  copy the staged files over the final ``page_N*`` names.
  Phase C  delete the staging area.

Crash recovery is decided by comparing the manifest token to the sentinel:
  * staging present, sentinel token ≠ manifest token → the batch never
    committed; nothing user-visible changed → discard the staging area.
  * sentinel token == manifest token → the docs ARE permuted; finish phases
    B + C from the staging area (which holds the complete target state).

Because doc CONTENT (including any entered text) moves with its page, this
works both before and after text entry (#204/#205) — text always follows its
photo.

This module is deliberately Streamlit-free (pure ``fs``/``db`` clients passed
in) so the whole migration is unit-testable with in-memory fakes.
"""

import json
import logging
import posixpath
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: Filename suffix variants a page may have in S3 (raw + derived).
PAGE_VARIANTS = ("", "_cropped", "_display")
#: Staging directory created inside the book's S3 folder during a reorder.
TMP_DIRNAME = "_reorder_tmp"
#: Manifest filename inside the staging directory (written LAST in phase A).
MANIFEST_NAME = "manifest.json"
#: Firestore collection holding the per-book reorder commit sentinel.
REORDER_COLLECTION = "page_reorders"


class ReorderError(Exception):
    """A page reorder could not be performed/completed. The message is safe to
    surface to the user; nothing user-visible has been changed unless the
    message says otherwise."""


def move_page_permutation(n_pages, from_page, to_page):
    """The ``{old: new}`` mapping for moving one page to a new position.

    Moving page ``from_page`` to position ``to_page`` cyclically shifts the
    pages in between by one (the standard list ``insert(pop())`` semantics).
    Returns ``{}`` when the move is a no-op. Raises :class:`ReorderError` on an
    out-of-range page/position.
    """
    for value, label in ((from_page, "page"), (to_page, "position")):
        if not isinstance(value, int) or not 1 <= value <= n_pages:
            raise ReorderError(
                f"Invalid {label} {value!r}: must be between 1 and {n_pages}."
            )
    if from_page == to_page:
        return {}
    if to_page > from_page:
        permutation = {old: old - 1 for old in range(from_page + 1, to_page + 1)}
    else:
        permutation = {old: old + 1 for old in range(to_page, from_page)}
    permutation[from_page] = to_page
    return permutation


def validate_permutation(permutation, n_pages):
    """Ensure ``permutation`` is a genuine permutation of a subset of 1..N
    (bijective over the same id set — the invariant the atomic doc rewrite
    relies on). Raises :class:`ReorderError` otherwise."""
    olds = set(permutation)
    news = set(permutation.values())
    if olds != news:
        raise ReorderError(
            "Reorder plan is not a permutation (a page number would be "
            "duplicated or lost) — aborted with no changes made."
        )
    out_of_range = [n for n in olds if not 1 <= n <= n_pages]
    if out_of_range:
        raise ReorderError(
            f"Reorder plan references page(s) {sorted(out_of_range)} outside "
            f"1..{n_pages} — aborted with no changes made."
        )


def _tmp_dir(folder):
    return f"{folder}/{TMP_DIRNAME}"


def _manifest_path(folder):
    return f"{_tmp_dir(folder)}/{MANIFEST_NAME}"


def read_pending_manifest(fs, folder):
    """The staged manifest for an interrupted reorder, or ``None``.

    A staging directory WITHOUT a manifest means phase A never finished (the
    manifest is written last) — treated as no pending reorder; callers discard
    it via :func:`discard_staging`.
    """
    path = _manifest_path(folder)
    try:
        if not fs.exists(path):
            return None
        with fs.open(path, "rb") as f:
            manifest = json.loads(f.read().decode("utf-8"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("Unreadable reorder manifest under %s: %s", folder, exc)
        return None
    if not isinstance(manifest, dict) or "token" not in manifest or "moves" not in manifest:
        logger.warning("Malformed reorder manifest under %s", folder)
        return None
    return manifest


def discard_staging(fs, folder):
    """Remove the staging directory (used when the commit never happened)."""
    tmp = _tmp_dir(folder)
    try:
        if fs.exists(tmp):
            fs.rm(tmp, recursive=True)
    except FileNotFoundError:
        pass


def reorder_committed(db, book_id, manifest):
    """Whether the Firestore commit for ``manifest`` happened — i.e. whether the
    sentinel written in the SAME atomic batch as the doc rewrite carries this
    manifest's token."""
    doc = db.collection(REORDER_COLLECTION).document(book_id).get()
    data = doc.to_dict() if getattr(doc, "exists", True) else None
    return bool(data) and data.get("token") == manifest.get("token")


def _copy_file(fs, src, dst):
    """Server-side copy when the filesystem provides one; read/write fallback."""
    copy = getattr(fs, "copy", None)
    if copy is not None:
        copy(src, dst)
        return
    with fs.open(src, "rb") as f:
        data = f.read()
    with fs.open(dst, "wb") as f:
        f.write(data)


def _finish_from_staging(fs, folder, manifest):
    """Phases B + C: copy every staged file over its final name, remove any
    scheduled stale variants, then delete the staging area. Safe to re-run
    (pure copies from an intact staging + idempotent deletes)."""
    tmp = _tmp_dir(folder)
    for tmp_name, final_name in manifest["moves"].items():
        _copy_file(fs, f"{tmp}/{tmp_name}", f"{folder}/{final_name}")
    # Stale-variant cleanup: a page WITHOUT e.g. a _cropped image moving onto a
    # position whose previous occupant HAD one would otherwise leave that stale
    # derived file in place — and it would be served (e.g. by Enlarge's
    # use_cropped load) as the wrong page. Scheduled during phase A.
    for name in manifest.get("deletes", []):
        try:
            fs.rm(f"{folder}/{name}")
        except FileNotFoundError:
            pass
    discard_staging(fs, folder)


def resume_pending_reorder(fs, db, book_id, folder):
    """Complete or discard an interrupted reorder found under ``folder``.

    Returns ``'finished'`` (docs were committed; files completed from staging),
    ``'discarded'`` (commit never happened; staging dropped, nothing changed),
    or ``None`` (no pending reorder).
    """
    manifest = read_pending_manifest(fs, folder)
    if manifest is None:
        # Also clear a manifest-less (incomplete phase A) staging dir if present.
        discard_staging(fs, folder)
        return None
    if reorder_committed(db, book_id, manifest):
        _finish_from_staging(fs, folder, manifest)
        return "finished"
    discard_staging(fs, folder)
    return "discarded"


def execute_reorder(fs, db, book_id, folder, permutation, n_pages,
                    edited_by=None):
    """Apply ``permutation`` (``{old_page: new_page}``) to the book's S3 files
    and Firestore page docs, transactionally (see module docstring).

    ``folder`` is the full S3 prefix (``sawimages/{title}``). ``db`` is a raw
    ``google.cloud.firestore.Client`` (supports ``batch()``). Raises
    :class:`ReorderError` with a user-safe message on any pre-commit failure
    (nothing changed) and on a pending-reorder conflict.
    """
    validate_permutation(permutation, n_pages)
    if not permutation:
        return

    if read_pending_manifest(fs, folder) is not None:
        raise ReorderError(
            "A previous reorder of this book did not finish. Use 'Finish "
            "pending reorder' first — no changes were made now."
        )

    # The atomic doc-rewrite batch (permutation + sentinel) must stay under
    # Firestore's 500-op batch limit; app books are capped at 60 pages so this
    # is a safety net, not an expected path.
    if len(permutation) + 1 > 450:
        raise ReorderError("Too many pages to reorder in one step.")

    # ---- Read every affected page doc up front; abort cleanly if any is
    # missing (drifted data) BEFORE anything is written anywhere.
    pages_coll = db.collection("pages")
    doc_contents = {}
    for old in sorted(permutation):
        snapshot = pages_coll.document(f"{book_id}_{old}").get()
        data = snapshot.to_dict() if getattr(snapshot, "exists", True) else None
        if not data:
            raise ReorderError(
                f"Page {old}'s database record is missing — reorder aborted "
                "with no changes made."
            )
        doc_contents[old] = data

    # ---- Phase A: stage every affected file under its NEW name; manifest last.
    token = f"{int(time.time())}_{uuid.uuid4().hex[:12]}"
    tmp = _tmp_dir(folder)
    moves = {}
    deletes = []
    try:
        for old, new in sorted(permutation.items()):
            for variant in PAGE_VARIANTS:
                src = f"{folder}/page_{old}{variant}.jpg"
                dst_name = f"page_{new}{variant}.jpg"
                if not fs.exists(src):
                    if variant == "":
                        raise ReorderError(
                            f"Page {old}'s photo is missing from storage — "
                            "reorder aborted with no changes made."
                        )
                    # Optional derived variant the moving page does NOT have:
                    # schedule removal of any stale one at its destination so
                    # the previous occupant's derivative is never served for it.
                    if fs.exists(f"{folder}/{dst_name}"):
                        deletes.append(dst_name)
                    continue
                _copy_file(fs, src, f"{tmp}/{dst_name}")
                moves[dst_name] = dst_name
        manifest = {
            "token": token,
            "book_id": book_id,
            "permutation": {str(k): v for k, v in permutation.items()},
            "moves": moves,
            "deletes": deletes,
        }
        with fs.open(_manifest_path(folder), "wb") as f:
            f.write(json.dumps(manifest).encode("utf-8"))
    except ReorderError:
        discard_staging(fs, folder)
        raise
    except (OSError, FileNotFoundError) as exc:
        discard_staging(fs, folder)
        raise ReorderError(
            "Copying the page photos failed before anything was changed "
            f"({exc}). Please try again."
        ) from exc

    # ---- Atomic commit: permuted doc contents + sentinel in ONE batch.
    now = datetime.now(timezone.utc)
    batch = db.batch()
    for old, new in permutation.items():
        data = dict(doc_contents[old])
        data["page_number"] = new
        data["last_updated"] = now
        batch.set(pages_coll.document(f"{book_id}_{new}"), data)
    batch.set(
        db.collection(REORDER_COLLECTION).document(book_id),
        {
            "token": token,
            "book_id": book_id,
            "permutation": {str(k): v for k, v in permutation.items()},
            "edited_by": edited_by,
            "at": now,
        },
    )
    batch.commit()

    # ---- Phases B + C. If we die in here, resume_pending_reorder finishes it
    # (the sentinel proves the commit happened).
    _finish_from_staging(fs, folder, manifest)


def page_display_path(folder, page_number):
    """Preferred preview path for a page (display derivative)."""
    return posixpath.join(folder, f"page_{page_number}_display.jpg")
