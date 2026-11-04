# Pilot corpus: empty story pages — measured findings (2026-11-04)

Read-only measurement of the production Firestore (`books` + `pages`
collections). No writes, no S3 mutations, no Anthropic calls.

**Corpus:** 276 books, 8,118 page documents.

## Headline

| Measure | Count |
|---|---|
| Books with **no text on any page** | **8** |
| Books where **every** `contains_story` page is empty | 0 |
| Books where **some** `contains_story` pages are empty | **193** |
| Books with **no page documents at all** | 6 |
| Total empty `contains_story=True` pages (pilot_import) | **~896** (~13% of corpus pages) |

An earlier figure of "17 books without text" given in conversation was wrong
and should not be relied on.

## The 8 books with no text anywhere

Five are `pilot_import` metadata-only shells — zero page documents **and** no
S3 folder, so there are no photos to re-extract from. They are unrecoverable
and must be excluded from any validation corpus:

- I Love Christmas
- Invisible Isabelle
- Seasons
- Ten Wriggly, Wiggly Caterpillars
- Things You Should Know about Bugs

The other three are student books still at `entry_status='started'` —
incomplete entry, not a fault:

- Mike's New Knee (account 1) — 7 page images in S3, 0 page documents. Expected:
  page documents are created lazily when text is first entered (#231).
- Chimpanzees for TEA! (account 2) — 18 page documents, no text.
- The Wonder (account 3) — 15 page documents, no text.

## The 193 books: wordless pages, not lost transcriptions

The positional signature rules out a transcription failure:

- **751 of 820** empty stretches are a **single isolated page**; 63 are pairs,
  6 are longer. A failed OCR pass or an abandoned entry session would cluster.
- Empty pages are distributed **uniformly across the story range** (roughly 5–9%
  of occurrences in each decile), not concentrated at the front or back where
  front matter, copyright pages and endpapers sit.
- **Zero** adjacent exact-duplicate text pairs, so this is not spread text being
  written to only one page of a pair.

Cause: `scripts/import_pilot_data.py` defaults the entire range to
`contains_story=True` when the pilot spreadsheet supplied no story-page range.
Full-bleed illustration pages inside the story therefore carry
`contains_story=True` with no text. This is normal picture-book structure
recorded under a label that is too broad — not missing data.

## Consequence for the validation harness

`planning/validation_harness_design_2026-11-04.md` proposes excluding these
pages from the OCR denominator as `unknown_missing_transcription`. **That is the
wrong default.** These ~896 pages are exactly where the automated pipeline can
hallucinate text onto a wordless page — the failure mode most likely to corrupt
the research record, and exclusion makes it unmeasurable.

Score agreement instead:

- Run the pipeline over them like any other page.
- Pipeline also returns empty -> two independent sources agree the page is
  wordless. Record as `agreed_wordless`; it is positive evidence, not an
  exclusion.
- Pipeline returns text -> `disputed_wordless`. Only these need human
  adjudication, which should be a far smaller set than 896.
- Report `agreed_wordless` / `disputed_wordless` as a named subgroup with its
  own denominators, and never let a wordless page score as a perfect OCR match
  simply because both sides are empty strings — count it in a separate
  wordless-agreement rate, not in token F1.
