# Brief: Issue #65 — Automated theme detection from extracted book text

**Branch:** `feat/65-theme-detection`  
**Model:** Sonnet  
**Read first:** `planning/agent_briefs/00_shared_context.md`

## What to implement

After a book's text has been entered, allow an admin or archivist to click "Suggest themes" which sends all story-page text to Claude Sonnet and pre-populates the theme checkboxes. Suggestions ADD to existing selections — they never uncheck a theme that is already selected.

## The exact theme list

From `text_content/forms.py` `BookForm.theme_options` (read this file):

```python
theme_options = {
    'disability': 'Disability',
    'race_ethnicity': 'Race/Ethnicity',
    'sexuality': 'Sexuality',
    'religion_spirituality': 'Religion/Spirituality',
    'gender': 'Gender',
    'social_class': 'Social class',
    'age': 'Age'
}
```

These are the Firestore field names (keys) and display labels (values).

## Step 1 — Add the prompt to `text_content/ai_prompts.py`

Add a new class attribute `theme_detection`:

```python
theme_detection = """\
Below is the full text of a children's picture book. Identify which of the \
following themes are EXPLICITLY represented in the text (not merely imaginable \
— they must be clearly present in the story):

Themes to check:
- disability: characters with physical or cognitive disabilities
- race_ethnicity: race, ethnicity, or cultural identity is part of the story
- sexuality: sexual orientation or LGBTQ+ identity is part of the story
- religion_spirituality: religion, faith, or spirituality is part of the story
- gender: gender identity or gender roles are explicitly addressed
- social_class: socioeconomic status or class difference is part of the story
- age: age-related themes (e.g. ageing, generational difference) are present

Respond with valid JSON only:
{
  "disability": true or false,
  "race_ethnicity": true or false,
  "sexuality": true or false,
  "religion_spirituality": true or false,
  "gender": true or false,
  "social_class": true or false,
  "age": true or false,
  "reasoning": "One sentence explaining which themes you found and why."
}

Book text:
"""
```

## Step 2 — Add the suggest_themes function and button to `pages/book_edit_home.py`

Read `pages/book_edit_home.py` in full first. It has a menu with options including "Edit metadata".

Add a new function and import anthropic at the top of the file:

```python
import json
import anthropic
```

Add this function (before the page-level code):

```python
def suggest_themes():
    """Call Claude Sonnet with all story-page text and suggest themes."""
    if 'ANTHROPIC_API_KEY' not in st.secrets:
        st.warning("AI theme suggestion requires an Anthropic API key.")
        return

    # Gather all story page text from Firestore
    book_id = st.session_state.current_book.document_id
    page_count = st.session_state.current_book.page_count
    story_text_parts = []

    for page_num in range(1, page_count + 1):
        doc = st.session_state.firestore.get_by_reference(
            collection='pages',
            document_ref=f"{book_id}_{page_num}"
        )
        if doc.exists:
            data = doc.to_dict()
            if data.get('contains_story') and data.get('text', '').strip():
                story_text_parts.append(data['text'].strip())

    if not story_text_parts:
        st.warning("No story text found. Please enter text for the book pages first.")
        return

    full_text = "\n\n".join(story_text_parts)
    prompt = AIPrompts.theme_detection + full_text

    with st.spinner("Analysing book text for themes..."):
        try:
            client = anthropic.Anthropic(api_key=st.secrets['ANTHROPIC_API_KEY'])
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)
        except Exception as e:
            st.error(f"Theme detection failed: {e}")
            return

    # Apply suggestions — only ADD, never remove existing selections
    from text_content import BookForm
    theme_keys = list(BookForm.theme_options.keys())
    added = []
    for key in theme_keys:
        if result.get(key) and not getattr(st.session_state.current_book, key):
            setattr(st.session_state.current_book, key, True)
            added.append(BookForm.theme_options[key])

    if added:
        st.success(f"Themes suggested: {', '.join(added)}. Reasoning: {result.get('reasoning', '')}")
    else:
        st.info(f"No new themes to add. Reasoning: {result.get('reasoning', '')}")
```

Then find the right place in the page UI to add the button. In `book_edit_home.py`, the "Edit metadata" option calls a function. Add the "Suggest themes" button within or near the metadata editing section. Look at how the existing navigation/options are structured and place a `st.button("🏷 Suggest themes")` that calls `suggest_themes()` when clicked.

## Imports to add

At the top of `book_edit_home.py`:
```python
import json
import anthropic
from text_content import AIPrompts
```

(Check what's already imported — don't duplicate)

## Verification

```
.venv/bin/python -m py_compile pages/book_edit_home.py text_content/ai_prompts.py
```
