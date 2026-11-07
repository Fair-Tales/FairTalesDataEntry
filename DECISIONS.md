# Decisions Log

Record of significant technical and architectural decisions.
Each entry: context → decision → reasons.
Status: `accepted` | `superseded` | `deprecated`.

---

## 001 — Revert to single Firestore database (defer decoupled-DB design)

**Status:** accepted

**Context:** Grace's grace-dev branch introduced a split Firestore architecture: a dedicated database for user credentials and a separate database for book/content data. `FirestoreWrapper` was updated with `connect_user()` and `connect_book()` methods routing to the two databases. This also caused `entered_by` on book records to be stored as a plain string username (instead of a document reference) because cross-database references are not supported in Firestore.

**Decision:** When merging grace-dev into claude-dev (June 2026), we reverted to a single Firestore database for both user credentials and book data. `connect_user()` and `connect_book()` were kept as separate methods (anticipating issue #48) but both currently route to the same default database. `entered_by` was restored to storing a Firestore document reference into the `users` collection.

**Reasons:**
- Decoupling the databases is the right long-term design but adds operational complexity before the immediate release.
- Existing book records in Firestore already store `entered_by` as a document reference; migrating them to strings would have required a data migration.
- Keeping the method split in code means the refactor to two databases (issue #48) can be done later with minimal code changes.

**Consequences / follow-up:**
- Issue #48 (decouple user credentials DB from book data DB) is deferred, not cancelled.
- When #48 is implemented, `entered_by` storage will need revisiting — either migrate to strings or keep cross-collection references within a single project.
- Credential rotation should happen around the time #48 is implemented (new Firebase service account will be needed for a second named database).

---

## 002 — Defer migration of existing 1970 birth-year records (issue #71)

**Status:** accepted

**Context:** `Author.birth_year` previously defaulted to `1970`, so any author created without a known birth year was stored with the value `1970` in Firestore. This is indistinguishable from a genuine 1970 birth year. Issue #71 changes the default to `None` and makes the form input optional, so future records will correctly represent unknown birth years as `None`.

**Decision:** Existing Firestore records that store `birth_year: 1970` are left as-is. No retroactive data migration is performed.

**Reasons:**
- A `1970` value in an existing record might be a genuine birth year; blanket conversion to `None` would corrupt authentic data.
- Safe migration would require manual review of each 1970 record, which is out of scope for this fix.
- The display layer (`pages/user_home.py`) already renders `None` as "Unknown" and can be extended to flag 1970 as ambiguous if desired in a follow-up issue.

**Consequences / follow-up:**
- Any existing author with `birth_year: 1970` is potentially wrong but cannot be corrected programmatically without human review.
- A future clean-up task could present all 1970 records to a data-entry user for confirmation or correction.

---

## 003 — Adopt `streamlit-keyup` for live author search (issue #72)

**Status:** accepted

**Context:** The home-page author search (`pages/user_home.py`) used `st.text_input`, which only reruns on Enter / blur, so results did not update live as the user typed. Streamlit's native per-keystroke input tracking (streamlit/streamlit#4553) is still unshipped, so there is no built-in way to filter as the user types.

**Decision:** Add the `streamlit-keyup` third-party component (`st_keyup`, pinned to `0.3.0` in `requirements.txt`) and use it for the author search field. The component reruns on each keystroke and is configured with a `debounce=300` (milliseconds).

**Reasons:**
- A native Streamlit live-filter input does not exist yet, so a component is the only way to deliver the live-filter UX requested in #72.
- `streamlit-keyup` is the component named in the issue, is also bundled in `streamlit-extras`, and exposes a `debounce` parameter.
- The 300ms debounce means the author lookup only re-runs after the user pauses typing, avoiding a Firestore read on every individual keystroke while still feeling responsive.

**Consequences / follow-up:**
- New runtime dependency `streamlit-keyup==0.3.0`; developers/deploys must reinstall requirements before running.
- `st_keyup` does not accept a `help` tooltip parameter (unlike `st.text_input`), so the previous help hint was folded into the field label.
- If Streamlit ships native keystroke tracking, this component could be revisited and removed.

---

## 004 — Automated UI testing strategy: Playwright + Claude in Chrome (interim production-DB use)

**Status:** accepted

**Context:** With stable widget keys now in place (#80), we want automated testing of the Streamlit app. Three complementary approaches are available: (1) **Playwright** scripted regression tests (#82); (2) Anthropic's **"Claude in Chrome"** browser-agent extension (GA beta on paid plans, incl. Max, in 2026); and (3) driving a Chrome browser from within a Claude Code session via the `claude-in-chrome` MCP tools.

**Decision:**
- **Playwright (#82) is the deterministic regression backbone** — scripted tests for the core, *non-AI* user journeys (login, navigation, search, manual book entry/validation, manage characters, results dashboard), using the stable #80 keys. The Claude API calls are stubbed/avoided in these tests (assert the UI reaches the right state, not model output).
- **Claude in Chrome / in-session Chrome driving is the exploratory layer** — reproducing bugs, verifying one-off flows, and offloading manual testing from the developer.
- **Interim data policy:** while in active development (the database currently holds only a handful of books and test users, **not production**), automated tests **MAY run against the real Firestore/S3** and incur real Anthropic API costs. Accepted trade-off for development speed.

**Reasons:**
- Playwright gives repeatable, cheap, deterministic regression coverage; Claude-in-Chrome gives flexible, code-free exploration. They serve different needs.
- The AI flows (vision extraction, ISBN lookup, character/alias detection, person lookup) are non-deterministic and cost API calls, so they are unsuitable for assertion in a deterministic suite.
- A separate test environment is not yet justified given the tiny, non-production dataset.

**Consequences / follow-up:**
- **Before production launch / wider rollout:** stand up a **test environment** (separate Firestore database + S3 bucket, plus a way to stub the Anthropic API) so automated testing does not pollute the production database or incur uncontrolled cost. Ties into #2 (Firestore out of test mode) and #48 (decouple credentials/book DBs).
- Anthropic notes Claude in Chrome is **not yet recommended for sensitive/mission-critical sites**; keep in mind as the app matures.

---

## 005 — `edit_log` audit collection as a raw-dict writer; immediate on-submit diff capture (issue #47)

**Status:** accepted

**Context:** The validation workflow (#47) lets a team member review SUBMITTED books and correct errors. Chris wants every correction captured — original value and change — as TRAINING DATA for future AI correction systems. Two design questions arose: (a) what data model for the audit records, and (b) when/how to compute the before/after.

**Decision:**
- **New Firestore collection `edit_log`**, written by a dedicated `EditLog` class (`data_structures/edit_log.py`) using a **raw-dict writer with a Firestore-generated id** (`collection.add()`, exposed via `FirestoreWrapper.add_document`) — i.e. it deliberately does **not** subclass `DataStructureBase`/use write-through `Field` descriptors. Schema per record: `book_id`, `book_title`, `entity_type` (`book`|`page`|`character`|`alias`), `entity_id`, `field`, `old_value`, `new_value`, `edited_by` (validator ref), `timestamp` (UTC), `context` (`validation`).
- **Capture mechanism: immediate on-submit diff** inside `pages/validation.py`. Each editor seeds its widgets from the entity's stored values (the originals) and, on save, compares the submitted values against those originals **before** writing through, logging one record per changed field. (Chosen over the originally-suggested open-time snapshot/diff.)

**Reasons:**
- An audit record is **append-only and immutable**, has **no natural deterministic `document_id`** (the same book/entity/field recurs over time), and is produced in **batches during a diff** — none of which the `DataStructureBase` pattern (single-field write-through to a content-derived id, bound to a `to_form()`) models. Forcing the fit would require an artificial id and a no-op form. This mirrors the documented `User` raw-dict exception but is even more clearly justified.
- Immediate on-submit diffing fits the per-form write-through pattern exactly, captures the precise before/after for each correction **including renames** (character/alias document ids are name-derived, so a rename changes identity — which an open-time snapshot keyed by id would mismatch), and needs no cross-navigation snapshot lifecycle. Page-text corrections (original transcription vs validated text) are captured as `entity_type='page', field='text'`.

**Consequences / follow-up:**
- New `edit_log` collection accumulates write-once records; include it in admin export (#69) if/when the data is needed for analysis/training.
- `old_value`/`new_value` are coerced to serialisable scalars (refs → `path` string) so a record can never fail to serialise.
- The book **title is read-only** in the validation review surface, because the title keys a book's pages/characters (the book `document_id` is title-derived); retitling would orphan them under the current single-DB id scheme. A safe book-rename migration is out of scope here.
- No in-app role-management UI yet (#47/#69 also cover admin granting roles); validators are set via the `role` field on the user document (#83).

---

## 006 — Persistent "Remember me" login via a signed, expiring cookie (issue #111)

**Status:** accepted

**Context:** Authentication lived only in Streamlit's per-tab `session_state`, so a hard page reload or a server restart/redeploy started a fresh session and bounced the user to the login form (#111). This is friction for a data-entry tool used in sessions, and it forces a human to re-enter the password after every restart during browser smoke-testing (Claude-in-Chrome can't autofill Streamlit's login form). Chris approved a **7-day** persistent session.

**Decision:**
- **Cookie component:** use the `CookieManager` from **`extra-streamlit-components`** (pinned `==0.1.71` in `requirements.txt`, mirroring DECISIONS 003's `streamlit-keyup` pin). It is **already a project dependency** (the package streamlit-authenticator builds on), so no new package is added — preferred over `streamlit-cookies-controller` / `streamlit-cookies-manager` for that reason. Streamlit cannot set cookies natively, hence a component is required.
- **What the cookie stores:** ONLY the `username` and an absolute `exp` (epoch-seconds) timestamp, base64url-encoded as a compact JSON payload, plus an **HMAC-SHA256 signature** over that payload: `"<payload_b64>.<hex_sig>"`. The **password is never stored**, nor anything else sensitive.
- **Signing key:** read from `st.secrets["cookie_signing_key"]`. **If the secret is absent the feature disables cleanly** — no cookie is written, no restore attempted, login behaves exactly as before (session-only). No key is invented or committed.
- **Restore point:** `Home.py` (the single entry script that runs before every page body, consistent with #107) calls `init_cookie_manager()` then `restore_session_from_cookie()` each rerun, before `navigate_pages().run()`. Restore verifies the signature (constant-time `hmac.compare_digest`) and expiry; on success it sets `authentication_status`/`username`.
- **Re-resolve role on restore (security):** the role/admin flag is **NOT trusted from the cookie**. On restore the role is re-fetched from the Firestore user document via `get_role()` and the user's continued existence is confirmed, so a **stale or forged cookie cannot escalate privileges** and a deleted user cannot be restored. Coordinates with the #83 three-tier roles.
- **Teardown:** `logout()` clears the cookie (`clear_remember_cookie()`) before wiping session state. **Note (#125):** clearing the cookie alone is *not* sufficient, because `clear_remember_cookie()` deletes via the **async** `CookieManager` while `restore_session_from_cookie()` reads **synchronously** from `st.context.cookies` (the request headers). The `st.rerun()` that ends `logout()` would therefore re-read the not-yet-expired request cookie and re-authenticate, making Sign Out a no-op while 'Remember me' is active. The fix is a one-shot `_just_logged_out` flag (`cookie_auth.JUST_LOGGED_OUT_FLAG`): `logout()` sets it after the session wipe (so the wipe cannot delete it, and it is intentionally **not** in `_LOGOUT_KEEP` so it does not persist past the single post-logout rerun), and `restore_session_from_cookie()` pops it and returns at the very top before any cookie read. A **residual** sub-second edge remains: a *new-session* hard reload issued before the async cookie delete propagates has no in-memory flag and may restore from the still-present request cookie. We do not gate on the `CookieManager` copy to close it (the component returns nothing on its first run — the reason restore reads `st.context.cookies` at all — so gating there would break the #111 cold-reload restore); revisit only if server-side session/revocation lands.

**Reasons:**
- Reusing an existing, maintained dependency avoids new supply-chain surface and matches how streamlit-authenticator already manages cookies.
- A signed (not encrypted) token is sufficient because the payload is non-secret (username + expiry); integrity — not confidentiality — is what matters, and HMAC provides it. Re-resolving role server-side closes the privilege-escalation vector that baking a role into the cookie would open.
- Gating on a secret keeps the feature opt-in per deployment and avoids committing any key.

**Consequences / follow-up:**
- **Deployment:** the feature is dormant until `cookie_signing_key = "<random-hex>"` is added to `.streamlit/secrets.toml` (and the Streamlit Cloud secrets). Generate with `python -c "import secrets; print(secrets.token_hex(32))"`.
- The `CookieManager` reads cookies via a frontend round-trip, so on the very first script run of a fresh page load the cookie may not yet be available; the component auto-reruns when it arrives and the session restores within a rerun or two (a brief flash of the login page is possible on a cold hard-reload). The login page redirects a freshly-restored user to their home page via a one-shot `_remember_restored` flag.
- `secure` is left unset (works over local HTTP); `same_site="strict"` limits CSRF exposure. Revisit `secure=True` if a stricter HTTPS-only posture is wanted in production.

---

## 007 — Direct browser-to-S3 photo upload via presigned PUT URLs (issue #114)

**Status:** accepted (Phase 1 — single-book "Add from Photos"; back-end + UI landed, **needs real-device + live-S3 testing and the S3 CORS policy applied before it works**)

**Context:** `st.file_uploader` fails on mobile (confirmed Pixel 8 Pro + Samsung S22+, even with two small photos): while the native full-screen photo picker is open the mobile browser drops the Streamlit websocket, so the selection is lost on reconnect (streamlit/streamlit#7230). Not a size/RAM/version issue. Archivists can't be relied on to switch browsers.

**Decision:**
- **Transfer path:** the phone PUTs each photo **straight to S3** using presigned PUT URLs the app mints, inside an `st.components.v1.html` iframe running vanilla JS (`XMLHttpRequest` per file → per-file progress bars, full resolution, no client resize). This bypasses the Streamlit websocket **and** the 1GB server entirely, fixing mobile reliability, server memory, and archival resolution at once. New module `photo_upload.py` holds the presign helpers + the component builder; user-facing strings live in `text_content` (`BookPhotoEntry`).
- **New dependency:** **`boto3`** (added to `requirements.txt`), used only to call `generate_presigned_url('put_object', …)`. `botocore` was already present transitively via `s3fs`/`aiobotocore`; installing `boto3` only bumped `botocore` 1.43.0 → 1.43.36 (patch) and added `s3transfer` — `s3fs`/`aiobotocore` still import and function.
- **Regional, SigV4-signed URLs:** the bucket is in **`eu-north-1`**, which only supports SigV4 and the *regional* endpoint. boto3's default emits the legacy global host `bucket.s3.amazonaws.com`, which a browser PUT cannot follow when it 400s on region mismatch. The client therefore pins `signature_version='s3v4'`, forces virtual-hosted addressing, and sets an explicit `endpoint_url=https://s3.{region}.amazonaws.com`, yielding `{bucket}.s3.{region}.amazonaws.com`. `Content-Type` is **not** signed so the browser may PUT jpeg/png/heic without header-matching.
- **Handshake (one-way component):** keys are `uploads/{session_id}/page_{i}.jpg` (i = selection order, MAX 60). `session_id` is `<safe-username>_<counter>_<timestamp>`, minted once and stored in `st.session_state` so reruns/reloads reuse the same prefix instead of orphaning a new one. A normal Streamlit **"Read the book"** button triggers a rerun; the page then **lists the S3 prefix** (`fs.ls(..., refresh=True)` to defeat fsspec's listing cache), natural-sorts, and feeds the bytes into the existing `extract_photo_first_metadata` pipeline. No bidirectional component is needed.
- **Final storage + cleanup:** the downloaded bytes are stashed in `photo_first_pages`, so the **existing** downstream pipeline (`uploader.upload_widget` → `_process_photo_batch`) orientation-corrects, crops, OCRs and writes them to `sawimages/{title}/page_N.jpg` exactly as today. The temp `uploads/{session_id}/` prefix is deleted on Continue (and on Cancel). `user_home.add_book_from_photos` resets the session so each new entry gets a fresh prefix.

**Reasons:**
- A presigned-URL, one-way component is the minimal change that removes the websocket from the transfer path; the "list the prefix" handshake avoids the complexity/fragility of a bidirectional Streamlit component.
- **In-memory download chosen over a pure S3 server-side copy** for Phase 1 (the issue's sanctioned fallback): the extraction "locate" pass must see every page anyway, and the existing downstream pipeline must read the bytes to orientation-correct/crop/OCR before writing `sawimages/{title}/`. A pure `uploads/→sawimages/` server-side copy would bypass that processing and regress behaviour, so the temp prefix is treated as a mobile-reliable transfer buffer that is processed then cleaned up. The phone→S3 transfer — the actual memory win — never touches the server.

**Consequences / follow-up:**
- **HARD PREREQUISITE (AWS, Chris):** the `sawimages` bucket needs a CORS policy allowing `PUT`/`GET` from the app origin, or the browser PUT is blocked (the `image/jpeg` PUT is non-simple, so the browser sends a CORS preflight). The Streamlit `components.html` iframe carries `allow-same-origin`, so requests use the **app origin** (the Streamlit/HF app URL), not `null`. CORS JSON is in the PR summary.
- **Untestable here:** mobile and live-S3 behaviour cannot be exercised in CI — this needs real-device + live-S3 testing on the deploy, with CORS applied first.
- **Phase-1 scope:** per-item *remove* in the uploader is deferred (true remove needs an S3 delete); the UI supports *adding* more photos. Batch upload (#84) and the QR/phone flow (#81) are the remaining follow-ups.

---

## 008 — Harden the direct-S3 presigned uploads (issue #126)

**Status:** accepted (code landed; the AWS lifecycle + CORS steps are **Chris-run on the live bucket**)

**Context:** `photo_upload.generate_put_urls` presigns only `Bucket`+`Key`; `Content-Length` and `Content-Type` are intentionally **unsigned** (so the browser can PUT jpeg/png/heic without header-matching). Consequence: an *authenticated* user could PUT arbitrarily large objects under `uploads/` (cost/DoS), and abandoned/closed-tab uploads accumulate because **both cleanup tools exclude `uploads/`**. Scoped to `uploads/` only.

**Decision (defence-in-depth, no regression to the working PUT uploader):**
- **Client-side size guard (50 MB, Chris-approved):** new constant `photo_upload.MAX_UPLOAD_BYTES = 50 * 1024 * 1024`, injected into the uploader JS (`__MAX_BYTES__`). Before PUTting each file the JS checks `file.size > MAX_BYTES`; an oversize file is **skipped** (it does not consume a presigned-URL slot) and a clear per-file error row is shown in the existing progress UI — the rest of the batch still uploads. The message is `BookPhotoEntry.upload_too_large` (text_content). This catches accidental huge files; it is **not** a security boundary (client JS is bypassable).
- **S3 lifecycle expiry for `uploads/` (7 days):** new standalone script `scripts/set_uploads_lifecycle.py` puts a bucket lifecycle rule (`ID=expire-uploads-prefix`, `Filter.Prefix=uploads/`, `Expiration.Days=7`). It is **dry-run by default** (prints the rule), `--execute` applies it, and it **merges** into any existing lifecycle config (keyed on the rule id) so unrelated rules are preserved. The script docstring carries the equivalent AWS CLI and Console steps. **Not run here** (no live creds in the worktree); Chris applies it.
- **CORS scope (deploy checklist):** confirm the `sawimages` bucket CORS policy allows the **app origin only** (not `*`) for `PUT`/`GET` on `uploads/`. Coupled with DECISIONS-007's CORS prerequisite — this just narrows the allowed origin before launch.

**Reasons:**
- The PUT uploader currently works well on real mobile devices; rewriting it risks regressing the hard-won mobile-reliability fix (DECISIONS-007). The 50 MB JS guard + lifecycle expiry + tightened CORS remove the practical accidental-abuse and cost-creep surface without touching the transfer path.

**Consequences / follow-up:**
- **True server-side hard cap (recommended follow-up, NOT done now):** a presigned **PUT** URL cannot cleanly enforce a maximum object size — `Content-Length` would have to be signed, which forces the client to declare an exact byte count up front and breaks the flexible browser PUT. The clean fix is to switch the mint to a **presigned POST** (`generate_presigned_post`) with a `["content-length-range", 0, MAX]` policy condition, which S3 enforces server-side regardless of the client. This is a larger change to both `generate_put_urls` and the uploader JS (multipart form POST instead of raw PUT), so it is deferred as a recommendation, not implemented in #126.
- **Chris must run (live AWS), before/at launch:** (1) `python scripts/set_uploads_lifecycle.py --execute` (or the CLI/Console equivalent in the script docstring); (2) confirm bucket CORS is scoped to the app origin only for PUT/GET on `uploads/`.
---

## 009 — Editing scope: own-books-only for all roles + `databot`-owned AI books + Validation all/own toggle (issue #131)

**Status:** accepted (reverses part of #83's "team+admin may edit any book")

**Context:** #83 introduced three roles (archivist / team / admin) and let team members and admins EDIT books entered by anyone (the `is_team_or_above()` edit-all branch in `pages/review_my_books.py`). Chris decided (2026-06-30) that cross-user *editing* on that page is the wrong surface: corrections by another person belong in the dedicated **Validation** workflow (#47, which already records an edit-audit log), while the per-archivist edit page should stay personal. Separately, AI-generated books (#122 reconstruction now, #123 automated pipeline later) have no human "owner" and must not be locked to whichever admin happened to trigger them.

**Decision:**
- **Edit page (`review_my_books.py`) is OWN-BOOKS-ONLY for ALL roles.** The `is_team_or_above()` edit-all branch is removed. For every role the editable list is the union of two `entered_by` equality queries: the current user's own books **and** books owned by the **`databot` system user**. Own books keep the existing `entry_status == 'started'` filter (submitted books are locked); **databot books are shown regardless of `entry_status`** so anyone can pick up an AI-generated book to finish/correct it. (The databot status choice is flagged to Chris for confirmation.)
- **`databot` owner:** a reserved system "user" (`utilities.DATABOT_USERNAME = 'databot'`) owns AI-generated books, making them editable by any role. Its `entered_by` value is produced by `utilities.databot_entered_by()`, which **always** returns `username_to_doc_ref('databot')` — a `users`-collection `DocumentReference` (to a possibly-non-existent `users/databot` doc), the SAME representation real books use. This single stable representation is used both to STAMP databot onto reconstructed books and to QUERY databot-owned books; it avoids an existence read and avoids silently switching between a string and a ref (which would split databot books into two non-matching owner values). Plain-string `entered_by` remains tolerated for legacy records.
- **AI-creation flows stamp databot:** `book_reconstruction.reconstruct_book_from_photos` overrides `book.entered_by = databot_entered_by()` right after `register()`. #123's automated pipeline must do the same (noted in code).
- **Validation (`validation.py`) gains an All/Just-mine scope control** (`st.radio`, key `validation_scope_radio`, default **All books**). The page is already gated to team+admin; "Just mine" filters the unvalidated list to books whose `entered_by` resolves to the current username (ref-or-string handled by the new `_entered_by_name` helper). All-books default preserves cross-user review as the team's primary surface.

**Reasons:**
- Keeps personal editing personal and routes cross-user corrections through the audited Validation workflow, where every change is logged as training data (#47).
- A dedicated databot owner cleanly expresses "AI book, anyone may edit" without a per-role special case in the query, and reuses the normal `entered_by` ownership mechanism.
- Defaulting Validation to "All" matches the team's job (review everyone's submissions) while still letting a validator focus on their own entries.

**Consequences / follow-up:**
- `ReviewBooks.all_header` / `all_select_label` (the #83 edit-all variants) are now unused; left in place for now.
- **Confirm with Chris:** databot books appear on the edit page even when `entry_status == 'completed'` (already in the validation queue) — intentional so they can be picked up, but unlike own-book behaviour (started-only).
- A real `users/databot` document is not required; create one only if a databot login/identity is ever needed. #123 must stamp `entered_by = databot_entered_by()` on its books.

## 010 — AI cost optimizations, evidence-based (OCR model/resolution, neighbour-skip, admin-tunable params)

**Status:** accepted (decisions made 2026-07-04; implementation in progress on branches)

**Context:** the two Claude pipelines (one-time pilot import; ongoing live app) needed cost reduction for a large research dataset, but data quality is a hard constraint. Ran controlled experiments (full write-up + numbers in `AI_COST_OPTIMIZATION.md`): an OCR quality eval on deliberately-hard books (48 pages × 5 model/resolution conditions, scored against text-layer ground truth) and a ground-truth validation of a neighbour-continuity OCR-skip (68 flanked pages).

**Decision:**
- **Import OCR: Opus 4.8 → Sonnet 5.** Eval showed equal recall within noise (44/48 pages tie; worst single-page gap ~1 word; all dense hard pages tied) at −60% OCR cost. Backstopped by the clean+judge flag and the per-book validated-pickle containment check.
- **Import neighbour-continuity OCR-skip (with guards).** For an image-only page flanked by text-layer pages, a cheap text-only judge decides if the story flows across it; if so, skip OCR (store `text=""`, `text_source="skipped_wordless"`). Validated: 81% OCR avoided on the 764 flanked pages, ~zero unique-word loss. Guards: skip only `flows AND not-missing`; **fail-safe → OCR on judge error**; Pass-2 `fits_context` + containment as backstops. Built as a **reusable, Streamlit-free module** so the same check runs in the live app as a validator QA flag (pages where the story doesn't flow → human review).
- **App extraction resolution: default 2000px, admin-configurable.** Eval showed 1568px holds quality on hard pages (the old "1568 hurts" comment did not reproduce with Sonnet 5); 1568px is the real cost lever (~−27%/book measured). Default 2000px conservatively (ground truth was text-layer pages; true image-only stylised pages are marginally harder); expose the knob so it can be tuned per batch.
- **Admin settings panel (global, Firestore-backed).** Expose the cost/quality API parameters (extraction resolution, per-flow models, max_tokens, feature toggles) as a global config editable by `admin` role **without a code deploy**, read through a cached helper with the current hardcoded values as defaults (backward-compatible). The panel is **gated behind an explicit safety toggle + warning and defaults off**, so key parameters can't be changed by accident.

**Reasons:**
- Every cost lever was validated against ground truth with worst-case pages weighted, not average-only, before adoption — quality is the hard constraint.
- The neighbour-continuity check is reusable and doubles as an app data-quality signal, so its value outlives the one-time import.
- Admin-tunable parameters decouple quality/cost tuning from deploys — valuable when a hard batch needs more fidelity or an easy batch can be cheaper.

**Consequences / follow-up:**
- Not adopted: Batch API (−50%) — refactor not worth ~$10–15 on a one-time import; N/A to the interactive app. Prompt caching ≈ $0 (prefixes below the cache minimum).
- Sonnet 5 was expected to rise from $2/$10 to $3/$15 per MTok around Sep 1 2026 (+~30% app baseline), prompting advice to front-load heavy entry. The rise was cancelled and did not occur: $2/$10 is permanent (confirmed against Anthropic's published pricing, 2026-11-07).
- App-side changes need live-Streamlit-server testing before the `main` deploy.
---

## 011 — Book rename is an id-changing, multi-collection migration (standalone script)

**Status:** accepted

**Context:** A test user's partially-entered book ("Clean Up!") needed to be set aside — renamed to "Clean Up! (Partial Entry)" — so students could re-enter that title from scratch. There was no rename function. A book's Firestore `document_id` is title-derived (`title.lower().replace(" ", "_")`), and every dependent document embeds that id and/or holds a `book` reference: pages (`{book_id}_{n}`), characters (`{book_id}_{name}`), aliases (`{book_id}_{name}`, plus a `character` ref), the book's own `characters` ref-list, and the S3 image folder (`sawimages/{title}`, the raw title). So a naive title edit would orphan all of it — the exact trap DECISIONS #005 flags when it makes the validation title read-only.

**Decision:** Perform book renames via a standalone, guarded migration script (`scripts/rename_book.py`), modelled on `scripts/data_cleanup.py`: runs OUTSIDE Streamlit, loads `.streamlit/secrets.toml` directly, builds its own `firestore.Client` + `s3fs`, reuses `s3_constants` and `data_cleanup.load_secrets` (code reuse, #129). It is **dry-run by default**, requires `--execute` **and** a typed `CONFIRM`, logs every write, and uses **create-new-before-delete-old** ordering so a mid-run failure leaves the original intact (re-run is safe — the target-exists guard trips). It recreates every page/character/alias under the new id with `book`/`character` refs repointed and remaps the book's `characters` list, then moves the S3 folder server-side, then deletes the old docs.

**Reasons:**
- The id-changing rename is a batch migration across four collections plus S3 — it does not fit the per-field write-through `Field` pattern (mirrors the `Character.rename()` single-entity precedent, scaled to the whole book).
- A guarded, logged, dry-run-first CLI matches the repo's norm for production data mutations (#120 cleanup tool) and keeps the destructive step behind an explicit confirmation.

**Consequences / follow-up:**
- First live use (2026-07-13): renamed `books/clean_up!` → `books/clean_up!_(partial_entry)` (12 pages, 10 characters, 3 aliases, 38 S3 objects); verified old id freed and no dangling refs.
- Follow-up issue filed: surface book renaming as an **admin function** in-app, reusing this script's migration logic (extract the ref-repointing core into a shared, Streamlit-callable helper).

---

## 012 — Remember-me cookie: SameSite=Lax, deferred sign-out delete, session-persistent logout guard (issues #207/#125)

**Status:** accepted (partially supersedes 006's #125 residual-race note)

**Context:** Pilot users reported (a) a browser refresh logging them out despite "Remember me" + a configured `cookie_signing_key` (#207), and (b) DECISIONS-006 had accepted a "sub-second" Sign-Out race. Empirical Playwright testing of the real `cookie_auth` flow (2026-07-14) showed the local reload-restore machinery works, but **Sign Out never actually deleted the browser cookie**: `logout()` rendered the CookieManager delete component and immediately `st.rerun()`, which replaced the page before the component iframe's delete JS ever executed — so the cookie survived indefinitely and any later reload silently re-authenticated the signed-out user (a shared-device hazard, not a sub-second window). Separately, production cold visits bounce through `share.streamlit.io/-/auth/app` (verified: the app URL 303s there), i.e. cross-site top-level navigations — which a `SameSite=Strict` cookie is not sent on.

**Decision:**
- **Cookie is now `SameSite=Lax`** (was `strict`), matching Streamlit's own `streamlit_session` cookie. Lax still blocks cross-site subresource/POST sends; CSRF exposure is nil (HMAC-signed, restores a read session only, role re-resolved server-side).
- **Sign-out delete is DEFERRED** (mirror of the #174 deferred write): `logout()` only sets `_pending_remember_clear` and reruns; the login page's signed-out branch renders `clear_remember_cookie()` + "Signing you out…" + `st.stop()` so the delete component completes, and its value-change rerun lands on the login form with the cookie actually gone. `clear_remember_cookie()` now renders the delete even when the getAll snapshot doesn't list the cookie.
- **`JUST_LOGGED_OUT_FLAG` persists for the remainder of the session** (restore PEEKS it; a successful new login pops it) — `st.context.cookies` keeps serving the connection-time (deleted) cookie for the rest of the websocket session, so a one-shot pop was insufficient.
- **Restore fallback + resilience:** when the request headers carry no cookie, restore falls back to the CookieManager component's `document.cookie` snapshot (covers header-stripping proxies / navigation quirks; hydrates a rerun later on a cold load). A **transient** user-lookup failure (GoogleAPIError, e.g. exhausted quota) skips restore WITHOUT clearing the cookie; only a clean "user does not exist" clears it.

**Consequences / follow-up:**
- Residual: a hard reload issued in the ~sub-second window between Sign Out and the deferred delete completing can still restore in a NEW session; far smaller than before (the delete now actually runs) — revisit only if server-side revocation lands.
- Live verification required on Streamlit Cloud (refresh-restore over the auth bounce cannot be exercised locally).

## 013 — Durable bulk photo batches with the complete shared pipeline (2026-11-04)

**Status:** implemented behind `BATCH_UPLOAD_DURABLE_ENABLED=true`; off by default pending pilot.

A batch owns ordered source records and stable group identities. Uploads accept immutable
selections of at most 60 photos, up to 1,200 photos per batch. Every selected slot must
succeed and the researcher explicitly finishes the selection and batch. Accepted chunks
are copied and verified under `batch_sources/` before advancing; that prefix is excluded
from orphan-book discovery and does not rely on the temporary uploads lifecycle. The
bounded upload allocation record is stored there too. Unaccepted uploads, including
phone uploads awaiting desktop acceptance, still require reopening before temporary
objects expire. Sources are retained; retention/deletion is a separate admin policy.

The conservative splitter requires mean luminance below 5 and maximum luminance below
18 for a separator. Ambiguous dark frames and corrupt photos fail visibly. Cover fallback
is bounded to 60 thumbnails. Metadata uses the existing helper with at most 60 images per
book; larger groups retain their pages and require manual metadata. The first/last photos,
source ranges and editable titles must be reviewed before confirmation. Black frames do
not become book pages; a chunk boundary never implies a book boundary.

Books and group bindings are created atomically, including title collisions. Each group
gets an immutable job identity, source keys, validated settings and processing version.
`BatchBook` adds provenance through Field descriptors without changing shared `Book`.
Background jobs share one active-book scheduler per application process, process pages
sequentially, and run the existing text-based whole-book character/alias detector. Page
and character leases renew; owner/revision-checked commits reject stale/cancelled work.
Owner-specific image artifacts prevent a stale worker overwriting the winning result.
Empty character success, no usable text, failure and incomplete OCR coverage are distinct.
A thread-local adapter injects explicit clients/settings and raw-client usage recording,
without changing the shared extraction modules or their concurrent evaluation context.

The script thread verifies final image copies, materializes Pages atomically and preserves
later human edits. Character/Alias records are still created only through existing review
and commit logic. Durable suggestions, including empty results, survive a fresh session.
Explicit page retries invalidate prior suggestions; finalization replaces only unchanged
pipeline placeholders/artifacts. Ordinary progress polling never starts paid work.

The user-home Batch Upload action discovers owner batches across sessions. Explicit
resume reconstructs jobs from stored sources and settings. Threads can continue after a
tab closes while the application process remains alive; process shutdown requires resume.
A provider response lost before persistence may be billed again on retry. Process-local
admission is not an account-wide concurrency limit; external workers/shared admission
remain future work. Backend choice is persisted and the old batch loop is unreachable
from new uploads (its small compatibility adapter retains existing regression coverage).

**Verification and rollout:** automated tests use only fakes, with outbound test sockets
blocked. Transactional fakes exercise contention, expiry, stale writes, cancellation,
replay, partial failure and finalization protection. Before enabling, verify real-device
ordering, covered lenses versus dark covers, failed separators, cross-chunk books,
browser/redeploy recovery, concurrent users, memory/quotas, and single-versus-batch
text/correction/character/alias parity. Verify Firestore rules permit the new collections
and S3 policy permits durable `batch_sources/` objects; no live service was accessed here.

**Guarded follow-up:** the old single-book uploader's OCR exception path still loses
already-known correction flags. Its produce/error contract must carry corrected and
rotation_uncertain into registration even on OCR failure, using `page_results`.
That guarded module was not modified. The new batch finalizer and reconstruction path
already preserve these flags. Legacy precomputed-empty handoff inside guarded utilities
also remains unchanged; durable batch review handles empty success explicitly.

## 014 — Alias/character data model driven by downstream NLP, and the measurement framework after cancelling fresh annotation (2026-11-05)

**Context.** The 20-book baseline harness run scored OCR at 0.9906 book-macro F1 but
alias F1 at 0.581 (precision 0.447, recall 0.830). Investigation established that the
precision figure is a property of the reference, not the pipeline: across the pilot
corpus the students recorded 200 aliases against 1,494 characters (0.13 per character),
48% of books record none at all, the median is 1 where any exist, and 67% are lowercase
descriptive forms such as `the brown bear` or `her daughter`. See
`planning/human_alias_behaviour_2026-11-05.md`.

**Decisions (Chris, 2026-11-05).**

1. **No fresh annotation round.** A complete independent double-annotation programme was
   designed and costed at ~550 person-hours; it is cancelled, not deferred. Every metric
   must be computable from the existing pilot reference, the pipeline's own output, the
   already-adjudicated artefacts, or the frozen rotation fixtures.
2. **Recovering the students' aliases plus extras is the desired behaviour.** Alias
   recall against the student reference is the headline alias metric. Reference
   *precision* is retired as a target and reported only as a diagnostic, always with the
   48%-empty caveat attached, because no pipeline improvement can raise it.
3. **Duplication is handled by grouping, never deletion.** The rule is derived from the
   downstream tasks the alias data feeds — speaker attribution, and extraction of the
   language relating to each character — not from tidiness. A deleted surface form can no
   longer match its mentions, so deletion is downstream recall loss. Measured over the
   dev20 output: exact casefolded matching finds 811 of 916 candidate mention
   occurrences; preserving every recorded surface and making articles optional at match
   time finds all 916, of which 894 (97.6%) resolve to a single character. A rule that
   deletes determiner-duplicate rows removes nine aliases and finds exactly the same 916.
   Deletion therefore buys nothing and costs rows.
4. **Articles are optional at match time; possessives are not.** `a`/`an`/`the` are
   optional when matching mentions. Possessives and relational possessors are preserved,
   because the possessor is the evidence of which character is meant — collapsing
   `Marlon's mum` into `his mum` destroys exactly the discrimination that identity
   correctness depends on. Possessive stripping is confined to the permissive M1
   groundedness check, whose question is only whether the text exists at all. The two
   normalisers are deliberately separate and must not call each other.
5. **Alias storage: raw surface form and canonical form as separate fields.** A human
   entering one form is sufficient; the canonical value is derived deterministically on
   write and is overridable, with the override explicit in the data. Records written
   before this change have no canonical field: the read path falls back rather than
   raising or dropping the alias. The backfill over the existing 1,494 characters and
   200 aliases is purely rule-based, costs nothing in API calls, is dry-run by default,
   idempotent, and overwrites nothing a student entered.
6. **No bias between merging and splitting identities.** Neither error is acceptable, so
   the design discriminates on textual evidence instead of adopting a prior. False-merge
   and false-split rates are reported separately with separate targets, never combined
   into one score. Unresolved identity ambiguity is represented as unresolved.
7. **Character-associated language is stored as separately labelled relations** — the
   character's own speech, narration about them, and language addressed to them — so any
   combination can be analysed later without re-running the pipeline.
8. **Publisher is recorded as two separate fields** (imprint and publishing house), with
   migration and scoring specified. This is convention C4 and is settled.
9. **Convention status.** C3 (descriptive aliases in scope) and C4 are accepted. C1
   (preserve book-authored lettering including illustration lettering, tagged), C2
   (`contains_story` means participation in the main work) and C6 (credited retellers
   count as authors) remain **provisional research conventions**: implemented as
   defaults, with the convention version recorded in the output so any of them can be
   revisited without re-running. Pipeline agreement cannot validate a research
   convention, and these are Chris's judgement, not findings.
10. **Claims are exploratory and pilot-only.** Whether mentions are attributed to the
    *right* character is unmeasured and unmeasurable without labels we have chosen not to
    create. The mention-attribution metric is therefore labelled a dictionary-uniqueness
    diagnostic, never accuracy. The transcription result is the strong, fully supported
    claim.
11. **Holdout.** The usable pool is 161 locally verified books (90 with student aliases),
    not the whole 276. The overfitting check is 40 books, sampled 27 alias-positive and
    13 alias-zero under a frozen seed, run as a *paired* baseline-and-candidate
    comparison at $57.73 with request reuse. Forty books buys roughly ±7 points on alias
    recall and ±8 on groundedness — coarse, but enough to expose real overfitting.

**Metrics after this decision.** Alias recall (headline), M1 groundedness (92.9% at
baseline: 95 of 112 aliases exact in the pipeline's own text, 9 more after determiner
stripping, 8 ungrounded, under boundary-safe matching), M2 redundancy, M3 referent collision, M4 mention attribution
(diagnostic only). An empty output is *undefined* groundedness, never perfect
groundedness. Groundedness carries a known confound: an alias can be ungrounded because
the text is missing rather than because the alias is wrong.

**Billing.** The Anthropic API key was moved to a Console organisation linked to Chris's
Ultra subscription, so pipeline spend draws on the $200 monthly credit allowance instead
of the production card. That organisation has no payment method attached, which proves
the credits are being used but means API calls hard-stop when the allowance is exhausted
rather than falling back — a live-app failure mode that did not previously exist.
Credits expire at the end of each billing cycle and do not roll over.

See `planning/pipeline_improvement_design_2026-11-05.md` and
`planning/alias_duplication_findings_2026-11-05.md`.

## 015 — Autonomy validation: resolvable gates, proof on the human-typed cohort, adjudicated rather than assumed AI superiority (2026-11-06)

**Objective.** The pipeline should be good enough that routine human review is not
required. Validation exists to tune it and then to prove it can run on its own — not to
sit permanently in the loop. Everything below was designed by Astra (ultra effort) across
six rounds, adversarially reviewed by Fable, and independently verified in-session.

1. **Almost every gate first proposed was unestablishable by construction.** The
   zero-error one-sided 95% bound is `C(n)=0.05^(1/n)`, so a 1% error ceiling needs 299
   perfect books, 0.5% needs 598 and 0.1% needs 2,995. Against 161 books, twelve of
   roughly seventeen proposed gates could never be met however good the pipeline was, and
   a family-wise alpha made even alias recall fail at 0.9193 on flawless output. **Gates
   are now set at each field's own attainable ceiling**, never a flat floor, with the
   observed point estimate reported beside every proven bound and an explicit
   establishable-from column. Unreachable ceilings (99.9% identifiers, the ≤0.5% rates)
   are retained as *reported, not gated*. Improving the pipeline cannot raise a ceiling;
   only more evidence can. Gates are never lowered after seeing results.

2. **The imported page text is machine-generated, so it is not a human reference.** The
   importer takes the PDF text layer at `TEXT_LAYER_MIN_CHARS = 20`, else vision OCR, else
   suppresses OCR on interior image-only pages via a neighbour-continuity judge, then
   stamps every book `validated=True, validated_by="pilot_import"`
   (`scripts/import_pilot_data.py:236,766`). The ~0.99 OCR F1 is therefore agreement with
   machine text, and empty/empty on skipped pages is Claude agreeing with Claude — now
   named as a correlated-error stratum, with F1 reported by provenance tier.

3. **The continuity judge was confidently wrong seven times out of seven.** All seven
   recovered strings sit on `text_source="skipped_wordless"` pages whose verdict was
   `confidence: "high"`, `flows_continuously: true`, `text_appears_missing: false`, each
   with a fluent narrative justification. Four are shorter than the 20-character rule, so
   they are import misses, not entry errors: `planning/wordless_adjudication_2026-11-05.md`
   attributes them wrongly, and 010's "~zero data loss" cannot be generalised. Rates are
   7/108 reference-empty story pages (6.48%), 7/772 pages (0.91%), 7/20 books (35%) —
   descriptive dev20 figures. Empty-page gates are unavailable without adjudication.

4. **Proof moved to the human-entered pilot cohort, reversing its earlier deferral.** It
   is the only genuinely human-typed page text in the corpus. Excluding two `databot`
   books leaves **57 completed works, 734 story pages, 32 empty, 702 non-empty typed
   pages**; ceilings `C(57)=0.9488005` and `C(702)=0.9957417`. The "undergraduate entry"
   objection barely applies: nine annotator strata of 33/13/2/2/2/2/1/1/1, the two largest
   supplying 80.7%, and only one book (*Billy and the Dragon*) entered from a student
   account. Chris entered 2 of the 57. Unlike the uniform `pilot_import` stamp, annotator
   identity is recorded here, so heterogeneity is stratified rather than assumed away.

5. **80 imported books are held out of all tuning, leaving 81.** Their characters, aliases
   and attributes are genuine human coding from the original study's `character_database.db`
   (1,297 characters and 178 aliases usable), so books withheld before tuning are valid
   proof works *for those fields only*; they add nothing to any text or empty-page
   denominator. The eligible complement is `121 = 63 alias-positive + 58 zero-alias`, the
   other 40 being locked in the protected paired experiment and usable only for tuning. The
   holdout is `80 = 42 + 38`, weighted ceiling `0.05^[58/(121×38)] = 0.9629164`. Sequence:
   freeze all 80 before any tuning, make targeted improvements on the 41 available now, and
   defer final policy selection until the protected experiment locks and all 81 are usable.

6. **The two cohorts are never pooled.** They differ in annotator, entry procedure,
   completeness and provenance with no established transportability, so 57 + 80 protected
   works is not a 137-work gate. Each carries its own gates and a pass on one cannot
   compensate for a failure on the other. The imported holdout buys an independent test of
   whether results transfer to the original study's entry process; it does not strengthen
   the pilot ceiling.

7. **AI superiority is measured, not assumed.** The expectation that the pipeline is more
   thorough than the reference is well evidenced, but building it into the scoring rule
   would make the design unfalsifiable, since every disagreement would score as a human
   omission. Instead, disagreements are sampled under a frozen stratified design and
   adjudicated blind against the source image, resolving five ways — AI-win, human-win,
   both wrong, both defensible, unresolved — with uncertainty never collapsed into an AI
   win. Conditional superiority requires a one-sided 95% lower bound above 0.5, net
   superiority above 0. The primary framing is **non-inferiority in completeness plus
   measured directional superiority where supported**. Agreement with a sparse reference is
   not the objective, and neither party is deemed correct by identity.

8. **Ratified research conventions.** The app's four gender options
   (`Female/Male/Non-specific/Transgender`, `text_content/forms.py:630`) and the
   `gender_help` pronoun rule are the codebook — the `# TODO: confirm taxonomy with team`
   governs ethnicity, not gender. `Transgender` has zero source observations (the DB holds
   only F/M/NGS) and is never silently certified; `plural` is hard-coded `False`
   (`import_pilot_data.py:854`) and is unsupported. The **completeness non-inferiority
   margin is 1 percentage point**, applied separately to text, characters and aliases, and
   does not license a point of invented text.

9. **The published relation evidence is reusable, cutting the work from 200 sections to
   17.** `CM_SPEECH_DETECTION_VALIDATION_NEWRUN_190924.json` holds 650 genuine human binary
   labels, not error flags, and reproduces table 23.2 exactly (TP 260, FP 3, FN 12, TN 375
   → 95.588% / 98.859% / 97.692%), so speech detection needs no new annotation. The
   attribution pickle holds only 0/1 flags, leaving 193 accepted speaker and 188 accepted
   recipient answers reusable and **17 rejected sections** needing corrected answers — a
   disjoint union, verified. The published run generated attribution from text alone
   (`openai_api/tasks.py:run_task_1`) and matched to the human character list separately
   (`run_task_3`), and scored *better* before matching (196/199 = 98.4925% and 192/199 =
   96.4824%) than after (193 and 188). The matching step is the lossy one, so the
   autonomous pipeline's machine inventory is the real risk. Alignment, not annotation, is
   the binding constraint: the flags are keyed to old sections over pdfplumber text.

10. **Human agreement cannot be computed from existing files.**
    `sample_15pc_Sophie_Kate_combined_FINAL.xlsx` has 2,125 sentences with one
    `direct_speech` column, one `speaker_char` column, no coder-identity column and no
    recipient column, so it cannot hold two independent codings; its two unheadered columns
    are a UDPipe parse review, not a second coder. Only recovering pre-reconciliation
    originals could settle it. The published figures are a single anchored annotator and
    must never be presented as inter-annotator agreement.

11. **Two adjudicator roles, individuals unnamed.** A primary blind source adjudicator plus
    a separate second reviewer for unresolved cases and a frozen 20% random reliability
    subset — which is what finally yields a human-agreement estimate. Chris's 2 qualified
    works route to the second reviewer. A second name alone does not establish
    independence; prior exposure is recorded.

12. **The audit interface is a first-class requirement, not a nicety.** A clunky screen is
    the difference between the audit happening and quietly not happening, which would leave
    no denominators and undetectable drift. An ordinary two-endpoint judgement costs three
    keys including an explicit lock, with evidence already on screen, no prefilled answers
    and no default-correct checkboxes. Where speed and blinding conflict, rigour wins and
    the cost is stated. See `planning/audit_interface_design_2026-11-06.md`.

13. **Run-phase audit carries a random component without a fixed percentage.** An agent
    recommends books for audit, done if and when there is time, but a random share within
    whatever volume is actually audited keeps rates estimable. The frozen canary set stays
    for drift.

14. **What is explicitly NOT available.** No book-level 0.95 text claim exists for this
    corpus; the strong text evidence is the page-level `C(702)=0.9957417`. The
    source-completeness audit (nine books) remains diagnostic and its certificate is
    withheld rather than dressed up. Relation claims are conditional on the measured
    cohort, not a future-book guarantee. The *Oi Puppies!* and *The Magic Paintbrush*
    reservations were dropped because completing them would have moved the text bound by
    0.169 points: all 17 unfinished records stay untouched in the database, excluded from
    tuning so their future proof eligibility survives, completable later by a human or an
    agent.

15. **Credits are not a decision.** Chris buys them as needed; no purchase amount is
    proposed. Accounting qualification still comes first: `ai_pricing.py` and
    `utilities.record_api_usage` (`utilities.py:521`) must handle OpenAI before any such
    spend is recorded, and `gateway.py:126` remains the only provider abstraction.
    Abstention is permitted within coverage floors, and calibration and proof precede new
    cataloguing.

See `planning/autonomy_validation_design_2026-11-06.md`,
`planning/relation_validation_protocol_2026-11-06.md` and
`planning/audit_interface_design_2026-11-06.md`.

## 016 — Enforce tuning exclusions before deserializing content (2026-11-07)

**Status:** implemented; offline synthetic tests, no tuning run performed.

The harness had no shared tuning loader: it decoded the entire reference manifest
before selection and independently read caches and prompt examples. New tuning
uses one source-aware load gate, with a small selective JSON reader for the
existing monolithic files. This avoids new exports, services or dependencies.
`extract` and `score` default to tuning checks; the original paired experiment
requires explicit `--original-experiment` and retains its previous loading rules.

The actual seal's exclusion file is a flat imported80 list; protected40 and the
tuning lists are in `freeze.json`. Both are checked without modifying the seal.
Unknown sources fail closed because the grouped crosswalk described in §1D is
not present. Release of protected40 is a trusted results/configuration lock
recorded immutably in an ordinary run record. Admitted IDs, refusals and detected
breaches use that same record; a detected exposure invalidates proof without a
redraw. This prevents accidental excluded inputs and catches missing provenance;
it adds no custody roles, approval gates or adversarial protection.
