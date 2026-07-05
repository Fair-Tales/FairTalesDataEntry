#!/usr/bin/env python3
"""Regenerable human-review export for flagged pages (issue #73 follow-up).

The pilot import's clean+judge pass (``scripts/import_pilot_data.py``) marks
individual ``pages`` documents ``needs_review=True`` (with ``review_priority``
"high"/"" and a human-readable ``review_note``) when a page's text doesn't read
as coherent story text or doesn't fit its neighbours. Those flags are queryable
in Firestore but not otherwise visible anywhere a human would look, so this
standalone (Streamlit-free) script streams the ``pages`` collection, resolves
each flagged page's book title, and emits a prioritized markdown report.

This connects to Firestore directly the same way ``import_pilot_data.py``
does (``load_secrets`` + a ``google.cloud.firestore`` client for project
``sawdataentry``) rather than via the app's ``FirestoreWrapper``, which needs a
live Streamlit session (#129 — reuse the existing direct-connection recipe
rather than inventing a second one).

Usage:
    .venv/bin/python scripts/export_review_pages.py [--out reports/pages_to_review.md]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

# This script's own directory is ``scripts/``; add it to the path so the bare
# ``import_pilot_data`` module (which has no package ``__init__.py``) resolves
# regardless of the current working directory (mirrors import_pilot_data.py's
# own repo-root sys.path fix-up, and scripts/test_import_pilot_data.py's import
# style).
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from import_pilot_data import DEFAULT_SECRETS, FIRESTORE_PROJECT, load_secrets  # noqa: E402

DEFAULT_OUT = "reports/pages_to_review.md"


def _make_firestore_client(secrets_path: str):
    """Build a ``google.cloud.firestore.Client`` from ``secrets.toml`` (mirrors
    ``import_pilot_data.LiveBackend.__init__`` — read-only here, so no S3 client
    is needed)."""
    from google.cloud import firestore
    from google.oauth2 import service_account

    secrets = load_secrets(secrets_path)
    firestore_key = secrets.get("firestore_key")
    if not firestore_key:
        raise KeyError("secrets is missing required 'firestore_key'")
    key_info = json.loads(firestore_key) if isinstance(firestore_key, str) else firestore_key
    creds = service_account.Credentials.from_service_account_info(key_info)
    return firestore.Client(credentials=creds, project=FIRESTORE_PROJECT)


def _book_id_from_page_doc(doc_id: str, page_number: object) -> Optional[str]:
    """Recover ``book_id`` from a page doc id ``"{book_id}_{page_number}"`` by
    stripping the known ``_{page_number}`` suffix (see ``page_document_id`` in
    ``import_pilot_data.py``). Returns ``None`` if the doc id doesn't end with
    that exact suffix (unexpected shape), so a caller can fall back or skip
    rather than silently mis-attributing the page to the wrong book."""
    suffix = f"_{page_number}"
    if page_number is not None and doc_id.endswith(suffix):
        return doc_id[: -len(suffix)]
    return None


def collect_flagged_pages(db) -> list[dict]:
    """Stream ``pages``, keep ``needs_review == True`` rows, and resolve each
    row's book title via a ``{book_id: title}`` map built from ``books``.

    Uses ``filter=FieldFilter(...)`` per the project's Firestore query
    convention.
    """
    from google.cloud.firestore_v1.base_query import FieldFilter

    book_titles: dict[str, str] = {}
    for doc in db.collection("books").stream():
        data = doc.to_dict() or {}
        book_titles[doc.id] = data.get("title") or doc.id

    flagged = []
    query = db.collection("pages").where(filter=FieldFilter("needs_review", "==", True))
    for doc in query.stream():
        data = doc.to_dict() or {}
        page_number = data.get("page_number")

        book_ref = data.get("book")
        book_id = getattr(book_ref, "id", None)
        if not book_id:
            book_id = _book_id_from_page_doc(doc.id, page_number)

        title = book_titles.get(book_id, book_id or "(unknown book)")

        flagged.append(
            {
                "book_id": book_id,
                "title": title,
                "page_number": page_number,
                "priority": data.get("review_priority") or "",
                "note": data.get("review_note") or "",
            }
        )

    return flagged


def render_markdown(flagged: list[dict]) -> str:
    """Render the prioritized markdown report: summary line, then a single
    ``Priority | Book | Page | Issue`` table sorted HIGH-first, then by title,
    then by page number."""
    total = len(flagged)
    high_count = sum(1 for row in flagged if row["priority"] == "high")
    books = {row["book_id"] for row in flagged}
    book_count = len(books)

    def sort_key(row):
        # HIGH priority first (False < True, so "not high" sorts high-first).
        return (row["priority"] != "high", (row["title"] or "").lower(), row["page_number"] or 0)

    ordered = sorted(flagged, key=sort_key)

    lines = [
        "# Pages flagged for review",
        "",
        f"{total} page(s) flagged, {high_count} high-priority, across {book_count} book(s).",
        "",
        "| Priority | Book | Page | Issue |",
        "| --- | --- | --- | --- |",
    ]
    for row in ordered:
        priority_label = "HIGH" if row["priority"] == "high" else "normal"
        note = row["note"].strip() if row["note"] else ""
        note = note.replace("|", "\\|").replace("\n", " ") if note else "(no note)"
        title = str(row["title"]).replace("|", "\\|")
        page = row["page_number"] if row["page_number"] is not None else "?"
        lines.append(f"| {priority_label} | {title} | {page} | {note} |")

    lines.append("")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Export a prioritized markdown report of pages flagged needs_review=True."
    )
    p.add_argument(
        "--secrets",
        default=DEFAULT_SECRETS,
        help=f"Path to .streamlit/secrets.toml (default: {DEFAULT_SECRETS}).",
    )
    p.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help=f"Output markdown path (default: {DEFAULT_OUT}); the parent directory is created.",
    )
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    db = _make_firestore_client(args.secrets)
    flagged = collect_flagged_pages(db)
    markdown = render_markdown(flagged)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(markdown)

    high_count = sum(1 for row in flagged if row["priority"] == "high")
    books = {row["book_id"] for row in flagged}
    print(
        f"Wrote {args.out}: {len(flagged)} flagged page(s), {high_count} high-priority, "
        f"across {len(books)} book(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
