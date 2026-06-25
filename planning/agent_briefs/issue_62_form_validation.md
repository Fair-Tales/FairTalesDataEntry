# Brief: Issue #62 — Form validation (required fields)

**Branch:** `feat/62-form-validation`  
**Model:** Sonnet  
**Read first:** `planning/agent_briefs/00_shared_context.md`

## What to implement

Validate required fields before form submission in four data structures. If a required field is blank (after stripping whitespace), show `st.warning()` and return without calling `register()` or `save_to_db()`.

The pattern to follow is `validate_user_details()` in `pages/register_user.py` — read it first.

## Specific changes

### 1. Book form — `data_structures/book.py`, function `form_content()`

In the `if submitted:` block, before `st.session_state['current_book'] = self`, add:

```python
if not _title.strip():
    st.warning("Book title is required.")
    return
```

Title is the Firestore document ID — a blank title creates an invalid record.

### 2. Author form — `data_structures/author.py`, `Author.to_form()`

After the `submitted = st.form_submit_button(...)` line, validate:
- `self.forename` (after `.strip()`) is non-empty
- `self.surname` (after `.strip()`) is non-empty

If either is blank, show `st.warning("Author first name and surname are required.")` and return.

### 3. Illustrator form — `data_structures/illustrator.py`, `Illustrator.to_form()`

Same pattern as Author: validate forename and surname are non-empty before proceeding to save.

### 4. Publisher form — `data_structures/publisher.py`, `Publisher.to_form()`

Validate `self.name.strip()` is non-empty before proceeding. Show `st.warning("Publisher name is required.")` if blank.

### 5. Character form — `pages/add_character.py`, `new_character()` function

Read this file to understand the structure. Validate that the character name field is non-empty before calling `st.session_state['current_character'].register()` or switching pages.

## What NOT to change

- Do not change `pages/register_user.py` — it already has validation
- Do not add validation to the text entry or alias forms
- Do not add any imports not already in the files

## Verification

After implementing, syntax-check all changed files:
```
.venv/bin/python -m py_compile data_structures/book.py data_structures/author.py data_structures/illustrator.py data_structures/publisher.py pages/add_character.py
```
