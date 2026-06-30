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
import s3fs
import streamlit as st

from text_content import Reconstruction
from utilities import (
    extract_photo_first_metadata,
    detect_book_characters,
    fuzzy_match_name,
    split_name,
    load_author_dict,
    load_illustrator_dict,
    load_publisher_dict,
    load_book_dict,
    load_character_dict,
)
from image_processing import exif_transpose_bytes
from data_structures import Book, Page, Character, Alias, Author, Illustrator, Publisher

# Reuse the per-page orientation-correct/crop/OCR helpers that drive the manual
# upload pipeline rather than reimplementing them. ``pages`` has no __init__.py;
# it is imported here as a PEP 420 namespace package (importing the module does
# not run its page body, which is guarded by ``if __name__ == "__main__"``).
from pages.uploader import extract_page_info, _process_page

#: S3 bucket holding book page images (first path segment, app-wide).
S3_BUCKET = "sawimages"

#: Immediate child prefixes under the bucket that are NOT book folders and so must
#: never be reported as orphans (mirrors scripts/data_cleanup.NON_BOOK_S3_PREFIXES:
#: the transient direct-upload area uploads/{flow}/{session}/).
NON_BOOK_S3_PREFIXES = ("uploads",)


def build_s3_filesystem():
    """Build an authenticated s3fs filesystem from the AWS secrets.

    Same configuration as the rest of the app (``pages/uploader.py`` /
    ``pages/add_book_photos.py``).
    """
    return s3fs.S3FileSystem(
        anon=False,
        key=st.secrets["AWS_ACCESS_KEY_ID"],
        secret=st.secrets["AWS_SECRET_ACCESS_KEY"],
    )


def _is_page_image(path):
    """True for ``page_N.jpg`` originals; False for ``page_N_cropped.jpg`` and
    anything else. Page images are the canonical originals; ``_cropped`` variants
    are derived and so are ignored when listing / counting a book's pages."""
    name = path.rsplit("/", 1)[-1].lower()
    if not name.startswith("page_") or not name.endswith(".jpg"):
        return False
    middle = name[len("page_"):-len(".jpg")]
    return middle.isdigit()


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


def count_folder_pages(fs, folder):
    """Number of ``page_N.jpg`` (non-cropped) objects under ``sawimages/{folder}``."""
    prefix = f"{S3_BUCKET}/{folder}"
    try:
        if not fs.exists(prefix):
            return 0
        return sum(1 for path in fs.find(prefix) if _is_page_image(path))
    except FileNotFoundError:
        return 0


def list_orphan_folders(fs=None):
    """Return orphaned ``sawimages/`` folders as ``(folder, page_count)`` tuples.

    An orphan is an immediate child folder under the bucket that holds page
    photos but matches no existing Firestore book (its doc was deleted/lost). The
    transient ``uploads/`` area is excluded. Sorted by folder name.
    """
    fs = fs or build_s3_filesystem()
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
    keys = natsort.natsorted([e for e in entries if _is_page_image(e)])
    photos = []
    for key in keys:
        with fs.open(key, "rb") as handle:
            photos.append((key.rsplit("/", 1)[-1], handle.read()))
    return photos


def _report(progress, message):
    """Invoke an optional progress callback with a (formatted) message string."""
    if progress is not None:
        progress(message)


def _resolve_or_create_person(extracted_name, person_cls, dict_key, cache_clear):
    """Fuzzy-match ``extracted_name`` to an existing author/illustrator, else create
    and register a new record. Returns the matched/created lookup-dict NAME key
    (the value the ``Book`` ref-field setter resolves via the session dict), or
    ``None`` when no usable name was extracted.
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


def _apply_metadata_to_book(book, metadata):
    """Set year / author / illustrator / publisher on an unregistered book from
    extracted metadata, creating new person/publisher records as needed."""
    year = metadata.get("published_year")
    if isinstance(year, int):
        book.published = year

    authors = metadata.get("authors") or []
    if authors:
        author_name = _resolve_or_create_person(
            authors[0], Author, "author_dict", load_author_dict.clear
        )
        if author_name is not None:
            book.author = author_name

    illustrators = metadata.get("illustrators") or []
    if illustrators:
        illustrator_name = _resolve_or_create_person(
            illustrators[0], Illustrator, "illustrator_dict", load_illustrator_dict.clear
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
    ``st.status``). Returns the ordered ``{page_number: Page}`` dict.
    """
    total = len(pages)
    photos_url = f"{S3_BUCKET}/{book.title}"
    page_objs = {}

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

        text, is_story, _page_type = extract_page_info(bytes_for_extraction, client)
        if text:
            page.text = text
        page.contains_story = is_story
        page_objs[page_number] = page

    return page_objs


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
         'moved': bool}                # True when title != source_folder

    Raises:
        ValueError: when no usable title could be derived, or a book with the
            derived title already exists (refusing to overwrite).
    """
    pages = list(pages)
    if not pages:
        raise ValueError(Reconstruction.error_no_photos)

    fs = fs or build_s3_filesystem()

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

    isbn = metadata.get("isbn")
    if isbn:
        book.comment = f"ISBN: {isbn}"
    _apply_metadata_to_book(book, metadata)

    # Initial full save (sets entered_by + datetime_created). Subsequent field
    # assignments below write through to Firestore individually.
    book.register()
    st.session_state.setdefault("book_dict", {})[title] = book.get_ref()
    load_book_dict.clear()
    st.session_state["current_book"] = book

    page_objs = _process_pages(book, pages, client, fs, progress)

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
    }
