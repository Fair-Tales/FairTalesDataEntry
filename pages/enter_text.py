import io
import json
import logging
import streamlit as st
import anthropic
from PIL import Image, ImageOps
from streamlit_dimensions import st_dimensions
from utilities import (
    page_layout, confirm_submit, check_authentication_status,
    detect_book_characters, clear_entity_form_state,
    get_s3_filesystem, get_anthropic_client,
    consume_pending_character_autodetect, stage_character_redetect,
    stage_reextract_refresh, consume_reextract_refresh,
    usable_precomputed_suggestions, strip_leading_article,
    CHARACTER_AUTODETECT_SOURCE_AUTO, CHARACTER_AUTODETECT_SOURCE_MANUAL,
)
from data_structures import Page, Character, Alias, ExtractionErrorLog
from image_processing import make_display_copy, apply_manual_correction
from pages.uploader import extract_page_info, PageExtractionError
from text_content import EnterText, ManageCharacters, AliasForm, CharacterForm

logger = logging.getLogger(__name__)

check_authentication_status()

def create_page_dict_from_db():
    """Load every page doc for the current book in ONE batched read (#78).

    Previously this issued a serial ``get_by_reference`` per page — N full
    network round trips at book open (the single slowest interaction in the
    app: 12+ s for a 30-page book). One ``get_all`` fetches the same snapshots
    in a single round trip. Missing-doc semantics are unchanged: a page id
    with no document yields ``to_dict() is None``, exactly as before.
    """
    book_id = st.session_state.current_book.document_id
    page_count = st.session_state.current_book.page_count
    doc_ids = [f"{book_id}_{page_num}" for page_num in range(1, page_count + 1)]
    snaps = st.session_state.firestore.get_all_by_ids('pages', doc_ids)
    pages_dict = {}
    for page_num in range(1, page_count + 1):
        snap = snaps.get(f"{book_id}_{page_num}")
        pages_dict[page_num] = Page(snap.to_dict() if snap is not None else None)
    st.session_state['book_pages_dict'] = pages_dict

def _save_current_page_text():
    """Persist the current page's text-area contents through to the page (#152).

    Auto-save replaces the old explicit "Save page" button: every control that
    leaves the text box (Next/Previous, the character/alias/manage/detect
    buttons, "Back to menu", "Submit") calls this BEFORE navigating so nothing
    the user typed is lost. Reads the text_area's live session_state value —
    keyed per page (``enter_text_page_text_<n>``) so it captures edits made right
    before the click — and writes it through the ``text`` Field, which persists
    it to Firestore. Dirty-checked so an unchanged page is not re-written every
    time the user moves around.
    """
    page_number = st.session_state.get('current_page_number')
    key = f"enter_text_page_text_{page_number}"
    if key in st.session_state:
        new_text = st.session_state[key]
        if new_text != st.session_state.current_page.text:
            st.session_state.current_page.text = new_text


def page_change(delta):
    # Auto-save the page we are leaving before changing the page number (#152).
    _save_current_page_text()
    st.session_state.current_page_number += delta

    if st.session_state.current_page_number < 1:
        st.session_state['current_page_number'] = 1
    elif st.session_state.current_page_number > st.session_state.current_book.page_count:
        st.session_state['current_page_number'] = st.session_state.current_book.page_count


# Display copies are ~150-300KB each (#184), so a much larger cache still costs
# little memory but makes back/forward paging and the N+1 prefetch instant.
@st.cache_data(max_entries=24)
def load_image(book, page_number, use_cropped=True, display=False):
    """Load a page image from S3.

    ImageOps.exif_transpose bakes any EXIF orientation tag into the pixels so
    portrait photos display the right way up. It is a no-op for images without
    an orientation tag (e.g. the re-encoded _cropped/_display versions), so it is
    safe to apply to every branch and to legacy photos stored before this fix.

    ``display=True`` (#184) loads the small ``page_{n}_display.jpg`` derivative
    that enter-text shows inline — far less S3 fetch + browser transfer than the
    full-res original. It falls back to the full-res image below when no display
    copy exists (legacy pages processed before the derivative was introduced), so
    the caller never has to special-case them. Enlarge / crop-and-rotate pass
    ``display=False`` to keep working on the full-resolution original.
    """
    if display:
        try:
            return ImageOps.exif_transpose(Image.open(fs.open(
                f"sawimages/{book}/page_{page_number}_display.jpg", mode='rb'
            )))
        except FileNotFoundError:
            # Legacy page with no display copy — fall back to full-res below.
            pass
    if use_cropped:
        try:
            return ImageOps.exif_transpose(Image.open(fs.open(
                f"sawimages/{book}/page_{page_number}_cropped.jpg", mode='rb'
            )))
        except FileNotFoundError:
            # No corrected version exists yet; fall back to the original below.
            pass
    return ImageOps.exif_transpose(Image.open(fs.open(
        f"sawimages/{book}/page_{page_number}.jpg", mode='rb'
    )))


def cropped_exists(book, page_number):
    try:
        fs.info(f"sawimages/{book}/page_{page_number}_cropped.jpg")
        return True
    except FileNotFoundError:
        return False


def _load_page_bytes_for_extraction(book, page_number):
    """Return the raw JPEG bytes for a page, preferring the corrected _cropped
    version — matching what load_image()/display_image() show the user, so a
    re-extract (#165) reads the same image the archivist is looking at.

    Returns raw file bytes (not a decoded PIL image) since that is what
    ``pages.uploader.extract_page_info``/``vision_json`` expect.
    """
    try:
        with fs.open(f"sawimages/{book}/page_{page_number}_cropped.jpg", mode='rb') as f:
            return f.read()
    except FileNotFoundError:
        with fs.open(f"sawimages/{book}/page_{page_number}.jpg", mode='rb') as f:
            return f.read()


def reextract_current_page(page_number):
    """Re-run OCR for exactly the current page on demand (#165).

    Reuses ``pages.uploader.extract_page_info`` + ``utilities.get_anthropic_client``
    (#129) — one user-initiated vision call, never automatic/looping. Auto-saves
    any in-progress text edit first (so it is not clobbered by a failed or
    unwanted re-extract), then on success writes the result through
    ``Page.text``/``Page.contains_story`` (write-through persists to Firestore,
    per the ``DataStructureBase`` convention) and reruns so the page re-renders
    with the fresh text.

    Both the text area and the "contains story" checkbox are keyed per-page
    (``enter_text_page_text_<n>``/``enter_text_contains_story_<n>``, see #80).
    Because THIS re-extract targets the page already on screen, those keys are
    already present in session_state from the current render, so simply
    updating ``current_page``'s fields and rerunning would NOT change what the
    widgets display — Streamlit ignores a widget's ``value=`` once its key is
    already populated. Assigning to those keys here is ALSO illegal (#198):
    this function runs mid-render, after the checkbox has been instantiated,
    and Streamlit raises ``StreamlitAPIException`` on assignment to an
    already-instantiated widget's key. So the refresh is STAGED instead
    (``stage_reextract_refresh``) and the top of the next script run
    (``consume_reextract_refresh``) writes the freshly extracted text/story
    directly onto the widget keys before those widgets exist — a legal
    assignment that refreshes the on-screen text deterministically (popping the
    keys and relying on ``value=`` re-seeding did not reliably update it).
    """
    _save_current_page_text()

    client = get_anthropic_client()
    if client is None:
        st.warning(EnterText.reextract_no_api_key)
        return

    book = st.session_state.current_book

    try:
        image_bytes = _load_page_bytes_for_extraction(book.title, page_number)
    except FileNotFoundError as exc:
        st.error(EnterText.reextract_image_missing)
        logger.warning(
            "Re-extract: no page image found for book=%s page=%s: %s",
            book.title, page_number, exc,
        )
        return

    with st.spinner(EnterText.reextract_spinner):
        try:
            text, is_story, _page_type = extract_page_info(
                image_bytes, client,
                book=book,
                page_number=page_number,
                page_name=f"page_{page_number}.jpg",
                flow=ExtractionErrorLog.FLOW_REEXTRACT,
            )
        except PageExtractionError:
            # The failure detail is already logged to extraction_errors by
            # extract_page_info itself (#132) — surface a friendly message here
            # rather than the raw error, and don't touch the existing text.
            st.error(EnterText.reextract_failed)
            return

    st.session_state.current_page.text = text
    st.session_state.current_page.contains_story = is_story
    st.session_state.book_pages_dict[page_number] = st.session_state.current_page

    # Stage the widget-state refresh + success flash for the next run (see
    # docstring above — assigning to the widget keys here would crash, #198).
    # The extracted text/story ride along so consume_reextract_refresh can seed
    # the widgets directly at the top of the next run.
    stage_reextract_refresh(
        st.session_state, page_number, EnterText.reextract_success, text, is_story
    )
    st.rerun()


@st.dialog(" ", width="large")
def enlarged_image_dialog(image):
    """Show the already-loaded page image at full width (#167).

    Takes the in-memory image the main view has ALREADY loaded rather than
    re-opening it from S3. On a slow/mobile connection a fresh S3 fetch inside the
    dialog could stall the script run long enough for the websocket to drop and
    reconnect with an empty session_state, which then bounces the user to login.
    Streamlit re-invokes the dialog with the same argument on subsequent reruns,
    so the image object is reused and the dialog performs no network I/O.
    """
    st.image(image, width="stretch")


@st.dialog(EnterText.image_edit_dialog_title, width="large")
def manual_correction_dialog():
    book = st.session_state['current_book'].title
    page_number = st.session_state.current_page_number

    # #209: edit the image the user is CURRENTLY LOOKING AT, not unconditionally
    # the raw original. The dialog previously always started from the original
    # photo while the main view showed the auto-corrected image — so when
    # auto-correction had already rotated the page, the rotate buttons appeared
    # to apply the wrong amount (the reported "180° only rotates 90°": raw+180
    # differs from the on-screen image by the auto-rotation). Basing the editor
    # on the displayed variant makes "rotate 180°" mean "rotate what I see by
    # 180°". Full resolution in both branches (display=False). Toggling "Show
    # original photo" before opening still edits the original from scratch.
    show_raw = bool(st.session_state.get(f"show_raw_{page_number}", False))
    editing_corrected = _page_has_cropped(book, page_number) and not show_raw
    base_image = load_image(book, page_number, use_cropped=editing_corrected)
    st.caption(
        EnterText.editing_corrected_caption if editing_corrected
        else EnterText.editing_original_caption
    )

    if '_manual_rotation' not in st.session_state:
        st.session_state['_manual_rotation'] = 0

    st.subheader(EnterText.rotation_header)
    col_a, col_b, col_c, _ = st.columns(4)
    rotate_90_left = col_a.button(EnterText.rotate_left_button, key="enter_text_rotate_left_button")
    rotate_90_right = col_b.button(EnterText.rotate_right_button, key="enter_text_rotate_right_button")
    rotate_180 = col_c.button(EnterText.rotate_180_button, key="enter_text_rotate_180_button")

    if rotate_90_left:
        st.session_state['_manual_rotation'] -= 90
    if rotate_90_right:
        st.session_state['_manual_rotation'] += 90
    if rotate_180:
        st.session_state['_manual_rotation'] += 180

    fine_angle = st.slider(EnterText.fine_adjustment_label, -45, 45, 0, key="fine_rotation")

    st.subheader(EnterText.crop_header)
    crop_left = st.slider(EnterText.crop_left_label, 0, 40, 0, key="crop_left")
    crop_right = st.slider(EnterText.crop_right_label, 0, 40, 0, key="crop_right")
    crop_top = st.slider(EnterText.crop_top_label, 0, 40, 0, key="crop_top")
    crop_bottom = st.slider(EnterText.crop_bottom_label, 0, 40, 0, key="crop_bottom")

    # Shared, unit-tested transform (#209): quarter-turn buttons accumulate in
    # _manual_rotation (clockwise positive); apply_manual_correction never
    # mutates base_image (safe with load_image's st.cache_data).
    img = apply_manual_correction(
        base_image,
        rotation=st.session_state['_manual_rotation'],
        fine_angle=fine_angle,
        crop_left=crop_left,
        crop_right=crop_right,
        crop_top=crop_top,
        crop_bottom=crop_bottom,
    )

    st.image(img, width="stretch", caption=EnterText.preview_caption)

    save_col, discard_col = st.columns(2)
    if save_col.button(EnterText.save_corrected_button, width="stretch", key="enter_text_save_corrected_button"):
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=95)
        corrected_bytes = buf.getvalue()
        # Persist the full-res corrected image.
        cropped_path = f"sawimages/{book}/page_{page_number}_cropped.jpg"
        with fs.open(cropped_path, 'wb') as f:
            f.write(corrected_bytes)
        # Regenerate the small display derivative from the SAME corrected bytes.
        # Post-#184 the inline view prefers page_{n}_display.jpg; without this the
        # manual correction would appear to "revert" — the new _cropped.jpg was
        # saved but display_image() kept showing the stale display copy generated
        # at processing time (the original, upside-down, orientation).
        display_path = f"sawimages/{book}/page_{page_number}_display.jpg"
        with fs.open(display_path, 'wb') as f:
            f.write(make_display_copy(corrected_bytes))
        # Record that a corrected image now exists (write-through persists to
        # Firestore) so _page_has_cropped() returns True without an S3 HEAD check
        # and display_image() defaults to the corrected view + "show original"
        # toggle. Covers a page the auto-pipeline left uncorrected (corrected
        # False/None) as well as re-correcting a bad auto-correction.
        st.session_state.current_page.corrected = True
        # A saved manual correction resolves any orientation uncertainty the
        # automatic check flagged (#217) — the user has now seen and fixed (or
        # confirmed) the page. Guarded so the common flag-not-set case costs no
        # extra Firestore write (each assignment writes through).
        if getattr(st.session_state.current_page, 'rotation_uncertain', False):
            st.session_state.current_page.rotation_uncertain = False
        # Invalidate the @st.cache_data image cache so the freshly written
        # _cropped/_display bytes are re-fetched from S3 (the cache would
        # otherwise return the pre-save image for these keys).
        load_image.clear()
        # Close the dialog and rerun the main page so the corrected image is
        # shown immediately (st.rerun() inside an st.dialog dismisses it).
        st.rerun()

    if discard_col.button(EnterText.discard_button, width="stretch", key="enter_text_discard_button"):
        st.rerun()


def _page_has_cropped(book, page_number):
    """Whether an auto-corrected image exists for the current page (#184).

    Prefers the ``corrected`` flag recorded on the Page doc at processing time so
    the common case costs NO S3 request. Only legacy pages saved before that flag
    existed (``corrected is None``) fall back to the per-render S3 HEAD check.
    """
    corrected = getattr(st.session_state.get('current_page'), 'corrected', None)
    if corrected is None:
        return cropped_exists(book, page_number)
    return bool(corrected)


def display_image():
    book = st.session_state['current_book'].title
    page_number = st.session_state.current_page_number
    has_cropped = _page_has_cropped(book, page_number)

    use_cropped = True
    if has_cropped:
        use_cropped = not col1.toggle(
            EnterText.show_original_toggle, value=False, key=f"show_raw_{page_number}"
        )
        if not use_cropped:
            col1.caption(EnterText.showing_original_caption)
        else:
            col1.caption(EnterText.auto_corrected_caption)
    else:
        col1.caption(EnterText.auto_correction_unavailable_caption)

    # The automatic orientation check couldn't decide which way up this page is
    # (#217): no rotation was applied at processing time, so prompt the user to
    # check rather than leaving a possibly-rotated page silent. Cleared when a
    # manual crop-and-rotate is saved. getattr-guarded for pre-#217 Page objects
    # still held in an open session.
    if getattr(st.session_state.current_page, 'rotation_uncertain', False):
        col1.warning(EnterText.rotation_uncertain_warning)

    if use_cropped:
        # Default view: ship the small display derivative (#184) — it is built
        # from the corrected image, so it matches the auto-corrected view and
        # falls back to full-res for legacy pages.
        page_image = load_image(book, page_number, use_cropped=True, display=True)
    else:
        # User explicitly asked for the original: load the full-res raw page.
        page_image = load_image(book, page_number, use_cropped=False)
    w, h = page_image.size

    col1.image(page_image, width="stretch")

    # Crop/rotate (#169): rendered below the photo, consistently with Enlarge
    # below. ALWAYS shown (#181) — it was previously hidden whenever an
    # auto-corrected version existed, which left a BAD auto-correction (wrong
    # rotation, bad crop) impossible to fix. The dialog starts from the image
    # CURRENTLY DISPLAYED (#209: corrected by default, the original when "Show
    # original photo" is on) and overwrites page_{n}_cropped.jpg, so it doubles
    # as the fix-a-bad-auto-correction tool.
    if col1.button(EnterText.edit_image_button, width="stretch", key="enter_text_edit_image_button"):
        # Start each editing session from a clean slate: clear any rotation/
        # crop state left from a previous open (including closing via the
        # dialog's native ✕, which we can't otherwise hook into).
        for _k in (
            '_manual_rotation', 'fine_rotation',
            'crop_left', 'crop_right', 'crop_top', 'crop_bottom',
        ):
            st.session_state.pop(_k, None)
        manual_correction_dialog()

    if col1.button(EnterText.enlarge_button, width="stretch", key="enter_text_enlarge_button"):
        # Enlarge must show the FULL-RES image (#184), not the downsized display
        # copy shown inline — load the full-res version of whichever variant
        # (corrected vs original) is currently selected.
        enlarged_image_dialog(load_image(book, page_number, use_cropped=use_cropped))

    # Prefetch the next page's display copy so "Next page" is instant (#184): the
    # cache is warmed while the user reads the current page. Guarded — a prefetch
    # miss must never break the render.
    next_page = page_number + 1
    if next_page <= st.session_state.current_book.page_count:
        try:
            load_image(book, next_page, use_cropped=True, display=True)
        except (FileNotFoundError, OSError) as exc:
            logger.debug("Prefetch of page %s failed: %s", next_page, exc)

    dimensions = st_dimensions(key="main")
    col_width = int(dimensions['width'] * 3 / 5) if dimensions else 500
    return int(col_width * h / w)

def adding_character():
    # Auto-save the current page text before leaving the text box (#152).
    _save_current_page_text()
    # Starting a fresh character entry: drop persisted form-widget state so the
    # new (empty document_id) character re-seeds from value=/index= (see #80).
    clear_entity_form_state("character_form_")
    st.session_state.now_entering = 'character'


def adding_text():
    st.session_state.now_entering = 'text'
    st.session_state.pop('current_alias', None)


def adding_alias():
    # Auto-save the current page text before leaving the text box (#152).
    _save_current_page_text()
    # Starting a fresh alias entry: drop persisted form-widget state so the new
    # (empty document_id) alias re-seeds from value=/index= (see #80).
    clear_entity_form_state("alias_form_")
    st.session_state['_alias_form_count'] = st.session_state.get('_alias_form_count', 0) + 1
    st.session_state.now_entering = 'alias'


def managing_characters():
    # Auto-save the current page text before leaving the text box (#152).
    _save_current_page_text()
    st.session_state.now_entering = 'manage'


def start_editing_character(character_id):
    """Open the inline edit form for the given character in the manage view."""
    # Drop any stale character-form widget state so the edit form re-seeds from
    # the character's stored values rather than a previous render (#80).
    clear_entity_form_state("character_form_")
    st.session_state['_editing_character_id'] = character_id


def cancel_editing_character():
    """Close the inline character edit form without changes."""
    st.session_state.pop('_editing_character_id', None)


@st.dialog(ManageCharacters.delete_character_dialog_title)
def confirm_delete_character(character_doc_dict, name):
    st.write(ManageCharacters.delete_character_warning.format(name=name))
    if st.button(ManageCharacters.confirm_delete_button, key="enter_text_confirm_delete_character_button"):
        Character(db_object=character_doc_dict).delete()
        st.session_state.now_entering = 'manage'
        st.rerun()
    if st.button(ManageCharacters.cancel_button, key="enter_text_cancel_delete_character_button"):
        st.rerun()


@st.dialog(ManageCharacters.delete_alias_dialog_title)
def confirm_delete_alias(alias_doc_dict, name):
    st.write(ManageCharacters.delete_alias_warning.format(name=name))
    if st.button(ManageCharacters.confirm_delete_button, key="enter_text_confirm_delete_alias_button"):
        Alias(db_object=alias_doc_dict).delete()
        st.session_state.now_entering = 'manage'
        st.rerun()
    if st.button(ManageCharacters.cancel_button, key="enter_text_cancel_delete_alias_button"):
        st.rerun()


def rerun_detect():
    """On-click handler for the "Re-run character detection" button (#170/#182).

    Stages an IMMEDIATE run via ``stage_character_redetect`` — the same staging
    used by the auto-run-after-OCR hook below — so detect_entry() executes the
    AI call as soon as it renders, with no separate confirmation click. A
    re-run is strictly ADDITIVE: suggestions matching characters already
    entered for this book are filtered out (see ``run_character_detection``),
    so it can only propose new characters/aliases and never replaces anything
    already entered. Reuses the existing detection + review pipeline (#129);
    does not write any Character/Alias itself.
    """
    _save_current_page_text()
    stage_character_redetect(st.session_state, source=CHARACTER_AUTODETECT_SOURCE_MANUAL)


def text_entry(element, image_height, delta=50):
    for message in st.session_state.pop('_detected_characters_result', []):
        element.success(message)

    # Success flash from a just-completed re-extract (#198): staged by
    # stage_reextract_refresh because st.success immediately before st.rerun()
    # is never seen.
    _reextract_message = st.session_state.pop('_reextract_result', None)
    if _reextract_message:
        element.success(_reextract_message)

    # The story toggle and text area are seeded from the current page, so suffix
    # their keys with the page number to re-seed (rather than bleed state) when
    # the user pages through the book (see #80).
    page_number = st.session_state.current_page_number

    st.session_state.current_page.contains_story = element.checkbox(
        EnterText.contains_story_label,
        value=st.session_state.current_page.contains_story,
        key=f"enter_text_contains_story_{page_number}"
    )

    # Re-extract this page's text on demand (#165): a dedicated button rather
    # than a side effect of the checkbox above, so a paid AI call is always an
    # explicit, user-initiated action.
    if element.button(
        EnterText.reextract_button,
        width="stretch",
        help=EnterText.reextract_help,
        key=f"enter_text_reextract_button_{page_number}",
    ):
        reextract_current_page(page_number)

    subcol1, subcol2 = element.columns(2)
    subcol1.button(EnterText.add_character_button, width="stretch", on_click=adding_character, help=EnterText.character_help, key="enter_text_add_character_button")
    subcol2.button(EnterText.add_alias_button, width="stretch", on_click=adding_alias, help=EnterText.alias_help, key="enter_text_add_alias_button")
    element.button(
        ManageCharacters.manage_button,
        width="stretch",
        on_click=managing_characters,
        help=ManageCharacters.manage_help,
        key="enter_text_manage_characters_button",
    )
    # Character detection runs automatically once the book's pages have been
    # read (#170/#179), so there is no separate "Detect characters (AI)" button
    # any more (#182) — only the additive re-run below, which executes
    # immediately on click.
    element.button(
        EnterText.rerun_detect_button,
        width="stretch",
        on_click=rerun_detect,
        help=EnterText.rerun_detect_help,
        key="enter_text_rerun_detect_button",
    )

    height = max(image_height - delta, 200)

    # The text area is intentionally NOT wrapped in an st.form: as a plain widget
    # its typed value is committed to session_state whenever the user interacts
    # with any other control, which is what lets the navigation handlers
    # auto-save the page before leaving it (#152). The old explicit "Save page"
    # button has been removed in favour of that auto-save-on-navigation. The key
    # stays per-page (see #80) so nav handlers can read this page's value.
    st.session_state['_page_text_editing'] = element.text_area(
        EnterText.page_text_label,
        height=height,
        value=st.session_state.current_page.text,
        disabled=not st.session_state.current_page.contains_story,
        key=f"enter_text_page_text_{page_number}"
    )

    # A second "Next page" control at the bottom of the text column, directly
    # above the Finish/Submit row rendered further down the page (#169) — a
    # natural continuation once the user has finished this page's text. Same
    # auto-saving page_change handler as the top nav button; only the key
    # differs (#80). Handled INLINE rather than via on_click (#78): this button
    # lives inside the entry-column fragment, where an on_click callback would
    # trigger only a FRAGMENT rerun — the page image and indicator outside the
    # fragment would keep showing the previous page. st.rerun() defaults to
    # scope="app", forcing the full rerun a page change requires.
    if element.button(
        EnterText.next_page_button,
        width="stretch",
        key="enter_text_next_page_bottom_button",
    ):
        page_change(1)
        st.rerun()


def character_entry(element):

    # Show the cast already saved for this book above the add form (#201), so
    # the user can see a name is taken (and jump to editing it) before typing.
    _render_saved_cast(element)

    st.session_state['current_character'] = Character(
        book=st.session_state['current_book'].title
    )
    with element.form('character'):
        st.session_state['current_character'].to_form()

    element.button(EnterText.cancel_character_button, width="stretch", on_click=adding_text, key="enter_text_cancel_character_button")


def alias_entry(element):

    # Aliases can only be attached to characters in this book. If none exist
    # yet, avoid rendering an empty form (a Streamlit form must contain a submit
    # button) and prompt the user to add a character first.
    if not st.session_state.get('book_character_dict'):
        element.warning(AliasForm.no_characters)
        element.button(EnterText.cancel_alias_button, width="stretch", on_click=adding_text, key="enter_text_cancel_alias_nochars_button")
        return

    st.session_state['current_alias'] = Alias(
        book=st.session_state['current_book'].title
    )
    form_key = f"alias_{st.session_state.get('_alias_form_count', 0)}"
    with element.form(form_key):
        st.session_state['current_alias'].to_form()

    element.button(EnterText.cancel_alias_button, width="stretch", on_click=adding_text, key="enter_text_cancel_alias_button")


def manage_characters_entry(element):

    element.subheader(ManageCharacters.header)
    element.write(ManageCharacters.intro)

    # Flash from a same-name add that was routed here to edit the existing
    # character instead (#201, see Character.to_form).
    _manage_flash = st.session_state.pop('_manage_flash', None)
    if _manage_flash:
        element.info(_manage_flash)

    book = st.session_state['current_book']
    # Rebuild the book-scoped character lookup from the book's reference list so
    # it reflects any additions/deletions, and keep it in session state so the
    # alias form stays scoped to this book.
    character_dict = book.get_character_dict()
    st.session_state['book_character_dict'] = character_dict

    if not character_dict:
        element.info(ManageCharacters.no_characters)
    else:
        # Batch the per-render reads (#78): the previous loop issued one
        # ``ref.get()`` PLUS one alias query PER character on every rerun of
        # the manage view (2N round trips). One ``get_all`` resolves all
        # character docs and one chunked ``in`` query fetches every alias of
        # every character; both are grouped locally below. Semantics match the
        # per-character queries exactly (an alias row is grouped by the same
        # ``character`` reference the ``==`` filter matched on).
        character_refs = list(character_dict.values())
        character_snaps = dict(zip(
            (ref.path for ref in character_refs),
            st.session_state['firestore'].get_all_by_references(character_refs),
        ))
        aliases_by_character = {}
        for alias_doc in st.session_state['firestore'].query_stream_in(
            collection='aliases', field='character', values=character_refs,
        ):
            alias_character = alias_doc.to_dict().get('character')
            if alias_character is not None:
                aliases_by_character.setdefault(alias_character.path, []).append(alias_doc)

        for name, character_ref in character_dict.items():
            character_doc = character_snaps.get(character_ref.path)
            if character_doc is None or not character_doc.exists:
                continue
            with element.expander(name):
                aliases = aliases_by_character.get(character_ref.path, [])
                st.write(ManageCharacters.aliases_label)
                if not aliases:
                    st.caption(ManageCharacters.no_aliases)
                for alias_doc in aliases:
                    alias_data = alias_doc.to_dict()
                    alias_name = alias_data.get('name', '')
                    alias_col, button_col = st.columns([3, 1])
                    alias_col.write(alias_name)
                    if button_col.button(
                        ManageCharacters.delete_alias_button,
                        key=f"delete_alias_{alias_doc.id}",
                    ):
                        confirm_delete_alias(alias_data, alias_name)
                if st.session_state.get('_editing_character_id') == character_ref.id:
                    # Inline edit form: reconstruct the character from its stored
                    # document so edits write through (and a rename migrates) via
                    # Character.edit_form.
                    character = Character(db_object=character_doc.to_dict())
                    with st.form(f"edit_character_{character_ref.id}"):
                        character.edit_form()
                    st.button(
                        ManageCharacters.cancel_edit_button,
                        width="stretch",
                        on_click=cancel_editing_character,
                        key=f"cancel_edit_character_{character_ref.id}",
                    )
                else:
                    st.button(
                        ManageCharacters.edit_character_button,
                        width="stretch",
                        on_click=start_editing_character,
                        args=(character_ref.id,),
                        key=f"edit_character_{character_ref.id}",
                    )
                if st.button(
                    ManageCharacters.delete_character_button,
                    key=f"delete_character_{character_ref.id}",
                ):
                    confirm_delete_character(character_doc.to_dict(), name)

    element.button(ManageCharacters.done_button, width="stretch", on_click=adding_text, key="enter_text_manage_done_button")


def _filter_existing_characters(suggestions):
    """Drop suggestions whose name matches a character already entered for this
    book (case-insensitive), so detection is strictly ADDITIVE (#182): a
    (re-)run can only propose NEW characters/aliases and never replaces or
    duplicates anything already entered. Existing characters remain available
    as 'Merge into' targets in the review form, so newly detected nicknames can
    still be attached to them as aliases; ``commit_detected_characters``'s
    document_exists checks remain the backstop.

    Returns ``(kept_suggestions, skipped_names)`` and stashes the skipped
    NAMES (#201) so the review form can say exactly which detected characters
    were dropped as already-saved — a bare count read as "the AI missed them".
    """
    existing = {
        name.lower()
        for name in st.session_state['current_book'].get_character_dict()
    }
    kept = [s for s in suggestions if s['name'].lower() not in existing]
    # Normalise each kept suggestion's aliases the SAME way commit_detected_
    # characters will (strip a leading article, drop blanks/dupes and the
    # character's own name), so the review form shows EXACTLY what gets saved —
    # e.g. "the Butterfly" displays as "Butterfly" rather than only being
    # stripped silently on save. Both detection paths (live re-run and the
    # precomputed auto-detect) route through here before the review form.
    for s in kept:
        s['aliases'] = _parse_aliases(", ".join(s.get('aliases', [])), s.get('name') or '')
    skipped_names = [s['name'] for s in suggestions if s['name'].lower() in existing]
    st.session_state['_detected_existing_skipped'] = skipped_names
    return kept, skipped_names


def run_character_detection():
    """Run the AI character detection and stash suggestions in session state.

    Status is always VISIBLE (#183): the call runs under an explicit spinner and
    every outcome is surfaced — success lands on the review form (with a
    success banner), zero suggestions shows the explicit "none found" notice
    there, and a failure shows an error message. Never silent.

    Returns True when suggestions were produced (and a rerun should follow),
    False otherwise (a warning/error has already been shown to the user).
    """
    client = get_anthropic_client()
    if client is None:
        st.warning(EnterText.detect_no_api_key)
        return False

    pages = [
        (page_number, page.text)
        for page_number, page in st.session_state.book_pages_dict.items()
        if page.contains_story and (page.text or "").strip()
    ]
    if not pages:
        st.warning(EnterText.detect_no_text)
        return False

    status = st.empty()
    progress = st.progress(0.0)

    def _on_progress(done, total):
        status.write(EnterText.detect_progress.format(done=done, total=total))
        progress.progress(min(done / total, 1.0))

    with st.spinner(EnterText.detect_spinner):
        try:
            suggestions = detect_book_characters(pages, client, progress_callback=_on_progress)
        except (anthropic.AnthropicError, json.JSONDecodeError, ValueError, KeyError) as error:
            st.error(EnterText.detect_failed.format(error=error))
            return False

    # Additive guard (#182): never re-suggest characters already in this book.
    suggestions, _skipped = _filter_existing_characters(suggestions)
    st.session_state['_detected_characters'] = suggestions
    return True


def _parse_aliases(text, exclude_name):
    """Split a comma-separated alias string, dropping blanks, duplicates and
    any value equal to the character's own name.

    Each alias has a leading article ("the"/"a"/"an") stripped (hotfix) so an
    auto-detected alias like "the Butterfly" is stored as "Butterfly"; dedup and
    the exclude-name check run on the stripped value so "the Butterfly" and
    "Butterfly" collapse together."""
    aliases = []
    seen = set()
    for part in (text or "").split(','):
        alias = strip_leading_article(part.strip())
        if alias and alias.lower() != exclude_name.lower() and alias.lower() not in seen:
            seen.add(alias.lower())
            aliases.append(alias)
    return aliases


def commit_detected_characters(rows):
    """Persist the reviewed character suggestions, honouring per-row actions.

    rows is aligned with st.session_state['_detected_characters']; each entry
    carries the (possibly edited) name/gender/flags, an alias string and a
    chosen action. 'Merge into' folds a row's names into the target character's
    aliases instead of creating a separate character.
    """
    book_title = st.session_state['current_book'].title
    original_names = [s['name'] for s in st.session_state['_detected_characters']]
    merge_prefix = EnterText.review_action_merge.split('{', 1)[0]

    # Rows the user wants to create, keyed by their original (AI) name so that
    # 'Merge into' actions can find their target.
    create_rows = {}
    for original_name, row in zip(original_names, rows):
        if row['action'] == EnterText.review_action_create and row['name'].strip():
            row['_aliases'] = _parse_aliases(row['aliases'], row['name'].strip())
            create_rows[original_name] = row

    # Existing characters already defined for this book are also valid merge
    # targets, resolved book-scoped (by reference) to avoid same-name collisions
    # across books.
    existing_chars = st.session_state['current_book'].get_character_dict()

    # Fold merged rows into a to-be-created character's alias list, or queue them
    # to be attached to an already-existing book character.
    unresolved = []
    merge_into_existing = []  # list of (target_ref, target_name, [alias names])
    for row in rows:
        action = row['action']
        if action == EnterText.review_action_create or action == EnterText.review_action_skip:
            continue
        target_name = action[len(merge_prefix):].strip()
        extra = [row['name'].strip()] + _parse_aliases(row['aliases'], row['name'].strip())
        target = create_rows.get(target_name)
        if target is not None:
            for alias in extra:
                if (
                    alias
                    and alias.lower() != target['name'].strip().lower()
                    and alias not in target['_aliases']
                ):
                    target['_aliases'].append(alias)
        elif target_name in existing_chars:
            merge_into_existing.append((existing_chars[target_name], target_name, extra))
        else:
            unresolved.append(row['name'].strip() or "(unnamed)")

    created_count = 0
    alias_count = 0
    skipped = []

    # --- Batched commit (#184) -------------------------------------------------
    # The old code did, per character/alias: one document_exists read + one
    # register() write + (per character) one book character-list write — dozens of
    # sequential Firestore round-trips on a book with many characters. Here we
    # build every entity UNREGISTERED (so the write-through Field descriptors never
    # touch Firestore while we assemble them), resolve existence with ONE get_all
    # per collection, and stage every write into a single WriteBatch committed
    # once. Total cost is a small constant number of round-trips regardless of how
    # many characters/aliases were detected.
    firestore = st.session_state.firestore
    book = st.session_state['current_book']

    # Build the characters we intend to create, de-duplicating by document id.
    planned_characters = []  # (Character, name, [alias names])
    seen_char_ids = set()
    for row in create_rows.values():
        name = row['name'].strip()
        character = Character(book=book_title)
        character.name = name
        character.gender = row['gender']
        character.human = row['human']
        character.protagonist = row['protagonist']
        character.plural = row['plural']
        cid = character.document_id
        if cid in seen_char_ids:
            continue
        seen_char_ids.add(cid)
        planned_characters.append((character, name, row['_aliases']))

    # ONE existence read across all candidate character ids.
    existing_char_ids = firestore.get_existing_ids(
        'characters', [c.document_id for c, _n, _a in planned_characters]
    )

    batch = firestore.write_batch()
    new_book_refs = []
    planned_aliases = []  # (character_ref, alias_name)

    for character, name, alias_names in planned_characters:
        if character.document_id in existing_char_ids:
            skipped.append(name)
            continue
        character.register_batched(batch)
        character_ref = character.get_ref()
        new_book_refs.append(character_ref)
        # Keep the session lookup dicts in sync (mirrors the manual flow) so the
        # new character is immediately selectable for alias entry etc.
        st.session_state['character_dict'][name] = character_ref
        st.session_state.setdefault('book_character_dict', {})[name] = character_ref
        created_count += 1
        for alias_name in alias_names:
            planned_aliases.append((character_ref, alias_name))

    # Aliases merged into already-existing book characters.
    for target_ref, target_name, extra in merge_into_existing:
        for alias_name in extra:
            if not alias_name or alias_name.lower() == target_name.lower():
                continue
            planned_aliases.append((target_ref, alias_name))

    # Build the alias entities, de-duplicating by document id (which is book+name
    # scoped), then ONE existence read across all candidate alias ids.
    alias_objs = []
    seen_alias_ids = set()
    for character_ref, alias_name in planned_aliases:
        alias = Alias(book=book_title)
        alias.character = character_ref  # a reference, stored directly
        alias.name = alias_name
        aid = alias.document_id
        if aid in seen_alias_ids:
            continue
        seen_alias_ids.add(aid)
        alias_objs.append(alias)

    existing_alias_ids = firestore.get_existing_ids(
        'aliases', [a.document_id for a in alias_objs]
    )
    for alias in alias_objs:
        if alias.document_id in existing_alias_ids:
            continue
        alias.register_batched(batch)
        alias_count += 1

    # Link every newly created character to the book in ONE batched update rather
    # than a per-character write-through. Update the in-memory list too (under the
    # reading_from_db guard so it does not trigger its own write) so the book stays
    # consistent this session without a re-read.
    book_updated = False
    if new_book_refs:
        existing_paths = {ref.path for ref in book.characters}
        additions = [r for r in new_book_refs if r.path not in existing_paths]
        if additions:
            updated = book.characters + additions
            book.reading_from_db = True
            book.characters = updated
            book.reading_from_db = False
            batch.update(book.get_ref(), {'characters': updated})
            book_updated = True

    # Commit only when something was actually staged (all-skip / all-existing runs
    # stage nothing).
    if created_count or alias_count or book_updated:
        batch.commit()

    messages = [EnterText.review_created.format(characters=created_count, aliases=alias_count)]
    if skipped:
        messages.append(EnterText.review_skipped.format(names=", ".join(skipped)))
    if unresolved:
        messages.append(EnterText.review_unresolved.format(names=", ".join(unresolved)))

    st.session_state['_detected_characters_result'] = messages
    st.session_state.pop('_detected_characters', None)
    st.session_state.pop('_detected_characters_source', None)
    st.session_state['now_entering'] = 'text'
    st.rerun()


def _edit_saved_character(character_id):
    """Route from a saved-cast Edit button (#201) into the manage view with the
    character's inline edit form open."""
    start_editing_character(character_id)
    st.session_state['now_entering'] = 'manage'


def _render_saved_cast(element):
    """Render the book's already-saved characters with per-character Edit
    buttons (#201), so wherever suggestions (or the add form) appear the FULL
    cast is visible and editable — "saved" no longer reads as "not detected",
    and a character needing changes is one click away (reuses the manage
    view's edit form, #129).
    """
    character_dict = st.session_state['current_book'].get_character_dict()
    if not character_dict:
        return
    element.markdown(EnterText.saved_cast_header)
    for name, character_ref in character_dict.items():
        name_col, button_col = element.columns([3, 1])
        name_col.write(name)
        button_col.button(
            EnterText.saved_cast_edit_button,
            key=f"saved_cast_edit_{character_ref.id}",
            on_click=_edit_saved_character,
            args=(character_ref.id,),
        )


def _skipped_existing_info(element):
    """Name the detected-but-already-saved characters (#201) — an st.info, not
    the old easily-missed caption with a bare count."""
    skipped_names = st.session_state.get('_detected_existing_skipped') or []
    if skipped_names:
        element.info(
            EnterText.detect_existing_skipped.format(names=", ".join(skipped_names))
        )


def character_review_form(element):
    suggestions = st.session_state['_detected_characters']
    if not suggestions:
        element.info(EnterText.detect_none_found)
        # Everything detected may already be entered for this book (#182) —
        # say WHICH, and show the saved cast, rather than implying the AI
        # found nothing (#201).
        _skipped_existing_info(element)
        _render_saved_cast(element)
        element.button(
            EnterText.back_to_text_button, width="stretch", on_click=adding_text, key="detect_back_none"
        )
        return

    if st.session_state.get('_detected_characters_source') == CHARACTER_AUTODETECT_SOURCE_AUTO:
        element.info(EnterText.auto_detect_banner)
    # Explicit, visible outcome (#183): say the run finished and what it found.
    element.success(EnterText.detect_success.format(count=len(suggestions)))
    _skipped_existing_info(element)
    _render_saved_cast(element)
    element.write(EnterText.review_instruction)
    names = [s['name'] for s in suggestions]
    # Characters already defined for this book are also valid merge targets, so a
    # detected duplicate can be folded into an existing character — not only into
    # another freshly-detected one.
    existing_names = [
        n for n in st.session_state['current_book'].get_character_dict()
        if n not in names
    ]

    with element.form('character_review'):
        rows = []
        for i, suggestion in enumerate(suggestions):
            st.markdown(EnterText.review_character_heading.format(n=i + 1))
            name = st.text_input(EnterText.review_name_label, value=suggestion['name'], key=f"rev_name_{i}")

            gender_index = (
                CharacterForm.gender_options.index(suggestion['gender'])
                if suggestion['gender'] in CharacterForm.gender_options
                else 0
            )
            gender = st.selectbox(
                EnterText.review_gender_label,
                options=CharacterForm.gender_options,
                index=gender_index,
                key=f"rev_gender_{i}",
            )
            human = st.checkbox(EnterText.review_human_label, value=suggestion['human'], key=f"rev_human_{i}")
            protagonist = st.checkbox(
                EnterText.review_protagonist_label, value=suggestion['protagonist'], key=f"rev_prot_{i}"
            )
            plural = st.checkbox(EnterText.review_plural_label, value=suggestion['plural'], key=f"rev_plural_{i}")
            aliases = st.text_input(
                EnterText.review_aliases_label,
                value=", ".join(suggestion['aliases']),
                key=f"rev_alias_{i}",
            )

            other_names = [n for j, n in enumerate(names) if j != i]
            merge_target_names = other_names + existing_names
            action_options = (
                [EnterText.review_action_create, EnterText.review_action_skip]
                + [EnterText.review_action_merge.format(name=n) for n in merge_target_names]
            )
            action = st.selectbox(
                EnterText.review_action_label, options=action_options, index=0, key=f"rev_action_{i}"
            )
            st.divider()

            rows.append({
                "name": name,
                "gender": gender,
                "human": human,
                "protagonist": protagonist,
                "plural": plural,
                "aliases": aliases,
                "action": action,
            })

        submitted = st.form_submit_button(EnterText.review_submit, key="enter_text_review_submit_button")
        if submitted:
            commit_detected_characters(rows)

    element.button(EnterText.cancel_button, width="stretch", on_click=adding_text, key="cancel_review")


def detect_entry(element):
    if '_detected_characters' not in st.session_state:
        # Landing on the detect view means a run was requested — by the
        # auto-run-after-OCR hook or the "Re-run character detection" button
        # (#170, see stage_character_redetect). There is no separate "Run
        # detection" confirmation click any more (#182): run immediately, with
        # the spinner/progress visible (#183).
        st.session_state.pop('_auto_run_detection', None)
        if run_character_detection():
            st.rerun()
            return
        # Failure / no story text / no API key: the explicit warning or error
        # is already on screen (run_character_detection is never silent, #183)
        # — give the user a way back rather than a blank screen.
        element.button(
            EnterText.back_to_text_button, width="stretch",
            on_click=adding_text, key="cancel_detect",
        )
        return
    character_review_form(element)


def user_entry_box(element, image_height, delta=50):
    if st.session_state.now_entering == 'text':
        text_entry(element, image_height, delta)
    elif st.session_state.now_entering == 'character':
        character_entry(element)
    elif st.session_state.now_entering == 'alias':
        alias_entry(element)
    elif st.session_state.now_entering == 'manage':
        manage_characters_entry(element)
    elif st.session_state.now_entering == 'detect':
        detect_entry(element)


@st.fragment
def _entry_column_fragment(image_height):
    """The right-hand entry column as a fragment (#78) — the app's hottest
    interactive section. Interacting with a widget inside a fragment reruns
    ONLY the fragment, so the frequent entry-column interactions (the
    contains-story checkbox, the text area's blur/commit, and every
    view-switch: add character/alias, manage, detect, edit/cancel, done) stop
    re-executing the whole script — page_layout, the sidebar, the image column
    and its ``st_dimensions`` component round trip.

    Why this boundary is safe — every path out of the column was audited:

    - The ``now_entering`` dispatch (``user_entry_box``) lives INSIDE the
      fragment, so view-switch ``on_click`` handlers need only a fragment
      rerun; nothing outside the column reads ``now_entering``.
    - Every flow that must refresh the page OUTSIDE the column already ends in
      an explicit ``st.rerun()``, which defaults to ``scope="app"`` (a full
      rerun) even when called from inside a fragment: the character/alias/edit
      form submits, the character-review commit, the detect run, the
      re-extract button, and the manage view's delete-confirmation dialogs
      (dialogs may be opened from a sequential fragment rerun).
    - The one control that previously relied on a bare ``on_click`` with an
      app-wide effect — the bottom "Next page" button (the page image and
      indicator outside must change) — is handled inline in ``text_entry``
      with an explicit app-scope ``st.rerun()``.
    - ``element`` is a container created INSIDE the fragment body (fragments
      cannot render widgets into externally created containers), positioned in
      ``col2`` by the ``with col2:`` at the call site.
    - ``image_height`` is a plain int captured at the last full run; fragment
      reruns replay it unchanged, which is correct — it only changes when the
      window/image changes, which itself triggers a full rerun.

    Deliberately NOT fragmented (risk outweighs the win, #78): the image
    column (its show-original toggle is a rare interaction, and the column
    both returns the layout height used here and hosts the crop/enlarge
    dialogs), and full-page Prev/Next paging (the top nav buttons must rerun
    the whole page anyway — image, indicator and entry column all change).
    """
    element = st.container()
    user_entry_box(element, image_height)

# def create_current_page_from_db():
#     st.session_state.current_page = Page(
#         st.session_state.firestore.get_by_reference(
#             collection='pages',
#             document_ref=f"{st.session_state.current_book.document_id}_{st.session_state.current_page_number}"
#         ).to_dict()
#     )


page_layout(current_page="./pages/enter_text.py")

# Consume a staged re-extract refresh (#198) BEFORE any of this page's widgets
# are instantiated: pops the refreshed page's widget-backed keys so the text
# area and contains-story checkbox re-seed from the freshly extracted
# current_page values (already persisted via the write-through Fields).
consume_reextract_refresh(st.session_state)

fs = get_s3_filesystem()

# A (re)processed photo batch invalidates the cached page images (#199): the
# upload pipeline just (re)wrote sawimages/{title}/page_N* — without this the
# @st.cache_data image cache keeps serving the PREVIOUS upload's image for each
# (book, page) key, which reads as wrong page order that slowly "fixes itself"
# as entries evict. The pipeline (pages/uploader._process_photo_batch) cannot
# import load_image (it would be a circular import), so it stages this flag.
if st.session_state.pop('_invalidate_image_cache', False):
    load_image.clear()

# Rebuild the page cache whenever the current book changes. Without this, the
# dict for the first book opened persists in session state and is shown for
# every subsequent book (stale text leaking across books).
_book_id = st.session_state.current_book.document_id
if (
    'book_pages_dict' not in st.session_state
    or st.session_state.get('_pages_dict_book_id') != _book_id
):
    create_page_dict_from_db()
    st.session_state['_pages_dict_book_id'] = _book_id
    st.session_state['current_page_number'] = 1
    st.session_state['now_entering'] = 'text'
    st.session_state['_page_text_editing'] = None
    # Build the book-scoped character lookup so alias entry is restricted to
    # characters in this book (rather than every character in the database).
    st.session_state['book_character_dict'] = (
        st.session_state.current_book.get_character_dict()
    )
    # Auto-run character detection right after OCR completes (#170):
    # pages.uploader._process_photo_batch flags this once its per-page OCR
    # loop finishes; consume it here (once, per book load) and land straight
    # on the existing detection review UI. When the background pipeline (#179)
    # already precomputed USABLE suggestions during the metadata step, show them
    # directly (filtered to be additive, #182) — no further AI call. Otherwise
    # (no precompute, a different book's stash, OR an empty/in-flight precompute
    # that would render an empty review) fall back to a live run — the SAME path
    # the "Re-run character detection" button uses, which is why that button
    # worked when first-landing auto-surfacing did not. Either way nothing is
    # written until the human reviews and submits "Create selected characters".
    if consume_pending_character_autodetect(st.session_state):
        _precomputed = st.session_state.pop('_precomputed_character_suggestions', None)
        _precomputed_suggestions = usable_precomputed_suggestions(_precomputed, _book_id)
        if _precomputed_suggestions is not None:
            _suggestions, _skipped = _filter_existing_characters(_precomputed_suggestions)
            st.session_state['_detected_characters'] = _suggestions
            st.session_state['_detected_characters_source'] = CHARACTER_AUTODETECT_SOURCE_AUTO
            st.session_state['now_entering'] = 'detect'
        else:
            # No usable precompute (missing / different book / empty because the
            # background detection had not finished): run detection live over the
            # freshly loaded page text, exactly like the working re-run button.
            # discard_previous=True drops any stale suggestions left from a
            # previous book so a leftover empty list can't block the fresh run.
            stage_character_redetect(
                st.session_state, source=CHARACTER_AUTODETECT_SOURCE_AUTO, discard_previous=True
            )

if 'current_page_number' not in st.session_state:
    st.session_state['current_page_number'] = 1

if 'now_entering' not in st.session_state:
    st.session_state['now_entering'] = 'text'

# Allow the book-edit menu's "Manage characters" item to open this page directly
# at the manage-characters view (issue #106). Apply the flag here — after the
# per-book init above, which resets now_entering to 'text' when the book changes
# — and clear it so it only takes effect on this entry.
if st.session_state.pop('_open_manage_characters', False):
    st.session_state['now_entering'] = 'manage'

# create_current_page_from_db()
st.session_state.current_page = st.session_state.book_pages_dict[
    st.session_state.current_page_number
]

st.header(EnterText.header)
st.write(EnterText.instruction)

col1, col2 = st.columns([3, 2])
previous_page = col1.button(EnterText.previous_page_button, width="stretch", key='b1', on_click=page_change, args=(-1,))
next_page = col2.button(EnterText.next_page_button, width="stretch", key='b2', on_click=page_change, args=(1,))

col1.write(
    EnterText.page_indicator
    % (st.session_state.current_page_number, st.session_state.current_book.page_count)
)
image_height = display_image()

if '_page_text_editing' not in st.session_state:
    st.session_state['_page_text_editing'] = None

# Entry column as a fragment (#78): positioned in col2 here; the fragment body
# creates its own container (see _entry_column_fragment for the full boundary
# rationale and what is deliberately not fragmented).
with col2:
    _entry_column_fragment(image_height)

# Finish/Submit sits directly below the text-entry column (#169); "Back to
# menu" is deliberately separated from it below by a visible gap so it is not
# adjacent/easy to mis-click.
save_button = st.button(
    EnterText.finish_submit_button, help=EnterText.save_help, width="stretch",
    key="enter_text_finish_submit_button",
)

st.write("")
st.write("")
st.divider()

return_button = st.button(
    EnterText.back_to_menu_button, width="stretch", key="enter_text_back_to_menu_button"
)

if return_button:
    # Auto-save the current page text before leaving for the menu (#152).
    _save_current_page_text()
    st.switch_page("./pages/book_edit_home.py")

if save_button:
    # Auto-save the current page text before opening the submit dialog (#152).
    _save_current_page_text()
    confirm_submit()


