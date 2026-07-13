import streamlit as st
from datetime import datetime, timezone
from text_content import CharacterForm
from utilities import load_character_dict, clear_entity_form_state
from .base_structure import DataStructureBase, Field


class Character(DataStructureBase):

    fields = {
        'is_registered': False,
        'book': None,
        'name': "",
        'gender': "",
        'ethnicity': "",
        'disability': "",
        'protagonist': False,
        'human': True,
        'plural': False,
        'datetime_created': -1,
        'last_updated': -1,
        'entered_by': None
    }

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {
        'name': 'Name',
        'gender': 'Gender',
        'ethnicity': 'Ethnicity',
        'disability': 'Disability',
        'protagonist': 'Is protagonist',
        'human': 'Is human',
        'plural': 'Is plural',
    }

    ref_fields = ['book']

    def __init__(self, db_object=None, book=None):
        super().__init__(collection='characters', db_object=db_object)
        if db_object is None:
            self.book = book

    def _document_id_for(self, name):
        """Compute the document id this character would have for ``name``.

        ``document_id`` is ``{book_id}_{name}``, so a rename changes the id;
        editing code uses this to test for a target-id collision (and to find
        the old id) without mutating ``self.name``.

        ``self.book`` is a ``DocumentReference`` whose ``.id`` is available
        locally, so use it directly rather than ``.get().id``, which makes a
        Firestore round-trip on every access of this hot-path property (mirrors
        the #128 fix in page.py; audit item 8).
        """
        return f"{self.book.id}_{name.replace(' ', '_').lower()}"

    @property
    def document_id(self):
        return self._document_id_for(self.name)

    def _render_fields(self, key_suffix):
        """Render the character form widgets and return the typed name.

        The non-identifying fields (gender/ethnicity/disability/protagonist/
        human/plural) are assigned to ``self`` so the write-through ``Field``
        descriptors persist edits immediately for a registered character. The
        **name** is deliberately returned as a local rather than written back:
        because ``document_id`` is derived from the name, writing it through
        would point the per-field update at a *new* document and orphan the old
        one. Callers decide how to apply a name change (create new vs migrate).
        """
        name = st.text_input(
            CharacterForm.name_label, value=self.name,
            key=f"character_form_name_{key_suffix}"
        )

        gender_index = 0
        if self.gender in CharacterForm.gender_options:
            gender_index = CharacterForm.gender_options.index(self.gender)
        self.gender = st.selectbox(
            CharacterForm.gender_label,
            options=CharacterForm.gender_options,
            index=gender_index,
            help=CharacterForm.gender_help,
            key=f"character_form_gender_{key_suffix}"
        )

        ethnicity_index = 0
        if self.ethnicity in CharacterForm.ethnicity_options:
            ethnicity_index = CharacterForm.ethnicity_options.index(self.ethnicity)
        self.ethnicity = st.selectbox(
            CharacterForm.ethnicity_label,
            options=CharacterForm.ethnicity_options,
            index=ethnicity_index,
            help=CharacterForm.ethnicity_help,
            key=f"character_form_ethnicity_{key_suffix}"
        )

        disability_index = 0
        if self.disability in CharacterForm.disability_options:
            disability_index = CharacterForm.disability_options.index(self.disability)
        self.disability = st.selectbox(
            CharacterForm.disability_label,
            options=CharacterForm.disability_options,
            index=disability_index,
            help=CharacterForm.disability_help,
            key=f"character_form_disability_{key_suffix}"
        )

        self.protagonist = st.checkbox(
            CharacterForm.protagonist_label,
            value=self.protagonist,
            help=CharacterForm.protagonist_help,
            key=f"character_form_protagonist_{key_suffix}"
        )
        self.human = st.checkbox(
            CharacterForm.human_label,
            value=self.human,
            help=CharacterForm.human_help,
            key=f"character_form_human_{key_suffix}"
        )
        self.plural = st.checkbox(
            CharacterForm.plural_label,
            value=self.plural,
            help=CharacterForm.plural_help,
            key=f"character_form_plural_{key_suffix}"
        )

        return name

    def to_form(self):

        st.header(CharacterForm.header)

        # Capture the entity id once, before any field is written back, so every
        # widget key below stays constant for this render. Keying per
        # document_id stops one character's values bleeding into the next (#80).
        key_suffix = self.document_id

        name = self._render_fields(key_suffix)

        submitted = st.form_submit_button(
            CharacterForm.save_button, key=f"character_form_submit_{key_suffix}"
        )

        if submitted:
            # This is a brand-new (unregistered) character, so the name assign
            # does not write through; it just sets the document id used below.
            self.name = name
            if st.session_state.firestore.document_exists(
                collection='characters',
                doc_id=self.document_id
            ):
                # Same-name add EDITS the existing character (#201). After the
                # AI review has created the book's cast, re-entering a detected
                # name used to show only a quiet warning — the Save button
                # looked dead. document_id is book-scoped ({book_id}_{name}),
                # so the collision is always THIS book's character: route to
                # the manage view with its edit form open, with an explanation.
                # (Buttons are not allowed inside an st.form, so this is a
                # direct route rather than an offered choice.)
                clear_entity_form_state("character_form_")
                st.session_state['_editing_character_id'] = self.document_id
                st.session_state['_manage_flash'] = (
                    CharacterForm.character_exists_editing.format(name=self.name)
                )
                st.session_state['now_entering'] = 'manage'
                st.rerun()
            else:
                self.register()
                character_ref = self.get_ref()
                # Link the character to the book currently being edited so the
                # book document holds the list of its characters.
                current_book = st.session_state.get('current_book')
                if current_book is not None:
                    current_book.add_character(character_ref)
                st.session_state['character_dict'][self.name] = character_ref
                # Keep the book-scoped lookup (used for alias entry) in sync.
                st.session_state.setdefault('book_character_dict', {})[self.name] = (
                    character_ref
                )
                # Invalidate shared cache so new/other sessions re-read this
                # character (this session sees it via the in-place update above).
                load_character_dict.clear()
                st.session_state['now_entering'] = 'text'
                st.rerun()

    def edit_form(self):
        """Render an edit form for an existing (registered) character.

        Re-uses the same widgets as ``to_form``. Edits to the non-name fields
        write through to Firestore immediately via the ``Field`` descriptors.
        On submit, a changed name triggers a safe rename migration (see
        ``rename``); an unchanged name needs no further work because the other
        fields have already been persisted by the write-through.
        """
        st.header(CharacterForm.edit_header)

        key_suffix = self.document_id

        new_name = self._render_fields(key_suffix)

        submitted = st.form_submit_button(
            CharacterForm.update_button, key=f"character_edit_submit_{key_suffix}"
        )

        if submitted:
            new_name = new_name.strip()
            if not new_name:
                st.warning(CharacterForm.name_required)
                return
            if new_name == self.name:
                # Non-name fields are already saved by write-through; nothing
                # more to do but close the edit form.
                pass
            elif self._document_id_for(new_name) == self.document_id:
                # Name differs only in case/whitespace, so the document id is
                # unchanged — no migration needed; a plain write-through updates
                # the stored name on the same document.
                self.name = new_name
            elif st.session_state.firestore.document_exists(
                collection='characters',
                doc_id=self._document_id_for(new_name)
            ):
                st.warning(CharacterForm.rename_exists)
                return
            else:
                # True rename: the document id changes, so migrate safely.
                self.rename(new_name)
            st.session_state.pop('_editing_character_id', None)
            st.session_state['now_entering'] = 'manage'
            st.rerun()

    def rename(self, new_name):
        """Migrate this character to ``new_name`` (and hence a new document id).

        ``document_id`` is ``{book_id}_{name}``, so a naive write-through would
        create a new document and orphan the old one — along with its aliases
        (whose ``character`` reference points at the old doc) and the book's
        ``characters`` ref-list entry. This recreates the character under the
        new id, repoints its aliases, swaps the book's characters-list entry,
        updates the session lookup dicts and finally deletes the old document.
        """
        firestore = st.session_state['firestore']
        old_ref = self.get_ref()
        old_name = self.name

        # Set the new name without triggering the per-field write-through: that
        # would target the not-yet-existing new document with `.update()` (which
        # fails on a missing doc). Suppress it and do a full `.set()` instead.
        self.reading_from_db = True
        self.name = new_name
        self.last_updated = datetime.now(timezone.utc)
        self.reading_from_db = False

        new_ref = self.get_ref()
        # Create the new document (full save) under the new id.
        self.save_to_db()

        # Repoint every alias from the old character reference to the new one.
        # An alias's own document id is book+alias-name based, so it does not
        # change here — only its `character` field.
        for alias_doc in firestore.query_stream(
            collection='aliases', field='character', op='==', value=old_ref
        ):
            firestore.update_field(
                collection='aliases',
                document=alias_doc.id,
                field='character',
                value=new_ref,
            )

        # Swap the entry in the current book's characters list (remove old ref,
        # add new); both re-assign the list so the write-through persists it.
        current_book = st.session_state.get('current_book')
        if current_book is not None:
            current_book.remove_character(old_ref)
            current_book.add_character(new_ref)

        # Keep the book-scoped lookup in sync.
        book_dict = st.session_state.get('book_character_dict', {})
        book_dict.pop(old_name, None)
        book_dict[new_name] = new_ref

        # Update the global lookup: only drop the old key if it still points at
        # this character (a different book may hold a same-named character).
        global_dict = st.session_state.get('character_dict', {})
        existing = global_dict.get(old_name)
        if existing is not None and existing.path == old_ref.path:
            global_dict.pop(old_name, None)
        global_dict[new_name] = new_ref

        # Finally remove the old document and invalidate the shared cache.
        firestore.delete_document(collection='characters', doc_id=old_ref.id)
        load_character_dict.clear()

    def delete(self):
        """Delete this character, its aliases, and unlink it from its book.

        Deletes every alias whose ``character`` reference points at this
        character, removes the character from the current book's character
        list, prunes the session lookup dicts, then deletes the character
        document itself.
        """
        firestore = st.session_state['firestore']
        character_ref = self.get_ref()

        for alias_doc in firestore.query_stream(
            collection='aliases', field='character', op='==', value=character_ref
        ):
            firestore.delete_document(collection='aliases', doc_id=alias_doc.id)

        current_book = st.session_state.get('current_book')
        if current_book is not None:
            current_book.remove_character(character_ref)

        st.session_state.get('book_character_dict', {}).pop(self.name, None)
        # Only drop the global entry if it still points at this character — a
        # different book may hold a character with the same name.
        global_dict = st.session_state.get('character_dict', {})
        existing = global_dict.get(self.name)
        if existing is not None and existing.path == character_ref.path:
            global_dict.pop(self.name, None)

        firestore.delete_document(collection='characters', doc_id=self.document_id)
