# Alias canonical backfill (offline first)

`scripts/backfill_alias_canonical.py` implements the additive alias migration in
the 2026-11-05 pipeline design. It preserves exact `Alias.name` strings, stored
document IDs, attachments, timestamps and unrelated metadata. Existing manual
overrides and unknown/stale canonical metadata are preserved and flagged. Only
missing fields of an absent or consistent derived bundle are filled. Canonical
forms retain articles and possessives; mention matching is a separate adapter.
The 1,494 characters remain readable through their existing references and need
**no migration reads or writes**.

The default is an entirely non-writing dry run. To prepare an offline ledger:

```bash
.venv/bin/python scripts/backfill_alias_canonical.py --snapshot aliases.json
```

The local JSON shape is:

```json
{"aliases": [{"id": "actual_legacy_document_id", "data": {"name": "The brown bear", "book": "books/example", "character": "characters/example_bear"}, "update_time": null}]}
```

Every invocation prints its read/write budget **before opening the input or
constructing clients**, then a complete JSON field diff with original raw fields,
references, timestamps and hashes. Snapshot mode requires no secrets and makes
zero service calls. It writes no local files; redirect stdout to save its ledger.

The future Firestore path is explicitly selected with `--firestore`. The default
limit estimates **200 alias reads** and caps them at **201 reads** (one overflow
sentinel, 0.402% of the 50,000/day allowance), then aborts without writes if more
than 200 aliases exist. Set `--max-aliases` to a reviewed higher bound if necessary.
It scans aliases once with no automatic stream retry, never dereferences book or
character references, and never reads the character/page corpus. Other application
usage and the account's remaining daily quota are unknown.

Future live execution, after the owner permits live service use:

```bash
.venv/bin/python scripts/backfill_alias_canonical.py --firestore
.venv/bin/python scripts/backfill_alias_canonical.py --firestore --execute --checkpoint cleanup_reports/alias_canonical_progress.json
```

These are two separate invocations and therefore two budgeted scans if both are
run. An execution invocation already prints its full diff before any write.
Execution performs at most one atomic conditional write per changed alias
(at most 200 at the default limit), through `Alias`'s shared Field persistence
layer; no direct document writes occur in this script. Snapshot `update_time`
preconditions reject both concurrent raw edits and concurrent human overrides.
A conflict stops the run; retry by rerunning a fresh, bounded scan. Confirmed
progress is saved after each write. Completed entries need no write on replay,
including when an earlier write succeeded but saving its checkpoint failed.
The raw-count/hash invariants compare the scanned data and proposed projection;
there is deliberately no second verification scan. Retaining the exact raw inputs
preserves matching inputs. Ignoring the added projection fields is a lossless
rollback. There are no model or S3 calls and no inference cost.

The frozen mention/grounding regression fixture at
`tests/fixtures/alias_dev20_predictions.json` is an exact, unmodified copy of
the cached dev20 prediction artifact at
`/tmp/claude-1000/-home-expert-Documents-uob-fair-tales-FairTalesDataEntry/94cd65ba-b47b-4f8e-aa72-0afc7c7bbd43/scratchpad/dev20-run/predictions.json`.
Both files are 384,458 bytes with SHA-256
`7976646dec39ab8798684894d0db64a3cf2e30affdd4e185ec6e4a2e807199cf`.
The fixture retains full raw predictions and recorded source-path references;
tests read only the repository fixture and never follow those paths or depend
on the temporary source remaining available. No inference was rerun to make it.

# scripts/data_cleanup.py — junk-data audit + guarded delete (issue #120)

A standalone CLI to find and (after manual inspection) delete junk / test /
incomplete data across **Firestore** and **S3**, ahead of production. It runs
**outside Streamlit**, loading `.streamlit/secrets.toml` directly and building
its own `firestore.Client` + `s3fs.S3FileSystem` (mirroring the app's
`FirestoreWrapper` and uploader config). It never imports the Streamlit-coupled
app modules.

There are two clearly separated phases. **Nothing is ever auto-deleted.**

## Phase 1 — AUDIT (default; read-only)

```bash
python scripts/data_cleanup.py --audit --report-dir cleanup_reports
```

Scans the data and writes three timestamped files to `--report-dir`:

* `cleanup_report_<ts>.md` — human-readable report, grouped by category.
* `cleanup_candidates_<ts>.json` — machine-readable candidate list (feeds Phase 2).
* `cleanup_candidates_<ts>.csv` — the same candidates, flat, for spreadsheets.

It also prints a per-category summary count. It deletes **nothing**.

### What each category checks

| Category | Check |
| --- | --- |
| **Books with no / too few images** | Counts `page_N.jpg` objects under `sawimages/{title}/` (ignoring derived `_cropped` variants). Flags books with 0, 1 or 2 (`TOO_FEW_IMAGES_THRESHOLD`). |
| **Orphaned S3 images** | Lists folders directly under `sawimages/` and flags any whose name matches no existing book's folder. The transient `uploads/` prefix is ignored (`NON_BOOK_S3_PREFIXES`). |
| **Junk / test names** | Runs `junk_name_reason()` over every book title and author/illustrator/publisher name: length ≤ 2, known test strings (`test`, `asdf`, `qwerty`, `xxx`, `foo`, …), all-same-character, numeric-only, keyboard-walk gibberish. An allowlist (`JUNK_NAME_ALLOWLIST`) protects genuine short names. |
| **Dangling references** | `pages.book` / `characters.book` pointing at a missing book; `aliases.character` pointing at a missing character (also flags a missing/None ref). |

The heuristics, allowlist, threshold and ignored prefixes are all module-level
constants near the top of `data_cleanup.py` and are meant to be tuned.

## Phase 2 — DELETE (guarded; dry-run by default)

1. Open `cleanup_candidates_<ts>.json` and **delete the entries you do NOT want
   removed** — keep only confirmed junk. (Or build a plain ids file, see below.)
2. Dry-run (prints exactly what would be deleted, deletes nothing):
   ```bash
   python scripts/data_cleanup.py --delete --ids cleanup_candidates_<ts>.json
   ```
3. For real — requires the `--execute` flag **and** typing `DELETE` when prompted:
   ```bash
   python scripts/data_cleanup.py --delete --execute --ids <curated>.json
   ```

### What deletion does, per candidate

* **Junk / too-few-images book** → its `books` doc + dependent `pages`,
  `characters` (and their `aliases`), aliases tied to the book, **and** its
  `sawimages/{title}/` S3 objects.
* **Orphaned S3 images** → the S3 objects under that prefix.
* **Junk author / illustrator / publisher** → its doc, but **skipped with a
  warning** if any book still references it.
* **Dangling page / alias** → that single document. **Dangling character** →
  the character doc plus its aliases.

Every deletion is appended (with a UTC timestamp) to the deletion log
(`--log-file`, default `<report-dir>/deletions.log`).

### Safeguards

* Audit is read-only; delete is **dry-run unless** `--execute` **and** a typed
  `DELETE` confirmation.
* `users`, `edit_log` and `collections` are never touched (a hard guard raises
  if a record ever targets them).
* Operates only on the reviewed input list you pass via `--ids`.
* Narrow, surfaced exceptions — no silent swallowing.
* Idempotent / re-runnable: deleting already-absent data is a no-op.

### Plain ids-file format (alternative to the JSON)

`--ids` also accepts a text file, one entry per line (`#` comments allowed):

```
books/some_test_book          # book + dependents + S3 folder
authors/test_author           # author doc (skipped if still referenced)
characters/some_book_goblin   # character + its aliases
pages/some_book_3             # single page doc
s3:sawimages/Orphan Folder    # S3 prefix only
```

## CLI reference

| Flag | Meaning |
| --- | --- |
| `--audit` | Phase 1 read-only scan (default if neither mode given). |
| `--delete` | Phase 2; dry-run unless `--execute`. |
| `--execute` | With `--delete`, actually delete (also needs typed `DELETE`). |
| `--dry-run` | With `--delete`, force dry-run (the default). |
| `--ids FILE` | Reviewed candidate JSON or plain ids file. |
| `--report-dir DIR` | Output dir for reports/log (default `./cleanup_reports`). |
| `--log-file FILE` | Deletion log (default `<report-dir>/deletions.log`). |
| `--secrets PATH` | secrets.toml path (default `.streamlit/secrets.toml`). |
| `--bucket NAME` | S3 bucket (default `sawimages`). |

## Running it

This must be run by Chris (or in the main working tree) where the real secrets
exist — an isolated agent worktree has no secrets and must not touch production.

```bash
.venv/bin/python scripts/data_cleanup.py --audit
# ...review + curate the JSON...
.venv/bin/python scripts/data_cleanup.py --delete --ids cleanup_reports/cleanup_candidates_<ts>.json   # dry-run
.venv/bin/python scripts/data_cleanup.py --delete --execute --ids <curated>.json                       # real
```

## Tests

Pure logic (junk-name heuristics, image-count classification, report rendering,
record loading, and the dry-run / execute planner) is unit-tested against an
in-memory fake backend with **no network access**:

```bash
.venv/bin/python -m pytest tests/test_data_cleanup.py
```
