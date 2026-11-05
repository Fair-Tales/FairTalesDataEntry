"""Offline planning estimates: obtaining an estimate must never consume reads."""
from __future__ import annotations

import json
from pathlib import Path

from .store import read_json


def _completed_query_allowance(queries):
    """Credit the per-variant allowance for saved, fully consumed variants."""
    allowance = 0
    for key, state in queries.items():
        identity = json.loads(key)
        if not isinstance(identity, list) or len(identity) != 2:
            raise ValueError("Invalid saved snapshot query identity")
        collection = next(iter(identity))
        variants = 2 if collection == "books" else 3
        completed = variants if state.get("done") else state.get("cursor", {}).get("value_index", 0)
        if type(completed) is not int or not 0 <= completed <= variants:
            raise ValueError("Invalid saved snapshot query cursor")
        allowance += completed
    return allowance


def estimate_snapshot_reads(out, *, books=None, estimated_books=195,
                            estimated_documents_per_book=50,
                            audit_source_changes=False, audit_membership=False):
    """Estimate remaining billed document reads using only arguments/local state.

    The default 50 documents/book rounds up the observed 9,055/195 corpus.
    Discovery still reads the entire entered-by population for a subset export.
    Each logical query searches legacy representations: two for book discovery,
    three for each related collection. Empty variants each have a minimum read.
    This is a planning estimate, never an enforceable quota or an upper bound.
    """
    if estimated_books < 1 or estimated_documents_per_book < 1:
        raise ValueError("Read estimates require positive book and document counts")
    out = Path(out)
    population = max(estimated_books, len(set(books or [])))
    selected = len(set(books)) if books else population
    capture = population + selected * (estimated_documents_per_book - 1)
    capture_empty = 2 + 9 * selected
    source = selected * estimated_documents_per_book
    membership = selected * (estimated_documents_per_book - 1)
    membership_empty = 9 * selected
    expected_memberships = None
    captured = 0
    basis = "planning assumptions"
    inventory_path = out / "inventory.json"
    progress_path = out / "inventory-progress.json"
    if inventory_path.exists():
        inventory = read_json(inventory_path)
        records = inventory.get("records", [])
        selected = len(records)
        captured = len(inventory.get("raw_documents", {})) + len(inventory.get("missing_documents", []))
        capture = capture_empty = 0
        source = captured
        expected_memberships = {
            json.dumps([collection, record.get("book", {}).get("id")]):
                len(record.get("related", {}).get(collection, []))
            for record in records for collection in ("pages", "characters", "aliases")
        }
        membership = sum(expected_memberships.values())
        membership_empty = 9 * selected
        basis = "frozen local inventory"
    elif progress_path.exists():
        inventory = read_json(progress_path)
        captured = len(inventory.get("raw_documents", {})) + len(inventory.get("missing_documents", []))
        capture = max(0, capture - captured)
        capture_empty = max(0, capture_empty - _completed_query_allowance(inventory.get("queries", {})))
        basis = "planning assumptions minus saved inventory progress"
    audit_path = out / "audit-progress.json"
    if audit_path.exists():
        progress = read_json(audit_path)
        source = max(0, source - len(progress.get("checked_documents", []))
                     - len(progress.get("checked_missing_documents", [])))
        queries = progress.get("queries", {})
        if expected_memberships is not None:
            membership = sum(
                0 if queries.get(key, {}).get("done") else
                max(0, count - len(queries.get(key, {}).get("paths", [])))
                for key, count in expected_memberships.items())
        else:
            membership = max(0, membership - sum(len(state.get("paths", [])) for state in queries.values()))
        membership_empty = max(0, membership_empty - _completed_query_allowance(queries))
    components = {
        "capture": capture + capture_empty,
        "source_change_audit": source if audit_source_changes else 0,
        "membership_audit": membership + membership_empty if audit_membership else 0,
    }
    if (out / "manifest.json").exists():
        components = dict.fromkeys(components, 0)
        basis = "completed local snapshot; no service reads"
    return {
        "estimated_firestore_reads": sum(components.values()),
        "remaining_reads_by_pass": components,
        "basis": basis,
        "estimated_entered_by_population": population,
        "estimated_selected_books": selected,
        "estimated_documents_per_book": estimated_documents_per_book,
        "captured_documents_reused": captured,
        "qualification": (
            "Planning estimate, not a hard cap or available-quota check. Actual corpus size, "
            "references, concurrent changes and empty queries can change the charge. "
            "The default assumes 195 books and 50 documents/book (measured: 9,055/195); "
            "increase --estimated-books/--estimated-documents-per-book for larger sources. "
            "--books still discovers the entire entered-by population. Adds a conservative "
            "one-read allowance for every legacy query variant, including wholly empty "
            "collections (2 discovery variants and 9 related variants/book); nonempty "
            "variants may use less of this allowance. S3 operations are separate."
        ),
    }
