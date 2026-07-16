#!/usr/bin/env python3
"""Backfill the small ``page_N_display.jpg`` derivatives for legacy books (#78).

Issue #184 introduced a screen-sized display derivative written alongside each
page at processing time, so enter-text/validation ship a ~175 KB image instead
of the multi-MB original. Books entered BEFORE #184 have no ``_display`` copies
(a 2026-07-15 bucket scan found them in only 34 of 245 book folders), so every
page view of a legacy book still pushes its ``_cropped`` re-encode — median
3.8 MB — through the app container to the browser. This one-off CLI generates
the missing derivatives in place.

For every ``page_N.jpg`` original that has no ``page_N_display.jpg`` sibling,
the derivative is generated with the app's own ``image_processing.
make_display_copy`` from the SAME source the app prefers to display:
``page_N_cropped.jpg`` when it exists, else the raw original. Existing display
copies are never touched, so the run is idempotent and safe to re-run.

Phases (mirroring ``scripts/data_cleanup.py``):

    AUDIT / DRY-RUN (default; writes NOTHING)
        Lists the bucket once, prints per-folder and total counts of missing
        display copies and exactly what would be written.

    EXECUTE (guarded)
        Requires BOTH ``--execute`` AND an interactive typed ``BACKFILL``
        confirmation. Each write is logged. Only ever CREATES
        ``page_N_display.jpg`` objects — it never overwrites or deletes.

Usage
-----
    # Dry run (default — writes nothing):
    python scripts/backfill_display_copies.py

    # Restrict to one book folder for a pilot run:
    python scripts/backfill_display_copies.py --folder "Some Book Title"

    # For real (asks you to type BACKFILL):
    python scripts/backfill_display_copies.py --execute
    python scripts/backfill_display_copies.py --execute --limit 200

This script runs OUTSIDE Streamlit, so — per the documented ``s3_constants`` /
``data_cleanup`` exception to the shared-helper rule (#129, CLAUDE.md) — it
loads ``.streamlit/secrets.toml`` directly (reusing ``data_cleanup.
load_secrets``) and builds its own ``s3fs.S3FileSystem`` with the same
credentials the app's ``utilities.get_s3_filesystem`` uses.

The live run must be executed by Chris where the real secrets exist.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass

# Make the repo root importable whether this runs as a standalone CLI
# (``python scripts/backfill_display_copies.py``) or under pytest.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from s3_constants import (  # noqa: E402
    S3_BUCKET,
    NON_BOOK_S3_PREFIXES,
    page_image_number,
)

#: Default path to the Streamlit secrets file (matches data_cleanup).
DEFAULT_SECRETS = ".streamlit/secrets.toml"

#: Default append-only log for execute runs.
DEFAULT_LOG = "cleanup_reports/backfill_display_log.txt"

_DISPLAY_RE = re.compile(r"^page_(\d+)_display\.jpg$", re.IGNORECASE)
_CROPPED_RE = re.compile(r"^page_(\d+)_cropped\.jpg$", re.IGNORECASE)


@dataclass(frozen=True)
class BackfillItem:
    """One missing display derivative: where it comes from, where it goes."""

    folder: str          # book folder name under the bucket
    page_number: int
    source_path: str     # full S3 path of the image to downscale
    dest_path: str       # full S3 path of the display copy to create


def plan_backfill(fs, bucket: str = S3_BUCKET, folder: str | None = None):
    """Scan the bucket ONCE and return the sorted list of missing derivatives.

    Pure planning — performs no writes. ``fs`` needs only ``find`` (anything
    file-listing-compatible with s3fs). Objects under the non-book prefixes
    (e.g. the transient ``uploads/`` area) are ignored. The source for each
    item is ``page_N_cropped.jpg`` when present (what the app displays by
    default), else the raw ``page_N.jpg``.
    """
    prefix = f"{bucket}/{folder}" if folder else bucket
    paths = set(fs.find(prefix))

    originals = {}   # (folder, page) -> raw path
    cropped = set()  # (folder, page)
    displays = set()  # (folder, page)
    for path in paths:
        parts = path.split("/")
        if len(parts) < 3:
            continue  # object directly under the bucket — not a book page
        top_folder = parts[1]
        if top_folder in NON_BOOK_S3_PREFIXES:
            continue
        name = parts[-1]
        page = page_image_number(name)
        if page is not None:
            originals[(top_folder, page)] = path
            continue
        match = _CROPPED_RE.match(name)
        if match:
            cropped.add((top_folder, int(match.group(1))))
            continue
        match = _DISPLAY_RE.match(name)
        if match:
            displays.add((top_folder, int(match.group(1))))

    items = []
    for (book_folder, page), raw_path in sorted(originals.items()):
        if (book_folder, page) in displays:
            continue  # already has a display copy — never touched
        if (book_folder, page) in cropped:
            source = f"{bucket}/{book_folder}/page_{page}_cropped.jpg"
        else:
            source = raw_path
        items.append(BackfillItem(
            folder=book_folder,
            page_number=page,
            source_path=source,
            dest_path=f"{bucket}/{book_folder}/page_{page}_display.jpg",
        ))
    return items


def run_backfill(fs, items, *, execute: bool, log=print):
    """Generate + upload the display copies (or just narrate them, dry-run).

    Returns ``(written, skipped_errors)``. A per-item failure (unreadable or
    undecodable source image) is logged and skipped — one bad page must never
    abort the run — and counted in ``skipped_errors``. Only ``dest_path``
    objects are ever written; nothing is overwritten or deleted.
    """
    # Imported here so ``plan_backfill`` and the tests of the planning logic
    # stay importable without the imaging stack.
    import io

    from PIL import Image, UnidentifiedImageError

    from image_processing import make_display_copy

    written = 0
    errors = 0
    for item in items:
        label = f"{item.dest_path}  (from {os.path.basename(item.source_path)})"
        if not execute:
            log(f"DRY-RUN would write: {label}")
            continue
        try:
            with fs.open(item.source_path, "rb") as f:
                source_bytes = f.read()
            display_bytes = make_display_copy(source_bytes)
            # ``make_display_copy`` returns UNDECODABLE input unchanged (that
            # suits the app's best-effort display path, not a backfill): verify
            # the derivative actually decodes before uploading, so a corrupt
            # legacy source can never gain a broken _display object.
            with Image.open(io.BytesIO(display_bytes)) as check:
                check.verify()
            with fs.open(item.dest_path, "wb") as f:
                f.write(display_bytes)
        except (FileNotFoundError, OSError, ValueError, UnidentifiedImageError) as exc:
            errors += 1
            log(f"ERROR skipping {item.dest_path}: {type(exc).__name__}: {exc}")
            continue
        written += 1
        log(f"wrote: {label}  ({len(display_bytes)} bytes)")
    return written, errors


def _summarise(items) -> str:
    per_folder = {}
    for item in items:
        per_folder[item.folder] = per_folder.get(item.folder, 0) + 1
    lines = [f"Missing display copies: {len(items)} pages across "
             f"{len(per_folder)} book folder(s)."]
    for book_folder in sorted(per_folder):
        lines.append(f"  {book_folder}: {per_folder[book_folder]}")
    return "\n".join(lines)


def _build_fs(secrets):
    """s3fs filesystem with the app's credentials (standalone-CLI exception to
    ``utilities.get_s3_filesystem`` — no Streamlit here; see module docstring)."""
    import s3fs

    for required in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if required not in secrets:
            raise KeyError(f"secrets is missing required '{required}'")
    return s3fs.S3FileSystem(
        anon=False,
        key=secrets["AWS_ACCESS_KEY_ID"],
        secret=secrets["AWS_SECRET_ACCESS_KEY"],
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill missing page_N_display.jpg derivatives (#78). "
                    "Dry-run by default; --execute + typed BACKFILL to write.",
    )
    parser.add_argument("--execute", action="store_true",
                        help="Actually upload the derivatives (also asks for a "
                             "typed BACKFILL confirmation). Default: dry run.")
    parser.add_argument("--folder", default=None,
                        help="Restrict to one book folder (pilot run).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N pages this run (resumable — "
                             "already-written copies are skipped next time).")
    parser.add_argument("--secrets", default=DEFAULT_SECRETS,
                        help=f"Path to secrets.toml (default {DEFAULT_SECRETS}).")
    parser.add_argument("--log-file", default=DEFAULT_LOG,
                        help=f"Append-only log for execute runs (default {DEFAULT_LOG}).")
    args = parser.parse_args(argv)

    # Reuse the cleanup CLI's secrets loader + logger (#129).
    from scripts.data_cleanup import load_secrets, _make_logger

    secrets = load_secrets(args.secrets)
    fs = _build_fs(secrets)

    print("Scanning bucket (read-only)...")
    items = plan_backfill(fs, folder=args.folder)
    print(_summarise(items))
    if not items:
        print("Nothing to do.")
        return 0
    if args.limit is not None:
        items = items[: args.limit]
        print(f"Limiting this run to {len(items)} page(s).")

    if not args.execute:
        run_backfill(fs, items, execute=False)
        print(
            f"\nDRY RUN complete — nothing was written. To run for real:\n"
            f"  python {sys.argv[0]} --execute"
            + (f" --folder \"{args.folder}\"" if args.folder else "")
        )
        return 0

    print(
        f"\nYou are about to UPLOAD {len(items)} new page_N_display.jpg object(s) "
        f"to the PRODUCTION '{S3_BUCKET}' bucket.\n"
        "Nothing is overwritten or deleted. Type BACKFILL (all caps) to proceed, "
        "or anything else to abort."
    )
    try:
        answer = input("Confirm: ")
    except EOFError:
        answer = ""
    if answer.strip() != "BACKFILL":
        print("Aborted - confirmation not given. Nothing was written.")
        return 1

    file_log = _make_logger(args.log_file)

    def log(msg):
        print(msg)
        file_log(msg)

    log(f"=== display backfill start: {len(items)} page(s) ===")
    written, errors = run_backfill(fs, items, execute=True, log=log)
    log(f"=== display backfill done: wrote {written}, errors {errors} ===")
    print(f"\nDone: wrote {written} display cop(ies), {errors} error(s). "
          f"Log: {args.log_file}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
