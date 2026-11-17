# Fable's review of Astra's findings, and a revised plan (17 November 2026)

Fable (claude-fable-5-2), read-only, as an independent check of
`review_value_method_reuse_2026-11-17.md`, with Chris's steer: keen on small screens,
on iterative prompt development on a few books before scaling up, and on batch mode
wherever possible. Verbatim apart from link paths. The orchestrating session rechecked
the edit counts, the ledger's fixed renewal day, the old page prompt's front-matter
instruction, the duplicate-alias counts and the Sonnet 5.5 line before recording it.

**Verdict.** Astra's facts hold against the code, and stopping the full replication programme is right. But it screens the new character prompt, which the baseline data already argue against; its renewal-day fix is blocked by the ledger; and it understates the measurement problem: three quarters of the headline text error is not misreading. I ran nothing; every figure is from files on disk.

**Benefit.** Ideas are killed for cents on a few books, survivors are proved on all 75 at half price, and no published figure means something other than its label.

**Hand check** (old prompts, 30 Iterate books, `evaluation_runs/baseline-p1-legacy/reports/book_scores.json`). First book: 72 deletions + 133 insertions + 63 substitutions = 268 edits on 1,239 reference tokens; 1 − 268/1,239 = 0.7837, as stored. All 30: insertions 3,777, deletions 669, substitutions 772. Per book, insertions average 15.7 per 100 tokens and the rest 5.2; together 20.9 = 1 − 0.791. So 75% of the text error is extra text; without it the score is about 0.95. The new prompts give deletions 2,007 and substitutions 1,345, so the old page prompt also reads better.

## 1. Astra's findings

| # | Astra | Verdict |
|---|---|---|
| 1 | Independent, character-only comparison | Agree |
| 2 | $2 screen; defer calibration; renewal day 29 | Partly |
| 3 | Acceptance rule proves less than it seems | Agree, and worse |
| 4 | Settle what counts as correct | Agree, stronger |
| 5 | Preserve evidence; version rescoring | Agree |
| 6 | Batch; caching; cheaper image checks | Partly |
| 7 | Typed, joinable export | Agree; not on the tuning path |
| Q1 | Small screens | Agree, under section 2's rules |
| Q2 | Lettering | Decided (025) |

1. Both traps confirmed. The cache key uses the replicate token (`scripts/extraction_eval/gateway.py:215`), while freshness compares run ids that hash the output folder (`scripts/extraction_eval/runner.py:75`, `scripts/extraction_eval/tuning_loop.py:207`). One `prompt_variant` feeds both stages (`extraction_core.py:86`, `:260`).
2. Screen first: yes. Wrong candidate: where the new character prompt finished it was no better, costs 11 times more, and 181 of 386 alias forms are duplicates against 7 of 447 (`evaluation_runs/baseline-p1-v2/reports/summary.md`). Build on the old prompt. The ledger refuses a changed renewal day (`scripts/extraction_eval/store.py:122-125`).
3. Floors are never tied to the incumbent (`scripts/extraction_eval/tuning_loop.py:210-212`). A candidate twice as noisy turns the 2.5% false-gain rate into about 9% (2.09/√2.5 = 1.32 standard deviations). The 53% is right.
4. The old page prompt asks for front and back matter (`text_content/pipeline_prompts.py:111-115`) and the scorer joins every page (`scripts/extraction_eval/inputs_manifest.py:153`). Gender, human and protagonist are best-case pairings over all reference characters, found or not (`scripts/extraction_eval/scoring.py:527-570`).
5. Scoring identity is a fixed string (`scripts/extraction_eval/cli.py:537`).
6. Caching no: under $5, and cache markers change request bodies, so stored responses stop replaying. Image checks no: already cached for all 75 books; changing them forces a $35 transcription re-run.

Q2. The character stage receives lettering by default (`conventions.py:60`); exclude it or character results will not carry over.

**Missed.**
- **Screen results replayed at the gate.** A gate run reusing a screen's replicate token replays responses already selected for looking good. Name tokens by purpose; check each gate log shows one paid character call per book.
- **Nothing guards over-generation.** Recall ignores extra characters; duplicate forms are computed (`scripts/extraction_eval/reporting.py:302`) but not guarded (`scripts/extraction_eval/tuning_loop.py:16`).
- **Unbudgeted first-run cost.** Since decision 024 an old-prompt run makes a book-details call per book that the old-prompt baseline never made (`utilities.py:2189-2192`); the printed estimate (about $1.55) will understate it. My estimate, unmeasured: $5–9 once for 75 books.
- **Model.** Sonnet 5.5 is now the current Sonnet at the same price (bundled Anthropic reference, `shared/model-migration.md:210`); a later switch voids prompt tuning, so screen it before the transcription re-run.

## 2. Revised plan

| Step | Cost | Build first, $0 | Decides |
|---|---|---|---|
| A. Rescore existing runs: story-page text, attribute agreement among found characters, extra-character and duplicate counts. Read every Iterate miss. | $0 | Scorer additions; new scoring id | Hypotheses; question 2 |
| B. Three more character-only passes, old prompts, 75 books, synchronous | $3 plus book-details calls | Merge `cf948bc`; dry run shows no page pending | Which misses recur; question 3 |
| C. Small-book development, synchronous | up to $8; $0.50–1 a try | Character-only variants of the old prompt | Promote or drop |
| D. Gate: promoted ideas on 55 + 20, 4 and 8 repeats, batch | $5 each at an assumed 2.5 cents a book; $3 baseline; $8 more if the 20-run calibration stays | Batch path | Keep or revert, approved rule |
| E. After the 29th: page-prompt tries on 6 short books ($3 each); one transcription re-run on 75 ($17, batch); model screen; rehearsal ($15) and proof ($30) in batch, projected | next cycle | Text measure settled | Page prompt, model, freeze |

This cycle: about $25–40 of $53.25, all Anthropic. OpenAI: $0 without a key, then up to $15 for step E's model screen.

**Small-book stage.** Twelve control books fixed now by hash within alias strata, two of them long; plus up to six case books per idea, chosen because the target miss recurs in every pass of step B (books picked on one bad run improve by themselves). Two passes per try. It can show truncation, rejected output, cost per book, whether targeted misses flip in both passes, and stable losses on controls. It cannot show an average gain: one character is 13.6 points on a book and 1.1 on a twelve-book mean, and one probe book moved from 4/8 to 8/8 with no prompt change. So screens only drop or promote. Against overfitting: hypothesis written first; rules name categories, never a book's names or words; three wordings, then drop the idea; every try logged; at the gate, report the gain separately on panel books and the other Iterate books, and send back a gain confined to the panel.

**Batch.** The dry run already stops at every uncached request (`scripts/extraction_eval/gateway.py:254`). Have it write each one, with its cache key, to a file; a script submits the file as a Message Batch and writes each successful result into the ledger under that key at half price; `extract` then reruns and finds everything cached. Gateway, parsers and keys are untouched: about a hundred lines plus tests. One round for character work, two when transcription changes, about six for never-run books. Limits: 50% off; 256 MB a batch, so transcription splits; usually under an hour, 24 at worst (bundled Anthropic reference, `python/claude-api/batches.md:3-11`). It does not fit development tries, where minutes matter and cents are at stake. One real loss: the ledger's hard per-request ceiling cannot hold, so a batch is admitted on cost measured in a synchronous screen. Saving: about $7–20 this cycle, $60–90 later (projections).

## 3. Before the next paid run
1. Purpose-named replicate tokens (no code).
2. Character candidates leave the page prompt byte-identical; a dry run showing no page pending is the proof (about 5 lines).
3. New scoring id with every scorer change; rescore every compared run (1 line).
4. Measure the book-details cost on one book; set the run cap to match.
5. Leave the renewal day at 1 and avoid paid runs on the 29th–31st; changing it means hand-editing the ledger and re-supplying the $134 cap.

## 4. Questions for Chris
1. **Small tests.** May an idea be dropped after a test on 12–18 of the 55 open books, without running the 20 unread books or counting among the 30 attempts? (a) Yes; anything promoted still takes the full approved test. (b) No, as approved. Recommend (a). Meanwhile: (b).
2. **What the text score counts.** Today the typists' story text is compared with everything transcribed, including covers and copyright pages the prompt asks for. (a) Keep. (b) Headline counts only pages the pipeline marks as story; all-pages figure beside it. (c) Headline counts only missing and misread words. Recommend (b). Unverified: where the old prompts' extra text sits; step A shows it.
3. **Run-to-run wobble.** The approved rule learns it from 20 repeat runs ($20, or $10 in batch) and assumes a changed prompt wobbles the same; strictly it needs repeating after every accepted change. (a) As approved. (b) Estimate it from the 4–8 repeats each comparison already makes, both versions, no extra cost. (c) Decide after step B. Recommend (c), leaning (b), which I have not simulated.
4. **Backup.** Everything replays $79.59 of paid responses from one file on one disk; losing it costs about $55 and changes the baseline. (a) Copy it to existing private storage now. (b) Later. Recommend (a).
5. **Duplicate aliases.** You accepted extra aliases "provided they are not duplicates". Should a rise in duplicates (a) block a change or (b) only be reported? Recommend (a).
6. **Scans or photos.** Tests use flatbed scans; the app receives phone photos. (a) Claim accuracy for scans only. (b) Also run about five phone-photographed open books, about $4. Recommend (a) now, (b) before any claim about the app.

**Not worth doing.** Prompt caching; cheaper image checks during tuning; raising the 20,000-token ceiling; loosening the configuration id (it fails loudly; run each incumbent from its commit's frozen worktree).

**Verification gaps.** `cf948bc`'s tests use fake requests; only the real dry run proves replay. New scores need a hand-checkable fixture; batch needs a same-keys, half-cost test.

**Not checked.** No command or test was run. Unverified: whether `cf948bc` exists in git or only as a scratchpad diff (it is committed on branch `impl/stage-replicates`), the batch id length limit, the real book-details cost, OpenAI paths. No Check per-book record or sealed file was opened.
