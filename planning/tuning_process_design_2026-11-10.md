# Tuning process design — 10 November 2026

**Status: design for Chris's approval. Nothing run or spent.** It applies DECISIONS 017 item 8 inside the ratified [analysis plan](analysis_plan_2026-11-10.md). The procedure is the loop in §5; the rest makes its numbers trustworthy.

## 1. What the baseline's errors actually are

From the exposed dev20 run: 20 books, 96 recorded characters, 82 matched under the ratified strict rule. Its inputs were typeset renders without pictures, so text errors were not analysed.

| Field | Errors | Kind | Count | Record → pipeline | Reached by |
|---|---|---|---:|---|---|
| Characters | 14 missed of 96 | Found, under a fuller name | 10 | cat → The Old Cat; Dad → Noi's dad | prompt, examples |
| | | Record splits what the pipeline groups | 3 | worm, worms → Worms | convention, examples |
| | | Genuinely omitted | 1 | elves | |
| Human | 19 wrong of 82 | Non-human called human; 16 are animals with ordinary names, in three books | 18 | Marlon, Granny, Billy | **pictures** |
| Gender | 9 wrong of 82 | Pronoun in the next sentence missed | 2 | hare, hyena → Non-specific | prompt, examples |
| | | Gendered noun, no pronoun | 2 | cow, hen (record Female) | convention (Q1) |
| | | Pipeline assigns what the record leaves open | 3 | Santa-nav → Female | prompt |
| | | Unclear from the text | 2 | | |
| Protagonist | 5 wrong of 82 | Ensemble cast; record marks several | 4 | one book | convention, examples |
| Pages | 5 failed of 772 | Blocked 3, refused 1, truncated 1 | 5 | | fallback |

- **The character gap is naming, not omission:** 85.4% strict, 95.8% once the ten fuller names count. Tuning must not close it by inventing names.
- **Human/non-human is beyond prompts and examples:** the character stage never sees a picture.
- **All nine gender errors concern non-human characters.**

These carry over to scans. Text figures do not, and the human finding can only be tested there.

## 2. What "better" means

The five ratified primaries, read as the mean over the Iterate books and the paired per-book change from the last accepted version. A tolerant character match is shown beside the strict one, to separate naming from omission. Four guardrails may not be traded away:

- **Alias groundedness** (emitted names found in the page text; 92.9% on dev20) must not fall, so recall cannot be bought with invented names.
- **Inserted text** (tokens added that the reference lacks, per 100 reference tokens) must not rise. Invented text on wordless pages shows here.
- **Failed pages** per 100 (0.65 on dev20) must not rise.
- **Cost per book** ($0.75 on dev20) may rise only for a stated gain.

## 3. Which books do what

| Set | Books | Use | Resolves a paired gain of about |
|---|---|---|---|
| Iterate | 55: the 34 exposed and 21 of the unused 41 | Every failure is read; examples come from here | 3 points |
| Check | 20 of the unused 41, ten per alias stratum | Scored only; no failure is read | 4 to 5 points |
| Rehearsal | 40 protected | Once, after freeze; then joins Iterate (95; 2 points) | |
| Proof | 80 held out | Once | ±4 to ±8 (analysis plan) |

- **Sharpness** is twice the standard error, taking the SD of per-book changes as 10 points, half the between-book SD on dev20. Measure it at the first comparison.
- **Check draw:** the first ten ids per stratum by SHA-256 of `tuning-check|20261110|<stratum>|<id>`, saved in the freeze folder before the baseline.
- **Leave one book out:** a book that supplied an example is scored with its own examples removed. `load_example_pack(held_out_book_ids=…)` exists but is not wired in; ten dev20 books supply all 26 examples today.

## 4. Run-to-run noise

Temperature cannot be pinned: Anthropic's release notes say non-default `temperature`, `top_p` and `top_k` return an error on Claude Sonnet 5, and thinking is adaptive by default. So per stage, set, record and put in the request identity what the API does accept: model id, thinking mode, `output_config.effort`, maximum tokens. Record the served model, token usage and stop reason.

Measure the floor before believing a small gain. Rerun the unchanged baseline with `--replicate`: the character stage twice more on all 55 Iterate books (about $1), the page stage twice more on ten (about $9). Each primary's floor is the SD of its Iterate mean across the three runs.

## 5. The loop

1. **Baseline from scans.** Run the old prompts and the new on 30 Iterate books; carry the better to the other 25 and the Check set. The `main` merge is waiting on this comparison.
2. **Noise floor** (§4) and **model screen** (§7).
3. **Error analysis.** Read every miss on the Iterate books, assign each to a §1 category, count.
4. **One hypothesis, one change:** a rule, an example or a structural step, aimed at the largest category a lever can reach.
5. **Re-score the whole Iterate set**, not just the cases the change was written for.
6. **Accept only if** the category's count falls; the targeted primary rises by more than twice its floor, or the count falls by a third; no other primary falls by more than its floor; no guardrail worsens. Otherwise revert.
7. **Log one row per attempt:** hypothesis, change, counts before and after, primaries, guardrails, cost, verdict. Reverted attempts stay.
8. **Consult the Check set three times only:** after the baseline, when Iterate stops improving or after five accepted changes, and before freeze. Read its five figures and nothing else. If Iterate has gained and Check has not, beyond Check's own noise, remove recent changes newest first.
9. **Stop** when no category has five cases a lever could reach, after 15 accepted changes, or when the cycle's budget is spent.

Work the character stage first: it costs about a cent a book and holds the measured gaps. Batch text-stage changes; each re-pass costs about $25.

## 6. The two levers, and what neither reaches

**Examples.** A fixed curated pack. Retrieval is cut: it needs an index and makes each prompt depend on the book, for no gain at this scale. Each example names the category it teaches and the log row that added it; at most two per category and twelve per stage, each the shortest excerpt that makes the point. The text stays out of the public repository: the pack in the private S3 bucket the app already reads, a copy in the evidence folder for the harness, and only ids, tags and its SHA-256 in git. Move it before adding an example.

**Prompt development.** An edge-case fix is steps 4 to 6; the full re-score is the regression test. Each accepted fix adds its cases to a must-stay-fixed list checked on every later run.

**Neither lever reaches:**
- *Human/non-human.* One small picture call per book asking whether each named character is drawn as a person, an animal or something else; about 3 cents, the price of today's key-page call. Gender stays text-only, as the codebook requires (Q2).
- *Blocked and refused pages.* A recorded failure status and one fallback attempt. Today a blocked page is stored as blank.
- *The name gap.* A reporting choice (Q3).

## 7. Model choice: first, and small

All three pinned models (Sonnet 5, Sonnet 4.6, Haiku 4.5) are previous-generation. Anthropic's API reference of 3 November lists them as still active with no retirement announced, and each has a successor at the same price or less. Prompts tuned to one model do not transfer cleanly, so screen before tuning, on 12 Iterate books with baseline prompts:

- **Transcription:** Sonnet 5 (incumbent), Sonnet 5.5 (same list price), Haiku 5.5 (from a twentieth).
- **Characters:** Sonnet 5, Sonnet 5.5, Opus 5.5.
- **Rotation** (31% of today's cost): Sonnet 4.6 against Haiku 5.5, on the existing rotation fixtures.
- **Effort:** Sonnet 5 thinks by default at effort `high`, and thinking is billed as output. Screen `low` against the default on transcription, where there is little to reason about.

Under $15. Keep the incumbent unless a candidate is better beyond the noise, or equal and either current or cheaper. Re-screen only if a model is retired before freeze. An OpenAI model joins as a later lap, once the adapter fixes land (review 3.12). Prices, status and the sampling rule in §4 were checked against Anthropic's bundled API reference (cached 3 November); confirm on the live pages before the screen.

## 8. Cost

Per book, from the dev20 ledger ($15.05, 20 books): transcription $0.46, rotation $0.23, metadata $0.05, characters $0.01. So a full pass over 75 books is about $56; a character-stage iteration on 55, with transcription cached, about $1 ($3 with the picture call); a text-stage re-pass on 55 about $25; the rehearsal about $27; the proof about $57.

**Envelope:** limit the evaluation workspace to $150 a billing cycle, leaving $50 of the $200 for the live app (about 65 uploads). Cycle 1: baseline $72, noise $10, screen $15, twelve character iterations $36; $133. Cycle 2: one text re-pass $25, rehearsal $27, proof $57; $109. This assumes scan pages cost what the stored renders did; the first two books check it.

## 9. Prerequisites, in order

Numbers in brackets are sections of the [review](fable_review_2026-11-10.md).

| # | What | Accepted when |
|---|---|---|
| 1 | Evaluation key in its own workspace, $150 limit (3.10; Chris) | A probe call is billed there |
| 2 | Ledger and evidence on a durable path (1.3) | A run resumes from cache after a reboot |
| 3 | Scan inputs, references from the Word exports, page mapping frozen on exposed books | All 55 Iterate books load; unmapped ones are listed |
| 4 | Prompt variant explicit; old prompts restored as a variant (3.1, 2.1) | Unchanged config replays dev20 from cache; no variant is refused |
| 5 | Primaries, ranges and §2 guardrails coded (3.3, 3.4, 3.8) | Identical output scores 1; fabricated text moves inserted text |
| 6 | Selection from a frozen file; freeze guard in every mode (3.2, 3.6) | Any held-out or protected id is refused |
| 7 | Leave-one-book-out wired into rendering (3.5) | Run record shows no example from the book scored |
| 8 | Failures recorded and recoverable (3.11, 5.1) | A simulated rate limit costs one call; a blocked page counts as failed |
| 9 | Generation settings explicit, in the request identity (§4) | Changing effort misses the cache |
| 10 | One configuration for harness and production (3.13) | Identical request bytes on a fixture page |

Not needed for tuning: the OpenAI adapter, the adjudication overlay, and the app defects in review §2, which gate the `main` merge.

## 10. Hand-off

Freeze: commit and tag the prompts, the example pack's hash, models and settings, scoring code, page mapping and the change log. Run the frozen pipeline and analysis once on the protected 40.

- **The rehearsal may change** anything mechanical that stops the analysis running as written: inputs, mapping, scoring bugs, failure handling. Each is fixed, re-tagged and noted.
- **If its figures fall short of the Check set's** beyond what the ranges explain, tuning reopens with the 40 added to Iterate; the unread Check set is then the only clean signal left.
- **It may not change** the five primaries, the interval, the references or the held-out ids.

Then the proof on the 80, once, every attempt listed.

## What this cuts

Automatic prompt optimisers; retrieval of examples; cross-validation folds; a model acting as judge; per-change significance tests beyond step 6; a separate regression framework; aliases as a tuning target; the 57 pilot books in the loop; OpenAI in the first lap.

## Open questions for Chris

**Q1. Gendered words without a pronoun.** Your written rule codes gender from pronouns only. The original coders also used the word itself: in the development books "cow" and "hen" are recorded Female though the text gives them no pronoun. Which should the pipeline follow?
- *The written rule.* Such characters stay Non-specific. Agreement with the record is slightly lower (2 of 9 gender disagreements) and the corpus matches its codebook.
- *The record's practice.* Words such as hen, queen and mum count. Agreement rises and the codebook is reworded to say so.

**Q2. Pictures for human versus animal.** The pipeline decides whether a character is human from the text alone. In three of 18 development books almost every character was wrong because they are animals with ordinary names; mistakes of that kind are 18 of the 19 errors. The original coders plainly looked at the pictures. May the pipeline look at them for this one question, never for gender?
- *Yes.* One small extra call per book, about 3 cents. It is the only way this figure improves.
- *No.* Human/non-human stays near 77% and is reported as unreliable for books that never say what their characters are.

**Q3. Fuller names.** The ratified plan counts a recorded character as found only when the pipeline uses the same name. In the development books 10 of the 14 "missed" characters had been found under a fuller name, such as "The Old Cat" for "cat": 85% strict, 96% counting those. Which is the headline?
- *Strict, as ratified.* The figure measures naming as well as finding. The pipeline is taught to emit the short form too, and the tolerant figure is reported beside it.
- *Tolerant.* A fuller or shorter name counts when it points to exactly one character, with strict reported beside it. This amends the plan, at no cost now because no scoring code is tagged.

## Sources

[Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing) · [Plans and pricing, Legacy section](https://claude.com/pricing) · [Release notes](https://platform.claude.com/docs/en/release-notes/overview) · [Adaptive thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking) · [Model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations)
