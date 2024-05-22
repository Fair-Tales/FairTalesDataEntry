import streamlit as st
from text_content import Instructions, AuthorForm
from .base_structure import DataStructureBase, Field


class Author(DataStructureBase):

    fields = {
        'is_registered': False,
        'forename': "",
        'surname': "",
        'birth_year': 1970,
        'gender': "",
        'entered_by': None,
        'datetime_created': -1,
        'last_updated': -1
    }

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {
        'forename': 'First name(s)',
        'surname': 'Surname',
        'birth_year': 'Birth year',
        'gender': 'Gender'
    }

    ref_fields = []

    def __init__(self, db_object=None):
        super().__init__(collection='authors', db_object=db_object)

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
            if st.session_state.firestore.document_exists(
                collection='authors',
                doc_id=self.document_id
            ):
                st.warning(AuthorForm.author_exists)
            else:
                st.session_state['current_author'] = self
                st.session_state['active_form_to_confirm'] = 'new_author'
                st.switch_page("./pages/confirm_entry.py")
