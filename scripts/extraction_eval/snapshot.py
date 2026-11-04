"""Read-only, resumable export. Importing this module opens no cloud clients."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from s3_constants import S3_BUCKET, book_folder_name, is_page_image, page_image_number

SNAPSHOT_VERSION = "pilot-snapshot-v1"
EXCLUDED_METADATA_ONLY_TITLES = frozenset({
    "I Love Christmas", "Invisible Isabelle", "Seasons",
    "Ten Wriggly, Wiggly Caterpillars", "Things You Should Know about Bugs",
})
_UNKNOWN = {"", "unknown", "n/a", "none", "null"}


def json_value(value: Any) -> Any:
    """Preserve absent keys and encode document refs without dereferencing them."""
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "path") and hasattr(value, "id"):
        return {"document_reference": value.path}
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported snapshot value: {type(value).__name__}")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _reference_path(value: Any, collection: str | None = None) -> str | None:
    if isinstance(value, dict):
        return value.get("document_reference")
    if hasattr(value, "path"):
        return value.path
    if isinstance(value, str) and value.strip():
        return value if "/" in value else f"{collection}/{value}" if collection else value
    return None


def _field(value: Any, provenance: str, *, status: str | None = None, **extra) -> dict:
    if status is None:
        status = "missing" if value is None or (isinstance(value, str) and
                   value.strip().casefold() in _UNKNOWN) else "observed"
    return {"value": value, "status": status, "provenance": provenance, **extra}


class SnapshotBackend(Protocol):
    """The intentionally read-only seam used by offline fixtures and the CLI."""
    def iter_books(self, entered_by: str): ...
    def iter_related(self, collection: str, book_id: str): ...
    def get_document(self, path: str): ...
    def list_images(self, folder: str): ...
    def read_image(self, path: str, version_id: str | None = None) -> bytes: ...
    def image_info(self, path: str): ...


class ReadOnlyCloudBackend:
    """Read methods only; require IAM read-only Firestore/S3 credentials as well."""
    def __init__(self, db, fs):
        self._db, self._fs = db, fs
        # Preserve pinned version GETs when the bucket supplies VersionId.
        # This changes local filesystem configuration only, not S3 state.
        self._fs.version_aware = True

    @staticmethod
    def _document(snapshot):
        if not snapshot.exists:
            return None
        return {"id": snapshot.id, "path": snapshot.reference.path,
                "data": json_value(snapshot.to_dict() or {}),
                "update_time": json_value(snapshot.update_time)}

    def _query(self, collection, field, values):
        from google.cloud.firestore_v1.base_query import FieldFilter
        seen = set()
        for value in values:
            for item in self._db.collection(collection).where(
                    filter=FieldFilter(field, "==", value)).stream():
                if item.id not in seen:
                    seen.add(item.id)
                    yield self._document(item)

    def iter_books(self, entered_by):
        return self._query("books", "entered_by", [entered_by,
                          self._db.collection("users").document(entered_by)])

    def iter_related(self, collection, book_id):
        return self._query(collection, "book", [
            self._db.collection("books").document(book_id), f"books/{book_id}", book_id])

    def get_document(self, path):
        return self._document(self._db.document(path).get())

    @staticmethod
    def _object(info):
        return {"path": info.get("name"), "etag": info.get("ETag"),
                "version_id": info.get("VersionId"), "size": info.get("size"),
                "last_modified": json_value(info.get("LastModified"))}

    def list_images(self, folder):
        prefix = f"{S3_BUCKET}/{folder}"
        try:
            objects = self._fs.find(prefix, detail=True, refresh=True)
        except FileNotFoundError:
            return []
        result = []
        for path in objects:
            if is_page_image(path) and str(Path(path).parent) == prefix:
                # LIST metadata omits VersionId even in a versioned bucket.
                # HEAD freezes the exact source revision before downloading.
                info = self.image_info(path)
                if info is None:
                    raise FileNotFoundError(f"Source object vanished while listing: {path}")
                result.append(info)
        return result

    def read_image(self, path, version_id=None):
        options = {"version_id": version_id} if version_id else {}
        with self._fs.open(path, "rb", **options) as stream:
            return stream.read()

    def image_info(self, path):
        try:
            return self._object(self._fs.info(path, refresh=True))
        except FileNotFoundError:
            return None


def create_live_backend(secrets_path=".streamlit/secrets.toml", *, project="sawdataentry"):
    """Explicit CLI-only construction; reuse the documented standalone #129 path."""
    from scripts.data_cleanup import FirestoreS3Backend, load_secrets
    connections = FirestoreS3Backend(load_secrets(str(secrets_path)), project=project)
    return ReadOnlyCloudBackend(connections._db, connections._fs)


def _normalise_document(document: dict, collection: str) -> dict:
    result = json_value(document)
    if not result.get("id"):
        raise ValueError(f"Document in {collection} has no id")
    result.setdefault("path", f"{collection}/{result.get('id')}")
    result.setdefault("update_time", None)
    result.setdefault("data", {})
    return result


def _collect_inventory(backend, entered_by, books):
    candidates = sorted((_normalise_document(d, "books")
                         for d in backend.iter_books(entered_by)), key=lambda d: d.get("id"))
    requested = set(books or [])
    matched = {value for d in candidates for value in (d.get("id"), d.get("data", {}).get("title"))}
    if requested - matched:
        raise ValueError(f"Unknown requested books: {sorted(requested - matched)}")
    if requested:
        candidates = [d for d in candidates if d.get("id") in requested or
                      d.get("data", {}).get("title") in requested]
    all_docs, records, excluded, audit = {}, [], [], []
    missing_documents = set()

    def retain(document, collection):
        doc = _normalise_document(document, collection)
        all_docs[doc.get("path")] = doc
        return doc

    def resolve(path):
        if path in all_docs:
            return all_docs.get(path)
        doc = backend.get_document(path)
        if doc is None:
            missing_documents.add(path)
            audit.append({"issue": "dangling_reference", "path": path})
            return None
        return retain(doc, path.split("/", 1)[0])

    folders = {}
    for original in candidates:
        book = retain(original, "books")
        data, book_id = book.get("data", {}), book.get("id")
        title = data.get("title", "")
        if title in EXCLUDED_METADATA_ONLY_TITLES and entered_by == "pilot_import":
            excluded.append({"id": book_id, "title": title,
                             "reason": "metadata_only_no_photos_measured_2026-11-04"})
            continue
        folder = book_folder_name(title, data.get("photos_url") or "")
        if not folder or folder in {".", ".."} or "/" in folder:
            raise ValueError(f"Unsafe/absent image folder for {book_id}")
        folders.setdefault(folder, []).append(book_id)
        related = {}
        for collection in ("pages", "characters", "aliases"):
            related[collection] = [retain(d, collection)
                                   for d in backend.iter_related(collection, book_id)]
        for name, collection in (("author", "authors"), ("illustrator", "illustrators"),
                                  ("publisher", "publishers")):
            path = _reference_path(data.get(name), collection)
            if path:
                resolve(path)
        for ref in data.get("characters") or []:
            path = _reference_path(ref, "characters")
            if path:
                document = resolve(path)
                if document and document.get("id") not in {
                        d.get("id") for d in related.get("characters", [])}:
                    related.get("characters", []).append(document)
        images = sorted((json_value(info) for info in backend.list_images(folder)
                         if is_page_image(info.get("path", ""))), key=lambda i: i.get("path"))
        records.append({"book": book, "related": related, "images": images, "folder": folder})
    for folder, ids in folders.items():
        if len(ids) > 1:
            audit.append({"issue": "ambiguous_book_folder", "folder": folder, "book_ids": ids})
    return {"version": SNAPSHOT_VERSION, "entered_by": entered_by,
            "requested_books": sorted(requested), "created_at": datetime.now(timezone.utc).isoformat(),
            "records": records, "raw_documents": all_docs, "missing_documents": sorted(missing_documents),
            "excluded_books": excluded, "audit": audit}


def _page_reference(raw: dict, *, ambiguous=False):
    story = raw.get("contains_story")
    story_status = "observed" if isinstance(story, bool) else "missing" if story is None else "ambiguous"
    if "text" not in raw or raw.get("text") is None:
        text = _field(None, "pilot_pdf_or_ai_ocr_then_optional_ai_cleaning", status="missing", kind="unknown")
    elif not isinstance(raw.get("text"), str):
        text = _field(raw.get("text"), "pilot_pdf_or_ai_ocr_then_optional_ai_cleaning",
                      status="ambiguous", kind="unknown")
    else:
        value = raw.get("text")
        kind = "wordless" if not value.strip() and story is True else "text"
        status = "not_annotated" if not value.strip() and story is False else "observed"
        if not value.strip() and not isinstance(story, bool):
            status, kind = "ambiguous", "unknown"
        text = _field(value, "pilot_pdf_or_ai_ocr_then_optional_ai_cleaning", status=status, kind=kind)
        if raw.get("text_source"):
            text["source_detail"] = raw.get("text_source")
    if ambiguous:
        text["status"], story_status = "ambiguous", "ambiguous"
    return {"text": text, "contains_story": _field(story,
            "pilot_spreadsheet_range_or_all_pages_default", status=story_status)}


def _metadata(data, docs):
    result = {"title": _field(data.get("title"), "pilot_spreadsheet"),
              "published_year": _field(data.get("published"), "pilot_spreadsheet_or_ai_lookup",
                  status="observed" if isinstance(data.get("published"), int) and
                  not isinstance(data.get("published"), bool) and data.get("published") > 0 else "missing")}
    for source, name, collection, provenance in (
            ("author", "authors", "authors", "pilot_spreadsheet_single_credit_may_be_incomplete"),
            ("illustrator", "illustrators", "illustrators", "pilot_ai_web_lookup"),
            ("publisher", "publisher", "publishers", "pilot_ai_web_lookup")):
        path = _reference_path(data.get(source), collection)
        entity = (docs.get(path) or {}).get("data", {})
        legacy_name = " ".join(str(entity.get(k) or "").strip() for k in ("forename", "surname")).strip()
        value = legacy_name if source == "author" else entity.get("name") or legacy_name
        record = _field(value or None, provenance)
        record["reference_path"] = path
        if source != "publisher":
            record["value"] = [value] if record.get("status") == "observed" else []
            record["complete"] = False
        result[name] = record
    return result


def _build_book(record, docs, copied, audit):
    book_doc, related = record.get("book", {}), record.get("related", {})
    data, book_id = book_doc.get("data", {}), book_doc.get("id")
    by_number, images_by_number = {}, {}
    for document in related.get("pages", []):
        raw, page_id = document.get("data", {}), document.get("id")
        number = raw.get("page_number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            audit.append({"issue": "invalid_page_number", "book_id": book_id, "document": document})
            continue
        by_number.setdefault(number, []).append(document)
        if page_id != f"{book_id}_{number}":
            audit.append({"issue": "mismatched_page_id", "book_id": book_id, "page_id": page_id,
                          "page_number": number})
    for info in record.get("images", []):
        images_by_number.setdefault(page_image_number(info.get("path", "")), []).append(info)
    numbers = sorted(set(by_number) | set(images_by_number))
    if numbers:
        missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
        if missing:
            audit.append({"issue": "page_number_gaps", "book_id": book_id, "numbers": missing})
    pages = []
    for number in numbers:
        documents, objects = by_number.get(number, []), images_by_number.get(number, [])
        ambiguous = len(documents) > 1 or len(objects) > 1
        if ambiguous:
            audit.append({"issue": "duplicate_page_alignment", "book_id": book_id, "page_number": number,
                          "document_ids": [d.get("id") for d in documents],
                          "image_paths": [o.get("path") for o in objects]})
        raw = documents[0].get("data", {}) if len(documents) == 1 else {}
        image = copied.get(objects[0].get("path")) if len(objects) == 1 else None
        if not objects or not documents:
            audit.append({"issue": "missing_image" if not objects else "missing_page_document",
                          "book_id": book_id, "page_number": number})
        pages.append({"id": documents[0].get("id") if len(documents) == 1 else f"{book_id}_{number}",
                      "page_number": number, "image": image,
                      "reference": _page_reference(raw, ambiguous=ambiguous),
                      "raw": raw, "source_document_paths": [d.get("path") for d in documents],
                      "available_images": objects})
    characters, character_ids = [], set()
    for document in related.get("characters", []):
        char_id, raw = document.get("id"), document.get("data", {})
        owner = _reference_path(raw.get("book"), "books")
        if owner != f"books/{book_id}":
            audit.append({"issue": "character_wrong_book", "book_id": book_id, "character_id": char_id})
            continue
        character_ids.add(char_id)
        characters.append({"id": char_id, "name": raw.get("name", ""), "aliases": [], "raw": raw})
    by_id = {c.get("id"): c for c in characters}
    for document in related.get("aliases", []):
        raw = document.get("data", {})
        path = _reference_path(raw.get("character"), "characters")
        target = path.split("/", 1)[-1] if path else None
        if path == f"characters/{target}" and target in character_ids:
            by_id.get(target).get("aliases").append(raw.get("name", ""))
        else:
            audit.append({"issue": "dangling_alias_attachment", "book_id": book_id,
                          "alias_id": document.get("id"), "character_path": path})
    char_status = "observed" if characters else "missing"
    return {"id": book_id, "title": data.get("title", ""), "pages": pages, "raw": data,
            "source_update_time": book_doc.get("update_time"), "folder": record.get("folder"),
            "reference": {"metadata": _metadata(data, docs), "characters": _field(characters,
                "pilot_sqlite_possible_name_and_alias_collisions", status=char_status, complete=False),
                "themes": _field(None, "pilot_has_no_theme_annotations", status="not_annotated")}}


def _verify_local_images(out: Path, manifest: dict):
    for book in manifest.get("books", []):
        for page in book.get("pages", []):
            image = page.get("image")
            if image:
                path = out / image.get("path", "")
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != image.get("sha256"):
                    raise ValueError(f"Snapshot image integrity failure: {path}")


def _create_snapshot(out, backend: SnapshotBackend, entered_by="pilot_import", books=None):
    """Freeze references first, resume image copies, then publish one immutable manifest.

    Completed snapshots are returned locally without reading the backend. Source
    revisions are checked before completion; concurrent changes remain explicit
    integrity findings. A fresh directory is required for a new reference version.
    """
    out = Path(out)
    manifest_path, inventory_path = out / "manifest.json", out / "inventory.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("entered_by") != entered_by or manifest.get("requested_books", []) != sorted(set(books or [])):
            raise ValueError("Snapshot selection differs; use a new output directory")
        _verify_local_images(out, manifest)
        return manifest
    out.mkdir(parents=True, exist_ok=True)
    if inventory_path.exists():
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        if inventory.get("entered_by") != entered_by or inventory.get("requested_books") != sorted(set(books or [])):
            raise ValueError("Partial snapshot selection differs; use a new output directory")
    else:
        inventory = _collect_inventory(backend, entered_by, books)
        _atomic_json(inventory_path, inventory)
    progress_path = out / "images-progress.json"
    copied = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
    audit = list(inventory.get("audit", []))
    hashes = {}
    for record in inventory.get("records", []):
        for info in record.get("images", []):
            source = info.get("path")
            existing = copied.get(source)
            if existing:
                local = out / existing.get("path", "")
                if not local.is_file() or hashlib.sha256(local.read_bytes()).hexdigest() != existing.get("sha256"):
                    raise ValueError(f"Partial snapshot image integrity failure: {source}")
            else:
                content = backend.read_image(source, info.get("version_id"))
                sha = hashlib.sha256(content).hexdigest()
                relative = f"images/{sha}.jpg"
                destination = out / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(".tmp")
                with temporary.open("wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                copied[source] = {**info, "source_path": source, "path": relative, "sha256": sha}
                _atomic_json(progress_path, copied)
            sha = copied.get(source, {}).get("sha256")
            if sha in hashes and hashes.get(sha) != source:
                audit.append({"issue": "duplicate_image_bytes", "paths": [hashes.get(sha), source], "sha256": sha})
            hashes[sha] = source
    docs = inventory.get("raw_documents", {})
    books_out = [_build_book(record, docs, copied, audit) for record in inventory.get("records", [])]
    source_changes = []
    for path, original in docs.items():
        current = backend.get_document(path)
        current = _normalise_document(current, path.split("/", 1)[0]) if current else None
        if _digest(original) != _digest(current):
            source_changes.append({"issue": "source_document_changed", "path": path})
    for path in inventory.get("missing_documents", []):
        if backend.get_document(path) is not None:
            source_changes.append({"issue": "source_document_appeared", "path": path})
    for record in inventory.get("records", []):
        book_id = record.get("book", {}).get("id")
        for collection in ("pages", "characters", "aliases"):
            original = record.get("related", {}).get(collection, [])
            # A book's character ref-list can also reveal missing collection
            # membership, so compare the query result to that same subset.
            original_paths = sorted(d.get("path") for d in original
                                    if _reference_path(d.get("data", {}).get("book"), "books") == f"books/{book_id}")
            current_paths = sorted(_normalise_document(d, collection).get("path")
                                   for d in backend.iter_related(collection, book_id))
            if original_paths != current_paths:
                source_changes.append({"issue": "source_collection_membership_changed",
                                       "book_id": book_id, "collection": collection})
        original_objects = record.get("images", [])
        current_objects = sorted((json_value(info) for info in backend.list_images(record.get("folder"))
                                  if is_page_image(info.get("path", ""))), key=lambda i: i.get("path"))
        if _digest(original_objects) != _digest(current_objects):
            source_changes.append({"issue": "source_image_inventory_changed", "folder": record.get("folder")})
        for info in original_objects:
            current = json_value(backend.image_info(info.get("path")))
            if _digest(info) != _digest(current):
                source_changes.append({"issue": "source_image_changed", "path": info.get("path")})
    manifest = {"version": SNAPSHOT_VERSION, "snapshot_id": _digest(inventory), "complete": True,
                "created_at": inventory.get("created_at"), "completed_at": datetime.now(timezone.utc).isoformat(),
                "entered_by": entered_by, "requested_books": inventory.get("requested_books", []),
                "books": books_out, "excluded_books": inventory.get("excluded_books", []),
                "raw_documents": docs, "audit": audit + source_changes,
                "source_consistent": not source_changes,
                "reference_eligibility_version": "pilot-agreement-wordless-v2"}
    _atomic_json(manifest_path, manifest)
    return manifest


def create_snapshot(out, backend: SnapshotBackend, entered_by="pilot_import", books=None):
    """Create/resume one snapshot under an exclusive local process lock.

    No live backend is constructed here. Supply a fake for tests or explicitly
    construct ``create_live_backend`` in the snapshot CLI only. Completed
    snapshots never read the supplied backend and never overwrite references.
    """
    import fcntl

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".snapshot.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            return _create_snapshot(out, backend, entered_by=entered_by, books=books)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
