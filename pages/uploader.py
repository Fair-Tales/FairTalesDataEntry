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
from text_content import Instructions, AIPrompts, BookPhotoEntry, Uploader
from utilities import (
    page_layout, check_authentication_status, extract_isbn, lookup_isbn
)


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


def _process_photo_batch(raw_bytes_list, sort_file_names, fs):
    """Run the upload + correction + extraction pipeline for one batch of page
    photos (already read into memory, in page order).

    Writes raw and corrected images to S3, registers Page docs, extracts text, and
    performs the copyright-page ISBN lookup. Shared by both the manual file-upload
    path and the photo-first reuse path (#59). The caller guards against re-running
    via '_upload_pipeline_done', which this function sets on completion.
    """
    total = len(sort_file_names)
    photos_url = f"sawimages/{st.session_state['current_book'].title}"

    # Phase 1 — upload raw photos to S3
    upload_status = st.empty()
    upload_progress = st.progress(0)
    corrected_bytes_list = []
    for fi, raw_bytes in enumerate(raw_bytes_list):
        upload_status.write(Uploader.saving_photo.format(current=fi + 1, total=total))
        # Normalise orientation so the stored photo and every downstream stage
        # (correction, extraction, display) work on correctly-oriented pixels
        # (fixes portrait photos, #51). Idempotent — a no-op once the EXIF tag is
        # baked in — so it's safe for both the manual-upload and photo-first
        # reuse paths that share this function.
        raw_bytes = exif_transpose_bytes(raw_bytes)
        corrected_bytes_list.append(raw_bytes)
        with fs.open(f"{photos_url}/page_{fi + 1}.jpg", 'wb') as f:
            f.write(raw_bytes)
        upload_progress.progress((fi + 1) / total)
    # Downstream correction/extraction should use the orientation-corrected bytes.
    raw_bytes_list = corrected_bytes_list

    st.session_state.current_book.photos_uploaded = True
    st.session_state.current_book.photos_url = photos_url
    st.session_state.current_book.page_count = total
    upload_status.write(Uploader.photos_saved)

    # Phase 2 — image correction + text extraction per page
    if 'ANTHROPIC_API_KEY' in st.secrets:
        ai_client = anthropic.Anthropic(api_key=st.secrets['ANTHROPIC_API_KEY'])
        process_status = st.empty()
        process_progress = st.progress(0)
        copyright_text = None

        for i, raw_bytes in enumerate(raw_bytes_list):
            page_number = i + 1
            process_status.write(
                Uploader.processing_page.format(page=page_number, total=total)
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
                    Uploader.page_corrected.format(page=page_number, total=total, method=method)
                )
            else:
                process_status.write(
                    Uploader.page_correction_unavailable.format(page=page_number, total=total)
                )
            process_progress.progress((i + 1) / total)

        process_status.write(Uploader.processing_complete)

        # ISBN lookup — use the copyright page text to fetch book metadata
        # and pre-populate the Add Book form.
        if copyright_text:
            isbn = extract_isbn(copyright_text)
            if isbn:
                isbn_metadata = lookup_isbn(isbn)
                if isbn_metadata:
                    st.session_state['isbn_metadata'] = isbn_metadata
                    st.info(
                        Uploader.isbn_metadata_found.format(isbn=isbn, title=isbn_metadata['title'])
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


def upload_widget(on_submit='enter_text'):

    fs = s3fs.S3FileSystem(
        anon=False,
        key=st.secrets['AWS_ACCESS_KEY_ID'],
        secret=st.secrets['AWS_SECRET_ACCESS_KEY']
    )

    # TODO: set file order (sort ascending? time modified?)
    def upload_page_photos():
        # Photos captured in the photo-first flow (#59) are reused here so the
        # user does not have to upload them a second time.
        stashed = st.session_state.get('photo_first_pages')

        if stashed:
            # Only run the pipeline once (Streamlit re-runs on every interaction).
            if not st.session_state.get('_upload_pipeline_done', False):
                st.write(BookPhotoEntry.reuse_notice.format(count=len(stashed)))
                sort_file_names = [name for name, _ in stashed]
                raw_bytes_list = [data for _, data in stashed]
                _process_photo_batch(raw_bytes_list, sort_file_names, fs)
        else:
            uploaded_files = st.file_uploader(
                Uploader.select_photos_label,
                accept_multiple_files=True,
                key="uploader_file_uploader",
            )
            if not uploaded_files:
                return

            # Only run the pipeline once. Streamlit re-runs the script when the
            # user clicks Continue — the file uploader still holds the selected
            # files on that re-run, so without this guard the whole pipeline
            # would fire again before st.switch_page redirects.
            if not st.session_state.get('_upload_pipeline_done', False):
                file_dict = {file.name: file for file in uploaded_files}
                sort_file_names = natsort.natsorted(list(file_dict.keys()), reverse=False)
                raw_bytes_list = [file_dict[name].getvalue() for name in sort_file_names]
                _process_photo_batch(raw_bytes_list, sort_file_names, fs)

        st.write(Uploader.upload_complete)
        submit = st.button(Uploader.continue_button, key="uploader_continue_button")

        if submit:
            st.session_state.pop('_upload_pipeline_done', None)
            st.session_state.pop('book_pages_dict', None)
            st.session_state.pop('photo_first_pages', None)
            if on_submit == 'enter_text':
                st.switch_page("./pages/enter_text.py")
            else:
                st.success(Instructions.upload_success_return)

    upload_page_photos()


# Page-level code runs only when uploader.py is the active page (Streamlit sets
# __name__ == "__main__" for the navigated page). Guarding this prevents the page
# from rendering when the module is merely imported for `upload_widget`
# (e.g. by page_photo_upload.py), which previously rendered the sidebar/back
# button twice and raised StreamlitDuplicateElementId.
if __name__ == "__main__":
    check_authentication_status()
    page_layout()
