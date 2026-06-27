import streamlit as st
from text_content import UserForm, GenderRegistration
from .base_structure import DataStructureBase, Field
from datetime import date


class User(DataStructureBase):
    """
    Data structure for a registered user.  The Firestore document already
    exists (created during registration), so ``is_registered`` is always
    set to ``True`` after ``__init__`` completes, enabling the write-through
    Field descriptor pattern to persist edits immediately on assignment.
    """

    fields = {
        'is_registered': False,
        'name': "",
        'user_gender': "",
        'user_birth_year': None,
        'newsletter_opt_in': False,
    }

    for field in fields.keys():
        if field not in [DataStructureBase.base_class_fields] + ['is_registered']:
            vars()[field] = Field()

    form_fields = {
        'name': 'Name',
        'user_gender': 'Gender',
        'user_birth_year': 'Birth year',
        'newsletter_opt_in': 'Newsletter opt-in',
    }

    ref_fields = []

    def __init__(self, db_object=None):
        if db_object is not None:
            # User documents created by register_user() do not contain
            # 'is_registered'; inject it so DataStructureBase.__init__ can
            # iterate self.fields without a KeyError.
            db_object = dict(db_object)
            db_object.setdefault('is_registered', True)
        super().__init__(collection='users', db_object=db_object)
        # User document always exists in Firestore before this class is
        # instantiated, so write-through must be active from the start.
        self.is_registered = True

    @property
    def document_id(self):
        return st.session_state['username']

    def to_form(self):

        st.subheader(UserForm.header)
        st.write(UserForm.page_intro)

        _name = st.text_input("Name", value=self.name).strip()

        gender_options = GenderRegistration.options
        gender_index = (
            gender_options.index(self.user_gender)
            if self.user_gender in gender_options
            else 0
        )
        _user_gender = st.selectbox(
            GenderRegistration.question,
            options=gender_options,
            index=gender_index,
            help=GenderRegistration.help,
        )

        year_options = [-1] + [y for y in range(1900, date.today().year + 1)]
        birth_year_index = (
            year_options.index(self.user_birth_year)
            if self.user_birth_year in year_options
            else 0
        )
        _user_birth_year = st.selectbox(
            UserForm.birth_year_question,
            options=year_options,
            index=birth_year_index,
            format_func=lambda x: "Prefer not to say" if x == -1 else str(x),
        )

        _newsletter_opt_in = st.checkbox(
            UserForm.newsletter_label,
            value=bool(self.newsletter_opt_in),
        )

        submitted = st.form_submit_button(UserForm.save_button_text)

        if submitted:
            self.name = _name
            self.user_gender = _user_gender
            self.user_birth_year = (
                _user_birth_year if _user_birth_year != -1 else None
            )
            self.newsletter_opt_in = _newsletter_opt_in
            st.success(UserForm.save_success)
