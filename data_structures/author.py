import streamlit as st
import anthropic
from text_content import AuthorForm
from .base_structure import DataStructureBase, Field
from datetime import date


class Author(DataStructureBase):

    fields = {
        'is_registered': False,
        'forename': "",
        'surname': "",
        'birth_year': None,
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
        from utilities import lookup_person_details

        st.header(AuthorForm.header)

        self.forename = st.text_input("First name", value=self.forename).strip()
        self.surname = st.text_input("Surname", value=self.surname).strip()

        # Apply any suggestion stored by a previous "Look up" click
        _suggestion = st.session_state.pop('_author_lookup_suggestion', None)
        if _suggestion:
            if _suggestion.get('birth_year'):
                self.birth_year = _suggestion['birth_year']
            if _suggestion.get('gender'):
                self.gender = _suggestion['gender']

        year_options = [-1, -2] + [y for y in range(1900, (date.today().year + 1))]
        if self.birth_year and self.birth_year in year_options:
            year_index = year_options.index(self.birth_year)
        else:
            year_index = 0

        year_given = st.selectbox(
            "What is the author's birth year?",
            options=year_options,
            index=year_index,
            placeholder="Select year of birth",
            format_func=lambda x: "I don't know" if x == -1 else ("Earlier year" if x == -2 else str(x))
        )

        if year_given > 0:
            self.birth_year = year_given
        else:
            self.birth_year = None

        st.write(AuthorForm.gender_prompt)
        gender_index = (
            AuthorForm.gender_options.index(self.gender)
            if self.gender is not None and self.gender != "" and self.gender in AuthorForm.gender_options
            else 0
        )
        self.gender = st.selectbox(
            "Gender",
            options=AuthorForm.gender_options,
            index=gender_index
        )

        submitted = st.form_submit_button("Submit")
        ai_available = 'ANTHROPIC_API_KEY' in st.secrets
        lookup_clicked = st.form_submit_button(
            "Look up birth year and gender",
            disabled=not ai_available,
            help=AuthorForm.lookup_help
        )

        if lookup_clicked:
            if self.forename.strip() or self.surname.strip():
                ai_client = anthropic.Anthropic(api_key=st.secrets['ANTHROPIC_API_KEY'])
                suggestion = lookup_person_details(self.name.strip(), 'author', ai_client)
                if suggestion:
                    st.session_state['_author_lookup_suggestion'] = suggestion
            st.rerun()

        if submitted:
            if not self.forename.strip() or not self.surname.strip():
                st.warning("Author first name and surname are required.")
                return
            if st.session_state.firestore.document_exists(
                collection='authors',
                doc_id=self.document_id
            ):
                st.warning(AuthorForm.author_exists)
            else:
                st.session_state['current_author'] = self
                st.session_state['active_form_to_confirm'] = 'new_author'
                st.switch_page("./pages/confirm_entry.py")
