# Changelog – Grace Dev

_Original document written by Grace at the end of her summer development work.
Converted from Word document to Markdown for version control._

---

## Changes

- Select boxes instead of number input for year inputs; includes options for unknown years (currently maps to `None`)
- `Home.py` no longer rendered — stops `initialise()` running multiple times
- Navigation change: using Streamlit `Page` elements (`st.navigation()`)
- Sidebar only has three navigable pages
- Automatic redirection to login page when not logged in
- Separate login page created
- Logout functionality
- Register information merged with login page using option menu to toggle
- Register page hyperlinks clickable and spelling fix
- Page configs moved to single `page_layout()` function in utilities; also includes sidebar config
- Separation of book and user data — unfortunately the `entered_by` field can no longer be stored as a reference (using text username instead). Ensure users can't change their name — or assign a unique ID to user.
- Author search functionality, with ability to search their books, formatted so most data is hidden
- Full illustrator and publisher functionality: they can be created when adding a book, saved by reference like authors
- Authors, illustrators and publishers can all be added at the same time and choices are remembered
- Admin functionality created which gives wider access — currently need to manually give admin status within Firestore
- Dummy validation page — only accessible with admin account

---

_\* Note (Chris): Check two new database names — confirm w/Grace. Collections created in code?
This annotation refers to Grace's planned split into two Firestore databases (user credentials + book data),
which was subsequently reverted. See DECISIONS.md #001._
