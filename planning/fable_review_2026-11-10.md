# Review of the work since 4 November — Fable 5.2, 10 November 2026

**Scope.** The 37 commits from `133f004` to `a89fd46` (about 17,000 lines of code, 20,000 of
tests and 90,000 words of design) and the sealed freeze. Six read-only review tracks ran in
parallel, all on Fable 5.2; I re-checked every blocker myself. Nothing was edited, sent or
spent. Requested by Chris before committing API credit, with publishable results and a
reusable corpus as the goals.

**Evidence key.** **R** = re-checked by me in the code or data. **T** = reproduced or traced
by a review track. **S** = suspected, not confirmed.

## Bottom line

The sealed draw is sound and the harness's arithmetic is right. Four things are not:

1. Book text is publicly readable on GitHub today (§1).
2. `claude-dev` is not safe to merge to `main` (§2).
3. The paid runs, as configured, would buy results that mean nothing (§3).
4. The validation design cannot pass whatever the pipeline does, and its text proof rests
   on a fact that is wrong (§4).

No money has been lost and the draw is unaffected. Everything below is fixable before any
spend.

## 1. Act today

| # | Finding | Evidence | Smallest fix |
|---|---|---|---|
| 1.1 | **The repository is public and holds near-complete text of the 20 development books.** `tests/fixtures/alias_dev20_predictions.json` (about 22,000 words) has been on `origin/claude-dev` since the 7 November push. `text_content/pipeline_examples.json` holds about 11 KB of verbatim excerpts. `reports/pages_to_review.md`, on `main` since July, quotes snippets from 79 books. | R: both the repository and the fixture return HTTP 200 without credentials | Make the repository private, or replace the fixture with synthetic text and purge it from history. Take the few-shot excerpts to the university copyright adviser. |
| 1.2 | A pushed planning note lists three student usernames beside their unfinished books (`planning/pilot_empty_text_findings_2026-11-04.md:35-39`). | R | Replace with the pseudonyms the later documents already use. |
| 1.3 | **The paid baseline exists only under `/tmp`.** The session scratchpad holds the sole copies of `spend.sqlite` (1,669 paid responses, $15.05), the dev20 snapshot and run, the 276-book census behind the "57 books" count, and the three scripts that made the freeze. `/usr/lib/tmpfiles.d/tmp.conf` is `D /tmp … 30d`: emptied at every boot. Up since 3 November. Because of 3.1, the current code cannot regenerate the baseline. | R | Copy the scratchpad (less `pilot-v1`, already sealed) to `autonomy-freeze/evidence/`; commit the freeze script. |
| 1.4 | The freeze has one copy. No copy exists on any other disk. | R | Copy the folder to `/media/storage` (RAID1, already holds the original study's photographs). No external drive needed. |

## 2. Do not merge `claude-dev` to `main` yet

`main` is still at the 20 July merge, so production is unaffected today.

| # | Finding | Evidence | Smallest fix |
|---|---|---|---|
| 2.1 | **The live prompts are replaced by a candidate that has never been run against a model.** Page, character and metadata extraction all call `prompt_for(...)`, default `rules-plus-icl`; the old prompts are deleted and no variant restores them. The old page prompt excludes signs, labels and sound effects and the new one includes them, so what counts as page text would change mid-corpus. | R: `text_content/ai_prompts.py:253-323`, `extraction_core.py:79-80`; `origin/main` has no `prompt_for` | Restore the old prompts as a named variant and keep production on it until the candidate has passed. |
| 2.2 | **Uploads queue behind one another across all users.** Production starts a worker per upload; `claude-dev` routes every upload through one queue that processes one book at a time. A second student waits for the first's whole book with the Book registered and no Pages yet; an abandoned wait leaves exactly the kind of partial book found nine times on 6 November, so this makes a repeat more likely. | R: `background_pipeline.py:711-766`; impact not load-tested | Keep per-upload workers for the single-book flow; queue only batch jobs. |
| 2.3 | **Delete no longer deletes.** `Character.delete()` and `Alias.delete()` set `entry_status='rejected'` and keep the document. A deleted character cannot be re-added (the form finds the hidden record), and neither the evaluation snapshot nor the results dashboard filters rejected records, so deleted entries count as human reference. | R: `data_structures/character.py:327-353`; no `entry_status` filter in `snapshot.py` | Reactivate on re-add; filter rejected records in both readers. |
| 2.4 | The validation page cache is never invalidated: a validator can overwrite an owner's fix with stale text. | T: `pages/validation.py:445-487` | Clear it when a review is opened or forgotten. |
| 2.5 | App start-up now imports the evaluation harness (`image_processing.py:32`, `alias_schema.py:8`), so a bad edit to the harness or example pack breaks the live app. | T | Move the two shared functions into an app module. |
| 2.6 | No deploy checklist exists. Remember-me cookies are invalidated once; `rename_book.py` is unsafe on post-deploy data. | T | A ten-line checklist; the app track drafted one. |

Sound here: the validation lease is transactional, missing-page creation is create-only,
batch materialisation never replaces a human-edited page, and the offline suite passes
(1,344 tests).

## 3. Before any paid run

| # | Finding | Evidence | Smallest fix |
|---|---|---|---|
| 3.1 | **The baseline arm no longer exists.** `baseline.json` pins no prompt variant, so it renders the new prompts. Baseline and candidate produce identical page-extraction cache keys. The approved $57.73 run would compare the candidate with itself and use up the 40 protected books. | R (default in code); T (93 of 93 dev20 pages missed the paid cache under `baseline.json`) | Restore the 5 November prompts (`4581496` matches the baseline's recorded hashes), make `prompt_variant` required, and require a dry run with near-total cache hits before a paid baseline. |
| 3.2 | **The protected-40 run can only use the unguarded mode.** `--original-experiment` sets `loader = None`: no freeze check at all. `--limit 40` selected 21 proof books on a manifest built from the real ids. | R: `cli.py:164-170`; T | In that mode still load the freeze and refuse anything outside protected40 and the exposed set. |
| 3.3 | **The research variables are not scored.** Gender, human, protagonist and plural appear nowhere in the scoring code. On dev20 the pipeline's `human` flag disagrees with the reference on 19 of 82 matched characters, 18 of them the same way (reference non-human, pipeline human). | R (no such endpoint); T (the dev20 counts) | Four count endpoints over matched pairs and a confusion table, registered before any held-out output. |
| 3.4 | **Invented text on wordless pages is invisible to the paired comparison.** Adding a fabricated sentence to all 101 agreed-wordless dev20 pages moved no comparison endpoint. | T; R (reference-empty pages return before scoring, `scoring.py:152-162`) | Register wordless agreement and the invented-token count as endpoints. |
| 3.5 | The few-shot examples come from 10 of the 20 dev books, with no leave-one-out, so a dev20 re-score partly tests the pipeline on its own examples. | R: `text_content/pipeline_examples.json`, 26 examples, 10 books | Exclude the target book's examples when rendering, or read the ablation on the other 10 books. |
| 3.6 | The frozen sampling design does not reach the report. `--books` yields `descriptive_only`, unweighted, no intervals; nothing can build an imported-80 selection carrying its 42/63 and 38/58 probabilities. | T | `extract --selection <frozen file>`; weighted single-arm estimates. |
| 3.7 | The comparison's 37 zero-margin gates fail a no-change candidate: with two arms of equal true quality on 40 books, at least one gate failed in 99% of simulations. The protocol names book-macro F1 as primary; the code compares token-micro. | T | A few named primaries, judged on an interval with a margin. |
| 3.8 | An output identical to the reference can score below 1.0 on character and alias recall when two characters share an alias. | T (reproduced) | Entity recall as maximum-matching size. |
| 3.9 | The adjudication overlay seeds corrections from the model's own output, so the seeding system scores 1.0 by construction; a second alias correction for the same book is rejected. | T | Record adjudicated truth independently of any output; fix the second-correction check. |
| 3.10 | **Evaluation and the live app draw one credit balance**, which stops hard when exhausted. The local cap defaults to the full $200, follows the calendar month rather than the billing cycle and cannot see app usage. | T, from Anthropic's published credit and workspace pages | Chris: put the evaluation key in its own Console workspace with a spend limit. |
| 3.11 | A rate limit is retried with no delay and then cached as a permanent failure; a billing stop is cached for ever, even after credits renew; one timeout halts the run. Recovery is hand-edited SQL or re-billing the run. | R: `gateway.py:157-210` | Honour `retry-after`; one `retry-failed` path. |
| 3.12 | **OpenAI adapter.** No `store: false`, so page photographs of in-copyright books are retained by default. Reasoning settings are rejected, so the pipeline's 16-token rotation calls return empty, are billed, and are cached as successes. Cache writes are priced at zero. | R (request body, `openai_adapter.py:49-71`); live behaviour S | Set `store: false`; allow a per-stage reasoning setting and token floor inside the request identity; price cache writes. All before the first call. |
| 3.13 | **The harness validates a configuration production cannot run.** The production job validator silently drops six candidate settings; production parses leniently and the harness strictly; whole-book story classification exists only in the harness. | T | One configuration object read by both, and a test that both build identical request bytes. |
| 3.14 | Three *Grinch* pages were blocked by the output content filter and one page was refused; the candidate schema now asks for a verbatim quote per mention, which may make this worse. | T (dev20 ledger); S (effect of the new schema) | Smoke-test two long books for under $1 before the dev20 run. |

Sound here: the hard cap stops before overspend, no request is re-sent silently, raw
responses are stored before parsing, the cache key is complete, both OpenAI usage shapes
normalise correctly, and every price in `ai_pricing.py` matches the published rate except
OpenAI cache writes. The weighted ratio, the whole-book bootstrap and the finite-population
interval were recomputed independently and agree.

## 4. The validation design

| # | Finding | Evidence |
|---|---|---|
| 4.1 | **The "human-typed" pilot text is not human-typed.** 55 of the 57 proof books (674 of 702 pages) were created between 8 July and 21 September 2026. #157 (on `main` by 7 July) left the photo flow as the only menu route for adding a book, and that flow writes Claude's transcription into `page.text` for the person to review; characters are auto-suggested the same way. The edit log records validator corrections only, and none of the 57 has been validated, so nothing shows what was changed. The pilot cohort therefore holds no independent human page text (the original books do: see 4.11). The claim was mine (6 November, "Verified…"); it checked who entered the books, not how the text got there. | R: `pages/uploader.py` at `700edf8`; census dates |
| 4.2 | **Every quality gate needs a perfect score.** "Set at the ceiling" means only an error-free result passes (design lines 174 and 403). On the realised holdout (652 character rows, 71 alias rows) a pipeline with 99% character recall passes 0.1% of the time and one with 99.5% passes 4%. Dev20 character recall is 0.854. | R: design text; 0.99^652 |
| 4.3 | **The non-inferiority test cannot pass under the design's own formula.** With the AI exactly matching the human on every book, the lower bound is −0.32 (57 books) or −0.27 (80), against a required −0.01. An AI 20 points better on every book still fails. The design calls this "a real power limitation" and keeps it as a release condition. | R: re-derived |
| 4.4 | No single analysis is pre-specified. For one claim the design offers three interval methods without saying which decides, and none of the proof analysis is coded. `freeze.json` is not in git. | T |
| 4.5 | The "AI better than human" test is unsound as written. Fields with 100 or fewer disagreements are censused, so the "lower bound above 0.5" is just the observed share and any majority passes. Items are the unit, so one book with ten omitted aliases counts ten times. For aliases, direction is confounded with conventions the adjudicator wrote this month. | T |
| 4.6 | The reused speech labels were not made blind. The original notebook shows GPT-4o's output while each sentence is labelled; `FOUND` is the labeller pressing "Is in GPT4o output?"; unpressed buttons default to 0; the file holds no book or text. "Beat 260/272" is not like-for-like, and overlap with the holdout cannot be established. | R: `speech_detection_validation_gpt4o.ipynb` cells 14–18 |
| 4.7 | The pilot proof cohort is neither frozen nor clean. It lives in the editable production database and its ids are in no sealed file. Two pairs look like the same work, one "completed" record has 3 of 19 pages, another has one page, and four of the books had page images in the July rotation evaluation. Independent works are plausibly 54–55. | T (titles and counts only) |
| 4.8 | The exposure record is incomplete. A planning note quotes reference aliases, 13 of the 71 held-out alias rows among them. `reports/pages_to_review.md` carries content notes on at least 36 held-out books. Untracked `scripts/rotation_prompt_eval/work/sample/` holds page photographs from 5 held-out and 2 protected books. 45 of the 80 held-out works share an author with tuning material. | T |
| 4.9 | **The page images stored for the original books are renders of the reference text itself.** Each is a page of the Word document made from the scan in 2021–22: typeset text on white, with pictures reduced to grey crops or missing. A pipeline reading them is reading the reference, so the 0.99 OCR figure is not evidence about book pages. Nothing in them shows whether a character is a person or an animal, which fits the `human` result in 3.3. **The original colour scans survive** at `/media/storage/Gender in Children's Literature/Books/`: 197 books, one colour image per page, no text layer; 175 of 192 have the same page count as their text PDF. | R: sealed image and both PDFs for one exposed book; PDF metadata for all |
| 4.10 | The seal is thinner than §1D: no `frame.json`, a flat list of 80 rather than the grouped exclusions, no realised counts. The relation protocol and audit-interface documents still describe a custodian, an undrawn sample and two reviewers; the single-adjudicator ruling is unlogged. `DECISIONS.md:527` calls page-level C(702) "the strong text evidence" where the design calls that pseudoreplication. | T; R (the DECISIONS line) |
| 4.11 | **The original books' text is human-entered, not machine-made** as DECISIONS 015 item 2 and the design say. All 197 text PDFs are Word exports (`Microsoft Word for Microsoft 365`) of documents the original team made from the scans; Chris confirms a person entered the text. Two thirds of the stored page text was then altered by a Claude cleaning pass at import; the unaltered text can be re-read from the PDFs. Added the same day, after Chris corrected the first version of this review. | R (PDF metadata); Chris (who entered it) |

### Proposed replacement

A two-page analysis plan, drafted by Astra and ratified by Chris, that supersedes §2 and §5
of the design:

- **Estimate, don't certify.** For each endpoint report the observed rate with a one-sided
  95% lower bound from the whole-book bootstrap that already exists and is correct. Keep
  C(n) only for the case where no error is observed.
- **One named primary per field.** Text: token recall and invented-token rate. Characters:
  recall of recorded characters. Attributes: agreement on gender, human and protagonist.
  Aliases: recall, reported descriptively.
- **Success means non-inferior to the human-entered record**, margin one point as already
  ratified, tested on paired book-level differences with the same bootstrap. For direction,
  adjudicate disagreements source-first and use an exact sign test over books.
- **Text: run the original books from their colour scans** and score against the original
  study's human-entered text. That is an end-to-end test with no new annotation, which
  Chris ruled out on 10 November. The pilot books then show agreement with human-reviewed
  text on phone photographs, and are labelled as that.
- **Protected 40.** Its old-versus-new comparison no longer exists at HEAD. Either restore
  the old prompts and run it as planned, or use the 40 as an untouched dress rehearsal of
  the frozen analysis. The plan should argue one.
- **Pre-specify an author-disjoint sensitivity analysis** and report alias recall with and
  without the nine books whose aliases were quoted.
- **Commit the plan, the tested analysis code and `freeze.json` (ids only) and tag them**
  before any proof output. Keep an append-only list of proof runs.

## 5. Data quality and the corpus

| # | Finding | Evidence |
|---|---|---|
| 5.1 | A failed page is stored as a blank page: `Page` has no extraction-status field, and failure writes empty text with `contains_story=False`. Five of 767 dev20 pages failed this way. | T |
| 5.2 | Missing model output becomes an asserted value in production: a character with no `human` field is stored `human=True`, an invalid gender becomes "Non-specific", a missing story flag becomes False. The harness rejects the same replies. | T (reproduced) |
| 5.3 | No record carries the model that produced it. `Character` has no source field, admin changes to the seven model settings are unlogged, model ids are floating aliases and no temperature is set or recorded. | T |
| 5.4 | The only code that writes characters without a click, `reconstruct_book_from_photos`, stores AI aliases as `legacy_unverified` (indistinguishable from student aliases), keeps only the first author and illustrator, and drops the evidence. Nothing persists what the harness scores. | T |
| 5.5 | Imported records store "never coded" as codes: ethnicity and disability are "Not specified" and plural is False on all 1,494 characters; all 195 books are `validated=True` by `pilot_import` though no person validated them; illustrator and publisher come from an AI web lookup with no provenance. | T |
| 5.6 | No stable work identifier: none of the 195 imported books has an ISBN or edition; the title slug is the only key. | T |
| 5.7 | Nothing is decided about releasing a corpus: what may be deposited, on what licence, with what consent from the people who entered records. No codebook or datasheet exists. | T (repo-wide search) |
| 5.8 | Quality flags are recorded and nothing acts on them. The acceptance policy the design scores is not in code. | T |

**Smallest route to a reusable corpus:** (1) add `extraction_status`, model, pipeline version
and source to each record, and parse strictly in production; (2) export never-coded fields
as missing; (3) one codebook and one datasheet, a markdown file each; (4) capture ISBN and
edition; (5) take the copyright, licence and consent questions to the university's copyright
and research-data services before designing a release. Derived annotations and identifiers
are the likely deposit, not photographs or full text; that is their call, not ours.

## 6. How the work was done

- **Three things I reported as verified were wrong in substance:** the pilot text as
  human-typed (4.1), the 650 speech labels as plain human truth (4.6), and the `main` merge
  as clean (§2). Each time I confirmed that a number reproduced or a merge had no conflicts,
  not how the data came to exist or what the code would do. A claim a design rests on must
  cite the code path or record that produced the data. The first version of this review
  made the same slip once more: it said no cohort held independent human text without
  opening the original study's files (4.11).
- **Ninety thousand words of design in four days cannot be audited by anyone**, and the one
  fact that had to be right was wrong in the middle of it. The binding content should be the
  two-page plan; the rest is background.
- **Over-built, and worth cutting:** the complement weighting (it moves the ceiling from
  0.9632 to 0.9629), the E/q formula, seven-decimal thresholds, the hypergeometric
  projection and acceptance-profile tables; the self-typed billing attestation; the second
  price table in each config; 27 of 59 registered endpoints that are undefined on dev20, six
  with no producer; the legacy overlay branch; `protocol.json`.
- **Housekeeping:** `CLAUDE.md` says Python 3.10 and the environment is 3.12.3;
  `requirements.txt` is unpinned; two `CONVENTION_VERSION` constants disagree. The pipeline
  pins `claude-sonnet-5`, `claude-sonnet-4-6` and `claude-haiku-4-5`. All three are
  previous-generation but still active, with no retirement announced (Anthropic's API
  reference, cached 3 November; an earlier draft said "Legacy" from a search summary). Each
  has a successor at the same price or less, so the tuning phase should compare models
  before a proof freezes one.

## What is sound, so it is not re-litigated

- Both draws replay identically from the sealed manifest under Python 3.10, 3.11 and 3.12;
  the id and file hashes match; the sets are disjoint and their union is 161.
- All 6,741 sealed images match their checksums; pages are contiguous; no dangling aliases.
- The frame account 200 → 195 → 161 is principled at every step.
- Every number quoted in the designs recomputes.
- The default tuning path fails closed on 11 of 11 bad inputs.
- The prompt examples draw only on exposed books; no held-out alias is in any prompt.

## Not checked

Any live behaviour of either API; the live Firestore and S3 since the 4 November capture;
the content of the 120 sealed books; whether an evaluation key distinct from production
exists (the secrets file was not opened); real concurrency on Streamlit Cloud.
