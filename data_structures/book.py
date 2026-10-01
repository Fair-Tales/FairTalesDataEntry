import streamlit as st
from google.cloud import firestore
from utilities import author_entry_to_name, navigate_to, split_name, clear_entity_form_state
from text_content import Instructions, BookForm
from .base_structure import DataStructureBase, Field
from .author import Author
from .illustrator import Illustrator
from .publisher import Publisher
from datetime import date, datetime, timezone

def _new_person(person_cls, extracted_key):
    """Create a fresh Author, seeding forename/surname from a name extracted by
    the photo-first flow (#59) if one is pending in session state.

    Used for the Author sub-entity only; the Illustrator is now a single-name
    entity (#156) handled inline like the Publisher below. The extracted name is
    consumed (popped) so it only pre-fills the sub-form once. Returns the new,
    unregistered person object.
    """
    person = person_cls()
    # Starting a fresh sub-entity: drop any persisted form-widget state so the
    # new Author form re-seeds from value=/index= (see #80).
    clear_entity_form_state(f"{person_cls.__name__.lower()}_form_")
    extracted = st.session_state.pop(extracted_key, None)
    if extracted:
        forename, surname = split_name(extracted)
        person.forename = forename
        person.surname = surname
    return person


def _new_named(entity_cls, form_prefix, extracted_key):
    """Create a fresh single-name entity (Illustrator #156 / Publisher), seeding
    its ``name`` from a photo-extracted value if one is pending in session state.

    Drops any persisted widget state for the entity's form so a new (empty
    document_id) record re-seeds from ``value=`` rather than inheriting the
    previous one (see #80). Returns the new, unregistered entity.
    """
    clear_entity_form_state(form_prefix)
    entity = entity_cls()
    extracted = st.session_state.pop(extracted_key, None)
    if extracted:
        entity.name = extracted
    return entity

def add_book_entries(self):
    if 'adding_book_entries' not in st.session_state or not st.session_state['adding_book_entries']:
        st.session_state['adding_book_entries'] = True
        st.rerun()
    else:
        if self.author is None:
            st.session_state['current_author'] = _new_person(Author, 'extracted_author_name')
            navigate_to("./pages/add_author.py")
        else:
            st.session_state['current_author'] = self.author.get()
        if self.illustrator is None:
            # Illustrator is now a single-name entity (#156), mirroring Publisher.
            st.session_state['current_illustrator'] = _new_named(
                Illustrator, "illustrator_form_", 'extracted_illustrator_name'
            )
            navigate_to("./pages/add_illustrator.py")
        else:
            st.session_state['current_illustrator'] = self.illustrator.get()
        if self.publisher is None:
            st.session_state['current_publisher'] = _new_named(
                Publisher, "publisher_form_", 'extracted_publisher_name'
            )
            navigate_to("./pages/add_publisher.py")
        else:
            st.session_state['current_publisher'] = self.publisher.get()
        st.session_state['adding_book_entries'] = False
        form_content(self)

def _isbn_year(published_date):
    if published_date and len(published_date) >= 4 and published_date[:4].isdigit():
        year = int(published_date[:4])
        if 1900 <= year <= date.today().year:
            return year
    return None

def form_content(self):
    st.header(BookForm.header)

    # Capture the entity id once, before any field is written back, so every
    # widget key below stays constant for this render even as fields change on
    # submit. Keying per document_id prevents one book's values bleeding into
    # the next (see #80).
    key_suffix = self.document_id

    isbn_meta = st.session_state.get('isbn_metadata', {})
    isbn_used = False

    if isbn_meta.get('title') and not self.title:
        _title_default = isbn_meta['title']
        isbn_used = True
    else:
        _title_default = self.title
    # The title derives the book's Firestore document_id (and, transitively, its
    # page/character document ids and S3 photo folder). Once a book is
    # registered, changing the title here would orphan or overwrite documents
    # (#224), so lock the field for a registered book — a dedicated rename tool
    # exists separately (scripts/rename_book.py).
    _title = st.text_input(
        BookForm.title_label, value=_title_default,
        disabled=self.is_registered, key=f"book_form_title_{key_suffix}"
    ).strip()
    if self.is_registered:
        st.caption(BookForm.title_readonly_caption)

    isbn_year = _isbn_year(isbn_meta.get('published_date', ''))
    if self.published != -1:
        published_index = self.published - 1900
    elif isbn_year is not None:
        published_index = isbn_year - 1900
        isbn_used = True
    else:
        published_index = 112
    _published = int(st.selectbox(
    BookForm.published_label,
    (x for x in range(1900, (date.today().year + 1))),
    index = published_index,
    key=f"book_form_published_{key_suffix}"
    ))
    # Photo-first AI pre-fill notice (#155/#150): tell the user the year was read
    # from their photos so they understand it's already populated.
    if st.session_state.get('ai_prefilled_year'):
        st.caption(BookForm.ai_prefill_year_caption)
    st.write(Instructions.author_publisher_illustrator_select)

    author_options = [BookForm.new_author_option] + list(
        st.session_state['author_dict'].keys()
    )
    author_index = 0
    if self.author is not None:
        # A saved book's OWN author reference is authoritative — prefer it over
        # any leftover current_author session key, which is only meaningful on
        # the add flow and can be stale (a different book / a wrong AI guess) by
        # the time the metadata is re-opened for editing. Editing seeded purely
        # from that stale session state showed the wrong author and, on submit,
        # silently overwrote the stored reference.
        _author_name = author_entry_to_name(self.author.get())
        if _author_name in author_options:
            author_index = author_options.index(_author_name)
    elif 'current_author' in st.session_state:
        _author_name = author_entry_to_name(st.session_state['current_author'])
        if _author_name in author_options:
            author_index = author_options.index(_author_name)

    _author = st.selectbox(
        BookForm.author_select_label,
        options=author_options,
        index=author_index,
        help=BookForm.author_help,
        key=f"book_form_author_{key_suffix}"
    )
    # "Found by AI" caption so the user knows the author was pre-filled from their
    # photos and will be confirmed on the next step (#155).
    if st.session_state.get('ai_prefilled_author'):
        st.caption(BookForm.ai_prefill_author_caption)

    publisher_options = [None] + list(
        st.session_state['publisher_dict'].keys()
    )

    publisher_index = 0
    if self.publisher is not None:
        # Prefer the book's own stored publisher over stale session state / ISBN.
        _publisher_data = self.publisher.get().to_dict() or {}
        _publisher_name = _publisher_data.get('name', '').replace('_', ' ')
        if _publisher_name in publisher_options:
            publisher_index = publisher_options.index(_publisher_name)
    elif 'current_publisher' in st.session_state:
        _publisher_name = st.session_state['current_publisher'].to_dict()['name'].replace('_', ' ')
        if _publisher_name in publisher_options:
            publisher_index = publisher_options.index(_publisher_name)
    elif isbn_meta.get('publisher') and isbn_meta['publisher'] in publisher_options:
        publisher_index = publisher_options.index(isbn_meta['publisher'])
        isbn_used = True

    _publisher = st.selectbox(
        BookForm.publisher_select_label,
        options=publisher_options,
        index=publisher_index,
        help=BookForm.publisher_help,
        format_func = lambda x: BookForm.new_publisher_option if x == None else x,
        key=f"book_form_publisher_{key_suffix}"
    )
    if st.session_state.get('ai_prefilled_publisher'):
        st.caption(BookForm.ai_prefill_publisher_caption)

    illustrator_options = [None] + list(
        st.session_state['illustrator_dict'].keys()
        )
    
    illustrator_index = 0
    if self.illustrator is not None:
        # Prefer the book's own stored illustrator over a stale session key, so
        # editing shows the saved illustrator rather than the wrong one / blank.
        _illustrator_name = author_entry_to_name(self.illustrator.get())
        if _illustrator_name in illustrator_options:
            illustrator_index = illustrator_options.index(_illustrator_name)
    elif 'current_illustrator' in st.session_state:
        _illustrator_name = author_entry_to_name(st.session_state['current_illustrator'])
        if _illustrator_name in illustrator_options:
            illustrator_index = illustrator_options.index(_illustrator_name)

    _illustrator = st.selectbox(
        BookForm.illustrator_select_label,
        options=illustrator_options,
        index=illustrator_index,
        help=BookForm.illustrator_help,
        format_func = lambda x: BookForm.new_illustrator_option if x == None else x,
        key=f"book_form_illustrator_{key_suffix}"
    )
    if st.session_state.get('ai_prefilled_illustrator'):
        st.caption(BookForm.ai_prefill_illustrator_caption)

    values = [
        BookForm.theme_options[theme]
        for theme in BookForm.theme_options.keys()
        if getattr(self, theme)
    ]
    _themes = st.multiselect(
        BookForm.themes_label,
        options=BookForm.theme_options.values(), help=BookForm.themes_help,
        default=values,
        key=f"book_form_themes_{key_suffix}"
    )

    _comment = st.text_input(
        BookForm.comment_label, value=self.comment, help=BookForm.comment_help,
        key=f"book_form_comment_{key_suffix}"
    )

    if isbn_used:
        st.caption(BookForm.isbn_prefill_caption)

    submitted = st.form_submit_button(BookForm.submit_button, key=f"book_form_submit_{key_suffix}")

    if submitted:

        if not _title.strip():
            st.warning(BookForm.title_required)
            return

        st.session_state['current_book'] = self
        # A registered book's title is locked (the input is disabled above): its
        # document_id — and its pages'/characters' ids and S3 folder — derive
        # from the title, so writing a changed title through the Field
        # descriptor would orphan or overwrite the wrong document (#224). Never
        # reassign it here; only a brand-new (unregistered) book sets its title.
        if not self.is_registered:
            self.title = _title
        self.published = _published
        self.author = _author
        self.publisher = _publisher
        self.illustrator = _illustrator
        self.comment = _comment

        for theme, theme_string in BookForm.theme_options.items():
            setattr(self, theme, theme_string in _themes)

        if not self.editing and st.session_state.firestore.document_exists(
            collection='books',
            doc_id=self.document_id
        ):
            st.warning(BookForm.book_exists)

        elif (self.author is None) or (self.illustrator is None) or (self.publisher is None):
            add_book_entries(self)
        else:
            self.editing = False
            if self.is_registered:
                if st.session_state.current_book.photos_uploaded:
                    navigate_to("./pages/enter_text.py")
                else:
                    navigate_to("./pages/page_photo_upload.py")
            else:
                st.session_state['active_form_to_confirm'] = 'new_book'
                navigate_to("./pages/confirm_entry.py")

class Book(DataStructureBase):

    fields = {
        'is_registered': False,
        'title': "",
        'author': None,
        'character_count': -1,
        'page_count': -1,
        'word_count': -1,
        'sentence_count': -1,
        'datetime_created': -1,
        'entered_by': None,
        'entry_status': 'started',
        'first_content_page': -1,
        'last_content_page': -1,
        'illustrator': None,
        'publisher': None,
        'last_updated': -1,
        'published': -1,
        'validated': False,
        'validated_by': None,
        'photos_uploaded': False,
        'photos_url': "",
        'comment': "",
        'datetime_submitted': -1,
        # Validation heartbeat (#200): set (throttled) whenever a validator has
        # this book OPEN in the validation UI, so its owner cannot reopen a book
        # that is actively being reviewed. ``-1`` = never opened for validation.
        'validation_active_at': -1,
        'validation_active_by': None,
        # List of Firestore references to the Character documents that appear in
        # this book. A character may be referenced by more than one book, which
        # is why the relationship is modelled as a list of references on the
        # book rather than nesting characters inside it. Defaults to an empty
        # list; older book documents predate this field and fall back to [].
        'characters': []
    }
    fields.update({
        theme: False
        for theme in BookForm.theme_options.keys()
    })

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {
        'title': 'Title',
        'published': 'Date first published',
        'author': 'Author',
        'publisher': 'Publisher',
        'illustrator': 'Illustrator',
        'comment': 'Comment'
    }
    form_fields.update(BookForm.theme_options)

    ref_fields = ['author', 'illustrator', 'publisher']  # Reference fields will display document ID for human consumption

    def __init__(self, db_object=None):
        super().__init__(collection='books', db_object=db_object)
        self.editing = False

    @property
    def document_id(self):
        return self.title.lower().replace(" ", "_")

    def to_form(self):
        if 'adding_book_entries' in st.session_state and st.session_state['adding_book_entries']:
            add_book_entries(self)
        else:
            form_content(self)

    def _write_characters_transform(self, transform):
        """Persist a membership change to this book's ``characters`` array with a
        Firestore atomic transform (``ArrayUnion``/``ArrayRemove``).

        Firestore applies the transform server-side, so a concurrent session
        editing the SAME book's cast (two tabs, or archivist + validator) can no
        longer clobber each other's edits by writing back a stale whole-list copy
        (#225). The ``last_updated`` bump rides in the SAME update call. No-op
        until the book is registered. The in-memory ``last_updated`` is mirrored
        (under the ``reading_from_db`` guard so it does not trigger its own
        write); the caller is responsible for keeping ``self.characters`` in sync.
        """
        if not self.is_registered:
            return
        now = datetime.now(timezone.utc)
        st.session_state.firestore.update_fields(
            collection=self.belongs_to_collection,
            document=self.document_id,
            values={'characters': transform, 'last_updated': now},
        )
        self.reading_from_db = True
        self.last_updated = now
        self.reading_from_db = False

    def add_character(self, character_ref):
        """Link a character (by Firestore reference) to this book.

        Keeps the in-memory ``characters`` list current (appended under the
        ``reading_from_db`` guard so the Field write-through does NOT fire a
        whole-list overwrite) and persists the single membership change with an
        atomic ``ArrayUnion`` so concurrent sessions cannot drop each other's
        characters (#225). No-op if already linked.
        """
        if all(ref.path != character_ref.path for ref in self.characters):
            self.reading_from_db = True
            self.characters = self.characters + [character_ref]
            self.reading_from_db = False
            self._write_characters_transform(firestore.ArrayUnion([character_ref]))

    def remove_character(self, character_ref):
        """Unlink a character (by Firestore reference) from this book.

        Mirrors :meth:`add_character`: updates the in-memory list under the
        ``reading_from_db`` guard and persists the removal with an atomic
        ``ArrayRemove`` (#225). No write when the character was not linked.
        """
        if any(ref.path == character_ref.path for ref in self.characters):
            self.reading_from_db = True
            self.characters = [
                ref for ref in self.characters if ref.path != character_ref.path
            ]
            self.reading_from_db = False
            self._write_characters_transform(firestore.ArrayRemove([character_ref]))

    def get_character_dict(self):
        """Return a {character name: reference} dict for this book's characters.

        References to characters that no longer exist are skipped. For books
        created before the ``characters`` list existed, the list is back-filled
        by querying the characters collection for documents whose ``book`` field
        points at this book, so existing data keeps working transparently.
        """
        refs = self.characters
        # Older book documents may store `characters` as a non-list value (an
        # earlier schema kept a numeric character *count* under this name), so
        # treat anything that isn't a list as "no list yet".
        if not isinstance(refs, list):
            refs = []

        # Back-fill from the character documents' own `book` reference for books
        # that predate the book->characters list (or whose value we just reset).
        if not refs and self.is_registered:
            book_ref = self.get_ref()
            refs = [
                doc.reference
                for doc in st.session_state['firestore'].query_stream(
                    collection='characters', field='book', op='==', value=book_ref
                )
            ]

        # Resolve every reference in ONE batched read (#78): this method runs on
        # nearly every enter-text render (saved cast, alias form, manage view,
        # detection filtering), and the previous serial ``ref.get()`` per
        # character cost a full network round trip each — 10 characters meant 10
        # sequential reads per rerun. ``get_all_by_references`` preserves the
        # reference order, so the dict's insertion order is unchanged.
        character_dict = {}
        existing_refs = []
        snaps = st.session_state['firestore'].get_all_by_references(refs)
        for ref, doc in zip(refs, snaps):
            if doc.exists:
                character_dict[doc.to_dict()['name']] = ref
                existing_refs.append(ref)

        # Persist the resolved list so the repair is paid for once. Membership
        # repairs go through atomic transforms (#225) so a concurrent session's
        # cast edit is never clobbered by writing back a whole-list copy:
        #   * dangling refs pruned from an existing stored list -> ArrayRemove;
        #   * a back-filled empty/absent list -> ArrayUnion of the resolved refs;
        #   * a legacy NON-list value (an earlier schema stored a numeric count
        #     here) -> plain whole-value overwrite, the only safe repair since a
        #     transform requires an array field to already exist.
        # Every branch keeps the in-memory list consistent and skips the write
        # when nothing changed.
        if self.is_registered:
            stored = self.characters
            if not isinstance(stored, list):
                # Legacy non-list value: convert to the resolved reference list.
                self.characters = existing_refs
            elif not stored:
                # Back-fill a book that predates the characters list.
                if existing_refs:
                    self.reading_from_db = True
                    self.characters = existing_refs
                    self.reading_from_db = False
                    self._write_characters_transform(
                        firestore.ArrayUnion(existing_refs)
                    )
            else:
                # Prune only the dangling references, atomically.
                existing_paths = {ref.path for ref in existing_refs}
                dangling = [
                    ref for ref in stored if ref.path not in existing_paths
                ]
                if dangling:
                    self.reading_from_db = True
                    self.characters = existing_refs
                    self.reading_from_db = False
                    self._write_characters_transform(
                        firestore.ArrayRemove(dangling)
                    )
        return character_dict

