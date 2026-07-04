import streamlit as st
from text_content import AliasForm
from .base_structure import DataStructureBase, Field


class Alias(DataStructureBase):

    fields = {
        'is_registered': False,
        'character': None,
        'book': None,
        'name': "",
        'datetime_created': -1,
        'last_updated': -1,
        'entered_by': None
    }

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {
        'name': 'Name'
    }

    ref_fields = ['character', 'book']

    def __init__(self, db_object=None, book=None):
        super().__init__(collection='aliases', db_object=db_object)
        if db_object is None:
            self.book = book

    @property
    def document_id(self):
        # ``self.book`` is a DocumentReference whose ``.id`` is available locally;
        # use it directly rather than ``.get().id``, which makes a Firestore
        # round-trip on every access of this hot-path property (mirrors the #128
        # fix in page.py; audit item 8).
        return f"{self.book.id}_{self.name.replace(' ', '_').lower()}"

    def to_form(self):

        st.header(AliasForm.header)

        # Capture the entity id once, before any field is written back, so every
        # widget key below stays constant for this render. Keying per
        # document_id stops one alias's values bleeding into the next (#80).
        key_suffix = self.document_id

        # Aliases may only be added to characters that belong to the book
        # currently being edited, so scope the options to the book-specific
        # lookup rather than the global character dict.
        book_character_dict = st.session_state.get('book_character_dict', {})
        character_options = list(book_character_dict.keys())

        if not character_options:
            st.warning(AliasForm.no_characters)
            return

        character_index = 0
        if self.character is not None:
            _character_name = self.character.get().to_dict()['name']
            if _character_name in character_options:
                character_index = character_options.index(_character_name)

        selected_character = st.selectbox(
            AliasForm.select_character_label,
            options=character_options,
            index=character_index,
            key=f"alias_form_character_{key_suffix}"
        )

        self.name = st.text_input(
            AliasForm.alias_label, value=self.name,
            key=f"alias_form_name_{key_suffix}"
        )

        submitted = st.form_submit_button(
            AliasForm.save_button, key=f"alias_form_submit_{key_suffix}"
        )

        if submitted:
            # Resolve the selected name to its reference via the book-scoped
            # dict (guarded with .get) so we link the right character even when
            # two books contain characters that share a name.
            self.character = book_character_dict.get(selected_character)
            if self.character is None:
                st.warning(AliasForm.no_characters)
                return
            if st.session_state.firestore.document_exists(
                collection='aliases',
                doc_id=self.document_id
            ):
                st.warning(AliasForm.character_exists)
            else:
                self.register()
                st.session_state['now_entering'] = 'text'
                st.session_state.pop('current_alias', None)
                st.rerun()

    def delete(self):
        """Delete this alias document from Firestore."""
        st.session_state['firestore'].delete_document(
            collection='aliases', doc_id=self.document_id
        )
