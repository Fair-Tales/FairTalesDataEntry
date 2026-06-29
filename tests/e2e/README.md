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
| `test_navigation.py` | Landing → Enter Data; Landing → View Results (→ collection picker); user-home option menu |
| `test_search.py` | Book & author live (`st_keyup`) search (non-matching query → warning) |
| `test_add_book.py` | Add-a-Book form renders; blank-title required-field validation |
| `test_collection_picker.py` | Picker renders; 3 method tabs render; search no-match; empty-selection button disabled |
| `test_results_dashboard.py` | Picker → "View all" → dashboard renders (title + chart/empty state) |
| `test_validation.py` | Awaiting-validation list + submitted-only toggle (**team/admin only**) |
| `test_batch_upload.py` | "Batch Upload" opens the batch page (header + upload widget + Detect button) |
| `test_manage_characters.py` | Per-book hub loads; "Manage characters" route reaches a known state (#106) |
| `test_sidebar_roles.py` | Role-gated sidebar links (Data validation / Admin) visibility + invariants |

This is an extensible **scaffold**, not exhaustive coverage. Extension points are
noted inline (e.g. matching-query search assertions and the deep "Manage
characters" view, both of which need a seeded fixture book).

### Wave-B updates baked in
- **Book search is now a live `st_keyup` component** (key `book_search_keyup`),
  not a plain `text_input` — typed via `helpers.fill_keyup`, same as author search.
- **"View results" routes through the collection picker** (`collection_picker.py`,
  #75) before the dashboard; the dashboard tests reach it via "View results for
  all books".
- New pages/flows covered: collection picker (#75), validation list (#47/#83),
  batch upload (#84), the book-edit "Manage characters" route (#106), and
  role-gated sidebar links (#83).

### AI flows are NOT exercised
Per `DECISIONS.md` 004, no AI path is triggered: collection "From photos"
extraction, batch "Detect books" splitting, theme suggestion, character
detection, ISBN/birth-year lookup. Those tests assert the UI reaches the right
state only (the upload/Detect widgets render) and stop before submission.

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
# optional: the account's tier (archivist / team / admin). When set, the
# role-gated tests assert exact validation-page + sidebar-link visibility;
# unset → they skip. Use a team/admin account to cover the validation flow.
export TEST_USER_ROLE="team"
# optional: stable name for any data a future test creates (default: e2e-<unixtime>)
export TEST_RUN_TAG="e2e-local"
```

Tests that require a login `skip` (don't fail) when `TEST_USER_EMAIL` /
`TEST_USER_PASSWORD` are unset, so the suite stays runnable without an account.
The role-gated tests (`test_validation.py`, and the exact-visibility check in
`test_sidebar_roles.py`) `skip` unless `TEST_USER_ROLE` is `team`/`admin`.
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
