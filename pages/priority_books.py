import streamlit as st
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from text_content import PriorityBooks as T
from utilities import check_authentication_status, page_layout, FirestoreWrapper

check_authentication_status()

page_layout(current_page="./pages/priority_books.py")

st.title(T.page_title)
st.write(T.intro)
st.divider()

firestore = FirestoreWrapper(auth=False)
db = firestore.connect_book(auth=False)
collection_ref = db.collection("priority_books")

# ── Read current list ────────────────────────────────────────────────────────
docs = list(collection_ref.order_by("added_at").stream())

if not docs:
    st.info(T.empty_list)
else:
    for doc in docs:
        data = doc.to_dict()
        title = data.get("title", "")
        author = data.get("author", "")
        notes = data.get("notes", "")

        header = f"**{title}**"
        if author:
            header += f"  —  {author}"

        col_text, col_btn = st.columns([10, 1])
        with col_text:
            st.markdown(header)
            if notes:
                st.caption(notes)

        # Remove button shown only to admins; rendered in the right column
        if st.session_state.get("admin", False):
            with col_btn:
                if st.button(T.admin_remove_button, key=f"remove_{doc.id}"):
                    collection_ref.document(doc.id).delete()
                    st.success(T.remove_success)
                    st.rerun()

# ── Admin add form ───────────────────────────────────────────────────────────
if st.session_state.get("admin", False):
    st.divider()
    st.subheader(T.admin_section_header)

    with st.form("add_priority_book", clear_on_submit=True):
        new_title = st.text_input(T.admin_add_label)
        new_author = st.text_input(T.admin_author_label)
        new_notes = st.text_input(T.admin_notes_label)
        submitted = st.form_submit_button(T.admin_add_button)

    if submitted:
        if not new_title.strip():
            st.error(T.add_error_empty)
        else:
            collection_ref.add({
                "title": new_title.strip(),
                "author": new_author.strip(),
                "notes": new_notes.strip(),
                "added_at": SERVER_TIMESTAMP,
            })
            st.success(T.add_success)
            st.rerun()
