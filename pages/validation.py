"""Data-validation workflow (issues #47 / #83).

A project **team member** (or admin) reviews books that archivists have
SUBMITTED, corrects any errors, and marks the entry APPROVED. A book is
"submitted and awaiting validation" when its ``entry_status`` is ``'completed'``
(set by ``confirm_submit``) and it has not yet been validated (``validated`` is
falsy). Review sessions acquire an exclusive lease. Approval checks ownership,
sets ``validated`` / ``validated_by`` and releases the lease in one transaction.

EDIT AUDIT LOG (issue #47, Part B)
----------------------------------
Every correction the validator makes is recorded to the ``edit_log`` collection
via ``EditLog.record`` (see ``data_structures/edit_log.py``), capturing the
ORIGINAL value and the new value as training data for future AI correction
systems.

Capture strategy — IMMEDIATE ON-SUBMIT DIFF (chosen over open-time snapshotting):
each editor below seeds its widgets from the entity's currently-stored values
(the originals), and on **save** compares the submitted widget values against
those originals BEFORE writing through. This fits the per-form write-through
pattern exactly, captures the precise before/after for each correction (including
character/alias renames, which change a document's id), and avoids brittle
matching of name-derived document ids that an open-time snapshot/diff would need.
Page-text corrections — the original transcription vs the validated text — are
captured here as ``entity_type='page', field='text'`` records.
"""

from datetime import date, datetime, timezone
import logging
from uuid import uuid4

import streamlit as st
from google.api_core.exceptions import GoogleAPIError

from utilities import (
    page_layout, check_authentication_status, is_team_or_above, entered_by_username,
    validation_heartbeat_due, get_s3_filesystem,
)
from image_processing import load_page_image
from data_structures import Book, Page, Character, Alias, EditLog
from data_structures.book import ValidationLeaseStatus, ValidationLeaseUnavailable
from page_save_recovery import PageSaveRecovery
from text_content import Validation, BookForm, CharacterForm

logger = logging.getLogger(__name__)

check_authentication_status()

# Validation is a team-member-and-above workflow (#83).
if not is_team_or_above():
    page_layout()
    st.error(Validation.not_authorised)
    st.stop()

page_layout(current_page="./pages/validation.py")


# ---------------------------------------------------------------------------
# Audit-log helpers
# ---------------------------------------------------------------------------
def _validator_ref():
    """The current validator's user ``DocumentReference``."""
    return st.session_state.firestore.username_to_doc_ref(st.session_state['username'])


def _log(book, entity_type, entity_id, field, old_value, new_value):
    """Record one before/after correction against ``book`` to the edit log."""
    EditLog.record(
        book_id=book.document_id,
        book_title=book.title,
        entity_type=entity_type,
        entity_id=entity_id,
        field=field,
        old_value=old_value,
        new_value=new_value,
        edited_by=_validator_ref(),
        entered_by=book.entered_by,
    )


def _current_ref_name(option_dict, current_ref):
    """Return the name in ``option_dict`` ({name: ref}) whose ref matches
    ``current_ref`` (compared by path), or None. Guarded so a stale/absent ref
    never raises (see CLAUDE.md lookup-guarding convention)."""
    if current_ref is None:
        return None
    current_path = getattr(current_ref, 'path', None)
    for name, ref in option_dict.items():
        if getattr(ref, 'path', None) == current_path:
            return name
    return None


def _guarded_index(options, value, default=0):
    """``options.index(value)`` guarded against ``value`` not being present."""
    return options.index(value) if value in options else default


def _clear_validation_heartbeat(book):
    """Release only this review session's lease, against fresh stored state."""
    review = _review_session(book.document_id)
    return book.release_validation_lease(
        review.get('token'), st.session_state.get('username'),
    )


def _review_session(book_id):
    """A token is authority only for its original book and signed-in user."""
    review = st.session_state.get('_validation_review_session') or {}
    if (review.get('book_id') == book_id
            and review.get('username') == st.session_state.get('username')):
        return review
    return {}


def _forget_review(book_id):
    """Discard local selection only after a confirmed exit or missing book."""
    st.session_state.pop('_validation_book_id', None)
    st.session_state.pop('_validation_review_session', None)
    st.session_state.get('_validation_heartbeat', {}).pop(book_id, None)
    current = st.session_state.get('current_book')
    if current is not None and current.document_id == book_id:
        st.session_state.pop('current_book', None)


def _review_back_button(book):
    """Return True for a Back attempt, including a failed, retryable release."""
    if not st.button(Validation.back_to_list_button, key="validation_back_to_list_button"):
        return False
    try:
        result = _clear_validation_heartbeat(book)
    except (ValidationLeaseUnavailable, GoogleAPIError):
        logger.warning('Validation release failed for %s', book.document_id, exc_info=True)
        st.error(Validation.release_failed)
        return True
    if result in (
        ValidationLeaseStatus.RELEASED, ValidationLeaseStatus.NOT_OWNED,
        ValidationLeaseStatus.MISSING,
    ):
        _forget_review(book.document_id)
        st.rerun()
    return True


def _review_retry_button():
    if st.button(Validation.retry_review_button, key="validation_retry_review_button"):
        st.rerun()


def _show_busy_review(book):
    owner = book.validation_active_by
    if owner and owner != st.session_state.get('username'):
        st.warning(Validation.other_validator_active.format(name=owner))
    else:
        st.warning(Validation.other_session_active)
    if not _review_back_button(book):
        _review_retry_button()


# ---------------------------------------------------------------------------
# Awaiting-validation list (Part A)
# ---------------------------------------------------------------------------
def _book_flag_label(row):
    """Title with a flag indicator + flagged-page count, for a book with
    ``needs_review`` pages (set on the book doc by the pilot import's
    clean+judge pass) — HIGH-priority books get a distinct label so a
    validator can spot them in the dropdown. Books with no flagged pages just
    show their plain title."""
    title = row.get('title', '')
    if not row.get('needs_review', False):
        return title
    review_pages = row.get('review_pages') or []
    count = len(review_pages)
    if row.get('high_priority_review', False):
        return Validation.flagged_high_label.format(title=title, count=count)
    return Validation.flagged_label.format(title=title, count=count)


def render_list():
    st.header(Validation.list_header)
    st.write(Validation.list_intro)

    # Admin/team can validate ANY book; a toggle lets them narrow to just the books
    # archivists formally submitted (entry_status == 'completed').
    submitted_only = st.toggle(
        Validation.submitted_only_toggle, value=False,
        key="validation_submitted_only_toggle",
    )

    # Imported (pilot) books arrive with validated=True already set (they were
    # never through the archivist submit/validate flow), so the default
    # 'not validated' filter below hides them entirely. This toggle is additive
    # and OFF by default so normal day-to-day validation is unaffected.
    show_validated = st.toggle(
        Validation.show_validated_toggle, value=False,
        key="validation_show_validated_toggle",
    )

    # Surfaces books with clean+judge review flags (mostly the same imported
    # books) so a validator can jump straight to them without a per-book pages
    # subquery — the book-level needs_review/review_pages/high_priority_review
    # fields already summarise this.
    only_flagged = st.toggle(
        Validation.only_flagged_toggle, value=False,
        key="validation_only_flagged_toggle",
    )

    # Scope control (#131): default to ALL books (cross-user review is the point of
    # this team+admin page), with an option to narrow to just the validator's own
    # entries.
    scope = st.radio(
        Validation.scope_label,
        options=(Validation.scope_all, Validation.scope_mine),
        index=0, horizontal=True,
        key="validation_scope_radio",
    )

    # Projection (#78): this list renders labels from a handful of small fields,
    # but previously streamed every book document IN FULL on every rerun —
    # including each book's large embedded lists (characters, themes, …). The
    # ``select`` fetches only what the list logic below reads; opening a book
    # fetches its full document once, by id, at that point. ``(doc.id, data)``
    # pairs are carried so the open step doesn't have to re-derive the id from
    # the title.
    docs = st.session_state.firestore.get_all_documents_stream(
        collection="books",
        select=[
            'title', 'validated', 'entry_status', 'needs_review',
            'review_pages', 'high_priority_review', 'entered_by',
        ],
    )
    # Filter in Python (rather than a Firestore query) so missing legacy fields
    # ('validated' / 'entry_status') fall back to a default instead of excluding a
    # document or raising.
    pending = [
        (doc.id, data)
        for doc, data in ((doc, doc.to_dict()) for doc in docs)
        if (show_validated or not data.get('validated', False))
        and (not submitted_only or data.get('entry_status') == 'completed')
    ]

    if only_flagged:
        pending = [(doc_id, data) for doc_id, data in pending if data.get('needs_review', False)]

    # "Just mine": keep only books the current validator originally entered.
    # entered_by may be a DocumentReference or a plain string, so normalise both
    # to the owner username before comparing (databot-owned books fall away here).
    if scope == Validation.scope_mine:
        username = st.session_state['username']
        pending = [
            (doc_id, data) for doc_id, data in pending
            if entered_by_username(data.get('entered_by')) == username
        ]

    # Flagged/high-priority books float to the top so a validator can prioritise
    # them, then the rest fall back to the existing alphabetical order.
    pending.sort(
        key=lambda row: (
            not row[1].get('high_priority_review', False),
            not row[1].get('needs_review', False),
            (row[1].get('title') or '').lower(),
        )
    )

    if not pending:
        st.info(Validation.none_pending)
        return

    selected_index = st.selectbox(
        Validation.select_book_label,
        options=list(range(len(pending))),
        format_func=lambda i: _book_flag_label(pending[i][1]),
        key="validation_select_book",
    )

    if st.button(Validation.open_review_button, key="validation_open_review_button"):
        # The list rows are PROJECTED (#78) — fetch the full document for the
        # one book actually being opened, so the Book object (and its editors)
        # see every field.
        if selected_index not in range(len(pending)):
            return
        doc_id, _row = pending[selected_index]
        try:
            snap = st.session_state.firestore.get_by_reference(
                collection='books', document_ref=doc_id
            )
        except GoogleAPIError:
            logger.warning('Validation open failed for %s', doc_id, exc_info=True)
            st.error(Validation.review_failed)
            return
        if not snap.exists:
            # Guard the stale-list race (book deleted since the stream above)
            # rather than constructing a Book from None (lookup-guarding
            # convention).
            st.warning(Validation.book_no_longer_exists)
            return
        book = Book(db_object=snap.to_dict())
        st.session_state['_validation_book_id'] = book.document_id
        st.session_state['_validation_review_session'] = {
            'token': uuid4().hex,
            'book_id': book.document_id,
            'username': st.session_state.get('username'),
        }
        st.session_state['current_book'] = book
        st.rerun()


# ---------------------------------------------------------------------------
# Metadata editor
# ---------------------------------------------------------------------------
def metadata_editor(book):
    st.subheader(Validation.metadata_header)

    # Title is the book's identity (keys pages/characters) — show, don't edit.
    st.text_input(
        BookForm.title_label, value=book.title, disabled=True,
        key="validation_meta_title",
    )
    st.caption(Validation.title_readonly_caption)

    author_dict = st.session_state.get('author_dict', {})
    publisher_dict = st.session_state.get('publisher_dict', {})
    illustrator_dict = st.session_state.get('illustrator_dict', {})

    with st.form("validation_metadata_form"):
        years = list(range(1900, date.today().year + 1))
        published = st.selectbox(
            BookForm.published_label, options=years,
            index=_guarded_index(years, book.published, default=len(years) - 1),
            key="validation_meta_published",
        )

        author_options = [Validation.none_option] + list(author_dict.keys())
        author_current = _current_ref_name(author_dict, book.author) or Validation.none_option
        author_name = st.selectbox(
            BookForm.author_select_label, options=author_options,
            index=_guarded_index(author_options, author_current),
            key="validation_meta_author",
        )

        publisher_options = [Validation.none_option] + list(publisher_dict.keys())
        publisher_current = _current_ref_name(publisher_dict, book.publisher) or Validation.none_option
        publisher_name = st.selectbox(
            BookForm.publisher_select_label, options=publisher_options,
            index=_guarded_index(publisher_options, publisher_current),
            key="validation_meta_publisher",
        )

        illustrator_options = [Validation.none_option] + list(illustrator_dict.keys())
        illustrator_current = (
            _current_ref_name(illustrator_dict, book.illustrator) or Validation.none_option
        )
        illustrator_name = st.selectbox(
            BookForm.illustrator_select_label, options=illustrator_options,
            index=_guarded_index(illustrator_options, illustrator_current),
            key="validation_meta_illustrator",
        )

        comment = st.text_input(
            BookForm.comment_label, value=book.comment, key="validation_meta_comment",
        )

        current_themes = [
            BookForm.theme_options[theme]
            for theme in BookForm.theme_options
            if getattr(book, theme)
        ]
        selected_themes = st.multiselect(
            BookForm.themes_label, options=list(BookForm.theme_options.values()),
            default=current_themes, key="validation_meta_themes",
        )

        submitted = st.form_submit_button(
            Validation.save_metadata_button, key="validation_meta_submit_button"
        )

    if not submitted:
        return

    book_id = book.document_id

    if published != book.published:
        _log(book, EditLog.ENTITY_BOOK, book_id, 'published', book.published, published)
        book.published = published

    # Reference fields: compare/record the human-readable names, assign by name
    # (the Field ref-setter resolves a name string to a reference; None clears).
    for current_name, new_name, field in (
        (author_current, author_name, 'author'),
        (publisher_current, publisher_name, 'publisher'),
        (illustrator_current, illustrator_name, 'illustrator'),
    ):
        if new_name != current_name:
            old_display = None if current_name == Validation.none_option else current_name
            new_display = None if new_name == Validation.none_option else new_name
            _log(book, EditLog.ENTITY_BOOK, book_id, field, old_display, new_display)
            setattr(book, field, new_display)

    if comment != book.comment:
        _log(book, EditLog.ENTITY_BOOK, book_id, 'comment', book.comment, comment)
        book.comment = comment

    for theme, theme_label in BookForm.theme_options.items():
        new_value = theme_label in selected_themes
        if new_value != getattr(book, theme):
            _log(book, EditLog.ENTITY_BOOK, book_id, theme, getattr(book, theme), new_value)
            setattr(book, theme, new_value)

    st.success(Validation.metadata_saved)


# ---------------------------------------------------------------------------
# Page-text editor (the key training-data capture)
# ---------------------------------------------------------------------------
def page_text_editor(book):
    st.subheader(Validation.pages_header)

    if not isinstance(book.page_count, int) or book.page_count <= 0:
        st.info(Validation.no_pages)
        return

    page_number = st.selectbox(
        Validation.page_select_label,
        options=list(range(1, book.page_count + 1)),
        key="validation_page_select",
    )

    text_key = f"validation_page_text_{page_number}"
    story_key = f"validation_page_contains_{page_number}"
    recovery = PageSaveRecovery("validation", book.document_id, page_number)

    def on_saved(record):
        # The pending operation retains these original values across retries.
        # A rejected creation must never be recorded as an applied correction.
        for correction in record.get("audit", []):
            _log(
                book, EditLog.ENTITY_PAGE, correction["entity_id"],
                correction["field"], correction["old_value"],
                correction["new_value"],
            )
        st.success(Validation.page_saved)

    # Collision/retry refreshes must seed BOTH widgets before the form exists.
    page = recovery.consume_refresh(text_key=text_key, story_key=story_key)
    pages = st.session_state.setdefault("_validation_page_cache", {})
    page_scope = (book.document_id, page_number)
    pending = recovery.state
    if pending is not None:
        page = pending.get("page")
    if page is None:
        page = pages.get(page_scope)
    if page is not None:
        pages[page_scope] = page
    if recovery.blocked:
        recovery.render(st, on_saved=on_saved)
        return

    recovery.render_retained_draft(st)

    # Show the page photo so the validator can check the transcription against the
    # actual image (this was missing — validators had text but no picture). Uses
    # the shared loader so it matches what the archivist saw in enter-text,
    # including the corrected crop / display derivative.
    try:
        page_image = load_page_image(
            get_s3_filesystem(), book.title, page_number, display=True
        )
        st.image(
            page_image, width="stretch",
            caption=Validation.page_photo_caption.format(n=page_number),
        )
    except FileNotFoundError:
        st.info(Validation.page_photo_missing)

    if page is None:
        doc = st.session_state.firestore.get_by_reference(
            collection='pages', document_ref=f"{book.document_id}_{page_number}"
        )
        if doc.exists:
            page = Page(db_object=doc.to_dict())
        else:
            st.info(Validation.page_not_entered)
            page = Page(page_number=page_number, book=book.get_ref())
        # Keep the displayed baseline through the form's submit rerun. A new
        # database read here could turn a missing placeholder into a registered
        # competitor's page and apply this form's stale values to that document.
        pages[page_scope] = page

    # These existing widget keys are page-number scoped. Re-seed them when a
    # different book uses that page number, while preserving ordinary reruns.
    widget_books = st.session_state.setdefault("_validation_page_widget_books", {})
    previous_book = widget_books.get(page_number)
    if previous_book is not None and previous_book != book.document_id:
        st.session_state[text_key] = page.text
        st.session_state[story_key] = page.contains_story
    widget_books[page_number] = book.document_id

    with st.form(f"validation_page_form_{page_number}"):
        contains_story = st.checkbox(
            Validation.page_contains_story_label, value=page.contains_story,
            key=story_key,
        )
        text = st.text_area(
            Validation.page_text_label, value=page.text, height=300,
            key=text_key,
        )
        submitted = st.form_submit_button(
            Validation.save_page_button, key="validation_page_submit_button"
        )

    if not submitted:
        return

    changes = {
        field: value
        for field, value in (("contains_story", contains_story), ("text", text))
        if value != getattr(page, field)
    }
    if not changes:
        st.success(Validation.page_saved)
        return

    audit = [
        {
            "entity_id": page.document_id,
            "field": field,
            "old_value": getattr(page, field),
            "new_value": value,
        }
        for field, value in changes.items()
    ]
    if not recovery.save(
        page, changes, source="validation_form",
        draft={"text": text, "contains_story": contains_story},
        audit=audit, on_saved=on_saved,
    ):
        recovery.render(st, on_saved=on_saved)


# ---------------------------------------------------------------------------
# Characters & aliases editor
# ---------------------------------------------------------------------------
def _edit_aliases(book, character_ref):
    """Render an editable alias list for the selected character."""
    st.markdown(f"**{Validation.aliases_label}**")
    firestore = st.session_state['firestore']
    alias_docs = list(
        firestore.query_stream(
            collection='aliases', field='character', op='==', value=character_ref
        )
    )
    if not alias_docs:
        st.caption(Validation.no_aliases)
        return

    for alias_doc in alias_docs:
        alias = Alias(db_object=alias_doc.to_dict())
        with st.form(f"validation_alias_form_{alias_doc.id}"):
            new_name = st.text_input(
                Validation.alias_name_label, value=alias.name,
                key=f"validation_alias_name_{alias_doc.id}",
            )
            submitted = st.form_submit_button(
                Validation.save_alias_button,
                key=f"validation_alias_submit_{alias_doc.id}",
            )
        if not submitted:
            continue
        new_name = new_name.strip()
        if not new_name or new_name == alias.name:
            continue
        # An alias document id is derived from its name, so a rename is a
        # create-new + delete-old migration. Guard against colliding with an
        # existing alias in this book.
        new_alias = Alias(book=book.title)
        new_alias.character = character_ref
        new_alias.name = new_name
        if firestore.document_exists(collection='aliases', doc_id=new_alias.document_id):
            st.warning(Validation.alias_exists)
            continue
        _log(book, EditLog.ENTITY_ALIAS, alias.document_id, 'name', alias.name, new_name)
        new_alias.register()
        firestore.delete_document(collection='aliases', doc_id=alias_doc.id)
        st.success(Validation.alias_saved)
        st.rerun()


def characters_editor(book):
    st.subheader(Validation.characters_header)

    # Character.rename and alias linking rely on the book being the current book
    # and on the book-scoped character lookup.
    st.session_state['current_book'] = book
    character_dict = book.get_character_dict()
    st.session_state['book_character_dict'] = character_dict

    if not character_dict:
        st.info(Validation.no_characters)
        return

    names = list(character_dict.keys())
    selected_name = st.selectbox(
        Validation.character_select_label, options=names,
        key="validation_character_select",
    )
    character_ref = character_dict[selected_name]
    char_doc = character_ref.get()
    if not char_doc.exists:
        st.warning(Validation.no_characters)
        return

    character = Character(db_object=char_doc.to_dict())

    with st.form(f"validation_character_form_{character_ref.id}"):
        new_name = st.text_input(
            CharacterForm.name_label, value=character.name,
            key=f"validation_char_name_{character_ref.id}",
        )
        new_gender = st.selectbox(
            CharacterForm.gender_label, options=CharacterForm.gender_options,
            index=_guarded_index(CharacterForm.gender_options, character.gender),
            key=f"validation_char_gender_{character_ref.id}",
        )
        new_ethnicity = st.selectbox(
            CharacterForm.ethnicity_label, options=CharacterForm.ethnicity_options,
            index=_guarded_index(CharacterForm.ethnicity_options, character.ethnicity),
            key=f"validation_char_ethnicity_{character_ref.id}",
        )
        new_disability = st.selectbox(
            CharacterForm.disability_label, options=CharacterForm.disability_options,
            index=_guarded_index(CharacterForm.disability_options, character.disability),
            key=f"validation_char_disability_{character_ref.id}",
        )
        new_protagonist = st.checkbox(
            CharacterForm.protagonist_label, value=character.protagonist,
            key=f"validation_char_protagonist_{character_ref.id}",
        )
        new_human = st.checkbox(
            CharacterForm.human_label, value=character.human,
            key=f"validation_char_human_{character_ref.id}",
        )
        new_plural = st.checkbox(
            CharacterForm.plural_label, value=character.plural,
            key=f"validation_char_plural_{character_ref.id}",
        )
        submitted = st.form_submit_button(
            Validation.save_character_button,
            key=f"validation_char_submit_{character_ref.id}",
        )

    _edit_aliases(book, character_ref)

    if not submitted:
        return

    entity_id = character.document_id

    # Non-name fields write through to the (current-id) document. Log each change.
    for field, old_value, new_value in (
        ('gender', character.gender, new_gender),
        ('ethnicity', character.ethnicity, new_ethnicity),
        ('disability', character.disability, new_disability),
        ('protagonist', character.protagonist, new_protagonist),
        ('human', character.human, new_human),
        ('plural', character.plural, new_plural),
    ):
        if new_value != old_value:
            _log(book, EditLog.ENTITY_CHARACTER, entity_id, field, old_value, new_value)
            setattr(character, field, new_value)

    # Name change: the document id is name-derived, so handle the three cases
    # (same-doc tweak, true migrating rename, collision) explicitly.
    new_name = new_name.strip()
    if not new_name:
        st.warning(Validation.character_name_required)
        return
    if new_name != character.name:
        _log(book, EditLog.ENTITY_CHARACTER, entity_id, 'name', character.name, new_name)
        if character._document_id_for(new_name) == character.document_id:
            # Differs only in case/whitespace: same document, plain write-through.
            character.name = new_name
        elif st.session_state.firestore.document_exists(
            collection='characters', doc_id=character._document_id_for(new_name)
        ):
            st.warning(Validation.rename_exists)
            return
        else:
            character.rename(new_name)

    st.success(Validation.character_saved)


# ---------------------------------------------------------------------------
# Review surface (Part A) + approval
# ---------------------------------------------------------------------------
def render_review():
    book_id = st.session_state['_validation_book_id']
    # Reload the book fresh each run so the editors reflect the latest persisted
    # state (all corrections write through immediately).
    try:
        doc = st.session_state.firestore.get_by_reference(
            collection='books', document_ref=book_id
        )
    except GoogleAPIError:
        logger.warning('Validation reload failed for %s', book_id, exc_info=True)
        st.error(Validation.review_failed)
        _review_retry_button()
        return
    if not doc.exists:
        _forget_review(book_id)
        st.rerun()
        return

    book = Book(db_object=doc.to_dict())
    st.session_state['current_book'] = book

    st.header(Validation.review_header.format(title=book.title))
    review = _review_session(book_id)
    if not review.get('token'):
        # Restored selection without its original token cannot identify the
        # owning tab. Only Open creates a new review session.
        st.warning(Validation.review_session_missing)
        _review_back_button(book)
        return

    # The throttle proposes a write time on a COPY. Publish it only after the
    # transaction commits a refresh. Every interaction still checks ownership,
    # including a form posted from a render before another session took over.
    heartbeat = dict(st.session_state.get('_validation_heartbeat', {}))
    refresh_due = validation_heartbeat_due(
        heartbeat, book_id, datetime.now(timezone.utc).timestamp(),
    )
    try:
        result = book.acquire_validation_lease(
            review['token'], review.get('username'), refresh_due,
        )
    except (ValidationLeaseUnavailable, GoogleAPIError):
        logger.warning('Validation acquisition failed for %s', book_id, exc_info=True)
        st.error(Validation.review_failed)
        if not _review_back_button(book):
            _review_retry_button()
        return
    if result == ValidationLeaseStatus.MISSING:
        _forget_review(book_id)
        st.rerun()
        return
    if result == ValidationLeaseStatus.BUSY:
        _show_busy_review(book)
        return
    if result not in (ValidationLeaseStatus.OWNED, ValidationLeaseStatus.REFRESHED):
        st.warning(Validation.review_session_missing)
        _review_back_button(book)
        return
    if result == ValidationLeaseStatus.REFRESHED:
        # A reacquisition can refresh even when the local throttle was not due.
        heartbeat[book_id] = book.validation_active_at.timestamp()
        st.session_state['_validation_heartbeat'] = heartbeat

    # Show the validator who originally entered this book. entered_by may be a
    # plain username string or a user DocumentReference; entered_by_username
    # (utilities, #129) guards both.
    entered_by_name = entered_by_username(book.entered_by)
    if entered_by_name:
        st.caption(Validation.entered_by_label.format(name=entered_by_name))
    st.write(Validation.review_intro)

    if _review_back_button(book):
        return

    tab_metadata, tab_pages, tab_characters = st.tabs(
        [Validation.tab_metadata, Validation.tab_pages, Validation.tab_characters]
    )
    with tab_metadata:
        metadata_editor(book)
    with tab_pages:
        page_text_editor(book)
    with tab_characters:
        characters_editor(book)

    st.divider()
    st.subheader(Validation.approve_header)
    st.write(Validation.approve_help)
    if st.button(Validation.approve_button, key="validation_approve_button"):
        try:
            result = book.approve_with_validation_lease(
                review['token'], review.get('username'), _validator_ref(),
            )
        except (ValidationLeaseUnavailable, GoogleAPIError):
            logger.warning('Validation approval failed for %s', book_id, exc_info=True)
            st.error(Validation.approval_failed)
            return
        if result == ValidationLeaseStatus.APPROVED:
            _forget_review(book_id)
            st.success(Validation.approved_success.format(title=book.title))
            st.rerun()
        elif result == ValidationLeaseStatus.MISSING:
            _forget_review(book_id)
            st.warning(Validation.book_no_longer_exists)
            st.rerun()
        else:
            st.warning(Validation.approval_lease_lost)
            _review_retry_button()


if '_validation_book_id' in st.session_state:
    render_review()
else:
    render_list()
