import streamlit as st
from six import BytesIO
from streamlit_option_menu import option_menu
from text_content import Alerts, PhotoUpload
from utilities import page_layout, check_authentication_status, get_s3_filesystem
from text_content import Instructions
from pages.uploader import upload_widget, append_photos_widget
from page_reorder import (
    ReorderError,
    execute_reorder,
    move_page_permutation,
    read_pending_manifest,
    resume_pending_reorder,
)
import qrcode
from requests.models import PreparedRequest

check_authentication_status()

def go_to_phone():
    st.write(Instructions.go_to_phone_instructions)
    # We build a URL to send the user to the photo upload page on their phone via QR code...
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    url = f"{st.secrets.app_url}qr_landing"
    user = st.session_state.username
    token = st.session_state.firestore.username_to_doc_ref(user).get().to_dict()['confirmation_token']
    params = {
        'user': st.session_state.username,
        'token': token,
        'book': st.session_state.current_book.document_id
    }
    req = PreparedRequest()
    req.prepare_url(url, params)
    qr.add_data(req.url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    temp = BytesIO()
    img.save(temp)
    st.image(temp.getvalue(), width="stretch")
    st.write(PhotoUpload.link_line % (req.url, req.url))

    st.write(PhotoUpload.finished_instruction)

    subcol1, subcol2 = st.columns(2)
    if subcol1.button(PhotoUpload.continue_button, width="stretch", key="photo_upload_continue_button"):
        if st.session_state.current_book.photos_uploaded:
            st.session_state['current_page_number'] = 1
            st.switch_page("./pages/enter_text.py")
        else:
            st.warning(Alerts.please_uploaded_photos)
    if subcol2.button(PhotoUpload.back_to_menu_button, width="stretch", key="photo_upload_back_menu_button"):
        st.switch_page("./pages/book_edit_home.py")


def upload_here():
    st.write(Instructions.upload_here_instructions)
    upload_widget()


def already_uploaded_options():
    """Tell the user their photos are in and processed, and let them continue to
    text entry or replace the photos (#180).

    This renders straight after the user's own upload finishes as well as on any
    return visit, so the message states what happened (photos processed, text
    read) and what's next — it must never read as a duplicate-upload warning.
    """
    page_count = getattr(st.session_state.current_book, 'page_count', None)
    count_str = f" ({page_count} pages)" if page_count else ""
    st.info(Instructions.photos_already_uploaded.format(count_str=count_str))
    skip_col, append_col, reorder_col, replace_col = st.columns(4)
    if skip_col.button(PhotoUpload.continue_to_text_button, width="stretch", key="photo_upload_skip_continue_button"):
        st.session_state.pop('_replacing_photos', None)
        st.session_state['current_page_number'] = 1
        st.switch_page("./pages/enter_text.py")
    if append_col.button(PhotoUpload.add_more_photos_button, width="stretch", key="photo_upload_append_button"):
        # Append forgotten pages after the current last page (#203). Scoped to
        # this book, mirroring _replacing_photos.
        st.session_state['_appending_photos'] = st.session_state.current_book.document_id
        st.rerun()
    if reorder_col.button(PhotoUpload.reorder_button, width="stretch", key="photo_upload_reorder_button"):
        # Reorder pages (#148): fix a wrong upload/sort order without
        # re-uploading. Scoped to this book, mirroring _replacing_photos.
        st.session_state['_reordering_photos'] = st.session_state.current_book.document_id
        st.rerun()
    if replace_col.button(PhotoUpload.replace_button, width="stretch", key="photo_upload_replace_button"):
        # Scope the override to this book so re-opening a *different* book still
        # offers the skip choice rather than dropping straight into uploading.
        st.session_state['_replacing_photos'] = st.session_state.current_book.document_id
        st.rerun()
    if st.button(PhotoUpload.back_to_menu_button, width="stretch", key="photo_upload_already_back_menu_button"):
        st.switch_page("./pages/book_edit_home.py")


def reorder_pages_view():
    """Move a page to a new position (#148) — the pages in between shift by one.

    Runs the transactional migration in ``page_reorder`` (page files + Page
    docs whose ids embed the page number), then invalidates enter-text's image
    cache and page dict so the new order shows immediately. Because the page
    DOC CONTENT moves too, any already-entered text follows its photo, so this
    is safe both before and after text entry (#204/#205).
    """
    fs = get_s3_filesystem()
    book = st.session_state.current_book
    folder = f"sawimages/{book.title}"
    n_pages = book.page_count if isinstance(book.page_count, int) else 0
    db = st.session_state.firestore.connect_book()

    st.subheader(PhotoUpload.reorder_header)

    # An interrupted earlier reorder (browser closed mid-migration) must be
    # resolved before a new one: resume_pending_reorder completes it when its
    # Firestore commit happened, or discards the untouched staging otherwise.
    if read_pending_manifest(fs, folder) is not None:
        st.warning(PhotoUpload.reorder_pending_warning)
        if st.button(PhotoUpload.reorder_resolve_button, key="photo_upload_reorder_resolve_button"):
            outcome = resume_pending_reorder(fs, db, book.document_id, folder)
            st.session_state['_invalidate_image_cache'] = True
            st.session_state.pop('book_pages_dict', None)
            if outcome == 'finished':
                st.success(PhotoUpload.reorder_resolved_finished)
            else:
                st.info(PhotoUpload.reorder_resolved_discarded)
        if st.button(PhotoUpload.back_to_menu_button, width="stretch",
                     key="photo_upload_reorder_pending_back_button"):
            st.session_state.pop('_reordering_photos', None)
            st.rerun()
        return

    if n_pages < 2:
        st.info(PhotoUpload.reorder_too_few_pages)
    else:
        st.write(PhotoUpload.reorder_instructions.format(count=n_pages))
        col_from, col_to = st.columns(2)
        from_page = int(col_from.number_input(
            PhotoUpload.reorder_from_label, min_value=1, max_value=n_pages,
            value=n_pages, step=1, key="photo_upload_reorder_from_page",
        ))
        to_page = int(col_to.number_input(
            PhotoUpload.reorder_to_label, min_value=1, max_value=n_pages,
            value=1, step=1, key="photo_upload_reorder_to_page",
        ))

        # Preview of the page being moved, so the user can confirm it is the
        # right one before committing. Display derivative first; raw fallback.
        preview_bytes = None
        for suffix in ("_display", ""):
            try:
                with fs.open(f"{folder}/page_{from_page}{suffix}.jpg", 'rb') as f:
                    preview_bytes = f.read()
                break
            except FileNotFoundError:
                continue
        if preview_bytes:
            st.image(preview_bytes, width=260,
                     caption=PhotoUpload.reorder_preview_caption.format(page=from_page))
        else:
            st.caption(PhotoUpload.reorder_no_preview.format(page=from_page))

        if st.button(PhotoUpload.reorder_apply_button, key="photo_upload_reorder_apply_button"):
            try:
                permutation = move_page_permutation(n_pages, from_page, to_page)
                if not permutation:
                    st.info(PhotoUpload.reorder_noop)
                else:
                    with st.spinner(PhotoUpload.reorder_working):
                        execute_reorder(
                            fs, db, book.document_id, folder, permutation,
                            n_pages, edited_by=st.session_state.get('username'),
                        )
                    # New order must show immediately everywhere.
                    st.session_state['_invalidate_image_cache'] = True
                    st.session_state.pop('book_pages_dict', None)
                    st.success(PhotoUpload.reorder_success.format(
                        page=from_page, position=to_page,
                    ))
            except ReorderError as exc:
                st.error(str(exc))

    if st.button(PhotoUpload.reorder_done_button, width="stretch",
                 key="photo_upload_reorder_done_button"):
        st.session_state.pop('_reordering_photos', None)
        st.rerun()


def show_upload_options():
    st.header(Instructions.photo_upload_header)
    st.write(Instructions.photo_upload_instructions)
    st.write(Instructions.photo_naming_instructions)

    # When photos were already captured via the photo-first flow (#59), default
    # to "Upload here" — that path reuses the stashed photos rather than
    # re-uploading them.
    selected_option = option_menu(
        None, ["Go to phone", "Upload here"],
        default_index=1 if st.session_state.get('photo_first_pages') else 0,
        icons=['phone', 'laptop'],
        menu_icon="cast", orientation="horizontal",
        key="upload_menu",
        styles={
            "nav-link": {"font-size": "15px", "text-align": "left", "margin": "0px", "--hover-color": "#eee"},
            "nav-link-selected": {"background-color": "green"},
        }
    )

    navigation_dict = {
        "Go to phone": go_to_phone,
        "Upload here": upload_here
    }

    navigation_dict[selected_option]()


page_layout()

st.title(
    PhotoUpload.enter_book_data_title.format(title=st.session_state.current_book.title)
)

# If photos already exist for this book, offer to skip to text entry or to
# replace them, rather than forcing the user back through the upload step.
_book_id = st.session_state.current_book.document_id
if (
    st.session_state.get('photo_first_pages')
    and not st.session_state.current_book.photos_uploaded
):
    # Photo-first flow (#59/#151): the page photos were already captured on the
    # "Add book by photos" page and are held in memory under `photo_first_pages`.
    # Do NOT show the upload chooser again — process the stashed photos straight
    # through and forward to text entry. The manual flow (no stashed photos)
    # still lands on the upload options below.
    upload_widget(auto_forward=True)
elif (
    st.session_state.current_book.photos_uploaded
    and st.session_state.get('_appending_photos') == _book_id
):
    # Append-more-photos flow (#203): upload extra photos that become new pages
    # AFTER the current last page, leaving every existing page untouched.
    append_photos_widget()
elif (
    st.session_state.current_book.photos_uploaded
    and st.session_state.get('_reordering_photos') == _book_id
):
    # Reorder-pages flow (#148): move a page to a new position without
    # re-uploading anything.
    reorder_pages_view()
elif (
    st.session_state.current_book.photos_uploaded
    and st.session_state.get('_replacing_photos') != _book_id
):
    already_uploaded_options()
else:
    show_upload_options()












