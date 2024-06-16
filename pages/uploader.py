import s3fs
import natsort
import streamlit as st
from data_structures import Page
from text_content import Instructions


def upload_widget(on_submit='enter_text'):

    fs = s3fs.S3FileSystem(
        anon=False,
        key=st.secrets['AWS_ACCESS_KEY_ID'],
        secret=st.secrets['AWS_SECRET_ACCESS_KEY']
    )

    # TODO: set file order (sort ascending? time modified? https://stackoverflow.com/questions/31588543/how-to-change-order-of-files-in-multiple-file-input)
    def upload_page_photos():
        uploaded_files = st.file_uploader(
            "Select page photos to upload",
            accept_multiple_files=True,

        )

        if uploaded_files:
            file_dict = {
                file.name: file
                for file in uploaded_files
            }
            sort_file_names = natsort.natsorted(list(file_dict.keys()), reverse=False)

            st.write("Saving page photos to the database, please stay on this page...")
            photos_url = f"sawimages/{st.session_state['current_book'].title}"

            for fi, name in enumerate(sort_file_names):
                uploaded_file = file_dict[name]
                page_number = fi + 1
                with fs.open(
                        photos_url + f"/page_{page_number}.jpg",
                        'wb'
                ) as out_file:
                    out_file.write(uploaded_file.read())

                page = Page(
                    page_number=page_number,
                    book=st.session_state['current_book'].title
                )
                page.register()

            st.session_state.current_book.photos_uploaded = True
            st.session_state.current_book.photos_url = photos_url
            st.session_state.current_book.page_count = len(uploaded_files)

            st.write("Page photo upload complete, you may continue.")
            submit = st.button('Continue')

            if submit:
                if on_submit == 'enter_text':
                    st.switch_page("./pages/enter_text.py")
                else:
                    st.success(Instructions.upload_success_return)

    upload_page_photos()



