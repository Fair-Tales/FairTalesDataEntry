# Brief: Issue #63 — ISBN lookup via Google Books API

**Branch:** `feat/63-isbn-lookup`  
**Model:** Opus  
**Read first:** `planning/agent_briefs/00_shared_context.md`

## What to implement

After photos are uploaded and the title page has been processed, extract an ISBN from the copyright page and use it to look up book metadata via the Google Books API, pre-populating the Add Book form.

## Where this fits in the flow

The upload pipeline is in `pages/uploader.py`. Currently it:
1. Uploads all photos to S3
2. Tries OpenCV + Sonnet rotation correction per page
3. Extracts text + classifies page type (story/not story) per page

Add a new step: after all pages are processed, look for a page where `page_type == "copyright"` (returned by the existing extraction prompt in `text_content/ai_prompts.py`). Extract the ISBN from that page's text. Call Google Books API. Store the result in session state for the Add Book form to pick up.

## Step 1 — ISBN extraction utility

Create a new function in `utilities.py`:

```python
import re

def extract_isbn(text):
    """Extract ISBN-13 or ISBN-10 from text. Returns string or None."""
    # ISBN-13: 13 digits, optionally grouped with hyphens/spaces
    isbn13 = re.search(r'978[-\s]?\d[-\s]?\d{2}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d', text)
    if isbn13:
        return re.sub(r'[-\s]', '', isbn13.group())
    # ISBN-10: 10 digits/X, optionally grouped
    isbn10 = re.search(r'\b\d[-\s]?\d{3}[-\s]?\d{4}[-\s]?\d[-\s]?[\dX]\b', text)
    if isbn10:
        return re.sub(r'[-\s]', '', isbn10.group())
    return None


def lookup_isbn(isbn):
    """
    Look up book metadata via Google Books API (free, no auth required).
    Returns dict with keys: title, authors, publisher, published_date, or None on failure.
    """
    import urllib.request
    import json
    url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}&maxResults=1"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get('totalItems', 0) == 0:
            return None
        info = data['items'][0]['volumeInfo']
        return {
            'title': info.get('title', ''),
            'authors': info.get('authors', []),
            'publisher': info.get('publisher', ''),
            'published_date': info.get('publishedDate', ''),
        }
    except Exception:
        return None
```

## Step 2 — Integrate into `pages/uploader.py`

After the per-page processing loop, add:

```python
# ISBN lookup — find the copyright page text
isbn_metadata = None
for i, page in enumerate(pages_processed):  # you'll need to track pages during the loop
    if page.get('page_type') == 'copyright' and page.get('text'):
        isbn = extract_isbn(page['text'])
        if isbn:
            isbn_metadata = lookup_isbn(isbn)
            if isbn_metadata:
                st.info(f"Found book metadata via ISBN {isbn}: {isbn_metadata['title']}")
            break

if isbn_metadata:
    st.session_state['isbn_metadata'] = isbn_metadata
```

**Note:** You will need to track the `page_type` from each page's extraction result during the loop. Currently `extract_page_info()` returns `(text, is_story)` — check if `page_type` is also available from the JSON result. If not, update `extract_page_info()` to also return `page_type`.

## Step 3 — Pre-populate the Add Book form

In `data_structures/book.py`, `form_content()`, check for `isbn_metadata` in session state and use it to pre-fill title and publisher fields:

```python
isbn_meta = st.session_state.get('isbn_metadata', {})
_title = st.text_input("Title", value=self.title or isbn_meta.get('title', ''))
```

For the published year, parse the year from `isbn_meta.get('published_date', '')` (format is usually "YYYY" or "YYYY-MM-DD").

For the author/illustrator/publisher, only pre-fill if the form currently has no selection. Show a note: "ℹ Metadata pre-filled from ISBN lookup — please verify."

Clear `st.session_state['isbn_metadata']` after it's been used once (after the book is confirmed).

## Important notes

- Google Books API is free and requires no API key for basic queries
- Use `urllib.request` (standard library) not `requests` to avoid adding a dependency
- The API may not return illustrators — that's expected
- If the API returns nothing, fail silently (no ISBN or no match is normal for older books)
- The title page extraction may already provide title/author from the vision prompt — consider whether ISBN supplements or replaces that

## Verification

```
.venv/bin/python -m py_compile utilities.py pages/uploader.py data_structures/book.py
```
