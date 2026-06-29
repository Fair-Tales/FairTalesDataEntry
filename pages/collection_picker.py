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

from text_content import Alerts, CollectionPicker
from utilities import (
    check_authentication_status,
    page_layout,
    navigate_to,
    fuzzy_match_name,
    extract_books_from_photos,
)
from data_structures import Collection

check_authentication_status()
page_layout(current_page="./pages/collection_picker.py")

# Running selection: {book title: book DocumentReference}. Persisted across reruns
# and accumulated across the three picker methods.
builder = st.session_state.setdefault("collection_builder", {})


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

    view_cols = st.columns(2)
    if view_cols[0].button(
        CollectionPicker.view_results_button,
        key="collection_view_results_button",
        disabled=not builder,
        width="stretch",
    ):
        # Hand off in the exact shape the dashboard expects: a list of book
        # references held under 'selected_collection'.
        st.session_state["selected_collection"] = list(builder.values())
        navigate_to("./pages/results_dashboard.py")
    if view_cols[1].button(
        CollectionPicker.view_all_button,
        key="collection_view_all_button",
        width="stretch",
    ):
        # An empty/absent selection makes the dashboard scope to ALL books.
        st.session_state["selected_collection"] = []
        navigate_to("./pages/results_dashboard.py")


# ---------------------------------------------------------------------------
# Method 1: search & select
# ---------------------------------------------------------------------------
def method_search():
    st.subheader(CollectionPicker.search_header)
    book_dict = st.session_state.get("book_dict", {})

    # Live-filter as the user types (mirrors user_home.book_search).
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


def method_predefined():
    st.subheader(CollectionPicker.predefined_header)
    id_map = _book_id_to_title()
    collections = _load_collections()

    if not collections:
        st.info(CollectionPicker.predefined_none)
    else:
        # Index-based options so two collections may share a display name.
        choice = st.selectbox(
            CollectionPicker.predefined_select_label,
            options=range(len(collections)),
            format_func=lambda i: (
                f"{collections[i].name} ({collections[i].owner})"
                if collections[i].owner
                else collections[i].name
            ),
            key="collection_predefined_select",
        )
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

    uploaded_files = st.file_uploader(
        CollectionPicker.photo_upload_label,
        accept_multiple_files=True,
        key="collection_photo_uploader",
    )

    if uploaded_files:
        if st.button(
            CollectionPicker.photo_extract_button,
            disabled=not ai_available,
            key="collection_photo_extract_button",
        ):
            images = [file.getvalue() for file in uploaded_files]
            client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
            extracted = None
            try:
                with st.spinner(CollectionPicker.photo_extracting):
                    extracted = extract_books_from_photos(images, client)
            except anthropic.AnthropicError as exc:
                st.error(CollectionPicker.photo_extract_failed.format(error=exc))
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
