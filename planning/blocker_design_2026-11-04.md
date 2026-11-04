Reviewed `claude-dev` at `90d01987eec56e800df98b544c31f5241dc65f92`. No files changed or live services accessed.

My recommendations introduce two deliberate behaviour changes: a missing-page creation collision requires reviewing the saved page before continuing, and validation permits only one active review session per book. Both choices avoid silently resolving conflicting research-data edits.

**1. Missing-page registration: create atomically, refuse collisions, retain failed edits**

Use Firestore’s create-only operation for **missing-page recovery**. If another session has already created the document, preserve the local draft and refuse the save. Do not automatically merge or overwrite.

This is deliberately narrower than changing every `Page.register()`: `pages/uploader.py` also registers newly constructed `Page` objects when processing existing page IDs. Changing that globally would alter reprocessing behaviour.

Firestore documents `DocumentReference.create()` as failing when the document exists; a preceding existence check followed by `set()` does not provide that guarantee. [Firestore Python reference](https://docs.cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.document.DocumentReference)

**Registration changes**

In [data_structures/base_structure.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/data_structures/base_structure.py:149), add a keyword-only `create_only=False` to `register()`, and allow `save_to_db()` to receive an explicit payload:

```python
def register(self, *, create_only=False):
    if self.is_registered:
        return

    entered_by = st.session_state.firestore.username_to_doc_ref(
        st.session_state["username"]
    )
    created_at = datetime.now(timezone.utc)

    payload = {
        **self.to_dict(),
        "entered_by": entered_by,
        "datetime_created": created_at,
        "is_registered": True,
    }

    # Any failure leaves the object's registration state unchanged.
    self.save_to_db(data=payload, create_only=create_only)

    previous_guard = self.reading_from_db
    self.reading_from_db = True
    try:
        self.entered_by = entered_by
        self.datetime_created = created_at
        self.is_registered = True
    finally:
        self.reading_from_db = previous_guard


def save_to_db(self, *, data=None, create_only=False):
    payload = self.to_dict() if data is None else data
    ref = self.get_ref()
    if create_only:
        ref.create(payload)
    else:
        ref.set(payload, merge=True)
```

The explicit payload matters: merely moving `self.is_registered = True` below the current `save_to_db()` would store `is_registered=False`.

Keep ordinary registration’s existing upsert semantics and `Character.rename()`’s existing `save_to_db()` behaviour. This does **not** fix `register_batched`: #243 needs acknowledgement after **batch commit**, not after `batch.set()` stages a write.

**Pending edits and retry**

In [data_structures/page.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/data_structures/page.py), add two contained methods, `save_edits(changes)` and `retry_pending_edits()`. Their contract should be:

- Accept only `text` and `contains_story`.
- Save an instance-local pending dictionary **before** assigning either field. Keep it outside `fields`, so it is never serialized.
- For an unregistered page, assign pending values while unregistered, then call `register(create_only=True)`. Clear pending only after acknowledgement.
- For a registered page, ordinary edits still use `setattr()` and therefore the existing `Field` path. Remove each pending field only after its write returns successfully.
- A retry of a registered field calls `update_record(field, desired_value)` explicitly. It must not rely on assigning the same value again.
- Do not accept a new edit operation while an earlier one remains unresolved. The UI presents recovery first.
- Let persistence exceptions propagate to the UI boundary.

That last retry rule also covers a failure after `Field.__set__` has changed memory, including failure of its subsequent `last_updated` write. Retrying the acknowledged-or-unacknowledged field value is idempotent absent a competing edit.

**This should compose with #241, but does not depend on it.** Equal-value suppression in `Field.__set__` must not suppress a pending retry. Initial retries call `register()` directly; registered retries call `update_record()` directly.

**Wire all recovery entry points**

In [pages/enter_text.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/pages/enter_text.py:68):

| Function/path | New logic |
|---|---|
| `create_page_dict_from_db()` | Keep the valid, unregistered `Page(page_number=..., book=book_ref)` placeholder. Construction remains write-free. |
| `_save_current_page_text()` | Check unresolved pending/recovery state **before** the dirty check. Route actual edits through `save_edits()`. Return a success boolean. |
| `text_entry()` story checkbox | Route changed fields through the same helper. Capture any live text edit before starting recovery, so failure handling cannot lose a draft submitted alongside the checkbox interaction. |
| `reextract_current_page()` | Continue only if the preceding text save succeeds. Retain both extracted values before persistence; retry persistence without another Anthropic call. Stage the existing OCR success refresh only after successful persistence. |
| Every caller of `_save_current_page_text()` | If it returns false, abort navigation, submit, mode changes, or extraction. Do not continue after an unsuccessful save. |

Also change [pages/validation.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/pages/validation.py:350), `page_text_editor()`. It has the same missing-document registration path:

- Build its placeholder with `book.get_ref()`, not `book.title`.
- Submit only changed fields through the shared recovery path.
- Retain the pending `Page` across reruns; this editor currently reconstructs it each run.
- For missing-page creation, emit correction audit records only after creation succeeds. A refused collision must not be logged as an applied correction. Preserve the original before/after values with the pending operation.

This does not make page persistence and audit logging atomic; that existing limitation remains.

**Recovery UI and widget state**

Factor the small shared recovery controller into a new root module, for example `page_save_recovery.py`, used by both pages.

Store recovery state under `(flow, book_id, page_number)`, including the pending page, local draft and operation source. Catch the create conflict separately—`google.api_core.exceptions.Conflict`, which includes `AlreadyExists`—before the broader datastore-error handler. Catch `GoogleAPIError` for other persistence failures; log and display the failure. Do not catch arbitrary exceptions.

On a transient failure:

- Show the retained draft and a **Retry save** button.
- Prevent normal editors/autosave from replacing the pending operation.
- Retry the pending operation, regardless of widget/object equality.

On a creation collision:

- Perform **no** `set()` or `update()`.
- Explain that another save already created the page.
- Offer **Load saved page**, retaining the local draft separately for copying/review.
- Fetch the document afresh. If the read fails or the document disappeared, retain recovery state.
- After a successful read, replace the cached/current page and stage both widget values for the next full rerun. Preserve the draft separately; do not automatically replay it.

The refresh must be consumed **before either widget is instantiated**. Until consumption, autosave must remain blocked. Otherwise loading A’s text into `Page.text` while B’s widget remains `""` recreates the data-loss bug on the next save.

Use a neutral, book-and-page-scoped refresh helper for collision recovery; do not produce an OCR-success message. Preserve the existing staged re-extraction refresh behaviour.

Put all labels/notices in `text_content`, for example a shared `PageRecovery` class. New keys should be deterministic, such as:

```python
f"{flow}_page_retry_save_{book_id}_{page_number}"
f"{flow}_page_load_saved_{book_id}_{page_number}"
```

No existing widget keys need changing.

**Coverage, limitations and rejected alternatives**

This prevents B’s placeholder from changing **any** part of A’s existing document, including transcription and provenance. It preserves #231: a genuinely missing page is still created when edited, while an untouched placeholder remains write-free.

Observable change: B’s checkbox change is not automatically applied after a collision. B reviews the saved page and reapplies the intended edit.

It does not solve:

- Concurrent edits to an already registered page; those remain last-writer-wins.
- Session destruction losing an unsaved in-memory draft.
- Uploader/reprocessing overwrite policy.
- #243’s batch-registration acknowledgement problem.
- Atomicity between edits and audit records.

If creation commits but its acknowledgement is lost, retrying `create()` can report a collision with this session’s own earlier write. Treat that conservatively as a collision. Distinguishing it would require an operation ID.

Rejected approaches:

- **Drop empty fields:** empty text and `False` can be intentional changes.
- **Read, then set:** still races.
- **Merge only “dirty” fields:** viable with atomic creation, explicit intent tracking and widget-baseline handling, but more complex than refusing this rare collision.
- **Reload and continue automatically:** risks the stale-widget overwrite described above.
- **Only fix flag ordering:** does not force a retry when memory already matches the widget.

**Tests**

Extend [tests/test_enter_text_missing_page.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/tests/test_enter_text_missing_page.py). Its current `_FakeDocRef.set()` replaces the whole document even with `merge=True`; correct that fake, otherwise it models the wrong operation.

Add atomic fake `create()` that raises the real `AlreadyExists` when the shared store contains the ID. Add `get()/exists/to_dict()`, write recording, and injected failures.

Assert:

1. Two placeholders share a store. A saves text; B saves only a story toggle. B gets a collision; A’s complete stored document and provenance remain unchanged.
2. Successful creation stores `is_registered=True`; failure leaves the object false.
3. Failed initial save followed by an identical-value retry actually attempts creation and succeeds.
4. Collision never falls back to `set()` or `update()`.
5. Untouched placeholder creates nothing.
6. Failed OCR save retains both values; retry results in **one total Anthropic call**.
7. Loading the saved page refreshes both widgets before rendering; subsequent unchanged autosave performs no write.
8. Failed reload or disappeared document preserves the draft.
9. Save failure prevents navigation/submit.
10. With a simulated #241 equal-value shortcut, registered-field retry still reaches `update_record()`.
11. Existing upload/reprocessing registration retains its current behaviour.

Use the definitions-only execution approach already present in `test_book_characters_atomic.py` to exercise real enter-text functions without importing the page body. Keep source assertions for wiring, not as the sole evidence of persistence correctness.

**2. Validation: an exclusive lease owned by a review session**

Use a transactionally acquired lease identified by a **review-session token**, with username retained for display. Release must compare that token against fresh Firestore state.

A singleton marker cannot safely represent multiple permitted reviewers. Therefore my chosen design changes the current soft warning into a temporary refusal to enter the review editors while another lease is active. If preserving simultaneous review is mandatory, use the multi-reviewer alternative below instead.

**Model and transaction changes**

In [data_structures/book.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/data_structures/book.py:314), add:

```python
"validation_active_token": None,
```

It remains a normal descriptor-backed field. Add dedicated Book methods:

```python
acquire_validation_lease(token, username, refresh_due)
release_validation_lease(token, username)
approve_with_validation_lease(token, username, validator_ref)
```

These methods perform atomic grouped operations, following the existing precedent of `Book._write_characters_transform`. Do not implement them as consecutive descriptor assignments.

For acquisition/refresh, the transaction callback does:

```python
snapshot = ref.get(transaction=tx)
if not snapshot.exists:
    return MISSING

data = snapshot.to_dict() or {}
now = datetime.now(timezone.utc)
active = validation_marker_active(
    data.get("validation_active_at"), now=now
)
ours = (
    bool(token)
    and data.get("validation_active_token") == token
    and data.get("validation_active_by") == username
)

if active and not ours:
    return BUSY, data

if not ours or not active or refresh_due:
    patch = {
        "validation_active_at": now,
        "validation_active_by": username,
        "validation_active_token": token,
        "last_updated": now,
    }
    tx.update(ref, patch)
    return REFRESHED, {**data, **patch}

return OWNED, data
```

Release makes another transactional read. Only an exact nonempty token **and** username match may write:

```python
{
    "validation_active_at": -1,
    "validation_active_by": None,
    "validation_active_token": None,
    "last_updated": now,
}
```

An expired lease may be released if its token is still current. A newer token must survive.

Approval transactionally checks that the caller still owns an active lease, then writes **in one update**:

```python
{
    "validated": True,
    "validated_by": validator_ref,
    "validation_active_at": -1,
    "validation_active_by": None,
    "validation_active_token": None,
    "last_updated": now,
}
```

Otherwise stale A could approve after B acquired ownership even though release itself was safe.

Keep transaction callbacks free of Streamlit calls, descriptor writes, session mutation and audit side effects. Apply returned state locally under `reading_from_db`, with `try/finally`, only after successful commit. Firestore can rerun transaction callbacks. [Firestore transaction guidance](https://firebase.google.com/docs/firestore/manage-data/transactions)

**Page lifecycle**

In `pages/validation.py`:

- `render_list()` Open creates a token for that review instance. Store token, book ID and username together in session state. Reuse across reruns and failed requests; generate a new token after leaving and opening again.
- `render_review()` verifies/acquires ownership before calling **any** editor. On `BUSY`, show the title, explanatory message, Back and Retry controls, then return.
- This gate must also prevent processing forms submitted from an earlier render. Disabling Approve alone is insufficient.
- `_clear_validation_heartbeat()` delegates to conditional release. No token means no authority to release.
- Back clears local selection/token/throttle state after successful release, confirmed nonownership, or a missing document. A datastore failure retains state and shows a retryable error.
- Approve calls the atomic approval method. Remove the existing separate `validated`, `validated_by` and clear assignments.

The random token is an ownership identifier, **never a widget key**. Keep existing widget keys; add a stable key such as `"validation_retry_review_button"`.

Add named-owner, other-session/generic, retry and operation-failure strings to `text_content.forms.Validation`. Update the current warning wording: it must no longer imply concurrent editing is permitted.

**Throttle, compatibility and errors**

Keep the 30-minute activity window and 30-second write throttle.

`validation_heartbeat_due()` currently advances its supplied dictionary before persistence. Pass it a copy, then publish the changed throttle entry only after a confirmed refresh. Verify ownership on every interaction even when the timestamp write is throttled.

A live legacy heartbeat without a token is treated as foreign, including when its username matches. Username cannot identify which tab owns it. It becomes claimable after expiry.

The installed SDK’s transaction runner raises `ValueError` **chained from `Aborted`** when contention retries are exhausted. Normalize only that specific case into a domain error:

```python
except ValueError as exc:
    if not isinstance(exc.__cause__, Aborted):
        raise
    raise ValidationLeaseUnavailable() from exc
```

Keep this narrowly around transaction execution. At the page boundary, catch that domain error and `GoogleAPIError`, log and display the failure. Never convert a datastore failure into successful acquisition or release.

**Coverage and limits**

This closes the reported sequence: A acquires; B is refused without changing A’s marker; B’s Back cannot clear A’s lease. It also handles same-user tabs and stale releases after takeover.

Observable changes:

- Concurrent review sessions cannot use the editors simultaneously.
- Reconnecting after losing session state can require waiting for the remaining lease period.
- Owning Back/Approve still release immediately, preserving #234. The existing recent-edit-log backstop can independently continue blocking owner reopening.

It does **not** guarantee protection while someone reads without interaction beyond 30 minutes. Their next interaction must reacquire or be refused. Browser close and navigation outside these exit paths still rely on expiry.

There is also a separate, pre-existing race in [pages/review_my_books.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/pages/review_my_books.py:118): the owner can read “inactive,” a validator can acquire, then the owner can write `entry_status="started"`. **The proposed ownership fix does not close that read/check/write race.**

Closing that requires transactional owner reopening against the same book document, plus a validation-open status check. Do not blindly restrict validation to completed/unvalidated books: this application intentionally supports reviewing in-progress and already-validated imported books. Likewise, existing databot/ordinary editing routes do not universally honour validation leases.

Rejected approaches:

- **Username-only release:** ownership is stolen on Open; same-user tabs also collide.
- **Fresh read then conditional descriptor writes:** still a check/write race.
- **Batch the heartbeat fields:** atomic fields, but no atomic ownership condition.
- **Skip B’s heartbeat but let B review:** A can leave and clear the only marker while B remains.
- **Shorter expiry:** undermines the deliberate reading allowance.
- **Per-reviewer map/subcollection:** can preserve soft concurrent review, but requires per-session membership, expiry aggregation and legacy handling. It is a larger design.

**Tests**

Retain [tests/test_validation_heartbeat_clear.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/tests/test_validation_heartbeat_clear.py)’s import shim, fake Streamlit and rerun sentinel. Replace the attribute-only persistence assumptions with model-helper tests against a shared store.

Existing background-pipeline tests provide useful fake reference/snapshot structure, but stub transactional claiming. They do **not** supply evidence for transaction races.

Add a fake transaction runner with snapshot versions, staged writes, commit-time conflict detection and callback replay. An interleaving hook should allow a competing write before commit. Test the actual transaction callback, not a stubbed lease decision.

Assert:

1. A acquires; B opens and leaves; A’s exact token/username/timestamp survive.
2. Same username, different tokens: second session cannot acquire or release.
3. Two claims from the same initially empty state: one succeeds; the other retries and returns `BUSY`.
4. Release racing with takeover retries and preserves the newer lease.
5. Owning Back clears all three fields atomically.
6. Owning Approve atomically approves and releases; lost ownership changes neither.
7. A stale posted form cannot invoke an editor after ownership is lost.
8. Exactly 30 minutes remains active under the existing inclusive predicate; beyond the boundary takeover succeeds.
9. Active tokenless legacy markers block; expired ones can be claimed.
10. Failed refresh does not advance throttle; failed release/approval does not show success or discard local review state.
11. Missing documents are never recreated by lease operations.
12. Same-owner interactions within the throttle window verify ownership without another heartbeat write.

An isolated Firestore-emulator concurrency test would additionally check the SDK adapter. A sequential fake alone cannot prove server concurrency behaviour.

**3. Remember-cookie creation: return whether a write was issued**

This needs only a small contract change.

In [cookie_auth.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/cookie_auth.py:237), change:

```python
def set_remember_cookie(username) -> bool:
```

Return `False` from every current no-write path:

- No signing key.
- No cookie manager.
- Credential lookup raises the currently handled `GoogleAPIError`.
- Missing user or missing/empty stored password hash.

Preserve existing logging. Return `True` immediately after `manager.set(...)` returns normally:

```python
manager.set(
    COOKIE_NAME,
    token,
    key="remember_set",
    expires_at=expiry,
    same_site="lax",
)
return True
```

Do **not** return `manager.set()`’s value. The installed component’s method does not provide the required boolean contract.

In [pages/login.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/pages/login.py:206):

```python
pending_remember = st.session_state.pop("_pending_remember_cookie", None)
if pending_remember and set_remember_cookie(pending_remember):
    st.info(Login.signing_in)
    st.stop()

# Existing redirect logic follows unchanged.
```

Keep this outside the login form. Update the comment to explain that waiting is required only after issuing a write.

**Why, behaviour and limits**

When cookie preparation fails, authenticated users immediately continue through the existing redirect path. Their current login succeeds without issuing a new remember cookie.

Successful issuance retains the existing component-rerun sequence. Fingerprint creation, token validation and password-reset revocation remain unchanged, preserving #238.

`True` means **write issued**, not browser persistence confirmed. This does not fix a component that fails to rerun after issuance. Unexpected exceptions, including synchronous `manager.set()` failures, still propagate.

Rejected alternatives:

- Always redirect immediately: can abort the component write.
- Requeue indefinitely: can turn a transient lookup failure into a login loop.
- Mint without a fingerprint: regresses revocation.
- Add a new acknowledgement/timer protocol: unnecessary for the verified no-write branch.

**Tests**

Extend [tests/test_cookie_auth.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/tests/test_cookie_auth.py)’s existing monkeypatches and `_FakeUserDoc`; no Firestore client is needed. Add `set_calls` to its fake manager.

Assert:

1. Valid hash returns `True`, issues exactly one write, and produces the expected fingerprint-bound token.
2. Every no-write branch returns `False` and issues zero writes.
3. A `ServiceUnavailable` lookup failure logs a warning.
4. Unexpected manager exceptions propagate.
5. Existing password-reset and legacy-token rejection tests continue passing.

In [tests/test_login_single_click.py](/home/expert/Documents/uob/fair_tales/FairTalesDataEntry/tests/test_login_single_click.py), execute the authenticated top-level branch using source/AST extraction and fake `st.stop`/`st.switch_page` sentinel exceptions:

- Cookie result `False`: redirect occurs in that execution; no signing-in notice or stop; authentication remains.
- Cookie result `True`: stop occurs, redirect flag remains; the next execution redirects without another cookie attempt.
- No pending cookie: ordinary redirect still works.
- Retain the existing assertion that cookie creation is outside `LoginForm`.

Browser verification remains necessary for the successful component-write/rerun path; these unit tests establish the server-side control flow.