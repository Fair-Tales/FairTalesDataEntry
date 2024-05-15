import streamlit as st
from text_content import Instructions, AuthorForm
from .base_structure import DataStructureBase


class Author(DataStructureBase):

    fields = {
        'forename': "",
        'surname': "",
        'birth_year': 1970,
        'gender': "",
        'book_count': -1,
        'entered_by': None,
        'datetime_created': -1
    }

    form_fields = {
        'forename': 'First name(s)',
        'surname': 'Surname',
        'birth_year': 'Birth year',
        'gender': 'Gender'
    }

    ref_fields = []

    def __init__(self, db_object=None):
        super().__init__(db_object=db_object)
        self.belongs_to_collection = 'authors'

    @property
    def name(self):
        return ' '.join([self.forename, self.surname])

    @property
    def document_id(self):
        return self.name.lower().replace(" ", "_")

    def to_form(self):

        st.header(AuthorForm.header)

        self.forename = st.text_input("First name", value=self.forename)
        self.surname = st.text_input("Surname", value=self.surname)

        self.birth_year = st.number_input(
            "Birth year", min_value=1900, max_value=2024, value=self.birth_year
        )

        st.write(AuthorForm.gender_prompt)
        gender_index = (
            AuthorForm.gender_options.index(self.gender)
            if self.gender is not None and self.gender != ""
            else 0
        )
        self.gender = st.selectbox(
            "Gender",
            options=AuthorForm.gender_options,
            index=gender_index
        )

        submitted = st.form_submit_button("Submit")

        if submitted:
            st.session_state['current_author'] = self
            st.session_state['active_form_to_confirm'] = 'new_author'
            st.switch_page("./pages/confirm_entry.py")
