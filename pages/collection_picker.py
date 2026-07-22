"""Results collection-picker (issue #75).

Sits between the landing page's "View results" button and the results dashboard.
Lets the user assemble / choose a book *collection* in one of three ways, then
hands the chosen collection to ``pages/results_dashboard.py`` via
``st.session_state['selected_collection']`` (a list of book references, exactly
what the dashboard's ``_book_id`` scoping expects):

1. SEARCH & SELECT — search the book database (reusing the ``book_dict`` search
   pattern from ``user_home.py``) and tick books into a custom collection.
2. PREDEFINED COLLECTIONS — browse/pick a named ``Collection`` from the Firestore
   ``collections`` collection, and (team/admin) save the current selection as a
   new predefined collection.
3. FROM PHOTOS — upload photo(s) of book covers/spines, send them to Claude
   vision to read the visible titles + authors, fuzzy-match them against the book
   database, and assemble a collection from the matches.

The running selection is held in ``st.session_state['collection_builder']`` as a
``{title: book_reference}`` dict so it persists across reruns and accumulates
across the three methods.
"""

import anthropic
import streamlit as st
from streamlit_option_menu import option_menu
from st_keyup import st_keyup

from text_content import Alerts, CollectionPicker, PhotoUpload
from utilities import (
    check_authentication_status,
    page_layout,
    navigate_to,
    fuzzy_match_name,
    extract_books_from_photos,
    get_s3_filesystem,
    get_anthropic_client,
)
from data_structures import Collection
from photo_upload import (
    get_upload_session_id,
    render_uploader,
    fetch_uploaded_photos,
    cleanup_prefix,
    reset_upload_session,
    render_go_to_phone,
)

# Shared "Upload here / Go to phone" chooser styling (#143).
_UPLOAD_MENU_STYLES = {
    "nav-link": {"font-size": "13px", "text-align": "center", "margin": "4px 2px", "--hover-color": "#eee"},
    "nav-link-selected": {"background-color": "green"},
}

check_authentication_status()
page_layout(current_page="./pages/collection_picker.py")

# Running selection: {book title: book DocumentReference}. Persisted across reruns
# and accumulated across the three picker methods.
builder = st.session_state.setdefault("collection_builder", {})

# Default scope on landing is ALL books (#163): an empty/absent
# 'selected_collection' is exactly what the dashboard treats as "every book", so
# a single click-through with nothing built shows all-books results. We do NOT
# materialise every book reference into the builder — the empty list is the whole
# mechanism.
st.session_state.setdefault("selected_collection", [])

# Widget key for the Search & Select left-hand quick-add dropdown (#163).
_SEARCH_MULTISELECT_KEY = "collection_search_multiselect"


def _sync_search_multiselect():
    """on_change for the search dropdown (#163): reconcile the running selection.

    The callback fires (with the user's new picks already in session state)
    BEFORE the script reruns, so we update ``builder`` here to match. Only titles
    that are selectable via this dropdown (i.e. valid ``book_dict`` keys) are
    touched, so books added by the other methods are never disturbed.
    """
    book_dict = st.session_state.get("book_dict", {})
    selected = set(st.session_state.get(_SEARCH_MULTISELECT_KEY, []))
    # Add newly picked titles.
    for title in selected:
        if title in book_dict:
            builder[title] = book_dict[title]
    # Drop titles that were unpicked (only those offered by this dropdown).
    for title in [t for t in list(builder) if t in book_dict]:
        if title not in selected:
            builder.pop(title, None)


def _book_id_to_title():
    """Reverse the session ``book_dict`` to map a book document id -> title.

    Lets us label a stored book reference (whether a DocumentReference or a plain
    id string) without an extra Firestore read.
    """
    return {
        ref.id: title
        for title, ref in st.session_state.get("book_dict", {}).items()
    }


def _book_title(ref, id_map):
    """Resolve a book reference (DocumentReference or id string) to its title."""
    if ref is None:
        return Alerts.no_matching_book
    book_id = ref if isinstance(ref, str) else getattr(ref, "id", None)
    return id_map.get(book_id, book_id or "")


# ---------------------------------------------------------------------------
# Current selection panel + handoff to the dashboard
# ---------------------------------------------------------------------------
def render_selection():
    st.subheader(CollectionPicker.selection_header)
    if not builder:
        st.info(CollectionPicker.selection_empty)
    else:
        st.write(CollectionPicker.selection_count.format(n=len(builder)))
        for title in list(builder.keys()):
            col_title, col_remove = st.columns([4, 1])
            col_title.write(f"- {title}")
            if col_remove.button(
                CollectionPicker.remove_book_button,
                key=f"collection_remove_{title}",
            ):
                builder.pop(title, None)
                st.rerun()
        if st.button(
            CollectionPicker.clear_selection_button, key="collection_clear_button"
        ):
            builder.clear()
            st.rerun()

    # Single "View results" button (#163). It is always enabled: with a built
    # selection it scopes to that collection; with nothing selected it hands off
    # an empty list, which the dashboard scopes to ALL books.
    if not builder:
        st.caption(CollectionPicker.view_results_all_hint)
    if st.button(
        CollectionPicker.view_results_button,
        key="collection_view_results_button",
        width="stretch",
    ):
        # Hand off in the exact shape the dashboard expects: a list of book
        # references held under 'selected_collection' (empty -> all books).
        st.session_state["selected_collection"] = list(builder.values())
        navigate_to("./pages/results_dashboard.py")


# ---------------------------------------------------------------------------
# Method 1: search & select
# ---------------------------------------------------------------------------
def method_search():
    st.subheader(CollectionPicker.search_header)
    book_dict = st.session_state.get("book_dict", {})
    titles = list(book_dict.keys())

    left, right = st.columns(2)

    # LEFT (#163): a dropdown of every book title as a faster add/remove path.
    # Its displayed value is refreshed from the builder each render so picks made
    # via the checkboxes / other methods (and the remove buttons) show here too;
    # the on_change callback reconciles the builder back the other way. Comparing
    # as sets means the refresh only fires on a genuine external divergence, so it
    # never clobbers an in-flight user interaction.
    with left:
        st.caption(CollectionPicker.search_dropdown_help)
        desired = [t for t in titles if t in builder]
        if set(st.session_state.get(_SEARCH_MULTISELECT_KEY, [])) != set(desired):
            st.session_state[_SEARCH_MULTISELECT_KEY] = desired
        st.multiselect(
            CollectionPicker.search_dropdown_label,
            options=titles,
            key=_SEARCH_MULTISELECT_KEY,
            on_change=_sync_search_multiselect,
        )

    # RIGHT: live-filter as the user types (mirrors user_home.book_search).
    with right:
        search_string = st_keyup(
            CollectionPicker.search_label,
            value="",
            debounce=300,
            key="collection_search_keyup",
        )
        if search_string and len(search_string) > 0:
            term = search_string.lower()
            matching_titles = [
                title for title in book_dict.keys() if term in title.lower()
            ]
            if not matching_titles:
                st.warning(Alerts.no_matching_book)
            else:
                st.write(
                    CollectionPicker.search_results_found.format(
                        count=len(matching_titles)
                    )
                )
                for title in matching_titles:
                    checked = st.checkbox(
                        CollectionPicker.add_book_checkbox.format(title=title),
                        value=title in builder,
                        key=f"collection_search_cb_{title}",
                    )
                    # Keep the running selection in step with the checkbox state.
                    if checked:
                        builder[title] = book_dict[title]
                    else:
                        builder.pop(title, None)


# ---------------------------------------------------------------------------
# Method 2: predefined collections
# ---------------------------------------------------------------------------
def _load_collections():
    """Stream the predefined collections from Firestore as ``Collection`` objects."""
    docs = st.session_state.firestore.get_all_documents_stream(
        collection="collections"
    )
    collections = []
    for doc in docs:
        data = doc.to_dict() or {}
        collections.append(Collection(db_object=data))
    return collections


def _render_create_form():
    st.divider()
    st.subheader(CollectionPicker.create_header)
    st.caption(CollectionPicker.create_help)

    if not builder:
        st.info(CollectionPicker.create_nothing_selected)
        return

    name = st.text_input(
        CollectionPicker.create_name_label, key="collection_create_name_input"
    )
    owner = st.text_input(
        CollectionPicker.create_owner_label, key="collection_create_owner_input"
    )
    if st.button(CollectionPicker.create_button, key="collection_create_button"):
        if not name.strip():
            st.warning(CollectionPicker.create_name_required)
            return
        new_collection = Collection()
        # Unregistered: these assignments don't write through yet — register()
        # performs the initial full save.
        new_collection.name = name.strip()
        new_collection.owner = owner.strip()
        new_collection.books = list(builder.values())
        if st.session_state.firestore.document_exists(
            collection="collections", doc_id=new_collection.document_id
        ):
            st.warning(CollectionPicker.create_exists)
            return
        new_collection.register()
        st.success(
            CollectionPicker.create_success.format(
                name=new_collection.name, n=len(new_collection.books)
            )
        )


# Sentinel option (#163): a virtual "All books" collection at the top of the
# predefined selectbox. It is not stored in Firestore — choosing it scopes the
# dashboard to every book via an empty ``selected_collection``.
_ALL_BOOKS_OPTION = "__all_books__"


def method_predefined():
    st.subheader(CollectionPicker.predefined_header)
    id_map = _book_id_to_title()
    collections = _load_collections()

    if not collections:
        # No named collections yet — the "All books" option below still lets the
        # user scope to the full corpus.
        st.info(CollectionPicker.predefined_none)

    # Options: the synthetic "All books" entry first, then index-based options so
    # two stored collections may share a display name.
    options = [_ALL_BOOKS_OPTION] + list(range(len(collections)))

    def _format_option(option):
        if option == _ALL_BOOKS_OPTION:
            return CollectionPicker.predefined_all_books_option
        return (
            f"{collections[option].name} ({collections[option].owner})"
            if collections[option].owner
            else collections[option].name
        )

    choice = st.selectbox(
        CollectionPicker.predefined_select_label,
        options=options,
        format_func=_format_option,
        key="collection_predefined_select",
    )

    if choice == _ALL_BOOKS_OPTION:
        st.caption(CollectionPicker.predefined_all_books_caption)
        if st.button(
            CollectionPicker.predefined_all_books_view_button,
            key="collection_predefined_all_books_button",
        ):
            # Empty selection -> the dashboard scopes to ALL books. We do NOT
            # materialise every reference into the builder.
            st.session_state["selected_collection"] = []
            navigate_to("./pages/results_dashboard.py")
    else:
        chosen = collections[choice]
        if chosen.owner:
            st.caption(
                CollectionPicker.predefined_owner_label.format(owner=chosen.owner)
            )

        refs = chosen.book_refs
        if not refs:
            st.info(CollectionPicker.predefined_empty_collection)
        else:
            st.write(CollectionPicker.predefined_books_label)
            for ref in refs:
                st.write(f"- {_book_title(ref, id_map)}")

            action_cols = st.columns(2)
            if action_cols[0].button(
                CollectionPicker.predefined_use_button,
                key="collection_predefined_use_button",
            ):
                for ref in refs:
                    builder[_book_title(ref, id_map)] = ref
                st.rerun()
            if action_cols[1].button(
                CollectionPicker.predefined_view_button,
                key="collection_predefined_view_button",
            ):
                st.session_state["selected_collection"] = list(refs)
                navigate_to("./pages/results_dashboard.py")

    _render_create_form()


# ---------------------------------------------------------------------------
# Method 3: from photos
# ---------------------------------------------------------------------------
def _render_photo_matches(extracted):
    if not extracted:
        st.info(CollectionPicker.photo_none_found)
        return

    book_dict = st.session_state.get("book_dict", {})
    titles = list(book_dict.keys())

    matched = {}  # extracted title -> matched database title
    unmatched = []
    for entry in extracted:
        extracted_title = entry["title"]
        match = fuzzy_match_name(extracted_title, titles)
        if match is not None and match in book_dict:
            matched[extracted_title] = match
        else:
            unmatched.append(extracted_title)

    if matched:
        st.write(CollectionPicker.photo_matched_header.format(count=len(matched)))
        for extracted_title, db_title in matched.items():
            st.write(
                "- "
                + CollectionPicker.photo_matched_item.format(
                    extracted=extracted_title, matched=db_title
                )
            )
        if st.button(
            CollectionPicker.photo_add_matched_button,
            key="collection_photo_add_button",
        ):
            for db_title in matched.values():
                builder[db_title] = book_dict[db_title]
            st.success(CollectionPicker.photo_added.format(n=len(matched)))

    if unmatched:
        st.write(
            CollectionPicker.photo_unmatched_header.format(count=len(unmatched))
        )
        for extracted_title in unmatched:
            st.write(f"- {extracted_title}")


def method_photo():
    st.subheader(CollectionPicker.photo_header)
    st.write(CollectionPicker.photo_instructions)

    ai_available = "ANTHROPIC_API_KEY" in st.secrets
    if not ai_available:
        st.warning(CollectionPicker.photo_no_api_key)

    # Direct browser-to-S3 upload (#118): replaces st.file_uploader so the native
    # photo picker no longer drops the websocket on mobile. Mint a stable temp
    # prefix (uploads/collection/{session_id}/) once, then let the user pick HOW to
    # fill it (#143): upload from this device, or scan a QR and upload from a phone.
    # Both land in the SAME prefix, so "Read books from photo(s)" reads it either
    # way. These cover/spine photos are transient — only used to read titles, NEVER
    # archived as book pages — so the temp prefix is cleaned up after extraction.
    session_id = get_upload_session_id("collection")

    upload_method = option_menu(
        None,
        [PhotoUpload.method_upload_here, PhotoUpload.method_go_to_phone],
        default_index=0,
        icons=["laptop", "phone"],
        menu_icon="cast",
        orientation="horizontal",
        key="collection_photo_upload_menu",
        styles=_UPLOAD_MENU_STYLES,
    )

    if upload_method == PhotoUpload.method_go_to_phone:
        render_go_to_phone("collection", session_id)
    else:
        st.write(CollectionPicker.photo_direct_upload_instructions)
        # Shared uploader recipe (#129/upload-duplication fix): cached URLs keep
        # the iframe HTML stable across reruns; the existing-slot seed makes
        # retries resume slots instead of duplicating photos.
        render_uploader(get_s3_filesystem(), "collection", session_id)

    if st.button(
        CollectionPicker.photo_extract_button,
        disabled=not ai_available,
        key="collection_photo_extract_button",
    ):
        fs = get_s3_filesystem()
        pages = fetch_uploaded_photos(fs, "collection", session_id)
        if not pages:
            st.warning(CollectionPicker.photo_no_photos_uploaded)
        else:
            images = [data for _, data in pages]
            client = get_anthropic_client()
            extracted = None
            try:
                with st.spinner(CollectionPicker.photo_extracting):
                    extracted = extract_books_from_photos(images, client)
            except anthropic.AnthropicError as exc:
                st.error(CollectionPicker.photo_extract_failed.format(error=exc))
            # Transient photos — drop the temp prefix + session id now they have
            # been read (they are never archived as book pages).
            cleanup_prefix(fs, "collection", session_id)
            reset_upload_session("collection")
            if extracted is not None:
                st.session_state["collection_photo_results"] = extracted

    results = st.session_state.get("collection_photo_results")
    if results is not None:
        _render_photo_matches(results)


# ---------------------------------------------------------------------------
# Page body
# ---------------------------------------------------------------------------
st.title(CollectionPicker.page_title)
st.write(CollectionPicker.intro)

st.divider()

# The method selector and search box are custom iframe components
# (streamlit_option_menu, st_keyup). Render them and the chosen method FIRST, at a
# stable position near the top of the page, then show the running-selection panel
# BELOW. Rendering the variable-height selection panel ABOVE these components made
# the panel lag one interaction behind the builder, and shifted the components on
# rerun so they blanked out / fell back to default state (#75 fixes).
selected_method = option_menu(
    None,
    [
        CollectionPicker.menu_search,
        CollectionPicker.menu_predefined,
        CollectionPicker.menu_photo,
    ],
    default_index=0,
    icons=["search", "collection", "camera"],
    menu_icon="cast",
    orientation="horizontal",
    key="collection_method_menu",
    styles={
        "container": {"flex-wrap": "wrap", "padding": "0.25rem 0"},
        "nav-link": {
            "font-size": "13px",
            "text-align": "center",
            "margin": "4px 2px",
            "--hover-color": "#eee",
        },
        "nav-link-selected": {"background-color": "green"},
    },
)

method_dict = {
    CollectionPicker.menu_search: method_search,
    CollectionPicker.menu_predefined: method_predefined,
    CollectionPicker.menu_photo: method_photo,
}

method_dict[selected_method]()

st.divider()

# Rendered AFTER the methods so the panel always reflects the just-updated builder
# (no one-interaction lag) and never shifts the iframe components above it.
render_selection()
