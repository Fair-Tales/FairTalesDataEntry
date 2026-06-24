import base64
import json
import s3fs
import natsort
import streamlit as st
import anthropic
from data_structures import Page
from text_content import Instructions
from utilities import page_layout, check_authentication_status

check_authentication_status()

_EXTRACTION_PROMPT = """\
Analyse this photo of a children's picture book page.

Instructions:
- Correct for any rotation or tilt in the image and focus on the book page itself, ignoring any background (table, hands, etc.).
- Transcribe ALL text visible on the page exactly as written, including speech bubbles and captions. Do not include page numbers.
- Classify whether this is a STORY page — meaning it contains narrative text that is part of the story itself. Pages that are NOT story pages include: title page, half-title, copyright, dedication, contents, about the author, publisher information, back-cover synopsis, end matter, or blank pages.

Respond with valid JSON only, no other text:
{
  "text": "<all text on the page, or empty string if none>",
  "is_story_page": true or false,
  "page_type": "<one of: story, title, copyright, dedication, contents, about_author, publisher_info, synopsis, blank, other>"
}"""


def extract_page_info(fs, photos_url, page_number, client):
    """Return (text, is_story_page) extracted from a page photo via Claude vision."""
    try:
        with fs.open(f"{photos_url}/page_{page_number}.jpg", 'rb') as f:
            image_data = base64.standard_b64encode(f.read()).decode('utf-8')
        response = client.messages.create(
            model="claude-sonnet-4-6",
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
        raw = response.content[0].text.strip()
        # Strip markdown code fences if the model wrapped the JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        return result.get("text", "").strip(), bool(result.get("is_story_page", False))
    except Exception:
        return "", False


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
                    text, is_story = extract_page_info(fs, photos_url, i + 1, ai_client)
                    if text:
                        page.text = text
                    page.contains_story = is_story
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
