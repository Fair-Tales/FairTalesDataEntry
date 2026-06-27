import streamlit as st
from text_content import CharacterForm
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

    @property
    def document_id(self):
        return f"{self.book.get().id}_{self.name.replace(' ', '_').lower()}"

    def to_form(self):

        st.header(CharacterForm.header)
        self.name = st.text_input("Name", value=self.name)

        gender_index = 0
        if self.gender in CharacterForm.gender_options:
            gender_index = CharacterForm.gender_options.index(self.gender)
        self.gender = st.selectbox(
            "Gender",
            options=CharacterForm.gender_options,
            index=gender_index,
            help=CharacterForm.gender_help
        )

        ethnicity_index = (
            CharacterForm.ethnicity_options.index(self.ethnicity)
            if self.ethnicity is not None and self.ethnicity != ""
            else 0
        )
        self.ethnicity = st.selectbox(
            "Ethnicity",
            options=CharacterForm.ethnicity_options,
            index=ethnicity_index,
            help=CharacterForm.ethnicity_help
        )

        disability_index = (
            CharacterForm.disability_options.index(self.disability)
            if self.disability is not None and self.disability != ""
            else 0
        )
        self.disability = st.selectbox(
            "Disability",
            options=CharacterForm.disability_options,
            index=disability_index,
            help=CharacterForm.disability_help
        )

        self.protagonist = st.checkbox(
            "Is protagonist?",
            value=self.protagonist,
            help=CharacterForm.protagonist_help
        )
        self.human = st.checkbox(
            "Is human?",
            value=self.human,
            help=CharacterForm.human_help
        )
        self.plural = st.checkbox(
            "Is plural?",
            value=self.plural,
            help=CharacterForm.plural_help
        )

        submitted = st.form_submit_button("Save character")

        if submitted:
            if st.session_state.firestore.document_exists(
                collection='characters',
                doc_id=self.document_id
            ):
                st.warning(CharacterForm.character_exists)
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
                st.session_state['now_entering'] = 'text'
                st.rerun()

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
