# Brief: Issue #57 — Code quality: specific technical debt items

**Branch:** `feat/57-code-quality`  
**Model:** Sonnet  
**Read first:** `planning/agent_briefs/00_shared_context.md`

## What to implement

Three targeted fixes. Read each relevant file before making changes.

---

## Fix 1 — Whitespace stripping on name inputs

**Problem:** If a user types " Bob " with leading/trailing spaces, it gets saved as-is. For Author/Illustrator the name becomes the Firestore document ID (`bob__smith` vs `bob_smith`) creating duplicates.

**Where:** In the `to_form()` method of each data structure, strip whitespace before assigning values.

**`data_structures/author.py`** — in `to_form()`:
```python
# Before (approximately):
self.forename = st.text_input("First name", value=self.forename)
self.surname = st.text_input("Surname", value=self.surname)

# After:
self.forename = st.text_input("First name", value=self.forename).strip()
self.surname = st.text_input("Surname", value=self.surname).strip()
```

Apply the same `.strip()` to `data_structures/illustrator.py` (forename, surname) and `data_structures/publisher.py` (name field).

For `data_structures/book.py`, in `form_content()`, strip `_title`:
```python
_title = st.text_input("Title", value=self.title).strip()
```

---

## Fix 2 — Alias form not clearing after submit

**Problem:** After adding an alias in `pages/enter_text.py`, the alias form fields don't clear, leaving stale values if the user tries to add another.

**Read:** `pages/enter_text.py` — specifically `alias_entry()` and `adding_text()`.

The `Alias` object is created fresh each time `alias_entry()` is called (`st.session_state['current_alias'] = Alias(...)`), which should reset the form. Check whether there is a session state key holding stale alias data. Look for `_alias_*` keys similar to `_page_text_editing`.

If the form is inside a `with st.form(...)` block, Streamlit should clear it automatically on submit. If it's not, wrap it. Also ensure `adding_text()` (the "cancel" callback) clears any stale alias session state.

The fix is likely: after a successful alias save in the form submit handler, call `st.session_state['current_alias'] = Alias(book=st.session_state['current_book'].title)` to reset it. Also check if `_page_text_editing` needs clearing when switching modes.

---

## Fix 3 — Replace `check_user_exists` with `document_exists`

**Problem:** `utilities.py` has a `check_user_exists(username)` function that opens its own Firestore connection (bypassing `FirestoreWrapper`) and is a less general solution than `FirestoreWrapper.document_exists()` which already exists.

**Read:** `utilities.py` — read both `check_user_exists()` and `FirestoreWrapper.document_exists()`.

**Where it's called:** `pages/register_user.py` calls `check_user_exists(_username)`.

**Fix:**
1. In `pages/register_user.py`, replace:
   ```python
   from utilities import (..., check_user_exists, ...)
   ...
   if check_user_exists(_username):
   ```
   with:
   ```python
   db = FirestoreWrapper().connect_user(auth=False)
   # OR use the existing firestore wrapper if available in session state
   ```
   
   Actually the cleanest fix: in `register_user()`, use `FirestoreWrapper` directly:
   ```python
   if FirestoreWrapper().connect_user(auth=False).collection('users').document(_username).get().exists:
       st.warning(Alerts.user_exists)
       return
   ```
   
   Or, since `FirestoreWrapper` has `document_exists(collection, doc_id)` via `connect_book()` — but users are in a different collection. Check whether `document_exists` can be used for the users collection. If not, the simplest clean fix is just to do the check inline in `register_user()` and remove the `check_user_exists` function from `utilities.py` after confirming nothing else calls it.

2. Remove `check_user_exists` from `utilities.py` after verifying it's not called anywhere else:
   ```
   grep -r "check_user_exists" --include="*.py" .
   ```

## Verification

```
.venv/bin/python -m py_compile data_structures/author.py data_structures/illustrator.py data_structures/publisher.py data_structures/book.py pages/enter_text.py pages/register_user.py utilities.py
```
