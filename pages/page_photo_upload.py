import s3fs
import streamlit as st

st.set_page_config(
    page_title="bde",
    initial_sidebar_state="collapsed"
)
from utilities import hide
from text_content import Instructions

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

        st.write("filename:", uploaded_file.name)
        with fs.open(
                f"sawimages/{st.session_state['current_book'].title}/page_{fi+1}.jpg",
                'wb'
        ) as out_file:
            out_file.write(uploaded_file.read())


upload_page_photos()

submit = st.button('Continue')

if submit:
    st.switch_page("./pages/enter_text.py")


