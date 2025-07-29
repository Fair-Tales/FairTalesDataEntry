import streamlit as st
from utilities import page_layout, check_authentication_status
from text_content import Instructions, Alerts
from data_structures import Book

check_authentication_status()
page_layout()

#db_user = st.session_state.firestore.connect_user()
#user_ref = db_user.document(
#    f"users/{st.session_state['username']}"
#)

my_books = st.session_state.firestore.get_by_field(
    collection="books",
    field="entered_by",
    match=st.session_state['username']
)

if len(my_books) == 0:
    st.warning(Alerts.no_user_books)
else:
    my_books = my_books.loc[my_books.entry_status == 'started']
    st.header("Review my books")
    st.write(Instructions.review_my_books)
    selected_title = st.selectbox(
        label="My entered books:",
        options=my_books.title
    )

    selected_book = Book(
        my_books[my_books.title == selected_title].iloc[0]
    )

    edit_button = st.button("Edit this book.")
    if edit_button:
        st.session_state['current_book'] = selected_book
        st.session_state['current_book'].editing = True
        st.switch_page("./pages/book_edit_home.py")

cancel_button = st.button("Cancel editing books.")

if cancel_button:
    st.switch_page("./pages/user_home.py")


#with st.form('edit_book_form'):
#    st.subheader(book['title'] + ':')
#    st.session_state['edited_book'] = st.data_editor(pd.DataFrame([book]))
#    st.session_state['edited_characters'] = st.data_editor(characters)
#    confirm = st.form_submit_button('confirm')

#    if confirm:
#        print(st.session_state['edited_book'])
#        print(st.session_state['edited_characters'])
