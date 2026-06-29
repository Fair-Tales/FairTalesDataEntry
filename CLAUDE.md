# FairTalesDataEntry



## Working style

**Unknown terms and tools:** If the user mentions something unfamiliar — a library, framework, tool, dataset, or concept — do a web search before responding. Do not guess or infer from name alone. Training data has a cutoff; web search gives current information.

**Accuracy over confidence:** This project is used for research, coding, and task automation. Do not hallucinate. If uncertain, say so explicitly and search or ask rather than fabricating an answer. Cite sources when making factual claims about external tools or APIs.

**Precision by default:** Operate as if temperature were zero — prefer the most correct, well-established answer over a creative or varied one. Avoid speculation. If multiple approaches exist, state the trade-offs rather than arbitrarily picking one. (Note: CC does not expose a literal temperature setting — this is a behavioral instruction.)

**Model recommendations:** Proactively suggest switching model when the task warrants it:
- Suggest **Haiku** (`/model haiku`) for mechanical tasks: formatting, simple edits, quick lookups
- Suggest **Opus** (`/model opus`) for hard problems: complex architecture decisions, subtle bugs, difficult multi-file refactors, research synthesis — anything where Sonnet is struggling or the stakes are high
- Default Sonnet is right for most coding work; only suggest switching when there's a clear reason

## Safety rules

The confirmation passphrase for all guarded actions is **`AFFIRMATIVE`** (capitals).
Pressing Enter or saying "yes" / "ok" / "sure" does not count.

### Require `AFFIRMATIVE` before:
- Writing any file outside the project directory (also enforced by the PreToolUse hook)
- System-level changes (OS config, services, hardware, anything outside this project)
- Destructive git: `git push --force`, `git reset --hard`, amending pushed commits
- File deletion: `rm -rf` or bulk deletes
- Package removal: `pip uninstall` or removing packages from `pyproject.toml`
- Process termination: `kill`, `pkill`, `killall`
- Destructive database operations: `DROP`, `TRUNCATE`, `DELETE` without `WHERE`

### Always get explicit, specific approval before:
- **Merging, rebasing, or cherry-picking branches** — especially into shared/integration
  branches like `claude-dev` or `main`. Never run `git merge` (or equivalent) until the user
  has explicitly approved *that specific merge*. A user saying a feature "works" or "looks
  good", or approving an earlier step, is **not** approval to merge — ask first, every time.

### Hard stops (never do, even with `AFFIRMATIVE`):
- Never commit files matching `*.env`, `*secret*`, `*credential*`, `*token*`, `*api_key*`.
- **Never merge into or push the `main` branch.** `main` is the production deploy branch (Streamlit Cloud auto-deploys from it) and is handled **exclusively by the user**. Prepare and integrate everything on `claude-dev`; the user performs the `main` merge + push themselves. Do not do it even if explicitly asked in passing — point the user to do it. (Decided 2026-06-29.)

## Environment
- Python 3.10
- Install deps: `pip install -e ".[dev]"`
- Linter/formatter: `ruff check . --fix && ruff format .`
- Type checker: `mypy src/`
- Tests: `pytest`

## Repository layout
```
(add your layout here)
```

## Conventions
- **User-facing text** — instruction strings, labels, prompts, alerts, and help text — must be defined in the `text_content` module (e.g. `EnterText`, `Alerts`, `BookForm`, `Instructions`, `GenderRegistration`), not written inline in pages/components. Pages reference the text from there.
- **Persistent data structures use write-through `Field` descriptors.** Entities stored in Firestore (`Book`, `Author`, `Illustrator`, `Publisher`, `Page`, `Character`, `Alias`) subclass `DataStructureBase` and declare their attributes as `Field()` descriptors (see `data_structures/base_structure.py`). Assigning to an attribute (e.g. `book.title = "X"`) runs `Field.__set__`, which stores the value and — unless the object is mid-load (`reading_from_db`) — calls `update_record()`, writing **that single field** to Firestore when the object `is_registered`. Reference fields (`author`/`illustrator`/`publisher`/`book`/`character`) may be assigned a string key, which is resolved to a reference via the session lookup dicts. A structure's `to_form()` binds form widgets to these fields, so **editing a form field writes through to the database**; `register()` performs the initial full save. New persisted entities must follow this pattern — subclass `DataStructureBase` and define `fields`, `form_fields`, `ref_fields`, `to_form()`, and `document_id` — rather than writing Firestore documents directly.
- **Streamlit image width:** always use `width="stretch"` (or an explicit pixel integer) when calling `st.image()`. Do not use the deprecated `use_container_width=` parameter — it was removed in recent Streamlit versions (see commit `867bf23`).
- **Error handling:** do not use broad `except` / `except Exception` blocks that silently pass. Catch the narrowest exception type that covers the failure, then either log the error or surface it to the user (follow `book_edit_home.py`'s pattern as the reference). Swallowing errors makes debugging and user feedback impossible.
- **Lookup guarding:** always guard `.index()` calls and dict lookups against the possibility that a stored value is no longer a valid option. Use a membership check before `.index()` and `.get(key, default)` for dicts. Never assume a value persisted in Firestore or session state still exists in the current option list (see #91 and the gender-lookup bug for context).
- **Firestore query style:** write all Firestore queries using the `filter=FieldFilter(...)` keyword argument (e.g. `collection.where(filter=FieldFilter("field", "==", value))`). Do not use the positional `where("field", "==", value)` form, which is deprecated.
- **Widget key naming (#80):** every interactive widget (`st.text_input`, `button`, `form_submit_button`, `selectbox`, `file_uploader`, `radio`, `text_area`, `checkbox`, `multiselect`, `slider`, `toggle`, `download_button`, container-method variants like `col1.button`) must declare an explicit, **stable** `key=` so browser automation (Playwright, #82) has deterministic selectors. Never key with `id()`, `uuid`, or randomness.
  - **Page-level widgets:** static `key="<context>_<purpose>_<type>"` (e.g. `login_submit_button`, `report_feedback_text_area`, `add_book_photos_extract_button`). For loop-rendered widgets, suffix a stable per-item id/index (e.g. `f"remove_{doc.id}"`, `f"rev_name_{i}"`). For per-page value-seeded widgets, suffix the page number (e.g. `f"enter_text_page_text_{page_number}"`) so paging re-seeds instead of bleeding state.
  - **Value-seeded entity forms (`data_structures/*.to_form()`/`form_content()`):** a *static* key here causes state-bleed — Streamlit persists one entity's value and ignores `value=`/`index=` seeding for the next. Instead, at the very top of the form (before any field is written back) capture `key_suffix = self.document_id` into a local, then key every widget `f"<entity>_form_<field>_{key_suffix}"` (e.g. `f"book_form_title_{key_suffix}"`, `f"character_form_gender_{key_suffix}"`). Capturing at the top keeps the suffix constant for the whole render even as fields change on submit, and makes it flip on exactly the render where the identifying field (title / forename+surname) has just been populated, which is what lets the author/illustrator "Look up" suggestion and the book sub-entry re-selects re-seed correctly.
  - **New-entity reset:** a brand-new unregistered entity has an empty/placeholder `document_id` (Book `""`; Author/Illustrator `"_"`; Character/Alias `"<book_id>_"`), so consecutive new entities of the same type share keys. Call `utilities.clear_entity_form_state("<entity>_form_")` at each "start a new X" choke point (e.g. `user_home.add_book`, `data_structures/book._new_person`, `enter_text.adding_character`/`adding_alias`) to pop that stale widget state so the next form re-seeds.
  - `User` is exempt (raw-dict, #90). Do not change keys that already exist; only add keys to keyless widgets.
- **`User` raw-dict exception:** the `User` entity is the only place the `DataStructureBase`/`Field` write-through pattern is deliberately bypassed — it is handled as a plain dict throughout the codebase. This is a known, documented exception. Issue #90 tracks the decision of whether to migrate `User` to `DataStructureBase` or formally ratify the raw-dict approach; until that decision is logged in `DECISIONS.md`, do not change the `User` handling pattern without first updating #90.

## Decisions
Significant technical decisions are logged in `DECISIONS.md`. Check it before proposing
changes to tooling, layout, or data handling.
