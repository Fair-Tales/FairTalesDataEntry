import s3fs
import streamlit as st
st.set_page_config(
    page_title="bde",
    initial_sidebar_state="collapsed"
)
from utilities import hide
from text_content import Instructions
from data_structures import Page

hide()
st.title(
    f"Enter book data: {st.session_state.current_book.title}"
)
st.header(Instructions.photo_upload_header)
st.write(Instructions.photo_upload_instructions)

fs = s3fs.S3FileSystem(
    anon=False,
    key=st.secrets['AWS_ACCESS_KEY_ID'],
    secret=st.secrets['AWS_SECRET_ACCESS_KEY']
)


# TODO: change per file size limit?!
# TODO: set file order (sort ascending? time modified? https://stackoverflow.com/questions/31588543/how-to-change-order-of-files-in-multiple-file-input)
def upload_page_photos():
    uploaded_files = st.file_uploader("Select page photos to upload", accept_multiple_files=True)
    for fi, uploaded_file in enumerate(uploaded_files):
        page_number = fi + 1
        st.write("Saving page photos to the database, please stay on this page...")
        with fs.open(
                f"sawimages/{st.session_state['current_book'].title}/page_{page_number}.jpg",
                'wb'
        ) as out_file:
            out_file.write(uploaded_file.read())

        page = Page(
            page_number=page_number,
            book=st.session_state['current_book'].title
        )
        page.register()
        page.save_to_db()

    if uploaded_files:
        st.session_state.firestore.update_field(
            collection='books',
            document=st.session_state.current_book.document_id,
            field='photos_uploaded',
            value=True
        )
        st.session_state.firestore.update_field(
            collection='books',
            document=st.session_state.current_book.document_id,
            field='photos_url',
            value=f"sawimages/{st.session_state['current_book'].title}"
        )

        st.write("Page photo upload complete, you may continue.")


upload_page_photos()

submit = st.button('Continue')

if submit:
    st.switch_page("./pages/enter_text.py")


