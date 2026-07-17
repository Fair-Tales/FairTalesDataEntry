# UX bugfix batch — handoff (2026-07-14)

Autonomous batch run. One branch per issue, all cut from `claude-dev` at `108c09c`.
Nothing merged, nothing pushed. Unit tests only (no live app / prod data touched).

Working notes per issue: branch, status, files, tests, assumptions, residual
risks, and the exact live-app verification steps for Chris.

---

## #174 — Login requires clicking Confirm twice (recurring)

**Branch:** `issue-174-login-confirm` — **Status: done**

### Diagnosis (empirically verified)
The July-5 fix (a084d9b, deferred cookie write outside the form) is sound and has
been deployed since Jul 7 — I rebuilt the exact login flow (form + CookieManager
+ rerun pattern) as a standalone Streamlit app and drove it with Playwright:
**one click logs in and writes the cookie** in a clean environment. So the
rerun/cookie ordering is NOT the residual cause.

The residual cause is **browser/password-manager autofill desync**:
- Streamlit defaults password inputs to `autocomplete="new-password"` (verified
  in streamlit 1.58 source), which marks the form as a REGISTRATION form.
- Autofill then paints values into the DOM without the input events Streamlit
  needs to sync widget state. Reproduced with Playwright: DOM-filled but
  unsynced fields submit as EMPTY strings → "Invalid credentials" / apparent
  no-op on the first click; the rerun re-registers the fields and the second
  click works. Exactly the recurring two-click symptom, and it explains why
  every rerun-side fix "worked on my machine" but regressed for students (who
  have saved passwords).

### Fix
- `pages/login.py`: email input `autocomplete="username"`, password input
  `autocomplete="current-password"` (browsers then autofill via real input
  events — the actual fix); plus an empty-submit guard in `confirm()` that
  shows a specific, actionable `Login.missing_fields` warning instead of the
  misleading "Invalid credentials", before any Firestore/bcrypt call (safety
  net + telemetry: future reports can distinguish the two cases).
- `text_content/forms.py`: new `Login.missing_fields` string.

### Files changed
- `pages/login.py`, `text_content/forms.py`
- `tests/test_login_single_click.py` (new, 5 tests — source-level regression
  locks: autocomplete attrs inside the form, guard-before-authenticate, text
  defined, cookie write never inside the form)
- `tests/e2e/test_login.py` (2 new Playwright tests for the live suite:
  autocomplete attributes; empty submit shows the specific warning)

### Tests
`tests/test_login_single_click.py` 5/5 pass; full unit suite green.

### Residual risks / notes
- A second, environment-only contributor likely remains: a first click on a
  **dropped websocket** (Streamlit Cloud idle) is lost client-side; no server
  code can catch that. Covered by the #167/#207 work (see below).
- The `Login.missing_fields` copy mentions clicking into autofilled fields —
  wording tweakable.

### Chris: live verification
1. Deploy branch; in Chrome with a SAVED password for the site: open login,
   let Chrome autofill, click Confirm ONCE → must land on Home.
2. Repeat with "Remember me" unticked → one click.
3. Click Confirm with both fields empty → the new "Please enter your email and
   password…" warning (not "Invalid credentials").
4. Optional: `pytest tests/e2e/test_login.py` against a running instance.

---

## #180 — Misleading "already uploaded photos" right after processing

**Branch:** `issue-180-already-uploaded-warning` — **Status: done (already fixed; locked with tests)**

### Finding
Issue #180's proposed solution was ALREADY implemented in commit `3177286`
(the #179–#183 batch, on claude-dev and in today's `main` merge `55966e8`):
`Instructions.photos_already_uploaded` was reworded into a status message —
confirms photos uploaded **and processed** (with page count), notes the
automatic text read, states the next step — shown by
`pages/page_photo_upload.already_uploaded_options()` with the same three
buttons. The student reports (Jul 6) predate that fix reaching production
(deployed today). No further UI change made — only regression tests locking
the wording properties.

### Files changed
- `tests/test_photo_upload_messages.py` (new, 3 tests)

### Tests
3/3 pass.

### Residual risks / notes
- Adjacent (NOT fixed, out of scope): in the QR-phone flow the DESKTOP
  session's in-memory `current_book.photos_uploaded` can be stale after the
  phone finishes, so the desktop "Continue" click can wrongly warn "please
  upload photos" until the book is re-opened. Worth a follow-up issue if
  observed.

### Chris: live verification
1. Upload + process photos for a book (either flow), then revisit the photo
   page for that book → message must read "This book's page photos are
   uploaded and processed (N pages)…" with next-step guidance, not a
   duplicate-upload warning. If satisfied, close #180 (fix deployed today).

---

## #168 — Redundant second confirmation for author/illustrator/publisher

**Branch:** `issue-168-redundant-confirm` — **Status: done (already fixed; locked with tests)**

### Finding
Already implemented in `446d4d0` (merged `1b80319`, on claude-dev and in
today's `main` deploy): the three entity forms register inline via the shared
`utilities.register_and_link_book_entity` helper and return straight to the
book form; `confirm_entry.py` now routes only `new_book` / `new_character`
(which have a genuine multi-field review step). All #168 acceptance criteria
are met, including duplicate detection and no orphaned confirm methods. No
code change made — regression tests only.

### Files changed
- `tests/test_single_step_entity_confirm.py` (new, 3 tests)

### Tests
3/3 pass.

### Chris: live verification
1. Add a book → "Add new" illustrator → submit the illustrator form once →
   you should land straight back on the book form with the illustrator
   selected (no intermediate Confirm/Edit page). Same for author/publisher.
2. If satisfied, close #168 (deployed today).

---

## #209 — Manual "rotate 180°" only rotates 90°

**Branch:** `issue-209-rotate-180` — **Status: done**

### Diagnosis
The button→degrees mapping was CORRECT (left −90 / right +90 / 180 +180,
applied as `img.rotate(-total, expand=True)`; now pinned by pixel-exact unit
tests). The real defect: the dialog always edited the **raw original** while
the main view shows the **auto-corrected** image. When auto-correction had
already rotated a page by 90°, saving raw+180° produces an image 90° away
from what the user was looking at — exactly "180° only rotated it 90°".

### Fix
- Dialog now starts from the image **currently displayed** (corrected by
  default; the original when "Show original photo" is toggled on), with a
  caption stating which (`EnterText.editing_corrected_caption` /
  `editing_original_caption` in text_content).
- Preview/save transform extracted to `image_processing.apply_manual_correction`
  (pure, Streamlit-free, never mutates its input — safe with `load_image`'s
  `st.cache_data`).

### Files changed
- `image_processing.py` (new `apply_manual_correction`)
- `pages/enter_text.py` (dialog base image + use helper; comment updates)
- `text_content/forms.py` (2 new captions)
- `tests/test_manual_rotation.py` (new, 8 tests: 90L/90R/180/270 exact pixel
  transposes, click accumulation, identity/no-mutation, crop %, degenerate crop)

### Tests
8/8 pass; full unit suite 258 passed.

### Assumptions / spec calls
- "Edit what you see" chosen over "always edit the original": it fixes the
  reported confusion, preserves a good auto-crop when only rotation is wrong,
  and the original remains reachable via the Show-original toggle. Iterative
  saves re-encode JPEG at quality 95 (negligible loss).

### Residual risks
- Editing an already-corrected image then saving overwrites `_cropped.jpg`
  with the re-encoded edit (same as before, but now based on the corrected
  file). The raw original `page_N.jpg` is never touched, so nothing is lost.

### Chris: live verification
1. Open a page whose auto-correction rotated it (or any corrected page) →
   Crop and rotate → caption says "Editing the corrected image…" and the
   preview matches the inline view.
2. Click "180°" → preview is exactly upside-down vs what was shown; Save →
   inline view is exactly 180° from before. Repeat for 90° left/right.
3. Toggle "Show original photo", reopen the editor → caption says "Editing
   the original photo." and preview matches the original.

---

## #203 — Upload additional/forgotten photos to an existing book

**Branch:** `issue-203-append-photos` — **Status: done**

### Implementation
- Entry point: new **"Add more photos (append pages)"** button on
  `page_photo_upload.already_uploaded_options()` (next to Continue/Replace),
  scoped per-book via `_appending_photos` (mirrors `_replacing_photos`).
- New `pages/uploader.append_photos_widget()`: direct-to-S3 uploader under a
  dedicated `APPEND_FLOW_KEY="append"` temp prefix (never mixes with a full
  re-upload), manifest **block-until-ready** + force-proceed (#199), phone-QR
  hand-off expander (#143 generic mode), Cancel (cleans the temp prefix).
- New `pages/uploader.append_photo_batch()`: computes
  `start = max(page_count, highest page_N.jpg in S3)` (new hole-robust
  `s3_constants.max_folder_page` + `page_image_number`, Streamlit-free), a
  belt-and-braces `exists(page_{start+1}.jpg)` abort guard, then runs the SAME
  pipeline via `_inline_ai_batch`/`_blank_batch`, which gained a
  `start_page=0` parameter (default = exact old behaviour). New pages are
  `N+1..N+k`; `page_count` advances to `N+k` by Book write-through; existing
  pages/docs/text are never touched. Copyright-ISBN lookup deliberately
  skipped (it seeds the ADD-book form only).
- Post-run: success summary (+ per-page OCR-failure warning), "Enter text for
  the new pages" jumps to page N+1 (pops `book_pages_dict`, invalidates the
  image cache), or Back to menu.

### Files changed
- `pages/uploader.py`, `pages/page_photo_upload.py`, `s3_constants.py`,
  `text_content/forms.py` (Uploader append strings + PhotoUpload button)
- `tests/test_append_photos.py` (new, 7 tests: number parsing, hole-robust
  max, append numbering + page_count + cache staging, stale-count/hole start,
  blank no-API-key path, collision guard aborts cleanly)

### Tests
7/7 pass; full unit suite 257 passed.

### Assumptions / spec calls
- `first_content_page`/`last_content_page` are ONLY ever written by
  `scripts/import_pilot_data.py` — no app code sets or reads them — so an
  append has nothing to update there (documented; the issue text assumed the
  app maintained them).
- The durable background job path (#179) is not used for appends — the inline
  pipeline is the one the QR/manual re-upload path already uses.
- Appended pages run character auto-detect marking like any inline batch.
- Ruff `--fix` removed two pre-existing unused imports in
  `page_photo_upload.py` (`s3fs`, `Page`).

### Residual risks
- Concurrent appends from two sessions on the same book: first wins, second
  aborts cleanly via the exists() guard (tested with a fake race).
- The append does NOT reorder — a forgotten MIDDLE page appends at the end;
  reordering it into place is #148/#204's job.

### Chris: live verification
1. Open a book with entered text (e.g. via Edit my books → photos) → "Add
   more photos (append pages)".
2. Upload 2 photos (try one from the phone QR expander) → "Add these photos
   to the book" → success message "pages N+1 to N+2".
3. "Enter text for the new pages" → lands on page N+1 with OCR text; page
   through pages 1..N and confirm images + text unchanged.
4. Check S3: no existing page_N.jpg rewritten (timestamps), new page files +
   `_cropped`/`_display` present; Firestore book doc `page_count` = N+2.
5. Try Cancel mid-flow → back to the options; `uploads/append/...` prefix
   removed.

---

## #148 (+ #204/#205) — Reorder photos/pages after upload

**Branch:** `issue-148-reorder-pages` — **Status: done (covers both before AND
after text entry)**

### Implementation
- UI: new **"Reorder pages"** option on `page_photo_upload.already_uploaded_options()`
  → a "move page X to position Y" view (pages between shift by one; preview
  thumbnail of the page being moved; repeatable). Deliberately NOT a full
  drag-to-reorder grid — one safe primitive that covers the real pilot cases
  (a page in the wrong place; a #203-appended page that belongs in the
  middle).
- Core: new Streamlit-free `page_reorder.py` — a TRANSACTIONAL migration
  (Page doc ids embed the page number, so reorder is id-changing; S3 renames
  collide if done naively):
  1. **Phase A** stages copies of every affected `page_N(.jpg|_cropped|_display)`
     under its NEW number in `sawimages/{title}/_reorder_tmp/`; a
     `manifest.json` (token + moves + scheduled stale-variant deletes) is
     written LAST.
  2. **Atomic Firestore batch**: rewrites the affected page docs (permutation
     over the same id set, `page_number` corrected, full content moved — so
     entered TEXT follows its photo, which is why #205 comes for free) plus a
     `page_reorders/{book_id}` sentinel carrying the manifest token.
  3. **Phase B/C**: staged files copied over the finals; stale derived
     variants at destinations deleted; staging removed.
  - **Crash recovery**: manifest token vs sentinel token — committed →
    "Resolve" finishes the file moves; uncommitted → staging discarded,
    nothing changed. Surfaced in the UI when a pending manifest is found.
- After every reorder/resolve: `_invalidate_image_cache` staged (consumed at
  the top of enter_text → `load_image.clear()`) and `book_pages_dict` popped.

### Files changed
- `page_reorder.py` (new), `pages/page_photo_upload.py`,
  `text_content/forms.py` (PhotoUpload reorder strings)
- `tests/test_page_reorder.py` (new, 12 tests: permutation maths, happy path
  incl. text-follows-photo and stale-`_cropped` removal, no-op, abort paths
  leave everything untouched, crash-before-commit discarded, crash-after-commit
  finished by resume, pending blocks new reorder)

### Tests
12/12 pass; full unit suite 262 passed.

### Assumptions / spec calls
- `book.page_count` bounds the reorder; first/last_content_page not touched
  (only ever written by the pilot importer — see #203 notes).
- A raw Firestore commit failure propagates (narrow-except convention; rare) —
  the user then sees the standard error and the "Resolve the unfinished
  reorder" affordance on return, which discards the uncommitted staging.
- New Firestore collection `page_reorders` (one small sentinel doc per book,
  doubles as an audit record of who reordered what).
- Word-order caches: enter-text rebuilds from docs, so nothing else to update;
  characters/aliases are book-level (no page refs).

### Residual risks
- Two sessions reordering the same book simultaneously: the pending-manifest
  guard blocks the second only after the first's phase A; a true concurrent
  double-commit could interleave. Acceptable for the pilot (books are
  owner-edited only).
- Firestore batch limit 500 ops guards at 450 (books cap at 60 pages).

### Chris: live verification
1. Book with entered text on pages 1–3 and a wrongly-placed page (e.g. append
   one via #203): photos screen → "Reorder pages" → move page N to 2.
2. Confirm in enter-text: page 2 is the moved photo, its text (if any) came
   with it, pages shifted by one, no page shows a wrong CORRECTED image
   (check one page that had auto-correction and one that didn't).
3. Firestore: `pages/{book}_N` docs have matching `page_number`; a
   `page_reorders/{book}` doc exists; S3 has no `_reorder_tmp/` left.
4. Interrupt test (optional): kill the tab right after clicking "Move the
   page"; revisit → "Resolve the unfinished reorder" → completes/discards
   safely.

---

## #169 — Enter-text page layout & button placement

**Branch:** none — **Status: already fixed (no change made)**

All three requested changes are already on claude-dev (commit `5d0f827`,
merged `49210e7`, in today's main deploy): crop/rotate rendered below the
photo next to Enlarge; a bottom "Next page" button
(`enter_text_next_page_bottom_button`) above Finish/Submit that reuses the
auto-saving `page_change` handler; and Back-to-menu separated below
Finish/Submit by an `st.divider()`. No further redesign attempted (per
brief). **Chris:** verify placement feels right in the live app and close
#169.

---

## #181 — Auto-rotation leaves pages upside-down + edit button hidden

**Branch:** `issue-181-rotation-gate` — **Status: done (part a already fixed;
part b: last detection hole closed, no prompt change)**

### Findings
- (a) The crop/rotate button being hidden when auto-correction ran was
  already fixed on claude-dev (always shown; the #209 branch further makes
  the dialog edit the displayed image). Nothing to do.
- (b) Investigation found the one REMAINING path where a saved corrected
  image skipped rotation detection — and it is the DEFAULT path: crop-quality
  gate ON + gate approves the crop. Orientation was trusted from the gate's
  single combined "properly cropped AND right way up?" yes/no, which is
  demonstrably weaker than the dedicated 0/90/180/270 question (whose prompt
  spells out the upside-down case, post-#154). A gate-approved upside-down
  page was saved uncorrected with no later check.

### Fix
`image_processing.correct_page_image`: run `get_rotation_angle` on EVERY
accepted crop while rotation correction is enabled — uniform invariant across
the high-confidence, gate-approved and gate-off paths. No prompt changes.
Cost: one extra cheap rotation-model call per gate-approved page (the
medium-confidence minority); the admin `enable_rotation_correction` toggle
still disables all of it.

### Files changed
- `image_processing.py`
- `tests/test_rotation_invariant.py` (new, 6 tests covering all accept paths,
  the upright no-op, the gate-rejected fallback, and the disabled toggle)

### Tests
6/6 pass; full suite 256 passed.

### Proposals for the residual detection problem (NOT implemented)
If 180° pages still appear after this: (1) Tesseract OSD (`--psm 0`) as a
deterministic, free second opinion — flag disagreement with the model for
review; (2) dual-OCR sanity check (OCR the page and its 180° flip, keep the
orientation with the higher-confidence/longer coherent text) on pages where
the model answered 0 with artwork-only cues; (3) a one-click "flip 180°"
quick action next to the show-original toggle. These belong with the
#194/#208 prompt work.

### Chris: live verification
1. Upload a batch containing a deliberately upside-down page photo whose
   crop is clean (the gate-approved path) → the saved page must display
   upright.
2. Watch the status line: gate-approved pages now show a "checking the
   orientation…" sub-step after the crop check.
3. Monitor rotation-model API cost — expected small increase (one routing
   call per gate-approved page).

---

## #167 + #207 — Mid-workflow logout + "Remember me" not restoring on refresh

**Branch:** `issue-207-remember-me` — **Status: done (empirically verified
locally; needs live Cloud verification)**

### Empirical findings (Playwright driving the REAL `cookie_auth` locally)
1. **Local reload-restore WORKS** with the deployed code — the suspected
   "CookieManager not hydrated on first run" race is already avoided by the
   synchronous `st.context.cookies` read. So the deployed refresh-logout is
   environment-specific.
2. **Production cold visits 303-bounce through
   `share.streamlit.io/-/auth/app`** (verified with a header check) — a
   cross-site top-level navigation. The remember cookie was
   `SameSite=Strict`, which browsers do NOT send on cross-site navigations;
   Streamlit's own `streamlit_session` cookie is `Lax`. Prime suspect for
   #207 on Cloud.
3. **REPRODUCED BUG: Sign Out never deleted the browser cookie.** `logout()`
   rendered the CookieManager delete component then immediately `st.rerun()`,
   unmounting the component iframe before its delete JS ran. The cookie
   survived indefinitely — a signed-out user was silently re-authenticated on
   the next reload (shared-device hazard; DECISIONS-006 had assumed a
   "sub-second" window).

### Fixes (all verified end-to-end in the local repro after the change)
- Cookie now **`SameSite=Lax`** (CSRF exposure nil: HMAC-signed,
  read-session-only, role re-resolved server-side).
- **Deferred sign-out delete**: `logout()` sets `_pending_remember_clear`;
  the login page renders `clear_remember_cookie()` + "Signing you out…" +
  `st.stop()` so the delete component completes (exact mirror of the #174
  deferred write). `clear_remember_cookie()` renders the delete even when the
  getAll snapshot doesn't list the cookie.
- **`JUST_LOGGED_OUT_FLAG` is now session-persistent** (peeked by restore,
  popped by the next successful login) — `st.context.cookies` serves the
  stale connection-time cookie for the rest of the websocket session, so the
  old one-shot pop guarded only one rerun.
- **Restore fallback**: when request headers carry no cookie, restore reads
  the CookieManager `document.cookie` snapshot (covers header-stripping
  proxies; hydrates a rerun later on cold loads).
- **Transient-failure resilience**: a `GoogleAPIError` from the user lookup
  (e.g. the pilot's exhausted Firestore quota) skips restore WITHOUT
  clearing the cookie; only a clean "user gone" clears it. (Plausible
  secondary cause of pilot logouts during the quota exhaustion.)
- #167 specifics: the Enlarge dialog already reuses the in-memory image and
  `check_authentication_status` already restores-before-redirect (deployed);
  the above makes that restore path more robust. Nothing further changed.

### Files changed
- `cookie_auth.py`, `pages/login.py`, `text_content/forms.py`
  (`Login.signing_out`), `DECISIONS.md` (new entry 012)
- `tests/test_cookie_auth.py` (new, 12 tests: token round-trip/tamper/expiry,
  component-snapshot fallback, persistent logout guard, transient-failure
  cookie preservation, user-gone clears, delete-renders-when-snapshot-empty,
  source locks on the deferred delete)

### Tests
12/12 pass; full suite 262 passed. Plus the scripted browser repro (see
scratchpad `repro207` description above) confirming: one-click login, reload
restores, sign-out deletes the cookie and sticks across reload.

### Residual risks / notes
- The Cloud auth-bounce behaviour cannot be exercised locally — item 1 of the
  live verification below is the decisive test.
- Existing users carry a Strict cookie until their next login rewrites it
  (Lax); if refresh-restore still fails for them, one re-login fixes it.
- A hard reload in the brief window between Sign Out and the deferred delete
  completing can still restore in a new session (much smaller than before).

### Chris: live verification (deploy this branch first — it's the one to test)
1. Log in with Remember me on the Cloud app; hard-refresh (Ctrl+Shift+R) →
   must land back signed in (possibly after a brief login-page flash).
   Repeat after closing and reopening the browser.
2. DevTools → Application → Cookies: `fairtales_remember` present with
   `SameSite=Lax` after login.
3. Sign out → brief "Signing you out…" → login form; check DevTools: cookie
   GONE. Reload → must stay signed out.
4. Sign out then immediately log back in (same session) → works.
5. Mid-workflow (#167): on enter-text, leave the tab idle 10+ minutes, then
   click Enlarge / Next page → should continue (or restore with the 🔌 toast),
   not bounce to login.

---

# Batch summary

## Recommended merge order into claude-dev
1. `issue-207-remember-me` — highest pilot impact (blocks daily work); no
   file overlap with the others except `text_content/forms.py` (additive).
2. `issue-174-login-confirm` — login UX; touches `pages/login.py` like #207
   (different regions: form inputs vs logout; merge #207 first, then this —
   trivial context-line reconciliation at most).
3. `issue-209-rotate-180` — self-contained (`image_processing.py` new helper +
   `pages/enter_text.py` dialog).
4. `issue-181-rotation-gate` — `image_processing.py` `correct_page_image`
   (different region from #209's addition; both add to the same file).
5. `issue-203-append-photos` — `pages/uploader.py`, `pages/page_photo_upload.py`,
   `s3_constants.py`.
6. `issue-148-reorder-pages` — `pages/page_photo_upload.py` (SAME function,
   `already_uploaded_options`, as #203 — see collision notes), new
   `page_reorder.py`.
7. `issue-180-already-uploaded-warning` / `issue-168-redundant-confirm` —
   tests only, any time.

## Branches touching the same files (merge thoughtfully)
- **`pages/page_photo_upload.py`: #203 and #148 BOTH edit
  `already_uploaded_options()` and the top-level routing chain.** #203 makes
  the button row `st.columns(3)` (adds Append), #148 also makes it
  `st.columns(3)` (adds Reorder) — after merging both it should be
  `st.columns(4)` with all four buttons, and BOTH routing `elif` branches
  (`_appending_photos`, `_reordering_photos`) must be present. This is the
  one genuine semantic conflict in the batch; two-line fix at merge time.
  (#203 also removed two pre-existing unused imports there.)
- **`text_content/forms.py`**: #174, #207, #209, #203, #148 all ADD strings to
  different classes/regions — textual conflicts possible, semantically
  additive; keep all.
- **`pages/login.py`**: #174 (form inputs + confirm guard) and #207 (logout /
  signed-out branch + confirm flag pop) — different regions, both touch
  `confirm()` (adjacent additions; keep both).
- **`image_processing.py`**: #209 (new `apply_manual_correction`) and #181
  (edit inside `correct_page_image`) — disjoint regions.
- **`pages/enter_text.py`**: only #209.
- **`tests/e2e/test_login.py`**: only #174.

## Deploy notes
- All branches: unit suite green (`pytest --ignore=tests/e2e`), ruff clean on
  changed files (one PRE-EXISTING `E402` in `image_processing.py`, untouched).
- Nothing here ran against production data: all testing was unit tests with
  fakes plus standalone local Streamlit repros (fake auth/secrets, no project
  Firestore/S3 access).
- New Firestore collection introduced by #148: `page_reorders` (tiny sentinel/
  audit docs, one per reordered book).
- After merging to claude-dev, run the full suite once more (the #203/#148
  merge is the only place tests could interact).
