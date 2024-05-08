import streamlit as st
from utilities import author_entry_to_name, hide

hide()


# TODO: add option to edit existing character (change names, aliases etc)

def new_character():

    # first find existing authors:
    author_dict = {
        author_entry_to_name(author): author.id
        for author in
        st.session_state.firestore.get_all_documents_stream(collection='authors')
    }

    with st.form("new_book"):
        st.header("Please enter the details of the new character.")

        metadata = {
            key: None for key in ['name', 'alias', 'gender', 'is_plural', 'is_human']
        }
        metadata['name'] = st.text_input("Full name (as most commonly used)")
        metadata['alias'] = st.text_input("Enter their alias")
        metadata['gender'] = st.selectbox(
            "Gender",
            options=[
                'Female',
                'Male',
                'Non-binary/Genderqueer/Gender non-conforming',
                'Not specified'
            ]
        )
        metadata['is_plural'] = st.checkbox("Is this a group or collection of characters? (e.g. 'the cavemen')")
        metadata['is_human'] = st.checkbox("Is this character human?", value=True)

        submitted = st.form_submit_button("Submit")
        if submitted:
            st.session_state['character_details'] = metadata
            st.session_state['active_form_to_confirm'] = 'new_character'
            st.switch_page("./pages/confirm_entry.py")


new_character()

cancel_button = st.button("Cancel adding new character.")

if cancel_button:
    st.switch_page("./pages/user_home.py")
