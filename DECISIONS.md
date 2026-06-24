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
