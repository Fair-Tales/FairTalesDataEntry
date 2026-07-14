#!/usr/bin/env python3
"""Standalone book-rename migration for FairTalesDataEntry.

Renames a single book (and hence its Firestore ``document_id``) together with
every dependent document and its S3 image folder. A book's id is derived from
its title (``title.lower().replace(" ", "_")``) and every page / character /
alias id embeds that book id and/or holds a ``book`` reference, so a naive
title edit would orphan them (see DECISIONS.md #005). This script performs the
full, safe migration:

    books/<old_id>              -> books/<new_id>          (fields copied, title +
                                                            photos_url updated)
    pages/<old_id>_<n>          -> pages/<new_id>_<n>       (book ref repointed)
    characters/<old_id>_<name>  -> characters/<new_id>_<name> (book ref repointed)
    aliases/<old_id>_<name>     -> aliases/<new_id>_<name>  (book + character refs
                                                            repointed)
    sawimages/<old title>/      -> sawimages/<new title>/   (server-side move)

It also remaps the book's ``characters`` reference-list to the new character
documents.

The tool runs OUTSIDE Streamlit (``st.secrets`` unavailable): it loads
``.streamlit/secrets.toml`` directly and builds its own ``firestore.Client`` and
``s3fs.S3FileSystem``, mirroring the app's config and reusing ``s3_constants``
and ``data_cleanup.load_secrets`` (code reuse, CLAUDE.md #129).

Usage
-----
    # Dry-run (default) - prints exactly what WOULD change, writes nothing:
    python scripts/rename_book.py --from "Clean Up!" --to "Clean Up! (Partial Entry)"

    # For real (creates new docs, moves S3, deletes old docs; asks you to
    # type CONFIRM and logs every write):
    python scripts/rename_book.py --from "Clean Up!" --to "Clean Up! (Partial Entry)" --execute

Safety
------
* Dry-run unless ``--execute`` AND a typed ``CONFIRM``.
* Aborts if the source book is missing or the target id already exists.
* Create-new-before-delete-old ordering: a mid-run failure leaves the old data
  intact (re-run is safe because the target-exists guard then trips).
* Every write is logged to ``--log-file``.
* Must run where the real secrets exist (main working tree); it cannot connect
  from an isolated worktree.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

# Make the repo root importable so the shared, Streamlit-free helpers resolve
# whether this runs as a standalone CLI or under pytest.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from s3_constants import S3_BUCKET, book_folder_name  # noqa: E402
from scripts.data_cleanup import load_secrets  # noqa: E402  (reuse, #129)

#: Default path to the Streamlit secrets file.
DEFAULT_SECRETS = ".streamlit/secrets.toml"

#: Firestore project id (matches FirestoreWrapper / data_cleanup).
PROJECT = "sawdataentry"


def book_document_id(title: str) -> str:
    """The Firestore ``books`` document id for ``title``.

    MUST match ``data_structures.book.Book.document_id`` exactly, otherwise the
    migration would write to the wrong id.
    """
    return title.lower().replace(" ", "_")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _remap_child_id(child_id: str, old_book_id: str, new_book_id: str) -> str:
    """Map a dependent doc id from the old book id to the new one.

    Page/character/alias ids are all ``{book_id}_{suffix}``, so swapping the
    ``{old_book_id}_`` prefix for ``{new_book_id}_`` preserves the suffix
    (page number / name slug) exactly. Raises if the id doesn't carry the
    expected prefix (defensive: the caller only passes children queried by
    ``book == old_ref``, which are formed from the old book id).
    """
    prefix = f"{old_book_id}_"
    if not child_id.startswith(prefix):
        raise ValueError(
            f"child id {child_id!r} does not start with expected prefix {prefix!r}"
        )
    return f"{new_book_id}_{child_id[len(prefix):]}"


class BookRenamer:
    """Performs (or dry-runs) the rename against real Firestore + S3 clients."""

    def __init__(self, secrets: dict, *, dry_run: bool = True, logger=None):
        import json as _json

        from google.cloud import firestore
        from google.oauth2 import service_account
        import s3fs

        key = secrets.get("firestore_key")
        if not key:
            raise KeyError("secrets is missing required 'firestore_key'")
        key_info = _json.loads(key) if isinstance(key, str) else key
        creds = service_account.Credentials.from_service_account_info(key_info)
        self.db = firestore.Client(credentials=creds, project=PROJECT)

        for required in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            if required not in secrets:
                raise KeyError(f"secrets is missing required '{required}'")
        self.fs = s3fs.S3FileSystem(
            anon=False,
            key=secrets["AWS_ACCESS_KEY_ID"],
            secret=secrets["AWS_SECRET_ACCESS_KEY"],
        )

        self.dry_run = dry_run
        self._logger = logger
        self.actions: list[str] = []

    # --- logging -----------------------------------------------------------
    def _log(self, msg: str) -> None:
        self.actions.append(msg)
        print(f"  {msg}")
        if self._logger is not None:
            self._logger(msg)

    def _tag(self) -> str:
        return "[DRY-RUN] would" if self.dry_run else "DID"

    # --- firestore helpers -------------------------------------------------
    def _book_ref(self, book_id):
        return self.db.collection("books").document(book_id)

    def _query_by_ref(self, collection, field, ref):
        from google.cloud.firestore_v1.base_query import FieldFilter

        return list(
            self.db.collection(collection)
            .where(filter=FieldFilter(field, "==", ref))
            .stream()
        )

    def _set_doc(self, collection, doc_id, data):
        self._log(f"{self._tag()} create {collection}/{doc_id}")
        if not self.dry_run:
            self.db.collection(collection).document(doc_id).set(data)

    def _delete_doc(self, collection, doc_id):
        self._log(f"{self._tag()} delete {collection}/{doc_id}")
        if not self.dry_run:
            self.db.collection(collection).document(doc_id).delete()

    # --- the migration -----------------------------------------------------
    def rename(self, old_title: str, new_title: str) -> int:
        old_id = book_document_id(old_title)
        new_id = book_document_id(new_title)
        if old_id == new_id:
            print(f"ERROR: old and new titles map to the same id {old_id!r}.")
            return 2

        old_book_ref = self._book_ref(old_id)
        new_book_ref = self._book_ref(new_id)

        old_snap = old_book_ref.get()
        if not old_snap.exists:
            print(f"ERROR: source book books/{old_id} does not exist.")
            return 2
        if new_book_ref.get().exists:
            print(f"ERROR: target book books/{new_id} already exists; aborting.")
            return 2

        book_data = old_snap.to_dict() or {}

        # Gather dependents up front (create-before-delete needs both lists).
        chars = self._query_by_ref("characters", "book", old_book_ref)
        aliases = self._query_by_ref("aliases", "book", old_book_ref)
        pages = self._query_by_ref("pages", "book", old_book_ref)

        old_folder = book_folder_name(old_title, book_data.get("photos_url", ""))
        new_folder = new_title  # S3 folder is the raw title (see _s3_prefix_for)
        s3_objects = self._list_s3(old_folder)

        print(
            f"\nPlan: rename books/{old_id}  ->  books/{new_id}\n"
            f"  title      : {old_title!r} -> {new_title!r}\n"
            f"  S3 folder  : {S3_BUCKET}/{old_folder} -> {S3_BUCKET}/{new_folder} "
            f"({len(s3_objects)} object(s))\n"
            f"  pages      : {len(pages)}\n"
            f"  characters : {len(chars)}\n"
            f"  aliases    : {len(aliases)}\n"
            f"Mode: {'DRY-RUN (nothing written)' if self.dry_run else 'EXECUTE'}\n"
        )

        # 1. Characters: create under new id, build old-ref-path -> new-ref map.
        char_ref_map = {}  # old ref.path -> new DocumentReference
        for snap in chars:
            new_char_id = _remap_child_id(snap.id, old_id, new_id)
            data = snap.to_dict() or {}
            data["book"] = new_book_ref
            self._set_doc("characters", new_char_id, data)
            char_ref_map[snap.reference.path] = (
                self.db.collection("characters").document(new_char_id)
            )

        # 2. Aliases: create under new id, repoint book + character refs.
        for snap in aliases:
            new_alias_id = _remap_child_id(snap.id, old_id, new_id)
            data = snap.to_dict() or {}
            data["book"] = new_book_ref
            old_char = data.get("character")
            if old_char is not None and getattr(old_char, "path", None) in char_ref_map:
                data["character"] = char_ref_map[old_char.path]
            self._set_doc("aliases", new_alias_id, data)

        # 3. Pages: create under new id, repoint book ref.
        for snap in pages:
            new_page_id = _remap_child_id(snap.id, old_id, new_id)
            data = snap.to_dict() or {}
            data["book"] = new_book_ref
            self._set_doc("pages", new_page_id, data)

        # 4. New book doc: copy fields, update title/photos_url, remap characters.
        new_book_data = dict(book_data)
        new_book_data["title"] = new_title
        new_book_data["photos_url"] = f"{S3_BUCKET}/{new_folder}"
        new_book_data["characters"] = list(char_ref_map.values())
        self._set_doc("books", new_id, new_book_data)

        # 5. Move the S3 image folder (server-side), then delete the old docs.
        self._move_s3(old_folder, new_folder, s3_objects)

        for snap in aliases:
            self._delete_doc("aliases", snap.id)
        for snap in chars:
            self._delete_doc("characters", snap.id)
        for snap in pages:
            self._delete_doc("pages", snap.id)
        self._delete_doc("books", old_id)

        print(
            f"\n{'Would rename' if self.dry_run else 'Renamed'}: books/{old_id} "
            f"-> books/{new_id}  "
            f"({len(pages)} pages, {len(chars)} characters, {len(aliases)} aliases, "
            f"{len(s3_objects)} S3 objects)."
        )
        if self.dry_run:
            print("\nDry-run only. Re-run with --execute to perform the rename.")
        return 0

    # --- S3 ----------------------------------------------------------------
    def _list_s3(self, folder: str) -> list:
        prefix = f"{S3_BUCKET}/{folder}"
        try:
            if not self.fs.exists(prefix):
                return []
            return list(self.fs.find(prefix))
        except FileNotFoundError:
            return []

    def _move_s3(self, old_folder: str, new_folder: str, objects: list) -> None:
        if old_folder == new_folder:
            return
        old_prefix = f"{S3_BUCKET}/{old_folder}"
        new_prefix = f"{S3_BUCKET}/{new_folder}"
        if not objects:
            self._log(f"{self._tag()} move S3 {old_prefix}/ -> {new_prefix}/ (0 objects)")
            return
        for key in objects:
            name = key.rsplit("/", 1)[-1]
            self._log(f"{self._tag()} move S3 {key} -> {new_prefix}/{name}")
            if not self.dry_run:
                self.fs.mv(key, f"{new_prefix}/{name}")  # server-side copy + delete
        if not self.dry_run and self.fs.exists(old_prefix):
            self.fs.rm(old_prefix, recursive=True)


def _make_logger(log_path: str):
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

    def logger(msg: str) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{_now()}\t{msg}\n")

    return logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rename a book across Firestore + S3 (id-changing migration).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--from", dest="old_title", required=True,
                        help="Current book title (exact, as stored).")
    parser.add_argument("--to", dest="new_title", required=True,
                        help="New book title.")
    parser.add_argument("--execute", action="store_true",
                        help="Actually perform the rename (also needs a typed CONFIRM).")
    parser.add_argument("--secrets", default=DEFAULT_SECRETS,
                        help=f"Path to secrets.toml (default: {DEFAULT_SECRETS}).")
    parser.add_argument("--log-file", default="cleanup_reports/rename_book.log",
                        help="Write log (default: cleanup_reports/rename_book.log).")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    dry_run = not args.execute
    logger = None

    try:
        secrets = load_secrets(args.secrets)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not dry_run:
        print(
            f"\nYou are about to RENAME the book {args.old_title!r} -> "
            f"{args.new_title!r} in PRODUCTION Firestore + S3.\n"
            "This creates new documents, moves the S3 image folder, and deletes "
            "the old documents. Type CONFIRM (all caps) to proceed."
        )
        try:
            answer = input("Confirm: ")
        except EOFError:
            answer = ""
        if answer.strip() != "CONFIRM":
            print("Aborted - confirmation not given. Nothing was changed.")
            return 1
        logger = _make_logger(args.log_file)
        logger(f"=== rename start: {args.old_title!r} -> {args.new_title!r} ===")

    try:
        renamer = BookRenamer(secrets, dry_run=dry_run, logger=logger)
        rc = renamer.rename(args.old_title, args.new_title)
    except (KeyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if logger is not None:
        logger("=== rename end ===")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
