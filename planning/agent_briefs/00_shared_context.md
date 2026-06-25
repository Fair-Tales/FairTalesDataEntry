# Shared Context for All Agents

## Project overview

FairTalesDataEntry is a Streamlit 1.58.0 data entry tool for a children's book diversity research project. Archivists use it to photograph books and enter metadata (title, author, illustrator, publisher, themes, characters). Data is stored in Google Cloud Firestore with images in AWS S3.

Working branch: **claude-dev**. Create a new branch from claude-dev for your work (e.g. `git checkout -b feat/62-form-validation`). Commit clearly. Do NOT push.

Python venv: `.venv/` — use `.venv/bin/python` and `.venv/bin/pip`.

## Key architectural patterns

### Every Streamlit page starts with:
```python
from utilities import page_layout, check_authentication_status
check_authentication_status()   # redirects to login if not logged in
# ... functions and dialogs defined here ...
page_layout()                   # calls st.set_page_config(layout="wide", ...)
```

### Firestore wrapper
`st.session_state.firestore` is a `FirestoreWrapper` instance (see `utilities.py`).
- `firestore.get_by_field(collection, field, match)` → pandas DataFrame
- `firestore.get_by_reference(collection, document_ref)` → Firestore document
- `firestore.get_all_documents_stream(collection)` → iterator of Firestore documents
- `firestore.update_field(collection, document, field, value)`
- `firestore.document_exists(collection, doc_id)` → bool

### Data structures
All inherit from `DataStructureBase` (see `data_structures/base_structure.py`).
- Fields are `Field()` descriptors — assigning to them auto-saves to Firestore when `is_registered=True`
- `obj.register()` sets `entered_by`, `datetime_created`, `is_registered=True`, and calls `save_to_db()`
- `obj.to_dict()` returns a plain dict of fields
- `obj.document_id` is the Firestore document ID (e.g. for Book it's `title.lower().replace(" ", "_")`)
- Reference fields (author, illustrator, publisher) are stored as Firestore DocumentReferences
- When reading back from Firestore, a reference field `.get().id` gives the document ID string

### Session state conventions
- `st.session_state.firestore` — FirestoreWrapper
- `st.session_state['username']` — logged-in user's email
- `st.session_state.get('admin', False)` — True if admin user
- `st.session_state['current_book']` — current Book object
- `st.session_state['author_dict']` — {name: firestore_ref} for all authors
- `st.session_state['publisher_dict']` — {name: firestore_ref} for all publishers
- `st.session_state['illustrator_dict']` — {name: firestore_ref} for all illustrators
- `st.session_state['book_dict']` — {title: firestore_ref} for all books

### Text content
All UI strings live in `text_content/` and are imported via `from text_content import Alerts, Instructions, BookForm` etc. AI prompts are in `text_content/ai_prompts.py` as `AIPrompts` class attributes.

### Navigation
`Home.py` registers all pages via `st.navigation()`. The sidebar is populated by `page_layout()` in `utilities.py`. Admin-only pages are added conditionally on `st.session_state.get('admin', False)`.

## File map

| File | Purpose |
|------|---------|
| `Home.py` | App entry point, session initialisation, page routing |
| `utilities.py` | FirestoreWrapper, page_layout(), check_authentication_status(), helper functions |
| `data_structures/base_structure.py` | DataStructureBase, Field descriptor |
| `data_structures/book.py` | Book data structure + form_content() |
| `data_structures/author.py` | Author data structure |
| `data_structures/illustrator.py` | Illustrator data structure |
| `data_structures/publisher.py` | Publisher data structure |
| `data_structures/page.py` | Page (book page) data structure |
| `data_structures/character.py` | Character data structure |
| `pages/register_user.py` | Registration form (has validate_user_details() as a pattern) |
| `pages/book_edit_home.py` | Book editing menu |
| `pages/enter_text.py` | Text entry per book page |
| `pages/validation.py` | Admin-only stub page |
| `text_content/alerts.py` | Alerts class — warning/error messages |
| `text_content/forms.py` | Form classes — BookForm, AuthorForm, etc. |
| `text_content/instructions.py` | Instructions class — help text |
| `text_content/ai_prompts.py` | AIPrompts class — Claude API prompts |

## Code style

- No docstrings or inline comments unless explaining a non-obvious invariant
- UI strings go in `text_content/` — don't hardcode long strings in pages
- Use `st.warning()` for validation errors, `st.error()` for serious errors
- Always syntax-check with `.venv/bin/python -m py_compile <file>` before committing
- Commit message: one clear sentence of what changed and why
