"""Sequential, local-only stage orchestration of the shared application core."""
from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
from pathlib import Path
import sqlite3
import json

from .gateway import DryRunMiss, RequestLimitError
from .billing import BillingStopped
from .store import (BudgetStopped, CachedFailure, OutcomeUnknown, atomic_json,
                    canonical, digest, read_json)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATHS = ("ai_runtime.py", "extraction_core.py", "utilities.py", "image_processing.py",
                "text_content/ai_prompts.py", "conventions.py", "bibliography.py",
                "character_evidence.py", "alias_schema.py", "alias_pipeline.py",
                "text_content/pipeline_prompts.py", "text_content/pipeline_examples.json",
                "text_content/pipeline_runtime.py", "text_content/publication.py")


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


def cached_isbn_lookup(out, lookup, *, dry_run=False, capture_raw=False,
                       replay_only=False, frozen_lookup_dir=None):
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
        path = Path(frozen_lookup_dir or Path(out) / "lookups") / f"{digest(str(isbn))}.json"
        if path.exists():
            saved = read_json(path)
            outcome = isbn_cache_outcome(saved)
            if outcome.get("status") != "failed":
                if outcome_sink is not None:
                    outcome_sink(outcome)
                return saved.get("value")
        if dry_run:
            raise DryRunMiss("Uncached ISBN lookup; dry run does not access the network")
        if replay_only:
            if outcome_sink is not None:
                outcome_sink({'status': 'failed', 'reason': 'frozen_lookup_unavailable'})
            return None
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
    replay_only = isbn_lookup is None
    capture_lookup_raw = False
    if isbn_lookup is None:
        isbn_lookup = lambda isbn: None
    out = Path(out)
    stages = Stages(out)
    selected = set(selection.get("book_ids", []))
    from conventions import convention_metadata
    results = {"version": 2, "snapshot_id": manifest.get("snapshot_id"),
               "run_id": getattr(getattr(gateway, "ledger", None), "run_id", None),
               "books": {}, "dry_run": dry_run, "conventions": convention_metadata()}
    settings = config.get("settings", {})
    stop_reason = None
    current_book_id = None
    isbn = cached_isbn_lookup(out, isbn_lookup, dry_run=dry_run, capture_raw=capture_lookup_raw,
                              replay_only=replay_only,
                              frozen_lookup_dir=config.get('frozen_lookup_dir'))

    def perform(key, callback, *, refresh_lookup=False):
        nonlocal stop_reason
        gateway.context = {"book_id": current_book_id, "stage": key}
        if stop_reason and stop_reason[0] == 'billing_stopped':
            return {'status': 'pending', 'error': stop_reason[1]}
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
        except BillingStopped as exc:
            stop_reason = ('billing_stopped', str(exc))
            value = {'status': 'billing_stopped', 'error': str(exc)}
        except (CachedFailure, RequestLimitError, OSError, ValueError, TypeError) as exc:
            value = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            if getattr(exc, 'failure_kind', None):
                value.update(failure_kind=exc.failure_kind,
                             raw_response=getattr(exc, 'raw_response', None),
                             raw_text=getattr(exc, 'raw_text', None))
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
                status = 'failed' if value.get('metadata_extraction_status') == 'invalid_response' else 'completed'
                return {"status": status, "value": value}
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
            predicted_pages = [dict(book_result['pages'].get(page.get('id'), {}),
                                    page_number=page.get('page_number')) for page in pages]
            page_stages_terminal = all(row.get('status') in {'completed', 'failed'} for row in predicted_pages)

            def context():
                if not page_stages_terminal:
                    from text_content.pipeline_runtime import CONTEXT_PENDING
                    return {'status': 'pending', 'error': CONTEXT_PENDING}
                images = []
                for page, prediction in zip(pages, predicted_pages):
                    if page.get('image'):
                        corrected = prediction.get('corrected_path')
                        image = Path(corrected).read_bytes() if corrected else local_image(snapshot_dir, page)
                        images.append((page.get('page_number'), image))
                return core.classify_book_context(predicted_pages, images, gateway, settings, gateway.record_usage)

            if settings.get('story_context_enabled', False):
                book_result['story_context'] = perform(f'{book_id}:story_context', context)
                from extraction_core import apply_context_classification
                final_pages = apply_context_classification(predicted_pages, book_result['story_context'])
                book_result['pages'] = {page.get('id'): result for page, result in zip(pages, final_pages)}

            def reconcile():
                if not page_stages_terminal:
                    return {'status': 'pending', 'error': 'Waiting for all page stages'}
                initial = book_result.get('metadata', {})
                if initial.get('status') != 'completed':
                    return {'status': 'failed', 'error': 'Initial metadata is unavailable'}
                images = [(page.get('page_number'), local_image(snapshot_dir, page))
                          for page in pages if page.get('image')]
                value = core.reconcile_metadata(initial.get('value', {}), predicted_pages, images,
                                                gateway, settings, gateway.record_usage)
                failed = (value.get('metadata_extraction_status') == 'invalid_response'
                          or value.get('metadata_finalization', {}).get('status') == 'failed')
                return {'status': 'failed' if failed else 'completed', 'value': value}

            if settings.get('metadata_reconciliation_enabled', False):
                book_result['metadata_initial'] = book_result.get('metadata')
                book_result['metadata_reconciliation'] = perform(f'{book_id}:metadata_reconciliation', reconcile)
                if book_result['metadata_reconciliation'].get('status') == 'completed':
                    book_result['metadata'] = book_result['metadata_reconciliation']
            def characters():
                predicted_pages = [dict(book_result["pages"].get(page.get("id"), {}), page_number=page.get("page_number")) for page in pages]
                # Failures supply no text. Unrun pages make the book incomplete:
                # do not freeze a character stage on a partial budget-stopped book.
                if any(row.get("status") not in {"completed", "failed"} for row in predicted_pages):
                    return {"status": "pending", "error": "Waiting for all page stages"}
                if (settings.get('story_context_enabled', False)
                        and book_result.get('story_context', {}).get('status') not in {'completed', 'failed'}):
                    return {'status': 'pending', 'error': 'Waiting for whole-book classification'}
                value = core.extract_characters(predicted_pages, gateway, settings, gateway.record_usage)
                from conventions import project_analysis_text
                input_pages = [{'page_id': source.get('id'), 'page_number': row.get('page_number'),
                                'text': project_analysis_text(row).get('analysis_text', '')}
                               for source, row in zip(pages, predicted_pages)
                               if row.get('status') == 'completed' and row.get('contains_story') is True
                               and project_analysis_text(row).get('analysis_text', '').strip()]
                return {"status": "completed", "value": value,
                        'raw_response': getattr(value, 'raw_response', None),
                        "input": "predicted_story_flags_and_text",
                        'input_pages': input_pages,
                        'conventions': convention_metadata(),
                        'input_coverage': {'total_pages': len(predicted_pages),
                                           'included_pages': len(input_pages),
                                           'eligible_story_pages': sum(row.get('status') == 'completed' and row.get('contains_story') is True for row in predicted_pages),
                                           'unresolved_pages': sum(row.get('contains_story') is None for row in predicted_pages)}}
            book_result["characters"] = perform(f"{book_id}:characters", characters)
            atomic_json(out / ("dry_run_predictions.json" if dry_run else "predictions.json"), results)
        results["incomplete"] = bool(stop_reason) or any(stage.get("status") not in {"completed", "failed"} for book in results["books"].values() for stage in [book.get("metadata", {}), book.get("characters", {}), *book.get("pages", {}).values()])
        results["stop_reason"] = stop_reason
        atomic_json(out / ("dry_run_predictions.json" if dry_run else "predictions.json"), results)
        return results
    finally:
        stages.close()
