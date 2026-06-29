# End-to-end (Playwright) tests — `tests/e2e/`

Deterministic browser regression tests for the core **non-AI** user journeys of
the FairTalesDataEntry Streamlit app (issue #82). They drive a real Chromium
browser against a **running** instance of the app, locating widgets by the stable
keys added in #80 (a Streamlit widget keyed `key="foo"` renders a wrapper with
CSS class `st-key-foo`).

These tests do **not** trigger the AI flows (no birth-year lookup, character
detection, ISBN lookup, or photo extraction). Per `DECISIONS.md` 004 they may run
against the real dev Firestore/S3; they are written to be read-only / navigation
assertions and create no data.

## What's covered

| File | Journey |
| --- | --- |
| `test_login.py` | Login page renders; invalid creds → error; valid creds → landing |
| `test_navigation.py` | Landing → Enter Data / View Results; user-home option menu |
| `test_search.py` | Book & author search (non-matching query → warning) |
| `test_add_book.py` | Add-a-Book form renders; blank-title required-field validation |
| `test_results_dashboard.py` | Results dashboard renders (title + chart/empty state) |
| `test_manage_characters.py` | Per-book management hub loads (skips if user has no books) |

This is an extensible **scaffold**, not exhaustive coverage. Extension points are
noted inline (e.g. matching-query search assertions and the deep "Manage
characters" view, both of which need a seeded fixture book).

## 1. Install

```bash
pip install -r requirements-dev.txt      # adds pytest + pytest-playwright
playwright install chromium               # one-time browser download
```

## 2. Start the app (separate terminal — the tests do NOT start it)

```bash
streamlit run Home.py                      # serves http://localhost:8501
```

## 3. Set environment variables

```bash
export APP_BASE_URL="http://localhost:8501"   # optional; this is the default
export TEST_USER_EMAIL="you@example.com"      # a CONFIRMED test user
export TEST_USER_PASSWORD="••••••••"
# optional: stable name for any data a future test creates (default: e2e-<unixtime>)
export TEST_RUN_TAG="e2e-local"
```

Tests that require a login `skip` (don't fail) when `TEST_USER_EMAIL` /
`TEST_USER_PASSWORD` are unset, so the suite stays runnable without an account.
No secrets are stored in the repo.

## 4. Run

```bash
pytest tests/e2e/                          # headless
pytest tests/e2e/ --headed                 # watch the browser
pytest tests/e2e/ --headed --slowmo 300    # slow, for debugging
pytest tests/e2e/test_login.py -k valid    # a subset
```

`pytest-playwright` provides the `--headed`, `--browser`, `--slowmo`,
`--screenshot`, `--video` and `--tracing` options.

## What a human must provide

1. The app **running** at `APP_BASE_URL`.
2. A **confirmed** test user's email + password in the env vars above (the dev DB
   must contain that account).

## Notes on selectors

* Plain widgets are reached via their `st-key-<key>` wrapper class, e.g.
  `.st-key-login_email input`, `.st-key-login_submit_button button`.
* Two widgets are custom components rendered in an `<iframe>`:
  `streamlit-option-menu` (nav menus) and `st_keyup` (live search). The helpers
  `click_option_menu()` and `fill_keyup()` in `helpers.py` descend into those
  iframes.
* A brand-new book has an empty `document_id`, so its form keys end in a trailing
  underscore: `book_form_title_`, `book_form_submit_`.
