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
        return f"{self.book.get().id}_{self.name.replace(' ', '_').lower()}"

    def to_form(self):

        st.header(AliasForm.header)

        character_options = list(
            st.session_state['character_dict'].keys()
        )
        character_index = (
            character_options.index(
                self.character.get().to_dict()['name']
            )
            if self.character is not None and self.character.get().to_dict()['name'] in character_options
            else 0
        )

        self.character = st.selectbox(
            "Select character",
            options=character_options,
            index=character_index
        )

        self.name = st.text_input("Alias", value=self.name)

        submitted = st.form_submit_button("Save alias")

        if submitted:
            if st.session_state.firestore.document_exists(
                collection='aliases',
                doc_id=self.document_id
            ):
                st.warning(AliasForm.character_exists)
            else:
                self.register()
                st.session_state['now_entering'] = 'text'
                st.rerun()
