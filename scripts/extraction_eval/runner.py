"""Sequential, local-only stage orchestration of the shared application core."""
from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
from pathlib import Path
import sqlite3
import json

from .gateway import DryRunMiss, RequestLimitError
from .store import (BudgetStopped, CachedFailure, OutcomeUnknown, atomic_json,
                    canonical, digest, read_json)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATHS = ("ai_runtime.py", "extraction_core.py", "utilities.py", "image_processing.py", "text_content/ai_prompts.py")


def code_fingerprint():
    paths = list(SOURCE_PATHS) + [str(path.relative_to(ROOT)) for path in sorted((ROOT / "scripts/extraction_eval").glob("*.py"))]
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}


def freeze_run(out, snapshot, selection, config, replicate, cap, monthly_cap, ledger):
    out = Path(out)
    path = out / "run.json"
    dependencies = {d.metadata.get("Name", "unknown"): d.version for d in importlib.metadata.distributions()}
    contract = {"version": 1, "snapshot_id": snapshot.get("snapshot_id"),
                "snapshot_hash": digest(snapshot), "selection": selection, "config": config,
                "replicate": str(replicate), "max_spend_usd": cap,
                "monthly_cap_usd": monthly_cap, "ledger": str(Path(ledger).resolve()),
                "sources": code_fingerprint()}
    if path.exists():
        saved = read_json(path)
        if saved.get("contract") != contract or saved.get("dependencies") != dependencies:
            raise ValueError("Run inputs/settings/code/caps changed. Use a new --out; the shared request cache will reuse identical calls")
        return saved
    result = {"run_id": digest({"out": str(out.resolve()), "contract": contract}),
              "contract": contract, "code_revision": "source SHA-256 hashes (no git commands)",
              "dependencies": dependencies}
    atomic_json(path, result)
    atomic_json(out / "selection.json", selection)
    return result


class Stages:
    def __init__(self, out):
        self.db = sqlite3.connect(Path(out) / "eval.sqlite")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS stages(key TEXT PRIMARY KEY,state TEXT,data TEXT)")

    def get(self, key):
        row = self.db.execute("SELECT state,data FROM stages WHERE key=?", (key,)).fetchone()
        return json.loads(row[1]) if row and row[0] in {"completed", "failed"} else None

    def put(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO stages VALUES (?,?,?)", (key,value.get("status", "pending"),canonical(value)))

    def close(self):
        self.db.close()


def estimate(manifest, selection, predictions=None):
    selected = set(selection.get("book_ids", []))
    books = [book for book in manifest.get("books", []) if book.get("id") in selected]
    predictions = (predictions or {}).get("books", {})
    total_pages = sum(len(book.get("pages", [])) for book in books)
    remaining_pages = 0
    remaining_books = 0
    for book in books:
        saved = predictions.get(book.get("id"), {})
        remaining_pages += sum(saved.get("pages", {}).get(page.get("id"), {}).get("status") not in {"completed", "failed"} for page in book.get("pages", []))
        remaining_books += any(saved.get(stage, {}).get("status") not in {"completed", "failed"} for stage in ("metadata", "characters"))
    return {"books": len(books), "pages": total_pages, "pending_pages": remaining_pages,
            "pending_books": remaining_books,
            "estimated_usd": round(2 * (.0111 * remaining_pages + .0103 * remaining_books), 4),
            "full_selected_estimate_usd": round(2 * (.0111 * total_pages + .0103 * len(books)), 4),
            "formula": "2 * (0.0111 * pending_pages + 0.0103 * pending_books)",
            "qualification": "Historical planning estimate, not a quote. Conditional calls and retries vary; exact cache hits are free. Strict per-call reservations may stop below the cap."}


def local_image(snapshot_dir, page):
    image = page.get("image") or {}
    relative = image.get("path")
    if not relative:
        raise FileNotFoundError("Snapshot page has no canonical source image")
    root = Path(snapshot_dir).resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError("Snapshot image path escapes the snapshot")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != image.get("sha256"):
        raise ValueError(f"Snapshot image hash mismatch: {relative}")
    return data


def validate_inputs(snapshot_dir, manifest, selection):
    selected = set(selection.get("book_ids", []))
    for book in manifest.get("books", []):
        if book.get("id") in selected:
            for page in book.get("pages", []):
                if page.get("image"):
                    local_image(snapshot_dir, page)


def isbn_cache_outcome(saved):
    """Classify a frozen provider answer, including legacy poisoned entries."""
    outcome = saved.get("outcome") or {}
    raw = saved.get("raw_response")
    found = saved.get("value") is not None
    absent = isinstance(raw, dict) and raw.get("totalItems") == 0
    if outcome.get("status") == "failed" or not (found or absent):
        return {**outcome, "status": "failed",
                "reason": outcome.get("reason", "no_provider_response")}
    return outcome or {"status": "found" if found else "not_found"}


def metadata_with_lookup_outcome(metadata, out):
    """Recover lookup provenance for old runs without changing their artifacts."""
    value = dict(metadata)
    isbn = value.get("isbn")
    if isbn and not value.get("isbn_lookup") and out is not None:
        path = Path(out) / "lookups" / f"{digest(str(isbn))}.json"
        if path.exists():
            value["isbn_lookup"] = isbn_cache_outcome(read_json(path))
    if (value.get("isbn_lookup") or {}).get("status") == "failed":
        value["lookup_failed_fields"] = [field for field in ("title", "authors", "publisher")
                                         if not value.get(field)]
    return value


def cached_isbn_lookup(out, lookup, *, dry_run=False, capture_raw=False):
    """Freeze provider answers, including absence, but never network failures.

    Old null-value/null-response entries have no evidence of a provider answer
    and are ignored. The optional outcome callback preserves the application's
    dict-or-None contract while carrying failures into metadata scoring.
    """
    try:
        parameters = inspect.signature(lookup).parameters.values()
    except (TypeError, ValueError):
        # Opaque callables retain the original single-positional-argument API.
        parameters = ()
    accepts_keywords = any(parameter.kind == inspect.Parameter.VAR_KEYWORD
                           for parameter in parameters)
    keyword_names = {parameter.name for parameter in parameters
                     if parameter.kind != inspect.Parameter.POSITIONAL_ONLY}

    def fetch(isbn, *, outcome_sink=None):
        path = Path(out) / "lookups" / f"{digest(str(isbn))}.json"
        if path.exists():
            saved = read_json(path)
            outcome = isbn_cache_outcome(saved)
            if outcome.get("status") != "failed":
                if outcome_sink is not None:
                    outcome_sink(outcome)
                return saved.get("value")
        if dry_run:
            raise DryRunMiss("Uncached ISBN lookup; dry run does not access the network")
        raw, outcomes = [], []
        kwargs = {}
        if accepts_keywords or "response_sink" in keyword_names:
            kwargs["response_sink"] = raw.append
        if accepts_keywords or "outcome_sink" in keyword_names:
            kwargs["outcome_sink"] = outcomes.append
        result = lookup(isbn, **kwargs)
        raw_response = raw[-1] if raw else None
        saved = {"isbn": isbn, "value": result, "raw_response": raw_response,
                 "outcome": outcomes[-1] if outcomes else {}}
        outcome = isbn_cache_outcome(saved)
        if outcome.get("status") != "failed":
            atomic_json(path, {**saved, "outcome": outcome})
        if outcome_sink is not None:
            outcome_sink(outcome)
        return result
    return fetch


def run_extract(snapshot_dir, manifest, selection, out, gateway, config, *, dry_run=False, core=None, isbn_lookup=None, progress=print):
    if core is None:
        import extraction_core as core
    capture_lookup_raw = isbn_lookup is None
    if isbn_lookup is None:
        from utilities import lookup_isbn
        isbn_lookup = lookup_isbn
    out = Path(out)
    stages = Stages(out)
    selected = set(selection.get("book_ids", []))
    results = {"version": 1, "snapshot_id": manifest.get("snapshot_id"),
               "run_id": getattr(getattr(gateway, "ledger", None), "run_id", None),
               "books": {}, "dry_run": dry_run}
    settings = config.get("settings", {})
    stop_reason = None
    current_book_id = None
    isbn = cached_isbn_lookup(out, isbn_lookup, dry_run=dry_run, capture_raw=capture_lookup_raw)

    def perform(key, callback, *, refresh_lookup=False):
        nonlocal stop_reason
        gateway.context = {"book_id": current_book_id, "stage": key}
        saved = stages.get(key)
        refreshing_lookup = False
        if saved is not None:
            value = metadata_with_lookup_outcome(saved.get("value") or {}, out) if refresh_lookup else {}
            if saved.get("status") != "completed" or (value.get("isbn_lookup") or {}).get("status") != "failed":
                return saved
            if dry_run:
                return {**saved, "value": value}
            refreshing_lookup = True
            # Preserve vision outputs and retry only the unmeasured dependency.
            def callback():
                from utilities import apply_isbn_metadata
                return {**saved, "value": apply_isbn_metadata(value, isbn_lookup=isbn)}
        if stop_reason and not refreshing_lookup:
            return {"status": "budget_stopped" if stop_reason[0] == "budget_stopped" else "pending", "error": stop_reason[1]}
        try:
            value = callback()
        except DryRunMiss as exc:
            return {"status": "pending", "error": str(exc)}
        except BudgetStopped as exc:
            stop_reason = ("budget_stopped", str(exc))
            value = {"status": "budget_stopped", "error": str(exc)}
        except OutcomeUnknown as exc:
            stop_reason = ("outcome_unknown", str(exc))
            value = {"status": "outcome_unknown", "error": str(exc)}
        except (CachedFailure, RequestLimitError, OSError, ValueError, TypeError) as exc:
            value = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if not dry_run:
            stages.put(key, value)
        return value

    try:
        for book in manifest.get("books", []):
            book_id = book.get("id")
            if book_id not in selected:
                continue
            current_book_id = book_id
            progress(f"Book: {book.get('title', book_id)} ({len(book.get('pages', []))} pages)")
            book_result = {"pages": {}}
            results["books"][book_id] = book_result
            pages = sorted(book.get("pages", []), key=lambda page: page.get("page_number", 0))
            # Metadata sees original ordered images and NO reference page hints.
            def metadata():
                images = [(page.get("page_number"), local_image(snapshot_dir, page)) for page in pages if page.get("image")]
                if not images:
                    raise ValueError("No images for metadata extraction")
                value = core.extract_metadata(images, gateway, settings, gateway.record_usage, isbn)
                return {"status": "completed", "value": value}
            book_result["metadata"] = perform(f"{book_id}:metadata", metadata, refresh_lookup=True)
            for page in pages:
                page_id = page.get("id")
                def extract(page=page):
                    result = core.extract_page(local_image(snapshot_dir, page), gateway, settings, gateway.record_usage)
                    corrected = result.pop("corrected_bytes", None)
                    if corrected is not None:
                        image_path = out / "images" / f"{hashlib.sha256(corrected).hexdigest()}.jpg"
                        image_path.parent.mkdir(parents=True, exist_ok=True)
                        if not image_path.exists():
                            from tempfile import NamedTemporaryFile
                            import os
                            with NamedTemporaryFile(dir=image_path.parent, delete=False) as handle:
                                handle.write(corrected)
                                handle.flush()
                                os.fsync(handle.fileno())
                                temporary = handle.name
                            os.replace(temporary, image_path)
                        result["corrected_path"] = str(image_path.resolve())
                    return result
                book_result["pages"][page_id] = perform(f"{book_id}:page:{page_id}", extract)
            def characters():
                predicted_pages = [dict(book_result["pages"].get(page.get("id"), {}), page_number=page.get("page_number")) for page in pages]
                # Failures supply no text. Unrun pages make the book incomplete:
                # do not freeze a character stage on a partial budget-stopped book.
                if any(row.get("status") not in {"completed", "failed"} for row in predicted_pages):
                    return {"status": "pending", "error": "Waiting for all page stages"}
                value = core.extract_characters(predicted_pages, gateway, settings, gateway.record_usage)
                return {"status": "completed", "value": value, "input": "predicted_story_flags_and_text"}
            book_result["characters"] = perform(f"{book_id}:characters", characters)
            atomic_json(out / ("dry_run_predictions.json" if dry_run else "predictions.json"), results)
        results["incomplete"] = any(stage.get("status") not in {"completed", "failed"} for book in results["books"].values() for stage in [book.get("metadata", {}), book.get("characters", {}), *book.get("pages", {}).values()])
        results["stop_reason"] = stop_reason
        atomic_json(out / ("dry_run_predictions.json" if dry_run else "predictions.json"), results)
        return results
    finally:
        stages.close()
