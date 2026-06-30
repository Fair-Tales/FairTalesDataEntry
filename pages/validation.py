"""Data-validation workflow (issues #47 / #83).

A project **team member** (or admin) reviews books that archivists have
SUBMITTED, corrects any errors, and marks the entry APPROVED. A book is
"submitted and awaiting validation" when its ``entry_status`` is ``'completed'``
(set by ``confirm_submit``) and it has not yet been validated (``validated`` is
falsy). Approving sets ``validated = True`` and ``validated_by`` to the
validator's user reference via the write-through ``Field`` pattern.

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

from datetime import date

import streamlit as st

from utilities import page_layout, check_authentication_status, is_team_or_above
from data_structures import Book, Page, Character, Alias, EditLog
from text_content import Validation, BookForm, CharacterForm

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


def _entered_by_name(entered_by):
    """The owner username for a book's ``entered_by``, handling BOTH shapes.

    ``entered_by`` may be a ``users``-collection ``DocumentReference`` (the normal
    case) OR a plain username string (legacy/single-DB records, see #131 / the
    ``databot`` owner). Returns the username string (the ref's ``.id``) or ``None``
    when unset, so callers can compare/display owners uniformly."""
    if not entered_by:
        return None
    if isinstance(entered_by, str):
        return entered_by
    return getattr(entered_by, 'id', None) or str(entered_by)


# ---------------------------------------------------------------------------
# Awaiting-validation list (Part A)
# ---------------------------------------------------------------------------
def render_list():
    st.header(Validation.list_header)
    st.write(Validation.list_intro)

    # Admin/team can validate ANY book; a toggle lets them narrow to just the books
    # archivists formally submitted (entry_status == 'completed').
    submitted_only = st.toggle(
        Validation.submitted_only_toggle, value=False,
        key="validation_submitted_only_toggle",
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

    docs = st.session_state.firestore.get_all_documents_stream(collection="books")
    # Filter in Python (rather than a Firestore query) so missing legacy fields
    # ('validated' / 'entry_status') fall back to a default instead of excluding a
    # document or raising.
    pending = [
        data
        for data in (doc.to_dict() for doc in docs)
        if not data.get('validated', False)
        and (not submitted_only or data.get('entry_status') == 'completed')
    ]

    # "Just mine": keep only books the current validator originally entered.
    # entered_by may be a DocumentReference or a plain string, so normalise both
    # to the owner username before comparing (databot-owned books fall away here).
    if scope == Validation.scope_mine:
        username = st.session_state['username']
        pending = [
            data for data in pending
            if _entered_by_name(data.get('entered_by')) == username
        ]

    pending.sort(key=lambda d: (d.get('title') or '').lower())

    if not pending:
        st.info(Validation.none_pending)
        return

    titles = [row.get('title', '') for row in pending]
    selected_title = st.selectbox(
        Validation.select_book_label, options=titles, key="validation_select_book"
    )

    if st.button(Validation.open_review_button, key="validation_open_review_button"):
        row = pending[titles.index(selected_title)]
        book = Book(db_object=row)
        st.session_state['_validation_book_id'] = book.document_id
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

    doc = st.session_state.firestore.get_by_reference(
        collection='pages', document_ref=f"{book.document_id}_{page_number}"
    )
    if doc.exists:
        page = Page(db_object=doc.to_dict())
    else:
        st.info(Validation.page_not_entered)
        page = Page(page_number=page_number, book=book.title)

    with st.form(f"validation_page_form_{page_number}"):
        contains_story = st.checkbox(
            Validation.page_contains_story_label, value=page.contains_story,
            key=f"validation_page_contains_{page_number}",
        )
        text = st.text_area(
            Validation.page_text_label, value=page.text, height=300,
            key=f"validation_page_text_{page_number}",
        )
        submitted = st.form_submit_button(
            Validation.save_page_button, key="validation_page_submit_button"
        )

    if not submitted:
        return

    contains_changed = contains_story != page.contains_story
    text_changed = text != page.text

    if not (contains_changed or text_changed):
        st.success(Validation.page_saved)
        return

    entity_id = page.document_id
    if contains_changed:
        _log(book, EditLog.ENTITY_PAGE, entity_id, 'contains_story',
             page.contains_story, contains_story)
    if text_changed:
        _log(book, EditLog.ENTITY_PAGE, entity_id, 'text', page.text, text)

    page.contains_story = contains_story
    page.text = text
    # A page the archivist never recorded is unregistered, so write-through has
    # nothing to update — perform the initial full save instead.
    if not page.is_registered:
        page.register()

    st.success(Validation.page_saved)


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
    doc = st.session_state.firestore.get_by_reference(
        collection='books', document_ref=book_id
    )
    if not doc.exists:
        st.session_state.pop('_validation_book_id', None)
        st.rerun()
        return

    book = Book(db_object=doc.to_dict())
    st.session_state['current_book'] = book

    st.header(Validation.review_header.format(title=book.title))
    # Show the validator who originally entered this book. entered_by may be a
    # plain username string or a user DocumentReference; _entered_by_name guards both.
    entered_by_name = _entered_by_name(book.entered_by)
    if entered_by_name:
        st.caption(Validation.entered_by_label.format(name=entered_by_name))
    st.write(Validation.review_intro)

    if st.button(Validation.back_to_list_button, key="validation_back_to_list_button"):
        st.session_state.pop('_validation_book_id', None)
        st.rerun()

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
        book.validated = True
        book.validated_by = _validator_ref()
        st.session_state.pop('_validation_book_id', None)
        st.success(Validation.approved_success.format(title=book.title))
        st.rerun()


if '_validation_book_id' in st.session_state:
    render_review()
else:
    render_list()
