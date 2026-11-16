# Pronoun handling end to end — 16 November 2026

**Recommendation:** language-analysis should own character-language attribution;
data-entry should supply paginated text and the cast. Existing analysis prompts
already request pronoun resolution; dialogue rules, ambiguity, provenance and
measurement need strengthening.

Code review only; no git, API calls or spending. `L/` means
`../fair-tales-language-analysis/`; notebook cells are zero-based. No sealed
snapshot, scans, Word exports or notebook outputs were read.

Exposure record: August 2024 speech-attribution fixtures in `L/openai_api/tests.py`
are authorised as already exposed; probable, unconfirmed candidate books are
`harry_and_the_dinosaurs_go_wild` (held out) and `the_night_before_christmas`
(protected); no passage-to-book mapping was established or passages reproduced here.

## What the code does today

| Language | Attribution and where references are lost |
|---|---|
| Speech and addressee | `L/openai_api/tasks.py:33–86`: full-text task 1 identifies speakers/recipients without a supplied cast; pronoun interpretation is implicit. Task 2 extracts spoken words. Task 3 matches strings to human SQLite names, aliases and meta-characters without full-book context (`prompts.py:76–115`; cast origin: `L/DATA_README.md:21`). `Unknown` survives, but failed database joins lose gender and disappear from gender-selected vocabulary totals (run notebook cells 80–81; `vocabulary_analysis.ipynb`, cell 4). |
| Actions and descriptions | `L/openai_api/action_extraction.py:205–227` and `adjective_extraction.py:151–175` explicitly request he/she/they/it resolution against human names/aliases. Outputs retain word, sentence and speaker, but no page/offset; exactly one character or `NONE`, without ambiguity. `gendered_words` drivers join names to human gender; `NONE`/failed joins leave character totals. spaCy alternatives use names/aliases and nearest preceding mention for he/she, without the claimed gender check (`adjectives_spacy.py:123–134`); other pronouns silently produce no record (`pos_spacy.py:75–86`). |
| Narration about characters | Analysis extracts verbs/descriptions, not complete narration-about relations. Entry requests these alongside speech/addressed-to relations (`text_content/pipeline_prompts.py:325–328`). Contrary to the brief, neither `language_relations` nor `coreference_evidence` is persisted on Character (`data_structures/character.py:12–40`; `pages/enter_text.py:1023–1028`). Raw audit retains them; omission passes validation (`character_evidence.py:31–34`). The documented 5 November run used old prompts: no evidence for these additions (DECISIONS 021). |

**Cast and drift.** Entry discovers its own cast, including groups/discourse roles;
analysis uses the human cast. Different casts, groups and aliases would compete.
Entry's `alias_pipeline.py:240–243` correctly leaves lexical hits as unresolved
candidates; it does not resolve pronouns. Analysis's later `resolution.py` also
works on endpoint strings, not story context: alias collisions overwrite in
`sna/loading.py:214–221`, and reciprocal references are incorrectly mapped to the
reader (`resolution.py:554`). SNA independently reloads/resolves speech;
`character_space/features.py:282–287` excludes plural speakers.

**Checkability.** Speech schemas retain section IDs/quotes without pages;
`L/run_gpt4o_tasks_final.ipynb`, cell 58, joins by position, clamping excess
positions: omissions/splits can acquire wrong quotes. Entry checks quote
containment, not attribution or candidate-ID membership (`character_evidence.py:75–91`).
Both routes receive text only. Entry prohibits book-memory inference; analysis
does not. Neither specifies full I/you/we rules. These are code findings, not
measured error rates.

## One design and the smallest useful test

**Ownership and contract.** Keep entry's cast, aliases, identity evidence,
membership and unresolved alternatives; cut its analytical `language_relations`
generation. Preserve DECISIONS 014's separate speech, addressed-to and
narration-about relations once in analysis.
Export stable cast IDs: entry's temporary model IDs are not persisted IDs.
All consumers should use that attribution file with explicit population filters.

Reuse the analysis client/prompts. Each occurrence needs book/page, exact quote
and offsets, relation/grammatical role, selected cast IDs, alternative candidates
and status: resolved, ambiguous, non-character, missing-cast or failed. Distinguish
grammatical subjects from agents in passives. Keep unresolved rows outside
exclusive character totals. Aliases suggest candidates;
they never establish pronoun identity. Validate IDs and source spans; join by
occurrence ID. These checks prevent the observed dropping/misjoining paths.

Require text-only evidence: I follows the local speaker; character-directed you
follows the supported addressee, never mere proximity. We needs evidenced
membership. Distinguish singular/plural they, character/object/expletive it,
story groups, generic references and reader/narrator roles. Multiple resolved
members differ from competing alternatives; never distribute a group onto
individuals without evidence. Retain uncertainty and forbid picture or familiar-book
inference. Cut nearest-name fallback and reciprocal-to-reader guessing.

**Proposed pilot, not run.** Reuse analysis's `action_extraction.py` and client:
one full-book call per DEV20 book, twenty calls. Admit
only `exposure.py`'s DEV20 through `tuning.py`, reusing the guarded loader pattern
in `scripts/alias_value_experiment.py:55–89`. Hold text and human cast/aliases fixed,
including books with empty casts; report missing-cast cases separately. This
isolates attribution; it cannot validate the pipeline's cast.

Adapt the action prompt to classify every pre-enumerated pronoun–verb occurrence,
using the rules above and recording dialogue participants/supporting spans in
the same call; preserve input page/quote anchors. Cut separate speech calls and
positional joins. Measure before the action stoplist: reproduce
the original direct-subject lexical-verb count, including reporting verbs, with
no auxiliary/coordination expansion. Reconcile any denominator discrepancy before
comparison. Tag a fresh result file with text/cast/prompt/model versions; existing
`pos_llm.py:36–43` cache keys omit prompt and input content.

**Estimated cost:** existing Haiku 4.5 configuration, assuming 60,000 input and
40,000 output tokens, gives **$0.26** at locally recorded $1/$5 per million
(`ai_pricing.py:47`): roughly $0.20–$0.50 before retries. Tokens are projections,
rates unverified today. The earlier $0.20 estimated the character stage. Record
usage once the evaluation key exists; agent review time is additional.

**Measures.** Use the previously reported denominators: distinct grounded
character-attributed occurrences / **539 he/she/it/they**, / **416 I/you/we**,
and / **955** combined. Count each occurrence
once, report groups separately, and retain failures/omissions in denominators.
Noncharacters make 100% coverage inappropriate. This tests pronoun-subject verbs;
adjective and general narration coverage remain unmeasured.

An agent checks **200 random assigned occurrences**, 100 per pronoun family
(census if fewer), against full context: right character, wrong character, not
a character, unclear. Report all four, counting unclear outside correct; weight
combined accuracy by assigned-family population sizes. Report confidence intervals:
roughly ±7 percentage points worst-case, ±3–4 near 95% correct with balanced
weights; unequal weights widen these. These describe this development set only.
Check another 50 random abstentions for recoverable references/cast gaps; report
it/they, dialogue and group errors separately without claiming precise subgroup rates.

Recommend a tuning target only after Chris sees substantial recovery in both
families, at least 95% sampled correctness with its 95% lower bound above 90%,
and no systematic dialogue/group failure. These are proposed criteria, not results;
tune coverage subject to accuracy, never forced assignment.

## Open questions for Chris

None before the pilot. DECISIONS 021 reserves adopting a tuning target until
Chris has seen its results.
