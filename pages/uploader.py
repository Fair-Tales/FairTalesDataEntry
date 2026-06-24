import base64
import s3fs
import natsort
import streamlit as st
import anthropic
from data_structures import Page
from text_content import Instructions
from utilities import page_layout, check_authentication_status

check_authentication_status()

_EXTRACTION_PROMPT = (
    "Transcribe all text visible on this children's book page exactly as written. "
    "Include story text, speech bubbles, and captions. Do not include page numbers. "
    "If the page contains no text, return an empty string. "
    "Return the transcribed text only, with no commentary."
)


def extract_page_text(fs, photos_url, page_number, client):
    try:
        with fs.open(f"{photos_url}/page_{page_number}.jpg", 'rb') as f:
            image_data = base64.standard_b64encode(f.read()).decode('utf-8')
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data
                        }
                    },
                    {"type": "text", "text": _EXTRACTION_PROMPT}
                ]
            }]
        )
        return response.content[0].text.strip()
    except Exception:
        return ""


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

            pages = []
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
                pages.append(page)

            st.session_state.current_book.photos_uploaded = True
            st.session_state.current_book.photos_url = photos_url
            st.session_state.current_book.page_count = len(uploaded_files)

            if 'ANTHROPIC_API_KEY' in st.secrets:
                st.write("Extracting text from page photos using AI, please wait...")
                progress = st.progress(0)
                ai_client = anthropic.Anthropic(api_key=st.secrets['ANTHROPIC_API_KEY'])
                for i, page in enumerate(pages):
                    extracted = extract_page_text(fs, photos_url, i + 1, ai_client)
                    if extracted:
                        page.text = extracted
                        page.contains_story = True
                    progress.progress((i + 1) / len(pages))

            st.write("Page photo upload complete, you may continue.")
            submit = st.button('Continue')

            if submit:
                st.session_state.pop('book_pages_dict', None)
                if on_submit == 'enter_text':
                    st.switch_page("./pages/enter_text.py")
                else:
                    st.success(Instructions.upload_success_return)

    upload_page_photos()

page_layout()
