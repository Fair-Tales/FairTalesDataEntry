import streamlit as st
from text_content import IllustratorForm
from .base_structure import DataStructureBase, Field
from datetime import date


class Illustrator(DataStructureBase):

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
        super().__init__(collection='illustrators', db_object=db_object)

    @property
    def name(self):
        return ' '.join([self.forename, self.surname])

    @property
    def document_id(self):
        return self.name.lower().replace(" ", "_")

    def to_form(self):

        st.header(IllustratorForm.header)

        self.forename = st.text_input("First name", value=self.forename)
        self.surname = st.text_input("Surname", value=self.surname)

        year_given = int(st.selectbox(
            "What is the illustrator's birth year?",
            options = (x for x in ([-1, -2]+[y for y in range(1900, (date.today().year - 15))])),
            index=94,
            placeholder="Select year of birth",
            format_func = lambda x: "I don't know" if x == -1 else ("Earlier year" if x == -2 else str(x))
        ))

        if year_given > 0:
            self.birth_year = year_given
        else:
            self.birth_year = None

        st.write(IllustratorForm.gender_prompt)
        gender_index = (
            IllustratorForm.gender_options.index(self.gender)
            if self.gender is not None and self.gender != ""
            else 0
        )
        self.gender = st.selectbox(
            "Gender",
            options=IllustratorForm.gender_options,
            index=gender_index
        )

        submitted = st.form_submit_button("Submit")

        if submitted:
            if st.session_state.firestore.document_exists(
                collection='illustrators',
                doc_id=self.document_id
            ):
                st.warning(IllustratorForm.illustrator_exists)
            else:
                st.session_state['current_illustrator'] = self
                st.session_state['active_form_to_confirm'] = 'new_illustrator'
                st.switch_page("./pages/confirm_entry.py")
