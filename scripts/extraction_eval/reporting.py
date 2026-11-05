"""Portable local reports from cached artifacts; importing this module is offline."""

from __future__ import annotations

import csv
import difflib
import html
import json
import os
import tempfile
from pathlib import Path

from text_content.alias_metrics import AliasMetrics

from .scoring import aggregate, score_book
from .runner import metadata_with_lookup_outcome


def _atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = handle.name
    os.replace(temporary, path)


def _csv(path, records, fields):
    path = Path(path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                             if isinstance(value, (dict, list)) else value
                             for key, value in record.items()})
        temporary = handle.name
    os.replace(temporary, path)


def _pretty(value):
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _image(path, base, output, label):
    if not path or "://" in str(path):
        return ""
    image_path = Path(path)
    if not image_path.is_absolute():
        image_path = base / image_path
    source = os.path.relpath(image_path.resolve(), output.resolve())
    return f'<figure><img loading="lazy" src="{html.escape(source, quote=True)}" alt="{html.escape(label)}"><figcaption>{html.escape(label)}</figcaption></figure>'


def snapshot_audit_provenance(manifest):
    """Keep missing legacy audit provenance unknown, even if consistency was asserted."""
    recorded = manifest.get("audits")
    recorded = recorded if isinstance(recorded, dict) else {}
    audits = {name: value if isinstance(value, bool) else None
              for name in ("source_changes", "membership")
              for value in (recorded.get(name),)}
    if any(value is None for value in audits.values()):
        status = "unknown"
    elif all(audits.values()):
        status = "audited"
    elif any(audits.values()):
        status = "partially_audited"
    else:
        status = "not_audited"
    consistent = manifest.get("source_consistent")
    if consistent is not False and not (status == "audited" and consistent is True):
        consistent = None
    return {"snapshot_audits": audits, "snapshot_audit_status": status,
            "snapshot_source_consistent": consistent}


def snapshot_audit_description(manifest):
    """Describe the audit coverage and consistency without claiming unrun checks passed."""
    provenance = snapshot_audit_provenance(manifest)
    audits = provenance.get("snapshot_audits", {})
    states = {True: "ran", False: "not run", None: "unknown"}
    consistency = {True: "verified", False: "changes detected", None: "not verified"}
    status = str(provenance.get("snapshot_audit_status", "unknown")).replace("_", " ").upper()
    return (f"Snapshot audits: {status}. "
            f"Source-change audit: {states.get(audits.get('source_changes'), 'unknown')}; "
            f"membership audit: {states.get(audits.get('membership'), 'unknown')}. "
            f"Source consistency: {consistency.get(provenance.get('snapshot_source_consistent'), 'not verified')}.")


def write_reports(manifest, predictions, out, protocol=None, selection=None, calls=None):
    """Write summary.json, books.csv, pages.csv, calls.csv and inspection.html.

    ``manifest['_snapshot_dir']`` and ``predictions['_run_dir']`` resolve local
    image paths. The original manifests need not be changed on disk. Selection
    rows use selected=[{id,role,stratum,inclusion_probability}] and book_ids.
    All filtering uses the frozen selection, never prediction availability.
    """
    protocol = protocol or {}
    selection = selection or {}
    calls = list(calls or [])
    output = Path(out)
    output.mkdir(parents=True, exist_ok=True)
    selected_rows = selection.get("selected", selection.get("books", []))
    sampling = {record.get("id"): record for record in selected_rows if isinstance(record, dict)}
    ids = selection.get("book_ids")
    if ids is None and sampling:
        ids = list(sampling)
    ids = set(ids) if ids is not None else None
    books = [book for book in manifest.get("books", []) if ids is None or book.get("id") in ids]
    if ids is not None and ids - {book.get("id") for book in books}:
        raise ValueError("Selection contains books absent from the snapshot")
    predicted_books = predictions.get("books", {})
    scores = []
    for book in books:
        prediction = predicted_books.get(book.get("id")) or {}
        stage = prediction.get("metadata") or {}
        if stage.get("status") == "completed":
            prediction = {**prediction, "metadata": {**stage, "value": metadata_with_lookup_outcome(
                stage.get("value") or {}, predictions.get("_run_dir"))}}
        score = score_book(book, prediction, protocol)
        sample = sampling.get(book.get("id"), {})
        for key in ("role", "stratum", "inclusion_probability", "holdout_group"):
            if key in sample:
                score[key] = sample[key]
        scores.append(score)
    summary = aggregate(scores, protocol)
    summary.update(snapshot_audit_provenance(manifest))
    summary.update({"snapshot_id": manifest.get("snapshot_id"), "run_id": predictions.get("run_id"),
                    "selection": selection, "snapshot_exclusions": manifest.get("excluded_books", manifest.get("exclusions", [])),
                    "adjudication": manifest.get("adjudication"),
                    "equivalence_version": protocol.get("equivalence_version"),
                    "settled_cost_usd": sum(call.get("cost_usd") or 0 for call in calls),
                    "outstanding_reservations_usd": sum(call.get("reserved_usd") or 0 for call in calls
                                                        if call.get("state") not in {"completed", "settled", "failed"})})
    from .comparison import book_comparison_record, comparison_protocol
    identity = comparison_protocol(protocol, manifest)
    summary["comparison_protocol"] = identity
    summary["book_scores"] = [book_comparison_record(score, protocol_identity=identity) for score in scores]
    cohort_path = Path(__file__).parent / "evidence" / "dev20-v1" / "empty_reference_cohort.json"
    cohort = json.loads(cohort_path.read_text(encoding="utf-8")) if cohort_path.exists() else None
    if cohort and cohort.get("snapshot_id") == manifest.get("snapshot_id"):
        from .monitoring import monitor_empty_reference
        summary["empty_reference_monitoring"] = monitor_empty_reference(manifest, predictions, cohort, selected_ids=ids)
    _atomic_text(output / "summary.json", json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    _atomic_text(output / "book_scores.json", json.dumps(scores, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    _atomic_text(output / "protocol.json", json.dumps(protocol, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    book_rows, page_rows, sections = [], [], []
    snapshot_root = Path(manifest.get("_snapshot_dir", "."))
    run_root = Path(predictions.get("_run_dir", "."))
    for book, score in zip(books, scores):
        book_id = book.get("id")
        actual_book = predicted_books.get(book_id, {})
        book_cost = sum(call.get("cost_usd") or 0 for call in calls
                        if call.get("book_id") == book_id or str(call.get("label", "")).startswith(f"{book_id}/"))
        book_rows.append({"book_id": book_id, "title": book.get("title"), "role": score.get("role", "descriptive"),
                          "pages": len(score["pages"]), "ocr_f1": score["ocr"]["f1"], "ocr": score["ocr"],
                          "wordless": score["wordless"], "metadata": score["metadata"], "characters": score["characters"],
                          "alias_foundation": score["alias_foundation"],
                          "optional_stages": score.get("optional_stages"), "story_context_fallback": score.get("story_context_fallback"),
                          "reference_hash": score["reference_hash"], "cost_usd": book_cost})
        parts = [f'<section><h2>{html.escape(str(book.get("title", book_id)))}</h2>',
                 f'<p>Book: {html.escape(str(book_id))}; settled cost: ${book_cost:.4f}</p>',
                 f'<details><summary>Book scores, metadata and character attachment</summary><pre>{_pretty({key: value for key, value in score.items() if key != "pages"})}</pre></details>']
        for page, page_score in zip(book.get("pages", []), score["pages"]):
            actual = actual_book.get("pages", {}).get(page.get("id"), {})
            expected = page.get("reference", {}).get("text", {}).get("value", "")
            actual_text = actual.get("text", "")
            page_rows.append({"book_id": book_id, "page_id": page.get("id"), "page_number": page.get("page_number"),
                              "reference_status": page_score["ocr"]["reference_status"],
                              "prediction_status": page_score["ocr"]["prediction_status"],
                              "subgroup": page_score["ocr"]["subgroup"], "f1": page_score["ocr"]["f1"],
                              "classification_source": page_score.get("classification_source"),
                              "cer": page_score["ocr"]["cer"], "wer": page_score["ocr"]["wer"],
                              "reference_text": expected, "predicted_text": actual_text,
                              "ocr": page_score["ocr"], "story": page_score["story"], "error": actual.get("error")})
            difference = "\n".join(difflib.unified_diff(str(expected or "").splitlines(), str(actual_text or "").splitlines(),
                                                       fromfile="reference", tofile="prediction", lineterm=""))
            image = page.get("image", {}) or {}
            image_path = image.get("path") if isinstance(image, dict) else image
            parts.extend([f'<article><h3>Page {html.escape(str(page.get("page_number")))}</h3>',
                          f'<p>{html.escape(page_score["ocr"]["subgroup"])}; {html.escape(page_score["ocr"]["prediction_status"])}</p>',
                          '<div class="images">', _image(image_path, snapshot_root, output, "Original image"),
                          _image(actual.get("corrected_path"), run_root, output, "Corrected image"), '</div>',
                          '<div class="texts"><div><h4>Reference</h4>', f'<pre>{html.escape(str(expected or ""))}</pre></div>',
                          f'<div><h4>Prediction</h4><pre>{html.escape(str(actual_text or ""))}</pre></div></div>',
                          f'<details><summary>Token omissions/extras, ordered differences, story and eligibility</summary><pre>{_pretty(page_score)}</pre><pre>{html.escape(difference)}</pre></details>',
                          f'<pre>{html.escape(str(actual.get("error") or ""))}</pre></article>'])
        parts.append("</section>")
        sections.append("\n".join(parts))
    _csv(output / "books.csv", book_rows, ["book_id", "title", "role", "pages", "ocr_f1", "ocr", "wordless", "metadata", "characters", "reference_hash", "cost_usd", "alias_foundation", "optional_stages", "story_context_fallback"])
    _csv(output / "pages.csv", page_rows, ["book_id", "page_id", "page_number", "reference_status", "prediction_status", "subgroup", "classification_source", "f1", "cer", "wer", "reference_text", "predicted_text", "ocr", "story", "error"])
    call_fields = sorted({key for call in calls for key in call}) or ["key", "run_id", "state", "reserved_usd", "cost_usd", "label", "error"]
    _csv(output / "calls.csv", calls, call_fields)
    document = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\' file: data:; style-src \'unsafe-inline\'">'
                '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pilot reference agreement</title>'
                '<style>body{font:16px system-ui;max-width:1200px;margin:auto;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
                'article{border-top:1px solid #bbb;padding:16px 0}.images,.texts{display:flex;gap:20px;flex-wrap:wrap}'
                '.texts>div{flex:1;min-width:260px}figure{margin:0;max-width:46%}img{max-width:100%;max-height:550px}details{padding:12px 0}</style></head><body>'
                '<h1>Pilot reference-agreement inspection</h1>'
                '<p>Imported OCR, cleaning and some metadata are AI-derived. These results measure agreement, not independently verified accuracy. '
                'Wordless agreement has a separate denominator. Disputed wordless pages require human adjudication. '
                'Failed or unrun eligible text recovers zero tokens; incomplete work remains visible.</p>'
                f'<p>{html.escape(AliasMetrics.report_intro)}</p>'
                f'<p><strong>{html.escape(snapshot_audit_description(manifest))}</strong></p>'
                f'<details open><summary>Run summary and denominators</summary><pre>{_pretty(summary)}</pre></details>'
                + "\n".join(sections) + '</body></html>')
    _atomic_text(output / "inspection.html", document)
    return summary
