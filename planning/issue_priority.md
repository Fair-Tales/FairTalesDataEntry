# Issue Priority List
_Last updated: 2026-06-24_

Issues are ordered by: high impact or user-blocking first, then quick wins, then
larger/longer-term work. Items marked **[Grace's roadmap]** originated in Grace's
roadmap document written at the end of her summer development work.

---

## Tier 1 — Do first (high impact, unblocks users or other work)

### #59 — Photo-initiated book creation (title page metadata extraction)
User uploads photos to start a new book; Claude extracts title, author,
illustrator, publisher from the title page and pre-populates the Add Book form.
Reverses the current flow (metadata before photos) and removes the most
tedious manual step.

### #58 — Automate page text extraction using Claude vision API
After photos are uploaded, call Claude API for each page to extract the text,
pre-populate the text entry fields, and let the user review/correct.
The `enter_text.py` UI already exists — this is primarily API integration.
_Shares infrastructure with #59 and #52._

### #47 — Admin management UI and validation workflow
Currently a stub page. Admins need to review and approve submitted books
before data is used in research. Blocking for data quality.

### #54 — Password reset, email validation, account management
Users will inevitably forget passwords; without reset they lose access
permanently. The email infrastructure already exists.
Pre-requisite for trusting real users with the app.

### #2 — Migrate Firestore from test mode
Test mode has open read/write rules. Must be done before real user data
accumulates at any scale.

---

## Tier 2 — Quick wins (small effort, meaningful improvement)

### #57 — Code quality and technical debt
Several are near one-liners: whitespace stripping on name inputs, alias form
not clearing after submit, confirmation dialog before cancel mid-flow.
Small bugs that will annoy users and corrupt data if left.

### #55 — Author search on user home
**[Grace's roadmap]** Grace listed this as completed in her changelog, so it
may already be implemented on grace-dev and just needs carrying across.
Worth checking before building from scratch.

### #51 — Photo upload instructions and image handling
**[Grace's roadmap]** Clearer naming/ordering instructions (largely content),
plus a portrait image orientation bug that could cause real data quality
issues. Also: allow skipping/replacing photos if already uploaded.

### #56 — Data protection, backup, and compliance housekeeping
Adding the data protection statement to T&Cs is just text. Firestore backup
scheduling is more work but important before scaling. Also: clear dev junk
from databases, QR link timeout.

### #1 — Lightweight requirements
Likely a quick tidy of requirements.txt.

---

## Tier 3 — Important but more involved

### #52 — Automate character and alias detection (Claude / OpenAI vision)
**[Grace's roadmap]** After text extraction, detect characters and aliases
across pages automatically; pre-populate character entry for user to review.
The harder AI problem (alias consolidation across pages). Shares API
infrastructure with #58 and #59. _Vertex AI dropped — use Claude or OpenAI._

### #46 — Character level tags (ethnicity, disability)
Core research requirement. Needs careful taxonomy discussion before building
the UI — don't rush the data model.

### #50 — Character–book linking, aliases scoped to book, delete
**[Grace's roadmap]** Data model change (store character references on book
document). Needs design decision on multi-book characters before building.
Also includes delete character/alias functionality.

### #49 — Remember me + in-app back button
**[Grace's roadmap]** Good UX but archivists can work around it. Back button
needs a session-state page history stack touching every page — non-trivial.

### #53 — Reduce Firestore read traffic and add caching
**[Grace's roadmap]** Not urgent at small scale but will matter as the book
database grows. Replace full-dict fetches with cached retrieval methods.

---

## Tier 4 — Deferred / longer term

### #48 — Decouple user credentials database from book data database
**[Grace's roadmap]** Explicitly deferred during the June 2026 grace-dev
merge. See DECISIONS.md #001. The `connect_user()` / `connect_book()` split
is already in the code to make this easier when the time comes.
_Credential rotation should happen alongside this work._

### #52 (original Vertex AI scope) — superseded
The Vertex AI approach for character detection has been replaced by the
Claude/OpenAI approach described in the updated #52 above.

---

## Not yet an issue — candidates to raise

- Migrate pilot study data to new databases (TODO in Home.py lines 39–47)
- Add 'help' instructions throughout the UI (Home.py line 20)
- TSV download with correct line/tab handling (Home.py line 26)
- Trigger notification to desktop when phone photo upload completes (Home.py line 35)
