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

(Sections for later issues appended as completed.)

## Merge/deploy order & file-collision map
(Filled in at end of run.)
