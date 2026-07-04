# Pilot corpus import (`scripts/import_pilot_data.py`) — issue #73

A careful, high-accuracy, **dry-run-first** CLI that imports the
`fair-tales-methods` pilot study corpus (~200 primary-school picture books)
into the production storage stack — **Firestore** for structured records/text,
**S3** (`sawimages`) for page images — in exactly the shape the Streamlit app
writes today.

It runs **outside Streamlit** (like `scripts/data_cleanup.py`): it loads
`.streamlit/secrets.toml` directly and builds its own `firestore.Client`
(project `sawdataentry`), `s3fs.S3FileSystem` and `anthropic.Anthropic`
clients. It deliberately does **not** import the Streamlit-coupled
`data_structures` package — the Firestore document schema for every entity is
replicated in the script by studying the `data_structures/*.py` classes.

---

## Quick start

```bash
# Always use the project venv:
PY=./.venv/bin/python

# 1. DRY RUN (default — writes NOTHING to Firestore or S3).
#    Prints per-book plan, corpus totals, every match gap, and a 1-book sample
#    of the AI illustrator/publisher lookup + per-page text result.
$PY scripts/import_pilot_data.py --methods-dir ../fair-tales-methods

# 2. REAL IMPORT (writes to Firestore + S3). Idempotent: books already present
#    in Firestore are skipped. Run only intentionally, from the main working
#    tree where the real secrets live.
$PY scripts/import_pilot_data.py --methods-dir ../fair-tales-methods --execute
```

The dry run works from an isolated git worktree (no secrets needed — pass
`--sample-ai 0` to skip the AI sample too). `--execute` requires
`.streamlit/secrets.toml`, so it must be run from the main checkout.

### Useful flags

| Flag | Purpose |
|---|---|
| `--methods-dir DIR` | Path to the `fair-tales-methods` checkout (default `../fair-tales-methods`). |
| `--excel / --db / --pdf-dir / --json` | Override individual source paths. |
| `--secrets PATH` | Path to `.streamlit/secrets.toml` (default `.streamlit/secrets.toml`). |
| `--execute` | Perform the real import. **Omit for a dry run.** |
| `--limit N` | Only process the first N matched books (useful for a small test run). |
| `--sample-ai N` | Dry-run only: run N real AI lookups as a sample (default 1; `0` disables). |
| `--lookup-model` / `--ocr-model` / `--clean-model` | Claude model ids (OCR/lookup default `claude-opus-4-8`; clean/judge `claude-sonnet-4-6`). |
| `--containment-threshold` | **Primary** cross-check: flag books capturing less than this fraction of the validated reference's words (default `0.85`). |
| `--compare-threshold` | Secondary Jaccard threshold (reported only, default `0.60`). |
| `--cache-dir` / `--no-cache` | Local result cache for OCR/clean+judge calls (skips re-paying on a re-run); `--no-cache` disables it. |
| `--no-clean` | Skip the AI clean/judge pass (import raw extracted text). |
| `--max-edge` / `--jpeg-quality` | Page-render resolution (default 2000px long edge) and JPEG quality (85). |

---

## Sources

Located in the sibling `fair-tales-methods` repo:

* **`Book-List-Final-NONA.xlsx`** (Sheet1) — one row per book. Columns used:
  `Title`, `Author ` (note the trailing space), `Author Gender` (M/F, or `M/F`
  for co-authored books), `Starting Page`, `Ending Page`,
  `Year of First Publication`. 206 data rows → 201 unique titles (5 blank-title
  rows are skipped; no duplicate titles).
* **`character_database.db`** (SQLite):
  * `characters` (1509 rows): `index, book, name, gender, human, alias_count, is_protagonist`.
  * `protagonists` (206) — not imported (protagonist status is already on
    `characters.is_protagonist`).
  * `aliases` (202 rows): `index, alias, character, character_id, book`.
    `character_id` is the `characters.index` of the canonical character
    (verified: 0 id/name mismatches).
* **`text_pdfs/*.pdf`** (197 files) — the scanned book pages, one PDF per book.
* **`data/book_dataframe.json`** — `Title → full book text` (whole-book text
  fallback; the importer prefers per-page text and does not currently use this).

---

## PDF tooling & the text-layer finding

**PyMuPDF / `fitz` does not install in this environment.** The working stack is:

* **Rendering pages → JPEG for S3: `pypdfium2`** (pure-Python PDFium binding,
  no external binaries). Verified rendering a page to a ~64 KB JPEG. `pdf2image`
  + poppler (`pdftoppm` **is** present at `/usr/bin/pdftoppm`) is a viable
  alternative but `pypdfium2` avoids the subprocess.
* **Per-page text layer: `pypdf`** (`PdfReader(...).pages[i].extract_text()`).

**Finding (197 PDFs, 6962 pages total):** most books carry a text layer but its
quality is uneven (word-per-line fragmentation on some), and **48 PDFs have no
extractable text on their first pages** (image-only scans). So the importer uses
a **hybrid, accuracy-first** per-page strategy:

* For each **story page** (see page range below), if the `pypdf` text layer has
  ≥ `TEXT_LAYER_MIN_CHARS` (20) characters, use it verbatim for `Page.text`.
* Otherwise fall back to **Claude vision OCR** (`--ocr-model`, default
  `claude-opus-4-8`) on the rendered JPEG.
* **Non-story pages** (front matter, endpapers, copyright) still get a rendered
  image in S3 but an empty `Page.text` and `contains_story=False`.

The dry-run report prints, per book and in the totals, how many story pages have
a usable text layer vs. how many will need AI OCR, so the OCR cost is visible
before `--execute`.

---

## Title matching & coverage

Books are matched across Excel / DB / PDF / JSON by a **normalised title**
(`normalise_title`: lower-case, `&`→`and`, unify curly quotes, collapse
non-alphanumerics to single spaces, trim). This is what reconciles the Excel
trailing-space column, punctuation, case, and the PDF filenames.

Coverage against the pilot sources (as inspected before `text_pdfs`/`data` were
temporarily removed from this environment):

| Check | Count | Notes |
|---|---|---|
| Excel unique titles | 201 | from 206 rows (5 blank-title rows skipped) |
| DB books (with characters) | 193 | |
| PDF files (unique titles) | 196 | 197 files; the `.doc.pdf` duplicate collapses |
| Excel titles with **no PDF** | 5 | `I Love Christmas`, `Invisible Isabelle`, `Seasons`, `Ten Wriggly, Wiggly Caterpillars`, `Things You Should Know about Bugs` |
| Excel titles with **no DB characters** | 8 | the 5 above minus 1 + `All Year Round`, `Ten in the Bed and Other Counting Rhymes`, `The Book With No Pictures`, `You Choose` |
| PDFs with **no DB characters** | 4 | `All Year Round`, `Ten in the Bed and Other Counting Rhymes`, `The Book With No Pictures`, `You Choose` (books with no named characters) |
| Duplicate PDF filename | 1 | `Sing A Song Of Bottoms.doc.pdf` duplicates `Sing A Song Of Bottoms.pdf`; the plain `.pdf` wins |
| PDFs not in Excel | 0 | every PDF matched an Excel row |
| DB books not in Excel | 0 | every DB book matched an Excel row |

The dry-run report re-computes and prints all of these live (a book with no PDF
gets no pages/images; a book with no DB characters gets Book + Author only). It
also lists the two DB `human` anomalies and every multi-author row.

---

## What is created per book

Records mirror the app's schema exactly (studied from `data_structures/*.py`),
with references stored as real Firestore `DocumentReference`s on `--execute`:

* **Book** (`books/{title_underscored}`): `title`; `published` = sheet year (or
  AI year if blank); `validated=True`, `validated_by='pilot_import'`,
  `entered_by='pilot_import'`; `author`/`illustrator`/`publisher` references;
  `photos_uploaded=True`, `photos_url="sawimages/{title}"`;
  `first/last_content_page` from the sheet range; `characters` = list of
  character references; `character_count`/`page_count`. Theme flags are omitted
  (no theme data; they default `False` on read).
* **Author** (`authors/{name_underscored}`): `forename`/`surname` (split on the
  last token), `gender` mapped from the sheet.
* **Illustrator** + **Publisher** (single-name entities, #156): names from an
  **AI web lookup** (title + author → illustrator, publisher, and year when the
  sheet is blank). Best-effort — `Unknown` on a miss, in which case no
  illustrator/publisher record or reference is created for that book.
* **Pages** (`pages/{book_id}_{n}`): one per physical PDF page — rendered JPEG
  to `sawimages/{title}/page_{n}.jpg`, `contains_story` and per-page `text` as
  described above.
* **Characters** (`characters/{book_id}_{name}`): from the DB `characters` rows
  for that book. Deduplicated by document id within a book (1509 DB rows →
  ~1507 documents, dropping 2 same-name collisions such as duplicate `we`).
* **Aliases** (`aliases/{book_id}_{alias}`): from the DB `aliases` rows, linked
  to the character resolved via `character_id` (202 rows, all resolvable).

---

## Mapping decisions

| Field | Source | Mapping |
|---|---|---|
| Author gender | sheet `Author Gender` | `M`→`Man`, `F`→`Woman`, else (blank / `M/F` co-author) → `Unknown` (`AuthorForm.gender_options`). |
| Character gender | DB `characters.gender` | `F`→`Female`, `M`→`Male`, `NGS`→`Non-specific`. `CharacterForm.gender_options` is `["Female","Male","Non-specific","Transgender"]` — there is **no "Non-binary" character option**, so any unexpected/`NBT` value also falls back to `Non-specific`. (The DB only actually contains `F`/`M`/`NGS`.) |
| Character human | DB `characters.human` | whitespace stripped; `H`→`True`, `NH`→`False`. The DB holds two strays — `Each Peach Pear Plum`/`I`=`NO` and `The Paper Dolls`/`paper dolls`=`NGS` — which are **flagged in the report** and default to `human=True` (the `Character.human` default). |
| Character protagonist | DB `characters.is_protagonist` | `1`→`True`, `0`→`False`. |
| **Character plural** | *(none)* | **No plural/singular indicator exists in the sheet or the DB.** `plural` is therefore **always `False`** and this is flagged in the report. If a plural column is added later, wire it into `build_character_doc`. |
| ethnicity / disability | *(none)* | Not present in the pilot data → `"Not specified"` (the first `CharacterForm` option for each). |
| Book `published` | sheet year, else AI | 4-digit year in 1900..current; falls back to the AI lookup's year when the sheet is blank; `-1` if still unknown. |
| Story page range | sheet `Starting`/`Ending Page` | Pages in `[start, end]` are story pages (`contains_story=True`, text extracted). No range → every page is a story page. |
| `entered_by` / `validated_by` | *(constant)* | `"pilot_import"` string on every record. |

---

## AI lookup approach

* **Illustrator / publisher / year:** `ai_lookup_book_metadata` sends the title
  + author to Claude (`--lookup-model`, default `claude-opus-4-8`) with the
  server-side **web search tool** (`web_search_20260209`), handling `pause_turn`
  continuations, and parses a JSON reply `{"illustrator", "publisher", "year"}`.
  This is a **UK** primary-school corpus, so the prompt asks for the original UK
  publisher/imprint as printed (e.g. Walker Books, not its US partner Candlewick
  Press), the illustrator as credited, and the year of **first** publication. It
  is instructed **not to guess** and to return `"Unknown"`/`null` when unsure;
  the `"Unknown"` guard is case-insensitive and also treats `""`/`"n/a"` as
  unknown. Any API/parse failure degrades to all-`Unknown` (logged) — never fatal.
* **Per-page OCR fallback:** `ai_ocr_page` sends the rendered page JPEG to Claude
  vision (`--ocr-model`, default `claude-opus-4-8`) to transcribe story text,
  used only when a page lacks a usable text layer.
* **Per-page clean + judge:** `ai_clean_and_judge` (`--clean-model`, default
  `claude-sonnet-4-6`) strips extraction/OCR junk + print artefacts and judges
  `makes_sense` / `fits_context`.
* **Structured outputs + caching:** the OCR and clean/judge calls use Anthropic
  structured outputs (`output_config` json_schema) for guaranteed-valid JSON, and
  prompt caching (`cache_control`) on their static instruction prefix. Results are
  cached locally under `--cache-dir` keyed by content hash, so a crash or
  `--overwrite` re-run reuses them instead of re-paying.

Model ids default to `claude-opus-4-8` to prioritise accuracy on a bounded
corpus. In a **dry run**, only `--sample-ai` lookups are actually performed
(default 1); every other book is planned as `Unknown`. `--execute` runs the AI
passes for every non-skipped book.

---

## Review flags & robustness (issue #73)

* **Per page** (`pages` doc): `needs_review` / `review_priority` / `review_note`,
  plus provenance — `text_source` (`layer`/`ocr`/`none`), `clean_status`,
  `ocr_model`, `clean_model`. A failed OCR call → high-priority flag; a failed
  clean/judge call → normal flag; an empty page that breaks context → high.
* **Per book** (`books` doc): `needs_review` / `review_pages` /
  `high_priority_review` / `review_note`. Books with **no PDF** or **no
  characters** are flagged and do **not** claim `photos_uploaded`; ambiguous
  multi-author rows are flagged for manual author entry.
* **Circuit breaker:** `MAX_CONSECUTIVE_AI_FAILURES` (default 5) consecutive AI
  failures abort the run — safely resumable, because each book's `books` document
  is written **last**.
* **Cross-check:** primary metric is **containment** (recall of the validated
  text, `--containment-threshold`, default 0.85); Jaccard is a secondary "excess
  text" indicator.

---

## Idempotency & safety

* **Dry-run by default** — writes nothing; `--execute` is the only write path.
* **Idempotent** — before each book, `books/{id}` existence is checked and the
  book is skipped if present (unless `--overwrite`). Re-running after a partial
  import re-processes only missing books.
* **Shared entities are never clobbered** — `authors` / `illustrators` /
  `publishers` are written **only if the doc does not already exist**, so an
  import can never overwrite a human-entered author's gender/`entered_by`. This
  makes `--overwrite` safe for shared entities too. Collisions are counted and
  reported.
* Firestore queries/writes use the same client config as `data_cleanup.py`
  (project `sawdataentry`, `.set(..., merge=True)`).
* Narrow exception handling throughout; AI failures now **record a review flag**
  rather than silently blanking/passing, and are logged, never swallowed.

---

## Verification (no live writes)

```bash
PY=./.venv/bin/python
$PY -m py_compile scripts/import_pilot_data.py          # AST/compile check
$PY -m pytest scripts/test_import_pilot_data.py -q       # unit tests: mappers,
                                                         # id derivation, builders
# Dry run against the sources, AI disabled (no secrets needed):
$PY scripts/import_pilot_data.py --methods-dir ../fair-tales-methods --sample-ai 0
```

`scripts/test_import_pilot_data.py` unit-tests the pure mappers
(gender / human / title-normalise / plural default), id derivation, page-range
logic and the document builders — it imports no Firestore / S3 / Anthropic code,
so it is safe in CI.

---

## Exact dry-run → execute commands

```bash
PY=./.venv/bin/python

# DRY RUN (default; writes nothing; safe from a worktree):
$PY scripts/import_pilot_data.py --methods-dir ../fair-tales-methods

# Small live test (first 3 books) — run from the main checkout:
$PY scripts/import_pilot_data.py --methods-dir ../fair-tales-methods --limit 3 --execute

# FULL LIVE IMPORT — run from the main checkout, intentionally:
$PY scripts/import_pilot_data.py --methods-dir ../fair-tales-methods --execute
```
