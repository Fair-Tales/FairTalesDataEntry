import base64
import json
import s3fs
import natsort
import streamlit as st
import anthropic
from data_structures import Page
from image_processing import (
    correct_book_page, check_crop_quality, get_rotation_angle, rotate_image,
    exif_transpose_bytes,
)
from text_content import Instructions, AIPrompts
from utilities import (
    page_layout, check_authentication_status, extract_isbn, lookup_isbn
)

check_authentication_status()


def extract_page_info(image_bytes, client):
    """Return (text, is_story_page, page_type) by sending image bytes to Claude Sonnet."""
    try:
        image_data = base64.standard_b64encode(image_bytes).decode('utf-8')
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
                    {"type": "text", "text": AIPrompts.page_extraction}
                ]
            }]
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        return (
            result.get("text", "").strip(),
            bool(result.get("is_story_page", False)),
            result.get("page_type", ""),
        )
    except Exception:
        return "", False, ""


def _process_page(raw_bytes, page_number, photos_url, fs, ai_client):
    """
    Run the staged correction pipeline for one page.

    Stage 1: OpenCV perspective correction + Haiku quality check.
    Stage 2: Rotation-only Sonnet fallback + Haiku quality check.

    Returns (image_bytes_for_extraction, method) where method is
    'opencv', 'rotation', or None (no correction applied).
    Saves page_{n}_cropped.jpg to S3 when a stage succeeds.
    """
    def _save_and_return(corrected, method):
        with fs.open(f"{photos_url}/page_{page_number}_cropped.jpg", 'wb') as f:
            f.write(corrected)
        return corrected, method

    # Stage 1 — OpenCV perspective correction
    corrected_bytes, opencv_ok = correct_book_page(raw_bytes)
    if opencv_ok and check_crop_quality(corrected_bytes, ai_client):
        return _save_and_return(corrected_bytes, 'opencv')

    # Stage 2 — rotation-only fallback via Sonnet
    angle = get_rotation_angle(raw_bytes, ai_client)
    if angle != 0:
        rotated_bytes = rotate_image(raw_bytes, angle)
        if check_crop_quality(rotated_bytes, ai_client):
            return _save_and_return(rotated_bytes, 'rotation')

    return raw_bytes, None


def upload_widget(on_submit='enter_text'):

    fs = s3fs.S3FileSystem(
        anon=False,
        key=st.secrets['AWS_ACCESS_KEY_ID'],
        secret=st.secrets['AWS_SECRET_ACCESS_KEY']
    )

    # TODO: set file order (sort ascending? time modified?)
    def upload_page_photos():
        uploaded_files = st.file_uploader(
            "Select page photos to upload",
            accept_multiple_files=True,
        )

        if uploaded_files:
            file_dict = {file.name: file for file in uploaded_files}
            sort_file_names = natsort.natsorted(list(file_dict.keys()), reverse=False)
            total = len(sort_file_names)
            photos_url = f"sawimages/{st.session_state['current_book'].title}"

            # Only run the pipeline once. Streamlit re-runs the script when the
            # user clicks Continue — the file uploader still holds the selected
            # files on that re-run, so without this guard the whole pipeline
            # would fire again before st.switch_page redirects.
            if not st.session_state.get('_upload_pipeline_done', False):

                # Phase 1 — upload raw photos to S3, keep bytes in memory
                upload_status = st.empty()
                upload_progress = st.progress(0)
                raw_bytes_list = []
                for fi, name in enumerate(sort_file_names):
                    upload_status.write(f"Saving photo {fi + 1} of {total}...")
                    # Normalise orientation up front so the stored photo and
                    # every downstream stage (correction, extraction, display)
                    # work on correctly-oriented pixels (fixes portrait photos).
                    raw_bytes = exif_transpose_bytes(file_dict[name].read())
                    with fs.open(f"{photos_url}/page_{fi + 1}.jpg", 'wb') as f:
                        f.write(raw_bytes)
                    raw_bytes_list.append(raw_bytes)
                    upload_progress.progress((fi + 1) / total)

                st.session_state.current_book.photos_uploaded = True
                st.session_state.current_book.photos_url = photos_url
                st.session_state.current_book.page_count = total
                upload_status.write("Photos saved.")

                # Phase 2 — image correction + text extraction per page
                if 'ANTHROPIC_API_KEY' in st.secrets:
                    ai_client = anthropic.Anthropic(api_key=st.secrets['ANTHROPIC_API_KEY'])
                    process_status = st.empty()
                    process_progress = st.progress(0)
                    copyright_text = None

                    for i, raw_bytes in enumerate(raw_bytes_list):
                        page_number = i + 1
                        process_status.write(
                            f"Processing page {page_number} of {total} "
                            f"(correcting image, extracting text)..."
                        )

                        bytes_for_extraction, method = _process_page(
                            raw_bytes, page_number, photos_url, fs, ai_client
                        )

                        page = Page(
                            page_number=page_number,
                            book=st.session_state['current_book'].title
                        )
                        page.register()

                        text, is_story, page_type = extract_page_info(
                            bytes_for_extraction, ai_client
                        )
                        if text:
                            page.text = text
                        page.contains_story = is_story

                        if page_type == 'copyright' and text and copyright_text is None:
                            copyright_text = text

                        if method:
                            process_status.write(
                                f"✓ Page {page_number} of {total} — "
                                f"auto-corrected ({method})"
                            )
                        else:
                            process_status.write(
                                f"⚠ Page {page_number} of {total} — "
                                "correction unavailable, using original"
                            )
                        process_progress.progress((i + 1) / total)

                    process_status.write("Processing complete.")

                    # ISBN lookup — use the copyright page text to fetch
                    # book metadata and pre-populate the Add Book form.
                    if copyright_text:
                        isbn = extract_isbn(copyright_text)
                        if isbn:
                            isbn_metadata = lookup_isbn(isbn)
                            if isbn_metadata:
                                st.session_state['isbn_metadata'] = isbn_metadata
                                st.info(
                                    f"Found book metadata via ISBN {isbn}: "
                                    f"{isbn_metadata['title']}"
                                )
                else:
                    # No API key — register pages without extraction
                    for i in range(total):
                        page = Page(
                            page_number=i + 1,
                            book=st.session_state['current_book'].title
                        )
                        page.register()

                st.session_state['_upload_pipeline_done'] = True

            st.write("Page photo upload complete, you may continue.")
            submit = st.button('Continue')

            if submit:
                st.session_state.pop('_upload_pipeline_done', None)
                st.session_state.pop('book_pages_dict', None)
                if on_submit == 'enter_text':
                    st.switch_page("./pages/enter_text.py")
                else:
                    st.success(Instructions.upload_success_return)

    upload_page_photos()

page_layout()
