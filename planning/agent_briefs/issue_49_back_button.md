# Brief: Issue #49 — In-app back button

**Branch:** `feat/49-back-button`  
**Model:** Opus  
**Read first:** `planning/agent_briefs/00_shared_context.md`

## Problem

Using the browser back button corrupts Streamlit session state because Streamlit is a single-page app — the browser navigates the URL but Streamlit doesn't know about it, leaving state in an inconsistent position.

## What to implement

A page history stack in session state that enables an in-app "← Back" button on all pages.

## Design

### Session state key: `_page_history`

A list of page paths (strings) maintained as a stack. The current page is not on the stack — only previous pages are.

When navigating forward (e.g. `st.switch_page("./pages/add_book.py")`), push the current page onto the stack before switching.

### `utilities.py` — add two helpers

```python
def navigate_to(page_path):
    """Navigate to a page, pushing the current page onto the back-history stack."""
    current = st.session_state.get('_current_page', None)
    if current:
        history = st.session_state.get('_page_history', [])
        history.append(current)
        st.session_state['_page_history'] = history
    st.switch_page(page_path)


def go_back(fallback="./pages/user_home.py"):
    """Navigate to the previous page in the history stack."""
    history = st.session_state.get('_page_history', [])
    if history:
        previous = history.pop()
        st.session_state['_page_history'] = history
        st.switch_page(previous)
    else:
        st.switch_page(fallback)
```

### `page_layout()` in `utilities.py` — set current page

Add a way for each page to register itself. This is tricky in Streamlit because pages don't easily know their own path. Use `st.context.headers` or a convention where each page sets `st.session_state['_current_page']` at the top.

The simplest approach: add an optional `current_page` parameter to `page_layout()`:

```python
def page_layout(current_page=None):
    st.set_page_config(initial_sidebar_state="collapsed", layout="wide")
    if current_page:
        st.session_state['_current_page'] = current_page
    # ... rest of page_layout ...
```

Then each page calls `page_layout(current_page="./pages/add_book.py")`.

### Add back button to `page_layout()`

In `page_layout()`, after the sidebar links, add a back button in the sidebar:

```python
history = st.session_state.get('_page_history', [])
if history:
    if st.sidebar.button("← Back"):
        go_back()
```

### Update `st.switch_page` calls throughout the codebase

Replace direct `st.switch_page(...)` calls with `navigate_to(...)` where appropriate — specifically on forward navigation (going deeper into a flow). Calls that are explicitly "cancel" or "go home" type actions can continue using `st.switch_page` directly.

**Key pages to update:** `pages/add_book.py`, `pages/add_author.py`, `pages/add_illustrator.py`, `pages/add_publisher.py`, `pages/book_data_entry.py`, `pages/confirm_entry.py`, `pages/enter_text.py`

## Important constraints

- History should be cleared when the user navigates to `user_home.py` (treat it as a root)
- History should be cleared on logout
- Don't go more than 10 levels deep (cap the list at 10 entries)
- Pages that use `on_click` callbacks with `st.switch_page` will need different handling — check each page

## Verification

Read all pages before making changes. This touches many files so be systematic.

```
.venv/bin/python -m py_compile utilities.py pages/add_book.py pages/add_author.py pages/add_illustrator.py pages/add_publisher.py pages/book_data_entry.py pages/confirm_entry.py pages/enter_text.py
```
