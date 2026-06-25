# Brief: Issue #69 — Admin data download page

**Branch:** `feat/69-admin-downloads`  
**Model:** Sonnet  
**Read first:** `planning/agent_briefs/00_shared_context.md`

## What to implement

A new admin-only page `pages/admin.py` with two data download buttons, and a link to the existing validation page. Wire it into the sidebar for admin users.

## Step 1 — Create `pages/admin.py`

```python
import io
import zipfile
import csv
import streamlit as st
from utilities import page_layout, check_authentication_status, FirestoreWrapper

check_authentication_status()

if not st.session_state.get('admin', False):
    st.error("This page is only accessible to admin users.")
    st.stop()

page_layout()

st.title("Admin")

# Link to validation page
st.page_link("pages/validation.py", label="→ Go to data validation")
st.divider()
```

### Download 1 — User list CSV

Query the `users` Firestore collection. Export only confirmed users (`is_confirmed == True`).

Columns: `email`, `name`, `newsletter_opt_in`, `account_creation_date`, `trust_rating`

```python
st.header("User data")
st.write("Download email addresses and newsletter opt-in status for confirmed users.")

if st.button("Prepare user data download"):
    db = FirestoreWrapper().connect_user(auth=False)
    users = db.collection('users').where('is_confirmed', '==', True).stream()

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=['email', 'name', 'newsletter_opt_in', 'account_creation_date', 'trust_rating'])
    writer.writeheader()
    for user in users:
        d = user.to_dict()
        writer.writerow({
            'email': d.get('username', ''),
            'name': d.get('name', ''),
            'newsletter_opt_in': d.get('newsletter_opt_in', False),
            'account_creation_date': str(d.get('account_creation_date', '')),
            'trust_rating': d.get('trust_rating', 0),
        })

    st.download_button(
        label="⬇ Download user list (CSV)",
        data=buf.getvalue().encode('utf-8'),
        file_name="fairtales_users.csv",
        mime="text/csv"
    )
```

### Download 2 — Book database ZIP of CSVs

One CSV per Firestore collection. Collections: `books`, `authors`, `illustrators`, `publishers`, `characters`, `pages`, `aliases`.

For Firestore DocumentReference fields, convert to the document ID string (`.id`) rather than the raw reference object, since the raw reference is not CSV-serialisable.

```python
st.header("Book database export")
st.write("Download a ZIP of CSV files — one per collection — for research use. May take a moment for large datasets.")

if st.button("Prepare book data download"):
    db = FirestoreWrapper(auth=True)._connect(auth=False)  # unauthenticated read
    # Use db = FirestoreWrapper().connect_book(auth=False) instead
    
    collections = ['books', 'authors', 'illustrators', 'publishers', 'characters', 'pages', 'aliases']
    
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for collection_name in collections:
            try:
                docs = st.session_state.firestore.get_all_documents_stream(collection=collection_name)
                rows = []
                for doc in docs:
                    d = doc.to_dict()
                    # Resolve Firestore references to their document ID
                    clean = {}
                    for k, v in d.items():
                        try:
                            # DocumentReference has an .id attribute
                            clean[k] = v.id if hasattr(v, 'id') else str(v) if v is not None else ''
                        except Exception:
                            clean[k] = str(v) if v is not None else ''
                    rows.append(clean)
                
                if rows:
                    csv_buf = io.StringIO()
                    fieldnames = list(rows[0].keys())
                    writer = csv.DictWriter(csv_buf, fieldnames=fieldnames, extrasaction='ignore')
                    writer.writeheader()
                    writer.writerows(rows)
                    zf.writestr(f"{collection_name}.csv", csv_buf.getvalue())
            except Exception as e:
                # If a collection is empty or errors, include an error note
                zf.writestr(f"{collection_name}_error.txt", str(e))
    
    st.download_button(
        label="⬇ Download book database (ZIP of CSVs)",
        data=zip_buf.getvalue(),
        file_name="fairtales_book_data.zip",
        mime="application/zip"
    )
```

**Important:** Use `st.session_state.firestore.get_all_documents_stream(collection=collection_name)` for the book data (it uses the authenticated connection). Use a separate unauthenticated `FirestoreWrapper().connect_user(auth=False)` for the user data (different trust level).

## Step 2 — Wire into navigation

**`utilities.py`** — in `page_layout()`, add admin sidebar link:
```python
if 'admin' in st.session_state and st.session_state['admin']:
    st.sidebar.page_link("pages/admin.py", label="Admin")
    # Keep validation.py linked from the admin page, not the sidebar directly
```

Remove the existing `st.sidebar.page_link("pages/validation.py", ...)` line from `page_layout()` since validation is now reached via the admin page.

**`Home.py`** — in `navigate_pages()`, add `pages/admin.py` to the "Menu" group for admin users (check how `pages/validation.py` is currently added and follow the same pattern).

## Verification

```
.venv/bin/python -m py_compile pages/admin.py utilities.py Home.py
```
