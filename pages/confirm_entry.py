import streamlit as st


# TODO: refactor this to be used repeatedly for different type of entry (e.g. character)
#    to use a method metadata_to_form to store session state entered data and re-display it in a form for revision?

st.write("Please double check the data you have entered. If you are happy click...")
# TODO: convert to dataframe for nicer display (with editing?)
st.write(st.session_state.book_metadata)

col1, col2 = st.columns(2)
confirm_button = col1.button("Confirm")
edit_button = col2.button("Edit")

if confirm_button:
    # TODO: store book metadata in Firestore
    st.warning("Not implemented!")

if edit_button:
    # TODO: re-display metadata in form for editing
    st.warning("Not implemented!")