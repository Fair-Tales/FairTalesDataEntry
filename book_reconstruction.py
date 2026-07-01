"""Reusable core: reconstruct a complete Book from a set of page photos (#122/#123).

Given an ordered set of page photos already in memory as ``(name, image_bytes)``
tuples, run the full AI pipeline end-to-end and create a complete,
validation-ready Book:

  1. metadata extraction (title / author(s) / illustrator(s) / publisher / year /
     ISBN) via :func:`utilities.extract_photo_first_metadata`;
  2. per-page orientation-correct + crop + OCR via the SHARED uploader pipeline
     (``pages.uploader._process_page`` + ``pages.uploader.extract_page_info``),
     writing ``page_N.jpg`` / ``page_N_cropped.jpg`` to ``sawimages/{title}/`` and
     creating ``Page`` records;
  3. character + alias detection (#52) via
     :func:`utilities.detect_book_characters`, creating ``Character`` / ``Alias``
     records linked to the book;
  4. the book is marked ``entry_status='completed'`` (and is NOT validated) so it
     lands in the validation queue (#47) for human review.

This is the SHARED core for both the orphan-reconstruction admin page (#122) and
the fully-automated upload->validation flow (#123). It is deliberately
flow-agnostic: it takes the photos, an Anthropic client and an optional progress
callback, and creates the entities directly via the ``data_structures``
write-through pattern. It renders NO Streamlit widgets itself, so each caller can
wrap it in its own ``st.status`` / progress UI.

It DOES depend on an initialised session (``st.session_state['firestore']`` and
the author / illustrator / publisher / book / character lookup dicts) and on
``st.secrets`` for the AWS + Anthropic credentials, exactly like the rest of the
entry pipeline.

S3 PATH DECISION (#122)
-----------------------
The canonical photo location for EVERY book in the app is ``sawimages/{title}/``
(see ``pages/uploader.py``); ``enter_text`` / ``validation`` load page images from
there with no special-casing. So the reconstruction pipeline always (re)writes the
freshly orientation-corrected photos to ``sawimages/{final_title}/``. For an
orphan whose folder name already equals the extracted title this simply
reprocesses the photos in place. When the AI-extracted title differs from the
original orphan folder name, the photos are re-written to the new canonical
``sawimages/{title}/`` folder (the bytes are already in memory, so nothing is
lost) and the ORIGINAL orphan folder is left untouched — deletion is deliberately
NOT performed here (it is a destructive op; the #120 cleanup CLI is the place to
remove the now-redundant duplicate once the reconstructed book has been
validated). The caller is told the source and destination folders so it can
surface that to the admin.
"""

from datetime import datetime, timezone

import natsort
import streamlit as st

from text_content import Reconstruction
from utilities import (
    extract_photo_first_metadata,
    detect_book_characters,
    fuzzy_match_name,
    split_name,
    lookup_person_details,
    load_author_dict,
    load_illustrator_dict,
    load_publisher_dict,
    load_book_dict,
    load_character_dict,
    databot_entered_by,
    get_s3_filesystem,
)
# Pure S3 path constants/helpers shared with the cleanup CLI (#129): keep ONE
# definition so the app and scripts/data_cleanup.py classify book folders /
# page images identically.
from s3_constants import (
    S3_BUCKET,
    NON_BOOK_S3_PREFIXES,
    is_page_image,
    count_folder_pages,
)
from image_processing import exif_transpose_bytes
from data_structures import (
    Book, Page, Character, Alias, Author, Illustrator, Publisher,
    ExtractionErrorLog,
)

# Reuse the per-page orientation-correct/crop/OCR helpers that drive the manual
# upload pipeline rather than reimplementing them. ``pages`` has no __init__.py;
# it is imported here as a PEP 420 namespace package (importing the module does
# not run its page body, which is guarded by ``if __name__ == "__main__"``).
from pages.uploader import extract_page_info, _process_page, PageExtractionError


def _expected_book_folders():
    """Set of S3 folder names that an existing Firestore book maps to.

    A book's photos live at ``sawimages/{photos_url-basename or title}``; we
    mirror that derivation so orphan detection matches ``scripts/data_cleanup``.
    """
    expected = set()
    for doc in st.session_state["firestore"].get_all_documents_stream(collection="books"):
        data = doc.to_dict() or {}
        source = (data.get("photos_url") or "").strip() or (data.get("title") or "")
        folder = source.rstrip("/").split("/")[-1]
        if folder:
            expected.add(folder)
    return expected


def list_orphan_folders(fs=None):
    """Return orphaned ``sawimages/`` folders as ``(folder, page_count)`` tuples.

    An orphan is an immediate child folder under the bucket that holds page
    photos but matches no existing Firestore book (its doc was deleted/lost). The
    transient ``uploads/`` area is excluded. Sorted by folder name.
    """
    fs = fs or get_s3_filesystem()
    expected = _expected_book_folders()
    orphans = []
    for entry in fs.ls(S3_BUCKET, detail=True):
        if entry.get("type") != "directory":
            continue
        folder = entry["name"].rstrip("/").split("/")[-1]
        if folder in NON_BOOK_S3_PREFIXES or folder in expected:
            continue
        count = count_folder_pages(fs, folder)
        if count > 0:
            orphans.append((folder, count))
    orphans.sort(key=lambda item: item[0].lower())
    return orphans


def fetch_folder_photos(fs, folder):
    """Download a folder's ORIGINAL page photos into memory, in page order.

    Reads ``sawimages/{folder}/`` and returns ``(name, image_bytes)`` tuples for
    the ``page_N.jpg`` originals (``_cropped`` variants are skipped), naturally
    sorted so ``page_2`` precedes ``page_10`` — the shape the extraction pipeline
    expects. Reused/generalised from ``photo_upload.fetch_uploaded_photos``.
    """
    prefix = f"{S3_BUCKET}/{folder}"
    entries = fs.ls(prefix, detail=False, refresh=True)
    keys = natsort.natsorted([e for e in entries if is_page_image(e)])
    photos = []
    for key in keys:
        with fs.open(key, "rb") as handle:
            photos.append((key.rsplit("/", 1)[-1], handle.read()))
    return photos


def _report(progress, message):
    """Invoke an optional progress callback with a (formatted) message string."""
    if progress is not None:
        progress(message)


def _auto_lookup_person(person, role, client, book_title):
    """Best-effort birth-year/gender enrichment for a freshly-created author or
    illustrator (#113).

    Mirrors the manual "Look up birth year and gender" button so pipeline-created
    people aren't left blank. ``lookup_person_details`` swallows and logs its own
    API/parse failures (returning ``None``), so a lookup miss simply leaves the
    fields at their defaults and never aborts the reconstruction. Writes through
    to Firestore via the ``Field`` descriptors, exactly as the form would.
    """
    suggestion = lookup_person_details(
        person.name.strip(), role, client, book_title=book_title
    )
    if not suggestion:
        return
    if suggestion.get("birth_year"):
        person.birth_year = suggestion["birth_year"]
    if suggestion.get("gender"):
        person.gender = suggestion["gender"]


def _resolve_or_create_person(extracted_name, person_cls, dict_key, cache_clear,
                              *, client=None, book_title=None, role=None):
    """Fuzzy-match ``extracted_name`` to an existing author/illustrator, else create
    and register a new record. Returns the matched/created lookup-dict NAME key
    (the value the ``Book`` ref-field setter resolves via the session dict), or
    ``None`` when no usable name was extracted.

    When a NEW person is registered and a ``client`` is supplied, best-effort
    auto-runs the birth-year/gender lookup (#113) so the automated photo-first
    pipeline populates these without a manual click.
    """
    if not extracted_name or not extracted_name.strip():
        return None

    lookup = st.session_state.get(dict_key, {})
    match = fuzzy_match_name(extracted_name, list(lookup.keys()))
    if match is not None and match in lookup:
        return match

    forename, surname = split_name(extracted_name)
    person = person_cls()
    person.forename = forename
    person.surname = surname
    if not person.forename and not person.surname:
        return None
    # An identically-named record may already exist without having fuzzy-matched
    # (e.g. below the cutoff); reuse it rather than colliding on the document id.
    if not st.session_state["firestore"].document_exists(
        collection=person.belongs_to_collection, doc_id=person.document_id
    ):
        person.register()
        # Auto-populate birth year + gender for the newly-created person (#113).
        # NOTE: #123's automated upload->validation pipeline MUST do this too.
        if client is not None and role is not None:
            _auto_lookup_person(person, role, client, book_title)
    person_ref = person.get_ref()
    st.session_state[dict_key][person.name] = person_ref
    cache_clear()
    return person.name


def _resolve_or_create_publisher(extracted_name):
    """As :func:`_resolve_or_create_person` but for the single-field Publisher."""
    if not extracted_name or not extracted_name.strip():
        return None

    lookup = st.session_state.get("publisher_dict", {})
    match = fuzzy_match_name(extracted_name, list(lookup.keys()))
    if match is not None and match in lookup:
        return match

    publisher = Publisher()
    publisher.name = extracted_name.strip()
    if not st.session_state["firestore"].document_exists(
        collection="publishers", doc_id=publisher.document_id
    ):
        publisher.register()
    publisher_ref = publisher.get_ref()
    st.session_state["publisher_dict"][publisher.name] = publisher_ref
    load_publisher_dict.clear()
    return publisher.name


def _apply_metadata_to_book(book, metadata, client=None):
    """Set year / author / illustrator / publisher on an unregistered book from
    extracted metadata, creating new person/publisher records as needed.

    ``client`` (when supplied) lets newly-created authors/illustrators have their
    birth year + gender auto-looked-up, with the book title as disambiguating
    context (#113)."""
    year = metadata.get("published_year")
    if isinstance(year, int):
        book.published = year

    book_title = getattr(book, "title", None)

    authors = metadata.get("authors") or []
    if authors:
        author_name = _resolve_or_create_person(
            authors[0], Author, "author_dict", load_author_dict.clear,
            client=client, book_title=book_title, role="author",
        )
        if author_name is not None:
            book.author = author_name

    illustrators = metadata.get("illustrators") or []
    if illustrators:
        illustrator_name = _resolve_or_create_person(
            illustrators[0], Illustrator, "illustrator_dict", load_illustrator_dict.clear,
            client=client, book_title=book_title, role="illustrator",
        )
        if illustrator_name is not None:
            book.illustrator = illustrator_name

    publisher = metadata.get("publisher")
    if publisher:
        publisher_name = _resolve_or_create_publisher(publisher)
        if publisher_name is not None:
            book.publisher = publisher_name


def _process_pages(book, pages, client, fs, progress):
    """Orientation-correct, crop, OCR and store each page; create Page records.

    Mirrors ``pages.uploader._process_photo_batch`` but flow-agnostic (no
    ``st.status``). Returns ``(page_objs, failed_pages)`` where ``page_objs`` is the
    ordered ``{page_number: Page}`` dict and ``failed_pages`` is the list of page
    numbers whose AI text-extraction failed (#132) — kept as blanks in the
    sequence for later manual entry.
    """
    total = len(pages)
    photos_url = f"{S3_BUCKET}/{book.title}"
    page_objs = {}
    failed_pages = []

    # Phase 1 — write the orientation-normalised originals to the canonical folder.
    corrected = []
    for index, (_name, raw_bytes) in enumerate(pages):
        _report(progress, Reconstruction.saving_photo.format(current=index + 1, total=total))
        raw_bytes = exif_transpose_bytes(raw_bytes)
        corrected.append(raw_bytes)
        with fs.open(f"{photos_url}/page_{index + 1}.jpg", "wb") as handle:
            handle.write(raw_bytes)

    book.photos_uploaded = True
    book.photos_url = photos_url
    book.page_count = total

    # Phase 2 — per-page correction + crop + OCR, then create the Page record.
    book_ref = book.get_ref()
    for index, raw_bytes in enumerate(corrected):
        page_number = index + 1
        _report(progress, Reconstruction.processing_page.format(page=page_number, total=total))
        bytes_for_extraction, _method = _process_page(
            raw_bytes, page_number, photos_url, fs, client
        )

        page = Page(page_number=page_number, book=book_ref)
        page.register()

        try:
            text, is_story, _page_type = extract_page_info(
                bytes_for_extraction, client,
                book=book, page_number=page_number,
                page_name=f"page_{page_number}.jpg",
                flow=ExtractionErrorLog.FLOW_RECONSTRUCTION,
            )
        except PageExtractionError:
            # Detail already logged to extraction_errors; keep the blank page in
            # the sequence and record it for the caller to surface (#132). A single
            # failed page no longer aborts the whole reconstruction.
            failed_pages.append(page_number)
        else:
            if text:
                page.text = text
            page.contains_story = is_story
        page_objs[page_number] = page

    return page_objs, failed_pages


def _detect_and_create_characters(book, page_objs, client, progress):
    """Run #52 character/alias detection over the book's story pages and create the
    Character + Alias records, linking each character to the book.

    Returns ``(characters_created, aliases_created)``.
    """
    story_pages = [
        (page_number, page.text)
        for page_number, page in page_objs.items()
        if page.contains_story and (page.text or "").strip()
    ]
    if not story_pages:
        return 0, 0

    _report(progress, Reconstruction.detecting_characters)

    def _on_progress(done, total):
        _report(progress, Reconstruction.detecting_progress.format(done=done, total=total))

    suggestions = detect_book_characters(story_pages, client, progress_callback=_on_progress)

    characters_created = 0
    aliases_created = 0
    for suggestion in suggestions:
        name = (suggestion.get("name") or "").strip()
        if not name:
            continue
        character = Character(book=book.get_ref())
        character.name = name
        character.gender = suggestion.get("gender", "")
        character.human = bool(suggestion.get("human", True))
        character.protagonist = bool(suggestion.get("protagonist", False))
        character.plural = bool(suggestion.get("plural", False))

        if st.session_state["firestore"].document_exists(
            collection="characters", doc_id=character.document_id
        ):
            continue
        character.register()
        character_ref = character.get_ref()
        book.add_character(character_ref)
        st.session_state.setdefault("character_dict", {})[name] = character_ref
        characters_created += 1

        for alias_name in suggestion.get("aliases", []) or []:
            alias_name = (alias_name or "").strip()
            if not alias_name or alias_name.lower() == name.lower():
                continue
            alias = Alias(book=book.get_ref())
            alias.character = character_ref
            alias.name = alias_name
            if st.session_state["firestore"].document_exists(
                collection="aliases", doc_id=alias.document_id
            ):
                continue
            alias.register()
            aliases_created += 1

    load_character_dict.clear()
    return characters_created, aliases_created


def reconstruct_book_from_photos(pages, client, *, fs=None, source_folder=None,
                                 progress=None):
    """Reconstruct a complete, validation-ready Book from a set of page photos.

    This is the shared core for #122 (orphan reconstruction) and #123 (fully
    automated upload). It runs metadata extraction, per-page OCR and #52
    character/alias detection, creating the Book + Pages + Characters + Aliases,
    and marks the book ``entry_status='completed'`` (not validated) so it appears
    in the validation queue (#47).

    Args:
        pages: ordered list of ``(name, image_bytes)`` page photos.
        client: an ``anthropic.Anthropic`` client.
        fs: optional s3fs filesystem; built from the AWS secrets when omitted.
        source_folder: optional existing ``sawimages/`` folder the photos came
            from (orphan flow). Used as a title fallback and reported back in the
            result so the caller can flag a leftover duplicate folder.
        progress: optional ``callable(message: str)`` for UI progress updates.

    Returns a dict::

        {'book': Book,                 # the registered, completed book
         'title': str,
         'page_count': int,
         'characters_created': int,
         'aliases_created': int,
         'source_folder': str | None,  # the orphan folder, if supplied
         'photos_folder': str,         # canonical sawimages/{title} folder name
         'moved': bool,                # True when title != source_folder
         'extraction_failures': list}  # page numbers the AI couldn't read (#132)

    Raises:
        ValueError: when no usable title could be derived, or a book with the
            derived title already exists (refusing to overwrite).
    """
    pages = list(pages)
    if not pages:
        raise ValueError(Reconstruction.error_no_photos)

    fs = fs or get_s3_filesystem()

    _report(progress, Reconstruction.extracting_metadata)
    metadata = extract_photo_first_metadata(pages, client)

    title = (metadata.get("title") or "").strip() or (source_folder or "").strip()
    if not title:
        raise ValueError(Reconstruction.error_no_title)

    book = Book()
    book.title = title
    if st.session_state["firestore"].document_exists(
        collection="books", doc_id=book.document_id
    ):
        raise ValueError(Reconstruction.error_book_exists.format(title=title))

    # Guard against silently overwriting a DIFFERENT orphan folder (#128). When the
    # AI-extracted title differs from the source folder and the destination folder
    # ``sawimages/{title}/`` already holds page photos (belonging to some other
    # orphan, since no book doc claims this title), writing our pages would clobber
    # them. Stop and surface it to the admin rather than overwrite.
    if source_folder and title != source_folder:
        existing_pages = count_folder_pages(fs, title)
        if existing_pages > 0:
            raise ValueError(
                Reconstruction.error_folder_collision.format(
                    title=title, count=existing_pages, source_folder=source_folder
                )
            )

    isbn = metadata.get("isbn")
    if isbn:
        book.comment = f"ISBN: {isbn}"
    _apply_metadata_to_book(book, metadata, client)

    # Initial full save (sets entered_by + datetime_created). Subsequent field
    # assignments below write through to Firestore individually.
    book.register()
    # AI-generated books are OWNED BY the ``databot`` system user (#131), not the
    # admin who happened to trigger reconstruction, so ANY role can pick them up to
    # edit (see pages/review_my_books.py). register() set entered_by to the current
    # user; override it to databot (write-through, since the book is now registered).
    # NOTE: #123's automated upload->validation pipeline MUST set this too.
    book.entered_by = databot_entered_by()
    st.session_state.setdefault("book_dict", {})[title] = book.get_ref()
    load_book_dict.clear()
    st.session_state["current_book"] = book

    page_objs, extraction_failures = _process_pages(book, pages, client, fs, progress)

    characters_created, aliases_created = _detect_and_create_characters(
        book, page_objs, client, progress
    )

    # Drop the book into the validation queue: completed + submitted, not yet
    # validated. These write through immediately (the book is registered).
    _report(progress, Reconstruction.finalising)
    book.entry_status = "completed"
    book.datetime_submitted = datetime.now(timezone.utc)

    photos_folder = book.title
    return {
        "book": book,
        "title": title,
        "page_count": book.page_count,
        "characters_created": characters_created,
        "aliases_created": aliases_created,
        "source_folder": source_folder,
        "photos_folder": photos_folder,
        "moved": bool(source_folder) and source_folder != photos_folder,
        # Page numbers the AI could not read (#132); kept as blanks for manual entry.
        "extraction_failures": extraction_failures,
    }
