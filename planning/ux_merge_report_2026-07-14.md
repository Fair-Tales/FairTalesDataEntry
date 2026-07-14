# UX batch — review & merge report (2026-07-14)

Reviewer/integrator pass over the 8 branches of the 2026-07-14 UX batch
(handoff: `planning/ux_batch_handoff_2026-07-14.md`). Every branch was
adversarially reviewed (diff vs `claude-dev`, convention compliance, regression
risk vs already-deployed fixes) and its full unit suite run on the branch
before merging. All merges are LOCAL to `claude-dev`; nothing pushed; `main`
untouched.

## Per-branch verdicts

| Branch | Verdict | Notes |
|---|---|---|
| `issue-207-remember-me` | **Merged** (+1 review fixup) | SameSite=Lax, deferred sign-out cookie delete, session-persistent logout guard, restore fallback + transient-failure resilience. Verified it does NOT regress the working local restore path: the synchronous `st.context.cookies` read stays the primary restore source; the component-snapshot read is a fallback only; Lax is strictly more permissive than Strict for top-level navigations. Fixup `2b73af1`: `logout()` now stages the deferred delete only when remember-me is enabled — otherwise no CookieManager component renders on the stop-and-wait run, no follow-on rerun arrives, and the user would stall on "Signing you out…" (local-dev/no-signing-key configuration only). |
| `issue-174-login-confirm` | **Merged** | Chris's suspicion checked: the two-click login WAS largely fixed by `a084d9b` (Jul 5, deferred cookie write) and that fix is untouched here. This branch is purely ADDITIVE — `autocomplete="username"`/`"current-password"` on the form inputs (Streamlit 1.58 supports the kwarg; addresses the autofill-desync residual) plus an empty-submit guard showing `Login.missing_fields` before any Firestore/bcrypt call. Its own test locks the a084d9b property (`set_remember_cookie` never inside the form), so the prior fix cannot silently regress. The branch's stale PARTIAL copy of the handoff doc was replaced at merge time with the full version from `ux-batch-handoff-2026-07-14`. |
| `issue-209-rotate-180` | **Merged** | Root cause confirmed in review: dialog edited the raw original while the view showed the corrected image. Now edits the displayed variant (original when "Show original photo" is on), caption states which. Transform extracted to pure `image_processing.apply_manual_correction` (pixel-exact tests, no input mutation — safe with the `st.cache_data` image cache). Save path unchanged. |
| `issue-181-rotation-gate` | **Merged** | One-condition change (`if rotation_on and not crop_gate_on:` → `if rotation_on:`): the dedicated 0/90/180/270 check now also runs on gate-APPROVED crops. Verified no double-rotation (the three accept paths are mutually exclusive, each applies the angle once and returns) and no reversion of the existing high-confidence / gate-off invariants — this strictly closes the last skip path. Cost: one extra cheap rotation call per gate-approved page; admin toggle still disables all of it. |
| `issue-203-append-photos` | **Merged** | Append flow reuses `_inline_ai_batch`/`_blank_batch` with `start_page` (default 0 = exact old behaviour — fresh uploads unchanged). Start = max(page_count, highest `page_N.jpg` in S3), hole-robust, plus an exists() collision abort. Own `APPEND_FLOW_KEY` temp prefix; #199 block-until-ready + force-proceed; #143 phone-QR expander; cancel cleans up. All referenced helpers/strings verified to exist; `page_count` default `-1` is guarded. |
| `issue-148-reorder-pages` | **Merged** | Streamlit-free `page_reorder.py`: stage-copies under new numbers + manifest last, ONE atomic Firestore batch (permuted doc contents incl. entered text + `page_reorders/{book_id}` sentinel), then finalise + stale-variant deletes. Crash recovery in both windows verified by tests (uncommitted → discard untouched; committed → resume finishes). Permutation maths and text-follows-photo checked by hand and by tests. Direct doc writes are justified here (id-changing migration needs batch atomicity — same precedent as `scripts/rename_book.py`); new Firestore collection `page_reorders` (tiny sentinel/audit docs). |
| `issue-180-already-uploaded-warning` | **Merged** | Tests-only, verified: adds `tests/test_photo_upload_messages.py`, zero behaviour change (the #180 rewording shipped in `3177286`). |
| `issue-168-redundant-confirm` | **Merged** | Tests-only, verified: adds `tests/test_single_step_entity_confirm.py`, zero behaviour change (the #168 inline registration shipped in `446d4d0`). |
| (#169) | No branch | Already fixed on claude-dev (`5d0f827`); nothing to merge. |

## Conflict resolutions

- **`pages/page_photo_upload.py` (#203 vs #148 — the one semantic conflict).**
  Imports: union of both sides (`append_photos_widget` + the `page_reorder`
  imports; the unused `s3fs`/`Page` imports removed by both sides stay
  removed). Button row: `st.columns(4)` — Continue / **Add more photos
  (append pages)** / **Reorder pages** / Replace. Routing chain: BOTH new
  `elif` branches kept (`_appending_photos` → `append_photos_widget`,
  `_reordering_photos` → `reorder_pages_view`), ahead of the
  already-uploaded-options branch.
- **`text_content/forms.py`** (twice: #174-after-#207, #148-after-#203):
  additive string conflicts in `Login` / `PhotoUpload`; kept all strings.
- **`pages/login.py`** (#174 after #207): auto-merged — verified both the #207
  logout/deferred-delete changes and the #174 guard + autocomplete inputs are
  present and correctly ordered.
- **`image_processing.py`** (#181 after #209): auto-merged, disjoint regions —
  verified `apply_manual_correction` and the widened `if rotation_on:` coexist.
- **`planning/ux_batch_handoff_2026-07-14.md`**: #174 carried a stale 73-line
  partial; replaced with the full 525-line handoff from
  `ux-batch-handoff-2026-07-14` in the #174 merge commit.

## Final status

- Full unit suite on merged `claude-dev`: **306 passed** (baseline 250 + 56
  new batch tests), `pytest -q --ignore=tests/e2e`.
- Ruff: **no new findings**. Repo-wide count went 49 → 47 (the batch removed
  two unused imports); the only flag touching changed files is the
  pre-existing, documented `E402` in `image_processing.py`.
- `git status` clean on `claude-dev`; nothing pushed; `main` untouched.
- Merge commits: `c284ee4` (#207), `1e4115a` (#174), `553bf4f` (#209),
  `ebda398` (#181), `f95e433` (#203), `b1426aa` (#148), `0174640` (#180),
  `2f05332` (#168), plus fixup `2b73af1` (#207 guard).

## Known residuals (accepted, from review)

- #207: a hard reload in the brief window between Sign Out and the deferred
  delete completing can still restore in a NEW session (far smaller window
  than before). Existing users carry a `Strict` cookie until their next login
  rewrites it as `Lax` — one re-login fixes a still-failing refresh-restore.
- #203/#148: concurrent same-book operations from two sessions are guarded
  (exists() abort / pending-manifest block) but not fully serialised —
  acceptable for the pilot (books are owner-edited).
- #148: `get_role` inside restore is outside the #207 transient-error guard
  (only `get_user` is wrapped) — negligible; and a crash exactly between the
  Firestore commit and the file copies leaves the fix behind the "Resolve the
  unfinished reorder" button on the reorder view (surfaced automatically).
- #174: `Login.missing_fields` wording is tweakable; empty-submit does not
  clear a stale `unconfirmed_username` notice (cosmetic).
