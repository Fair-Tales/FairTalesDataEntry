import streamlit as st
from utilities import author_entry_to_name, navigate_to, split_name, clear_entity_form_state
from text_content import Instructions, BookForm
from .base_structure import DataStructureBase, Field
from .author import Author
from .illustrator import Illustrator
from .publisher import Publisher
from datetime import date

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
    _title = st.text_input(
        BookForm.title_label, value=_title_default, key=f"book_form_title_{key_suffix}"
    ).strip()

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
    if 'current_author' in st.session_state:
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
    if 'current_publisher' in st.session_state:
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
    if 'current_illustrator' in st.session_state:
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

    def add_character(self, character_ref):
        """Link a character (by Firestore reference) to this book.

        Re-assigns the ``characters`` list (rather than mutating in place) so
        that the Field descriptor write-through persists the new list to
        Firestore when the book is registered. No-op if already linked.
        """
        if all(ref.path != character_ref.path for ref in self.characters):
            self.characters = self.characters + [character_ref]

    def remove_character(self, character_ref):
        """Unlink a character (by Firestore reference) from this book."""
        self.characters = [
            ref for ref in self.characters if ref.path != character_ref.path
        ]

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

        character_dict = {}
        existing_refs = []
        for ref in refs:
            doc = ref.get()
            if doc.exists:
                character_dict[doc.to_dict()['name']] = ref
                existing_refs.append(ref)

        # Persist the resolved list so the repair is paid for once: this
        # overwrites a legacy non-list value, saves a back-filled list, and
        # prunes any dangling references. Short-circuit guards len() against a
        # legacy non-list value and avoids a write when nothing changed.
        if self.is_registered:
            stored = self.characters
            if not isinstance(stored, list) or len(existing_refs) != len(stored):
                self.characters = existing_refs
        return character_dict

