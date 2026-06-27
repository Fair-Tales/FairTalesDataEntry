import streamlit as st
from text_content import CharacterForm
from utilities import load_character_dict
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
                st.session_state['character_dict'][self.name] = self.get_ref()
                # Invalidate shared cache so new/other sessions re-read this
                # character (this session sees it via the in-place update above).
                load_character_dict.clear()
                st.session_state['now_entering'] = 'text'
                st.rerun()
