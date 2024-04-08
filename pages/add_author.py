import streamlit as st
from utilities import author_entry_to_name, hide

hide()


# TODO: wire this up with session state so that the form is prepopulated if editing an already entered book.

def new_author():

    with st.form("new_author"):
        st.header("Please enter details of the new author.")
        st.write(
            """
            You can do a quick internet search for the author birth year and gender,
            but please select `unknown` for their gender if it is not clear.
            """
        )
        metadata = {
            key: None for key in ['forename', 'surname', 'gender', 'birth_year']
        }
        metadata['forename'] = st.text_input("First name")
        metadata['surname'] = st.text_input("Surname")
        metadata['birth_year'] = st.number_input(
            "Birth year", min_value=1900, max_value=2024, value=2023
        )

        metadata['author'] = st.selectbox(
            "Gender",
            options=[
                'Female',
                'Male',
                'Non-binary/Genderqueer/Gender non-conforming',
                'Unknown'
            ]
        )
        submitted = st.form_submit_button("Submit")
        if submitted:
            st.session_state['author_details'] = metadata
            st.switch_page("./pages/confirm_entry.py")


new_author()

cancel_button = st.button("Cancel this book entry.")

if cancel_button:
    st.switch_page("./pages/user_home.py")
