"""Edit-my-books page: in-progress, submitted and AI-generated books (#200/#202).

Editing is OWN-BOOKS-ONLY for ALL roles (#131, reversing part of #83): every
user — archivist, team member or admin — may edit only the books they entered
themselves, PLUS books owned by the special ``databot`` system user (the owner
of AI-generated books).

The page shows three sections (#202):

* **Books in progress** — the user's own ``entry_status == 'started'`` books,
  editable exactly as before.
* **Your submitted books** — the user's own SUBMITTED books (#200). Submitting
  locks a book, and during the pilot students who submitted every book saw an
  empty edit page with no explanation (diagnosed 2026-07-13: every "missing"
  book was simply ``entry_status == 'completed'``). Submitted books are now
  listed with their status, and — as long as a book is NOT yet validated and
  NOT currently being validated (proxied by recent validation edit_log
  activity, see ``utilities.validation_recently_active``) — the owner can
  REOPEN it, which sets ``entry_status`` back to ``'started'`` (write-through
  ``Field``) and records the change to the ``edit_log`` audit collection.
* **AI books to finish** — databot-owned books, shown REGARDLESS of
  ``entry_status`` so anyone can pick one up and finish/correct it (#131).
"""

import pandas as pd
import streamlit as st
from utilities import (
    page_layout,
    check_authentication_status,
    databot_entered_by,
    validation_recently_active,
    submitted_book_reopen_block,
    REOPEN_BLOCK_VALIDATED,
)
from text_content import Instructions, Alerts, ReviewBooks
from data_structures import Book, EditLog

check_authentication_status()
page_layout()

firestore = st.session_state.firestore
user_ref = firestore.username_to_doc_ref(st.session_state['username'])
databot_ref = databot_entered_by()

own_books = firestore.get_by_field(collection="books", field="entered_by", match=user_ref)
databot_books = firestore.get_by_field(collection="books", field="entered_by", match=databot_ref)

# Split own books into in-progress vs submitted. A missing/NaN entry_status
# (legacy records read via pandas) counts as 'started' — matching the Book
# field default — so an old book is editable rather than silently hidden.
if len(own_books):
    if 'entry_status' in own_books.columns:
        _status = own_books.entry_status.fillna('started')
    else:
        # Every fetched doc predates the field: all count as in progress.
        _status = pd.Series('started', index=own_books.index)
    in_progress_books = own_books.loc[_status == 'started']
    submitted_books = own_books.loc[_status != 'started']
else:
    in_progress_books = own_books
    submitted_books = own_books


def _book_from_row(books_df, title):
    """The Book for ``title``, reconstructed from its already-fetched row."""
    return Book(books_df[books_df.title == title].iloc[0])


def _open_for_editing(book):
    st.session_state['current_book'] = book
    st.session_state['current_book'].editing = True
    st.switch_page("./pages/book_edit_home.py")


st.header(ReviewBooks.header)

# Success flash from a just-completed reopen (st.success immediately before
# st.rerun() would never be seen).
_reopen_message = st.session_state.pop('_reopen_result', None)
if _reopen_message:
    st.success(_reopen_message)

if not (len(in_progress_books) or len(submitted_books) or len(databot_books)):
    st.warning(Alerts.no_user_books)
else:
    # --- Books in progress (the user's own, still editable) -------------------
    st.subheader(ReviewBooks.in_progress_header)
    if len(in_progress_books):
        st.write(Instructions.review_my_books)
        selected_title = st.selectbox(
            label=ReviewBooks.select_label,
            options=in_progress_books.title,
            key="review_books_select"
        )
        if st.button(ReviewBooks.edit_button, key="review_books_edit_button"):
            _open_for_editing(_book_from_row(in_progress_books, selected_title))
    else:
        st.info(ReviewBooks.none_in_progress)

    # --- The user's submitted books (#200) -------------------------------------
    if len(submitted_books):
        st.subheader(ReviewBooks.submitted_header)
        st.write(ReviewBooks.submitted_intro)
        submitted_title = st.selectbox(
            label=ReviewBooks.submitted_select_label,
            options=submitted_books.title,
            key="review_books_submitted_select",
        )
        submitted_book = _book_from_row(submitted_books, submitted_title)

        # "Currently being validated" proxy (#200): recent validation-context
        # audit records for this book. Only checked when not already validated
        # (the cheaper, decisive block). Single-field equality query — the
        # context/timestamp filtering happens in Python, so no composite index
        # is required.
        if submitted_book.validated:
            block = REOPEN_BLOCK_VALIDATED
        else:
            recent_activity = validation_recently_active(
                doc.to_dict() for doc in firestore.query_stream(
                    collection='edit_log',
                    field='book_id',
                    op='==',
                    value=submitted_book.document_id,
                )
            )
            block = submitted_book_reopen_block(
                {'validated': submitted_book.validated}, recent_activity
            )

        if block == REOPEN_BLOCK_VALIDATED:
            st.info(ReviewBooks.submitted_validated_info)
        elif block is not None:
            st.info(ReviewBooks.submitted_being_validated_info)
        elif st.button(ReviewBooks.reopen_button, key="review_books_reopen_button"):
            # Audit first (mirrors validation.py's log-then-write order), then
            # write the status back through the Field descriptor.
            EditLog.record(
                book_id=submitted_book.document_id,
                book_title=submitted_book.title,
                entity_type=EditLog.ENTITY_BOOK,
                entity_id=submitted_book.document_id,
                field='entry_status',
                old_value=submitted_book.entry_status,
                new_value='started',
                edited_by=user_ref,
                entered_by=submitted_book.entered_by,
                context=EditLog.CONTEXT_REOPEN,
            )
            submitted_book.entry_status = 'started'
            st.session_state['_reopen_result'] = ReviewBooks.reopen_success.format(
                title=submitted_book.title
            )
            st.rerun()

    # --- AI-generated (databot) books, for anyone to finish (#131/#202) --------
    if len(databot_books):
        st.subheader(ReviewBooks.databot_header)
        st.write(ReviewBooks.databot_intro)
        databot_title = st.selectbox(
            label=ReviewBooks.databot_select_label,
            options=databot_books.title,
            key="review_books_databot_select",
        )
        if st.button(ReviewBooks.databot_edit_button, key="review_books_databot_edit_button"):
            _open_for_editing(_book_from_row(databot_books, databot_title))

cancel_button = st.button(ReviewBooks.cancel_button, key="review_books_cancel_button")

if cancel_button:
    st.switch_page("./pages/user_home.py")
