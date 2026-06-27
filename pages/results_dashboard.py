import pandas as pd
import streamlit as st

from utilities import check_authentication_status, page_layout
from text_content import CharacterForm, ResultsDashboard

check_authentication_status()

page_layout(current_page="./pages/results_dashboard.py")

st.title(ResultsDashboard.page_title)
st.write(ResultsDashboard.intro)


def _book_id(book_ref):
    """Normalise a book reference (DocumentReference or id string) to its id.

    A collection (see #75) may store either Firestore DocumentReferences or plain
    document-id strings, and a character's ``book`` field is a DocumentReference.
    Returning the id for both lets us compare them directly.
    """
    if book_ref is None:
        return None
    if isinstance(book_ref, str):
        return book_ref
    return getattr(book_ref, "id", None)


# Determine the scope. Default is all books; if a collection of book
# references/ids is held in session state, restrict to characters in that set.
selected_collection = st.session_state.get("selected_collection")
scope_ids = None
if selected_collection:
    scope_ids = {_book_id(item) for item in selected_collection}
    scope_ids.discard(None)

# Single read of the characters collection, aggregated in Python afterwards.
characters = st.session_state.firestore.get_all_documents_stream(
    collection="characters"
)

gender_options = CharacterForm.gender_options
human_counts = {gender: 0 for gender in gender_options}
nonhuman_counts = {gender: 0 for gender in gender_options}

for character in characters:
    data = character.to_dict()
    gender = data.get("gender")
    if gender not in gender_options:
        # Skip characters with no/unknown gender so the chart only shows the
        # four defined categories.
        continue
    if scope_ids is not None and _book_id(data.get("book")) not in scope_ids:
        continue
    if data.get("human", True):
        human_counts[gender] += 1
    else:
        nonhuman_counts[gender] += 1

combined_counts = {
    gender: human_counts[gender] + nonhuman_counts[gender]
    for gender in gender_options
}
total_in_scope = sum(combined_counts.values())

if scope_ids:
    st.caption(ResultsDashboard.scope_collection_caption.format(n=len(scope_ids)))
else:
    st.caption(ResultsDashboard.scope_all_caption)


def _render_chart(title, counts):
    st.subheader(title)
    chart_data = pd.DataFrame(
        {ResultsDashboard.count_column_label: [counts[g] for g in gender_options]},
        index=gender_options,
    )
    chart_data.index.name = ResultsDashboard.gender_column_label
    st.bar_chart(chart_data)


if total_in_scope == 0:
    st.info(ResultsDashboard.empty_message)
else:
    _render_chart(ResultsDashboard.combined_chart_title, combined_counts)
    _render_chart(ResultsDashboard.human_chart_title, human_counts)
    _render_chart(ResultsDashboard.nonhuman_chart_title, nonhuman_counts)

st.divider()
st.subheader(ResultsDashboard.work_in_progress_header)
st.write(ResultsDashboard.work_in_progress_intro)
for item in ResultsDashboard.work_in_progress_items:
    st.markdown(f"- {item}")
