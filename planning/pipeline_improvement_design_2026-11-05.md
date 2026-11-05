# Pipeline improvement design — 5 November 2026

Design only. This document is the sole repository change requested. No extraction, implementation, git commands, or live-service access were performed. Findings below come from local source, cached predictions, reports and snapshot records. No new photographic adjudications are introduced; the seven text-recovery adjudications are accepted from the supplied adjudication document, and the genuinely blank *Book With No Pictures* p6 verdict (byte-identical to p24) from Chris's review update. A cached dev20 Crocodile p16 illustration was inspected diagnostically, without a new identity or text adjudication; the alias decisions below use the cited cached text. Other judgments based on CSV text are explicitly provisional.

The alias design now starts from Chris's downstream uses: **T1, speaker/addressee attribution, and T2, character-associated language extraction**. Preserve recorded expressions and group them under supported identities; resolve occurrences in context rather than delete variant strings. Losing a match can lose a speaker or a character's language, while attaching it to the wrong identity can contaminate both analyses. Type B attribution errors therefore take priority over Type A redundancy. Repair entity matching and the bibliographic data model before attributing gains to prompts, then improve evidence-backed attachment and story-page context. Test cheaper rotation separately with strict quality guards and preserve OCR's willingness to find text. The measured scores concern an incomplete, partly machine-derived reference and output consistency, not established downstream accuracy. Chris has cancelled the fresh annotation programme. This revision uses existing student positives, cached text, the supplied adjudications and frozen historical fixtures only. Recovering students’ aliases **plus supported extras without losing or misassigning mentions** is the objective; true mention recall and wrong-attribution rates remain unmeasured (§4.1.3).

## 1. Evidence, terminology and decisions

Read first, [human alias behaviour](human_alias_behaviour_2026-11-05.md) settles descriptive alias scope empirically and establishes that the sparse pilot reference cannot measure alias precision. Read together, [the harness design](validation_harness_design_2026-11-04.md), [empty-text measurements](pilot_empty_text_findings_2026-11-04.md), and [photographic adjudication](wordless_adjudication_2026-11-05.md) establish a hierarchy: individual photographic adjudication overrides an aggregate inference about empty text. Empty/empty agreement is useful evidence; it does not independently verify wordlessness. The earlier estimate of 17 affected books is obsolete.

Local artifact root, abbreviated `ART` below:

```text
/tmp/claude-1000/-home-expert-Documents-uob-fair-tales-FairTalesDataEntry/94cd65ba-b47b-4f8e-aa72-0afc7c7bbd43/scratchpad
```

Primary evidence: `ART/baseline_scores.json`, `ART/dev20-run/{predictions.json,calls.json,selection.json}`, `ART/dev20-run/reports/{pages.csv,books.csv}`, and `ART/dev20-snap/manifest.json`. Split checks additionally use `ART/pilot-v1/{inventory.json,images-progress.json}` and `ART/EMPTY_TEXT_DIAG.json`; the output audits are `ART/round3/{dup_analysis.txt,grounding.txt}`, also recorded in [alias duplication findings](alias_duplication_findings_2026-11-05.md). That note is a historical census; the task-derived rules below supersede its tidiness framing. An accidental inventory schema print exposed some `a_letter_to_santa` page text during the previous revision; §8 records its additional quarantine. No held-out page text or prediction was used to design examples. Every page number in this document is the **photograph/page_number in pages.csv**, not the printed folio. Exact book IDs are given where needed to disambiguate.

The active paths are `AIPrompts.character_detection`, `AIPrompts.page_extraction`, `extract_book_metadata`, `extract_copyright_metadata`, `locate_key_pages`, `extract_photo_first_metadata`, and `lookup_isbn`. There is no `fetch_book_metadata` definition in this checkout. The older per-page character extraction/consolidation prompts are retained but inactive. `extraction_core.extract_characters` currently receives only successfully extracted text whose **predicted** `contains_story` is true. Fixing classification therefore changes character inputs and requires an end-to-end rerun of that stage.

**Decision status and research ownership.** C3's names-plus-descriptions scope and C4's two-field publication model are Chris's accepted decisions; do not reopen them. C1/C2/C6 and the remaining ontology and acceptance-policy choices are provisional research conventions. The defaults below make implementation determinate, but their appropriateness for Chris's research is his judgement, not a finding established by pipeline agreement.

**This round's four decisions are settled:** derive duplication handling from T1/T2; store both raw and canonical forms while accepting one-form manual entry and all legacy records; discriminate identities without a merge-leaning or split-leaning prior; and conduct an **APPROVED IN PRINCIPLE ~40-book** overfitting check. Its execution has a **BLOCKING PRECONDITION: Chris must link the Claude subscription Console organisation and the run must use those credits, not the production Anthropic key**. Dev20 tuning is not blocked by this held-out precondition. The design recommendation is lossless three-layer storage plus an occurrence ledger (§4.1.1), article handling at match time, and separate false-merge/false-split reporting. The corrected paired-run arithmetic is in §8; the earlier rough ~$37 is not a measured paired-run cost.

| ID | Operational convention | Status and independent basis |
|---|---|---|
| C1 | Preserve readable book-authored lettering, including illustration lettering, in archival transcription; tag its kind and derive a separately versioned analytical view. | **Provisional research scope.** The photographs independently establish that the lettering exists, not that Chris's analysis must include it. Pipeline output cannot settle that choice. |
| C2 | `contains_story` means participation in the main work: narrative, substantive nonfiction or wordless narrative sequence. Exclude covers, decorative endpapers, genuine blanks, bibliographic matter, glossary/index and supplemental activities. | **Provisional research definition.** Imported all-pages defaults are not independent labels. Retain this prompt convention; imported-label agreement cannot validate it. No new story annotation is planned; the unresolved scope question is collected in the final section. |
| C3 | Lexical aliases include proper names **and descriptive referring expressions**, attached to the entity they denote. Pronouns are coreference evidence, body parts are not aliases for their owners, and unattested display labels are separate. | **Names-plus-descriptions scope accepted by Chris and independently supported by human practice:** 1,494 characters, 200 aliases (0.13/character), 93/195 books empty, median one alias among the other 102; 134/200 strings (67%) begin lower-case, including `the brown bear`, `the littlest one`, `her daughter`, `each bird`, `scary monster`. This is evidence from human annotation, not a defence derived from what the pipeline emits. Entity-population boundaries below remain provisional. |
| C4 | Store the photographed edition's imprint/year and first-publication publisher/year as **separate, scoped fields**, preserving source evidence and all publication events. | **Accepted by Chris.** Photo-supported publication statements ground particular values; Chris's decision grounds the choice of data model. Do not claim the old score independently validates either target. Schema, migration and scoring are specified in §§4.4–4.5. |
| C5 | Use the explicit study, safety rules, workload and budget in §8 as the proposed acceptance policy. | **Chris's research judgement.** These are implementable planning defaults, not empirically established thresholds for publication or authority to spend. Absolute adequacy for his analysis must be distinguished from a relative improvement claim. |
| C6 | Preserve contributor roles; include explicitly credited retellers in the analytical authors projection, excluding editors/designers without an author credit. | **Provisional research projection.** Visible role credits independently ground extraction; including retellers as authors is a research judgement, not a deduction from current scores. |

C3 does not empirically settle generic nonfiction species, implied narrator/reader roles, dreamed/hypothetical characters or collective/subset identity. Default: preserve categorized evidence; score narrative individuals/collectives separately from generic groups and discourse roles, and retain the legacy all-entity diagnostic. Retain ambiguous identity without forcing either identity or nonidentity. No unresolved decision is silently treated as an empirical result. Implement under these versioned defaults, retaining unresolved cases; any later research-policy change requires a new convention and symmetric rescoring before a claim, never a post-hoc choice of whichever definition scores better.

## 2. Alias and character audit — completed before prompt design

The headline needs a correction in terminology. The scorer compares normalized `{canonical name} ∪ aliases`. Thus **197 predicted versus 106 reference means name variants, including canonical names**, not 197 additional aliases. There are 88 matched variants, **109 uncredited predictions**, and **18 missing reference variants**; the net difference is 91. Entity matching finds 82/96 reference entities among 102 predictions, with an ambiguity bound of 83 matches. Every one of the 18 scored books has `incomplete_annotation`.

I audited all 109 uncredited variants, rather than selecting a convenient sample. Reproduction: read each `books.csv.characters` JSON object; retain the scorer's `matches`, `unmatched_prediction` and normalized `variants`; for a matched entity take predicted variants minus that reference entity's variants; for unmatched predicted entities retain every variant. Inspect each against that book's ordered `pages.csv` reference and predicted text. For ambiguous matching, retain the scorer's lower-bound assignment and flag the ambiguity. Literal text supports a string's existence, not automatically its attachment to a particular character.

**Corrected census, before prompt reasoning.** The original text search reproduced 90 printed/string-supported and 19 apparently absent or synthetic strings, but that axis does not identify who is wrong. Printed pronouns/body parts can be invalid aliases and an entity-match failure can strand every otherwise correct variant. The corrected accounting is:

| Primary accounting bucket | Exclusive count | `scorer_artefact` | `prediction_wrong` | Interpretation |
|---|---:|---:|---:|---|
| Supported reference omissions | 45 | 0 | 0 | Text-supported omitted variants/entities, still requiring identity/photo review. |
| Scorer-only footprint | 21 | 21 | 0 | Alignment candidates or withheld ambiguous matches; not evidence that the detector invented an entity. |
| Prediction-fault audit flags | 30 | 3 | 30 | 19 synthetic/apparently absent strings, five attachment audit flags, six definite pronoun/body-part exclusions. Three overlapping cases also suffer a scorer alignment failure. |
| Other unresolved cases | 13 | 0 | 0 | Remaining scope/coreference/OCR questions. |
| **Total** | **109** | **24** | **30** | **45 + 21 + 30 + 13 = 109; flags overlap in three cases.** |

`scorer_artefact` marks **24 affected variants**, the largest single recovery opportunity before model changes. More precisely, 19 variants on nine predicted entities have no exact normalized reference-variant overlap at `scoring.py:326`; five more on the worm prediction are withheld by conservative one-to-one ambiguity. It would be inaccurate to call all 24 literal-overlap failures. Examples include `old cat`/`cat`, `Cindy-Lou Who`/`Cindy-Lou`, `big brown bear`/`bear`, `six cats`/`cats`, `other big ducks`/`big ducks`, and the stick-carrying man. This is a scorer footprint, **not a promise of 24 additional correct lexical matches**: every variant on an unaligned entity is currently uncredited, even though some remain wrong or absent from the sparse reference.

`prediction_wrong` is an explicit audit flag, not 30 photographic verdicts. Move `he`, `they`, and `long grey trunk` (*Cave Baby*), `he` (*Runaway Pea*), and `me`, `you` (*Same but Different*) out of unresolved: the five pronouns and one body part violate C3. The 19 synthetic/apparently absent strings remain an open source-verification question for Chris (final section); missing OCR or filtered input can hide printed words. For Santa pp31/33, **the names are printed, including “Zac. Zara. Zybil!” on p33**. The concatenated `alfie ava ben brooke charlie chloe zac zara zybil` is synthetic as one alias; its component names must not be described as unprinted. Retain the historical 90/19 string-search result only as a diagnostic, not a verified printed/unprinted partition. The appendix provides per-book flags and the three overlaps.

**Matching repair, with zero new inference cost.** Add a versioned, book-scoped entity-alignment layer before lexical scoring. Record stable reference entity IDs and only those identity-equivalence edges supported by the existing appendix/adjudications and cached source evidence. Exact normalized overlap remains one source of an edge, not the sole identity test. Preserve one-to-one matching, collision reporting and ambiguity bounds. For `worm`/`worms`, retain the unresolved split; do not infer many-to-one identity from spelling. The appendix's scorer flags identify candidates, not automatically verified edges. No new annotation is commissioned. Freeze deterministic rules and existing evidence before prompt trials; apply them unchanged to the held-out student reference and report unmatched/ambiguous counts. Do not globally strip adjectives, accept substrings, or equate a town with its inhabitants. Current `_entities` equivalences affect both alignment and lexical credit: keep those operations separate. Entity identity credit never credits every alias automatically. Report raw scorer → corrected scorer at fixed predictions separately from extraction gains.

**Alias recall against the existing student reference remains a known-positive recovery guard, not the downstream objective.** The historical baseline is **0.830 = 88/106 known name variants**, including canonical names; preserve that definition as `known_variant_recall_v1`, with a proposed target **≥0.90**. Also report aliases-only recall against explicitly recorded student aliases: the cached dev20 recomputation is **6/10 = 0.600** under fixed existing alignment (§4.1.1). It has a different denominator and must not inherit 0.830 as its baseline. Books with no recorded aliases cannot contribute to that aliases-only numerator/denominator. Both measures assess recovery of known positives, not all aliases that exist, and remain exploratory. §8 defines the holdout comparison and non-regression checks.

**Reference-precision 0.447 is diagnostic only, always with the caveat that 48% (93/195) of books record zero aliases.** It is a coverage artefact of the reference and cannot be improved by improving the pipeline: its numerical value can rise by suppressing valid extras, but that does not fix reference coverage or demonstrate quality. Never gate, rank prompts, or claim precision/F1 accuracy from it. Complete-reference alias precision/F1 is deleted from this study; we are not obtaining the annotations needed to measure it. Retain M1 groundedness as an annotation-free output-to-text check, with M2/M3 checking redundancy and collision separately. None measures T1/T2 accuracy; occurrence coverage, wrong attribution and identity discrimination have their own definitions (§§4.1.1–4.1.3).

Representative **actual reference–prediction comparisons** establish the design direction:

| Book and photograph | Reference / prediction discrepancy | Text evidence and provisional conclusion |
|---|---|---|
| *All for One*, pp10–11 (`all_for_one`) | Reference Granny / Mum; predicted possessive forms `Marlon's granny` / `Marlon's mum` | The possessive forms occur in the text. Retain them as evidence-backed aliases; incomplete reference is a plausible cause. |
| *How the Grinch Stole Christmas*, p40 | Reference Grinch lacks predicted `the fake Santa Claus` | “the fake Santa Claus lied” refers to the Grinch in this passage. A valid descriptive/disguise alias is not over-generation. |
| Same book, p42 | Reference Grinch lacks predicted `the old liar` | “himself, the old liar” is direct textual coreference. Retain. |
| Same book, p25 | Reference Max lacks predicted `dog` form | Text calls Max the dog. Retain attested forms attached to Max. |
| Same book, p38 | Reference `Cindy-Lou`; prediction `Cindy-Lou Who` | “Little Cindy-Lou Who” is printed in both text streams. Resolve entity identity; do not require the model to shorten a correct name to match annotation. |
| Same book, p64 | Prediction attaches `Who-ville` to the Whos | The printed back-cover text says “citizens of Who-ville”. The town is not an alias for its inhabitants. Incorrect story inclusion also exposes characters to synopsis text. |
| Same book, p24 | `Saint Nick` attached to Grinch | “I look just like Saint Nick!” is a comparison, insufficient evidence of identity. A disguise explicitly named as the actor, as on p40, is different from a simile. |
| *The Three Little Pigs*, p10 (`the_three_little_pigs`) | Predicted `Straw Man` label absent from supplied text | The source has “a man carrying a large bundle of straw”. Preserve the unnamed man, but do not invent this lexical variant. |
| *The Little Bully*, pp20/22 (`the_little_bully`) | Predicted `Billy’s mom` not verbatim | The book already prints “his mom” and “Mom”. The possessive expansion is generated, not attested; preserve the mother entity and its actual mentions, with any display label separate. |
| *Santa is Coming to Devon* (`santa_is_coming_to_devon`) | Predicted `Santa Claus` absent from supplied text | Do not expand `Santa` from cultural knowledge. Untranscribed print remains an audit possibility; no page extraction failure was recorded for this book. |
| *The Enormous Crocodile*, pp19/22 (`the_enormous_crocodile`) | `girl who was older than the others` → Playground children; `one little girl` → Fair children | The first is an individual group member; the second is explicitly named Jill. Neither is an alias for the whole group. |

Rows without a page citation are diagnostic audit cases, not ready-made gold ICL examples. Only the page-cited, directly evidenced examples specified in §4 enter the prompt pack. Use the existing 109-variant appendix and 18 missing variants as development evidence; unresolved source-absence or identity assertions remain unresolved, without commissioning a new review round. Only already-supported verdicts enter adjudication overlays. Broad substring matching alone is unacceptable: it would credit Who-ville incorrectly and can confuse an individual with a group.

The 18 missing reference variants also contain label and ontology mismatches. Concrete recall targets include *Santa* p10, where “the elves” load a sack but the predicted distinct group is absent; *Crocodile* p19, where the older girl needs a local individual/speaker mention with cross-scene identity unresolved; and *The Ugly Duckling* p11, where “the sad little duckling” is an omitted alias. Conversely, do not force the reference alias `pigs` onto the third individual pig, or split generic `worm`/`worms` solely to reproduce the reference. The worm book's ambiguous matching is especially poor evidence of detection failure.

## 3. Other measured failure mechanisms

**Publisher and credits.** Chris has accepted both publication targets. Most of the old publisher agreement **0.50 is a data-model/field-semantics problem, not a prompt problem**: repair event scope and imprint representation before measuring extraction changes. All 10 publisher mismatches were inspected: three concern different publication events, six concern branding/imprint/legal-name granularity, and one is a clear extraction/merge candidate (*Cave Baby*). These are provisional text-based diagnoses, not nine automatically correct predictions:

| Book | Reference → prediction | Evidence and action |
|---|---|---|
| *A Squash and a Squeeze*, p6 | Methuen → Macmillan Children's Books | Copyright text distinguishes first publication in 1993 by Methuen Children's Books and this edition in 2016 by Macmillan Children's Books. Store both. |
| *The Enormous Crocodile*, p2 | Jonathan Cape → Puffin Books | Text distinguishes Jonathan Cape 1978 from Picture Puffins 1980. Store both; do not mark either historical fact false. |
| *How the Grinch Stole Christmas*, p6 | Random House / 1957 → HarperCollinsChildren's Books / 1973 | Text describes first UK publication in 1973, this edition in 2008 and an arrangement with Random House. A regional first year is not worldwide first publication. |
| *Cave Baby*, p3 | Macmillan Children's Books → Macmillan's Books; title Cave Baby → Baby | Clear candidate extraction/layout problem. Reconcile cover, title and copyright evidence; never let the first nonempty title-page value win automatically. |
| *It's Christmas* | Little Tiger Press → Little Tiger | Possible abbreviated imprint; review equivalence with photographed evidence. |
| *Norman the Slug with the Silly Shell* | Simon & Schuster Children's Books → Simon & Schuster | Parent/imprint granularity; do not assume every parent/subsidiary is equivalent. |
| *Room on the Broom* | Macmillan → Macmillan Children's Books | Same issue. Preserve raw imprint. |
| *The Runaway Pea* | Simon & Schuster Children's UK → Simon & Schuster UK Ltd | Edition/imprint/legal entity distinction requiring a scoped equivalence record. |
| *The Same but Different Too* | Nosy Crow → Nosy Crow Ltd | Candidate legal suffix equivalence. |
| *The Storm Whale* | Simon & Schuster UK → Simon and Schuster | Name normalization plus territorial/imprint scope; review, do not globally strip distinctions. |

Additional concrete weaknesses: *Santa*, p1 loses “Coming” from its title; p6 credits **Robert Dunn and Amerigo Pinelli**, while the title-stage result includes only Dunn. *Enormous Crocodile* predicts author `PQALDDAHL` and illustrator `Quentin13laI\e`; nonempty garbled strings block the current fill-only merge, although p2 has clear role-specific Roald Dahl/Quentin Blake copyright credits. *All Year Round* predicts Sally Hewitt against reference Sally Hewit, a possible reference typo; its p6 OCR names Mike Atkinson and Simon Morse with a corrupt separator, rather than reference illustrator Tony Kenyon. That is an edition/reference dispute, not a reason to prompt for Kenyon. *The Three Little Pigs* misses both reference Ian Beck credits; p1/p6 can support name/author review but do not establish illustrator merely from generic copyright. *Wiggling Worms at Work* has no predicted year; p42's cover-art copyright 2004 does not establish first publication. These require cross-page evidence, not guessed names or a blanket model upgrade.

The 15 saved ISBN lookup artifacts have `raw_response:null` and `value:null`, without an outcome distinguishing absence, failure or disabled lookup. They cannot substantiate successful enrichment. Existing `apply_isbn_metadata` fills only missing title/authors/publisher; it does not add illustrator/year or replace nonempty bad credits. Preserve those historical unknown lookup outcomes. A new experiment must use identical frozen lookup inputs for baseline and candidate and report unresolved/failed enrichment separately.

**Story classification.** Of 55 raw false positives, **50 have empty predicted text**. They cluster around page boundaries/end matter, but images are needed before calling all 50 decorative endpapers. The five nonempty cases are *Squash* p34 (`si`), *Grinch* p64 (synopsis), *Santa* p36 (possibly the ending), *Runaway Pea* p34 (“THE END …”), and *Worms* p37 (supplementary experiment); they need distinct judgements.

Of **31 raw false negatives, 18 have empty text**. The other 13 are 12 *All Year Round* pages (main instruction plus glossary) and *Bear Hunt* p5 (apparently a title page). Of the 18 empty cases, **13 are *The Book With No Pictures***: pp6, 8, 10, 12, 14, 24, 28, 30, 36, 38, 42, 44, 48. All 13 predictions are `page_type="blank"`, `contains_story=false`. Chris has photograph-adjudicated **p6 as genuinely blank**; the pipeline is right and the reference is wrong, owing to the pilot spreadsheet's all-pages default. The cached manifest verifies that p24 shares p6's image SHA256 `2799b9e051da36c4de2cd83c47f506b2744ac5a6d8b9ca3cecc861c5e1d6ccd9` (47,627 bytes). Transfer the verdict with that provenance, not as an independent photographic inspection.

The **remaining 17** means 18 empty cases minus directly reviewed p6, **not 17 in addition to those 13**. It includes p24, the other 11 unreviewed pages of that book, and *Santa* pp16/32, *Sharing a Shell* p17, *Little Bully* p33 and *Worms* p17. They need photographic/sequence adjudication before supporting either missed-wordless-story or overbroad-reference claims; the verified identical-image transfer resolves p24, leaving **16 unresolved**. The previous argument read this evidence backwards: these 13 predictions do not establish that “empty means non-story” is invalid. A genuinely blank page is non-story under C2; a wordless illustrated narrative scene can be story. Distinguish them from photographs and sequence, not OCR emptiness or the imported flag. Freeze the **101-page empty-reference monitoring cohort in §8** before any recall-oriented page prompt is evaluated; only the already verified blank image supplies a wordless truth gate.

**OCR.** There are four failures in the eligible text-recovery denominator and a fifth failure on the excluded *All for One* cover. Three *Grinch* pages (27, 28, 31) are recorded provider content-filter rejections, not OCR omissions; *Book With No Pictures* p41 hits the 2,048-token ceiling while generating a long run of `e`. The cover has a refusal stop reason. The four scored pages account for **153 of 234 missing reference tokens (65.4%)**. Do not describe these as four ordinary recognition errors or expect visual prompting to fix provider refusals.

Residual examples: *All for One* p17 misses its last two dialogue turns; *Santa* p33 garbles stylized names; *Storm Whale* p18 omits record-cover lettering that the reference transcribed; *Grinch* p30 misses “WHO EXPRESS”; *Book With No Pictures* pp27/40 involve repeated-letter or repeated-block discrepancies. Conversely, reference strings include `th e`, `war ms`, `roll ick`, and `aaid` where predictions plausibly recover normal print. Their photographic truth remains an open question (final section); do not tune to these uncertain strings. Ordered CER/WER and token F1 measure different things; reading-order error must not be hidden behind a high bag-of-tokens score.

## 4. Proposed prompts and example pack

These are literal replacement prompt specifications, not prompts run during this task. Use a versioned pack `pipeline-evidence-v1`; store exact rendered bytes and example source page IDs/hashes in request manifests. Examples are bounded excerpts: never represent omitted portions as the full transcription of a page. In image prompts, attach **development-book images/crops** as gold demonstrations only where an existing adjudication already supports the expected output. The other examples remain usable as explicitly text-derived behavioral demonstrations; no new photo review is a precondition for the text pack. The text-only examples below already specify the intended behavior and can be used without pretending images were inspected here.

### 4.1 Character and alias detection

Keep one whole-book call. Do not revert to one call per page: cross-page identity is necessary, and the current full-book call costs about one cent per book. Replace the unsupported “Tom/Tommy are probably the same” example with evidence-based cases. Add compact evidence records; preserve the existing character attributes and legacy `name`/`aliases` projection.

```text
You identify characters and their referring expressions in ONE supplied book.
Use only the supplied main-content pages. The examples below are separate books;
their people, wording and relationships must not be imported into the target.

Find all text-evidenced entities: named or unnamed people, animals, acting or
speaking personified objects, and groups acting collectively. A mention can
identify a character even if it appears once. Ordinary objects, places, food,
and a thing mentioned only as a comparison are not characters on that basis.
For informational books, record generic organism groups separately with
entity_kind="generic_group"; do not silently equate them with an individual.
Record implied narrator/reader roles with entity_kind="discourse_role" and
explicit role evidence. Do not infer a visual-only character from world knowledge.

For every name or alias, copy a referring expression from the supplied text and
give its page and a short exact supporting quote. Do not create nicknames,
expand names, translate Mum to mom, pluralize an individual, or add familiar
names from your knowledge of the book. Keep punctuation/case as printed.
Pronouns provide coreference evidence; do not put he/she/it/they/I/me/you/us in aliases.
A body part such as "long grey trunk" is not an alias for its owner. Preserve it
as coreference evidence when useful, without adding it to lexical aliases.

Merge only when the text supports identity: explicit naming, unambiguous
continuity, apposition, or a clearly identified actor under a disguise. Shared
species, gender, similar names, a simile, possession, or membership of a group
does not establish identity. An individual and a group containing that individual
are separate entities. A place and its inhabitants are separate.

Choose the clearest attested name as name. If an unnamed entity requires a
display label, set name_is_label=true and retain the attested mentions separately;
the display label is not a lexical alias. For unresolved identity, preserve the
mention and alternative identity links without committing either a merge or a
split. Put candidate IDs and evidence in unresolved_mentions; an unresolved
mention is retained even when no human review is available.

Use one canonical display name per supported referent. aliases preserves every
attested referring form, including article, possessive and case variants. Copy
raw forms; the adapter derives a separate canonical field. Do not shorten or
suppress an expression to make this list tidy. Keep exact occurrences in mentions;
matching handles articles and deduplicates repeated hits on the same occurrence.
Retain modifier variants such as "the whale" and "the little whale" when both
occur; shared words do not license merging characters. Do not generate shortened
substrings: each shorter form needs its own occurrence. Named possessives carry
owner evidence and must not be discarded as meaningless wording.

For identity decisions compare explicit naming/apposition, grammatical number,
co-occurring actors, different actions, and attributed dialogue across pages.
Number is evidence, not proof: reindeer has the same singular/plural spelling.
Two mentions in a sentence can be coreferential (apposition); distinct subjects
acting toward one another are stronger evidence of different individuals.
Different quoted turns alone do not prove different speakers. Record the exact
speech/answering cues and do not infer an addressee merely from proximity.

Before returning, check expressions shared across character records. Merge duplicate
records only with explicit identity evidence and retain their former IDs. Keep a
collective and its members separate, linked by member_of. "The reindeer" can name
the herd while "the youngest reindeer" names a member; do not count the herd's
mentions as that individual's. A generic "girl" or "children" is not a globally
unique lookup key. Attach each occurrence using its page and local context. If
identity is unresolved, retain candidate IDs and exclude that occurrence from
exclusive per-character totals. A parenthetical display disambiguator is a label,
not an attested lexical alias. An elided list of names is an editorial summary,
not one printed referring expression.

Use gender only where explicit language supports it; otherwise Non-specific.
Use Transgender only if explicitly stated. Human means a person, not an animal
or personified object. plural identifies a collective; protagonist requires clear
main-character evidence. Do not infer attributes from a stereotyped name or job.

Return JSON only:
{"characters":[{"id":"c1","name":"...","name_is_label":false,
"entity_kind":"individual","aliases":[],"gender":"Non-specific",
"human":false,"plural":false,"protagonist":false,
"mentions":[{"surface":"...","page":1,"quote":"..."}],
"identity_evidence":[],"member_of":[],"editorial_labels":[]}],"unresolved_mentions":[]}

entity_kind is individual, collective, generic_group, or discourse_role.
identity_evidence contains page/quote records supporting nontrivial merges.
unresolved_mentions contains surface/page/quote/candidate_entity_ids.
Return empty lists if no eligible entities/mentions occur.

EXAMPLE A — How the Grinch Stole Christmas, photographs 38, 40, 42 (excerpts):
38: "Little Cindy-Lou Who, who was not more than two."
40: "But, you know, that old Grinch was so smart and so slick"
40: "the fake Santa Claus lied"
42: "Then he went up the chimney, himself, the old liar."
Expected decisions: Cindy-Lou Who is separate from the Grinch. In this continuous
scene, "the fake Santa Claus" and "the old liar" refer to the Grinch. Preserve
these as evidenced descriptive aliases; do not add Saint Nick or Who-ville.
Use the exact supplied expressions in mentions. A shortened canonical display
name alone does not establish an additional printed lexical variant.

Contrast photograph 24: "I look just like Saint Nick!" is a comparison, not
evidence that Saint Nick and the Grinch are the same entity. Photograph 11's
"Every Who / Down in Who-ville" names a population and a place, not two aliases.

EXAMPLE B — The Runaway Pea, photograph 20 (complete short text):
"Owwww," cried the pea, "my bottom's on fire,"
Expected decision: the pea is a personified individual. Its name can be
"the pea"; aliases is empty on this excerpt. "my bottom" and "fire" are not
additional characters. Do not invent a proper name for the pea.

EXAMPLE C — All for One, photographs 10, 11, 14, 23 (excerpts):
10: "said Marlon's granny"; 11: "said Marlon's mum";
14: "Mum!"; 23: "warned Granny".
Expected decisions: the mum and granny are separate entities. Their respective
possessive forms and direct forms are valid referring expressions; preserve them
in mentions; handle articles during matching as specified below. Do not discard relational
phrases or create the synonym "mom". Use the exact apostrophe in the input.

EXAMPLE D — The Enormous Crocodile, photographs 19 and 22 (excerpts):
19: "a girl who was older than the others said"
22: "one little girl, whose name was Jill said"
Expected decisions: the older girl is an individual, not an alias of the playground
children. "one little girl" attaches to Jill in the second passage, not to all
the fairground children. Membership does not mean identity.

Target book pages:
{pages_json}
```

The generic-group and discourse-role inclusion in the scored character population is **C3**. Proposed default: include individuals/collectives in the headline narrative character metric; report generic groups and discourse roles as separate strata, retaining them all in the archive. Apply the same ontology to reference and predictions, and also retain the legacy all-entity score. C3's descriptive-alias scope is settled; its remaining entity-population boundaries are provisional. Output these categories without claiming a gain manufactured by exclusions.

Deterministic validation must check evidence quotes against the stated input page with Unicode/whitespace normalization; it may flag a mismatch but must not delete the entire entity. Record `unsupported_surface`, `wrong_page`, or `unresolved_attachment`; correction/review operates on that record. Never synthesize an alias from `name_is_label=true`. The adapter must retain the new evidence and unresolved lists: the current `detect_book_characters` projection otherwise discards extra keys. Scoring must evaluate evidence-bearing lexical mentions separately from entity display labels. This is a documented schema/metric change, not a prompt-only gain.

### 4.1.1 Duplication: preserve mentions, discriminate identities

**Task-derived rule: group variants under one supported identity; do not remove recorded surfaces.** Both T1 and T2 need occurrences in text, not a tidy list of names. Deleting a surface loses any occurrences that no remaining pattern can recover. This is potential downstream recall loss, not neutral tidying; a shorter substring may still hit but lose the modifier/owner evidence needed to identify the character. Conversely, duplicate patterns for the *same* identity need not inflate counts: deduplicate their hits by source occurrence. For T2, a Type B wrong attachment imports another character's language into the target's profile; for T1 it can reverse speaker/addressee edges. Rank **wrong attribution and lost identity distinctions first**, missing mentions second, and redundant representations last. This is a priority between error types, **not** a preference for merging over splitting or vice versa. Both identity errors can cause wrong attribution.

Effort follows that ranking: implement one deterministic, lossless storage/matching adapter for Type A, then direct the character-prompt, context-evidence and regression work at Type B and occurrence recovery. No API trial is justified solely by reducing alias rows. T1 also needs speech spans, turn boundaries, speaker/addressee relations and pronoun/coreference links; alias matching alone does not solve dialogue attribution. T2 needs a versioned rule defining what text is associated (speech, descriptions, actions or another span). Preserve those relations and offsets separately from aliases, and retain unknown speakers/addressees rather than infer them from the nearest name. The initial measurable common component is lexical mention attribution; full T1/T2 accuracy is not established by it.

**Historical M2/M3 census retained, not adopted as an identity rule.** The raw denominator is **105 predicted characters and 112 explicit aliases across 20 books**, including two books without eligible character references. It differs from §2's 18-book, 197-name-variant scorer denominator. `round3/dup_analysis.txt` lowercases, deletes punctuation with `re.sub(r"[^\w\s]", "", s)`, removes one leading `the|a|an|my|your|his|her|its|our|their`, then tests equality or strict token-set inclusion. Its nine `article_only` pairs already include the five `alias_eq_name` pairs. The four remaining pairs are Mum/his mum and Granny/his granny in *All for One*, and Mum/his mum and Dad/his dad in *Cave Baby*.

| Historical audit component | Measured dev20 incidence | Interpretation under T1/T2 |
|---|---|---|
| M2 strict equality under the old determiner rule | **9 pairs / 9 characters / 4 books**; **9/105 = 8.6%**, **4/20 = 20%** | Candidate pattern sharing within an identity; not nine records to delete or nine semantically interchangeable possessives. |
| M2 modifier/possessive subset flags | **44 intra-character pairs / 33 characters / 13 books** | Wording can preserve identity evidence and must remain matchable. |
| M2 union | **53 pairs / 37 characters / 13 books**; **35.2%**, **65%** | Count each affected character/book once; subtypes overlap. |
| M3 broad cross-character flags, original normalizer | **12 surface pairs / 9 record pairs / 2 books = 10%** | Santa and Enormous Crocodile; flags, not true errors. |

The 56 `subset` rows are 44 intra-character + 12 cross-character. The earlier punctuation-as-token-separator checker gives **14 surface pairs / 10 record pairs**, still two books, adding two Santa/Santa-nav comparisons. Preserve both versioned audit baselines; neither licenses merging Santa with the navigation device. The new article-only matcher has its own diagnostic baseline (§4.1.3), not these old possessive-stripping counts.

**Data model: three layers, with an occurrence ledger linking them.** Use `alias_schema_version="alias-mentions-v2"`. A canonical alias string is a lexical representation, never a character ID.

| Layer | Persisted or derived contract | Why it is needed |
|---|---|---|
| 1. Character identity | Book-scoped immutable analytical `entity_id`, canonical display name, `entity_kind`, existing character reference, `identity_evidence`, `member_of`, former-ID links and unresolved identity alternatives. Seed legacy identity from its stored reference; carry that identity through later rename mappings. | One referent can have many expressions; group and member remain different identities. No bulk rename of the 1,494 existing characters is required. |
| 2. Recorded alias entries | Existing `Alias.name` is the **raw recorded surface**; add separate `canonical_form`, `canonical_source` (`derived`, `manual`, `legacy_fallback`), `canonical_rule_version`, `canonical_input_hash`, immutable `storage_id`, source/provenance, `entry_status` and optional `supersedes`/effective-attachment links. Keep the existing book/character references. Model output retains all emitted surfaces, even unsupported ones in the audit ledger. | Satisfies Chris's raw + canonical choice without erasing student input. A many-entry family is indexed by entity ID; choosing a display representative never removes another entry. |
| 3. Mention patterns | Derived, rebuildable index of normalized token sequences, article options, alias-entry IDs, candidate entity IDs and owner/context constraints; version and hash its input. Patterns may map to multiple entities. | Match-time equivalence is separate from archival wording and identity. Never use a global string→single-character map. |
| Occurrence ledger | `book_id`, page/text-version hash, raw start/end offsets, exact surface, matched entry/pattern IDs, candidate IDs, resolved ID or unresolved status, evidence and optional speech/coreference/language-span relations. | Count each source mention once per task, retaining ambiguity and auditability. Different patterns hitting one span are one occurrence, not extra observations. |

The compatibility `aliases` export continues to expose **all recorded raw forms**, not one shortest representative per family. Export the separate canonical field and the occurrence view with explicit versions. Consumers must use resolved occurrence identities for counts; summing alias rows is invalid. Operational patterns use the effective accepted attachment: explicitly rejected/superseded attachments are inactive, with the reason and corrective link retained. All raw records, including inactive ones, remain in the archival export; a separate active view exposes statuses and provenance. Missing legacy status defaults to a retained, unverified candidate, never silent exclusion. A corrected wrong attachment must not continue contributing its former candidate ID, but its historical surface and student record must remain recoverable. Editorial labels stay separate and never become search patterns merely because a canonical field or display name contains them.

**Canonical field versus match-time article handling.** Derive `canonical_form` deterministically from the raw field using NFKC, casefold, canonical apostrophes, Unicode word tokens retaining internal apostrophes, punctuation as token separators and collapsed whitespace. Preserve the original bytes in `name`; the canonical field removes **no articles, possessives, modifiers, number or negation**. This is the existing `normalize_name` tokenization with article removal disabled. Do not stem, accent-fold, singularize, use token-set matching or erase named genitives. A human override changes the grouping/display representation with provenance; it does not invent a text-attested alias or merge identities. Build matching patterns from raw entries and text-attested character names, not arbitrary override text.

**Recommend handling `a`, `an`, `the` at match time.** Generate a full-token pattern and an optional leading article around the same remaining token sequence; link both to the original entry. Thus `the little whale` and `little whale` can retrieve the same candidate without rewriting either stored string. Permit this for common-noun referring expressions; preserve lexicalized proper names where an initial article is integral, or mark the broad match ambiguous if category is unknown. An article-only string generates no empty pattern. Keep full and article-free offsets related to the same occurrence; record which match tier fired. Match within pages and token boundaries. Prefer the maximal compatible referring span for counting, but retain nested alternatives and **union their candidate identities before resolving**: longest-match alone must not choose a wrong character or hide a collision. A bare `whale` hit inside `the little whale` is only candidate retrieval; retain the whole referring phrase and its modifier evidence. Optional-article matching can improve retrieval and can introduce ambiguity, so it must pass the occurrence checks below.

Pronominal possessives (`my`, `your`, `his`, `her`, `its`, `our`, `their`) are **not articles** in this design. They carry perspective/owner evidence; resolve through context rather than strip them. Named genitives such as `Marlon's` likewise remain constrained. Keep quantifiers (`one`, `all`, `each`), ordinals/superlatives, negation, number and modifiers. `the whale`, `the little whale`, `storm whale`, and Chris's `the littlest penguin` / `little penguin` remain distinct patterns linked to an identity only when context supports that link. A singular/plural head can suggest candidates, never prove identity; `reindeer` does not distinguish grammatical number by spelling.

**One-form entry and backwards compatibility are required paths, not optional migration work.** The implementation must cover:

- Manual entry: [enter_text.py:498](../pages/enter_text.py#L498) clears `alias_form_` state; [enter_text.py:711](../pages/enter_text.py#L711) constructs the alias and calls `to_form()` at line 729. [alias.py:76](../data_structures/alias.py#L76) is the single string input. Replace the destructive `strip_leading_article` at [alias.py:89](../data_structures/alias.py#L89) with raw preservation. Derive canonical data on **save**, before initial `register()` at line 103; a student supplies no second field and makes no model call. Offer an optional advanced canonical override/reset-to-derived control. An override is audited and retained on unrelated edits; it never changes the raw field or character attachment.
- Reviewed AI suggestions: [enter_text.py:850](../pages/enter_text.py#L850), `_parse_aliases` at [line 905](../pages/enter_text.py#L905), the alias input at [line 1226](../pages/enter_text.py#L1226), and persistence at [line 1055](../pages/enter_text.py#L1055) must share the same adapter. The current parser strips determiners, case-deduplicates and removes forms equal to the name. Preserve the emitted list and raw surfaces before presentation. Use structured alias rows for new responses so commas within a surface are not treated as delimiters; keep a documented legacy comma-input adapter without claiming it can reconstruct previously lost punctuation. A rejected suggestion remains in the run audit, separate from accepted entries.
- Persistence: add fields/defaults through [Alias.fields at line 9](../data_structures/alias.py#L9) and `Field` descriptors, with `form_fields`/exports updated. [Field.__set__ at line 25](../data_structures/base_structure.py#L25) writes single fields through; [the base loader at line 53](../data_structures/base_structure.py#L53) initializes missing fields under `reading_from_db`. For a new entity, populate fields while unregistered and use `register`/`register_batched`; for an existing entry use the descriptors and audit changed fields before assignment. Ordinary `register(create_only=True)` already exists; the batched path currently uses `batch.set(..., merge=True)` and marks registration before commit ([base_structure.py:195](../data_structures/base_structure.py#L195)). Extend that shared path to create-only conflict semantics with registration acknowledged after successful commit; do not call it unchanged and claim overwrite protection. Do not replace this with ad hoc Firestore document writes. Treat canonical form + source/rule/input hash as a validated bundle: partial/stale bundles fall back on read until completion, never drive matching. Capture the raw hash before deriving and reject a concurrent-source change. A pre-read/hash check alone is not race-safe: implement conditional update-time checks or a transaction in the shared persistence/descriptor layer for the canonical bundle and backfill, aborting and re-reading on conflict. Preserve the Field-based entity interface; do not overwrite a human override that arrived after the dry-run/check.
- Read fallback: declare `canonical_form=""` and safe defaults. A **pure, non-writing** `effective_canonical()` checks absent/null/NaN/blank/non-string values, unknown rule/source values and stale derived hashes; derive from `name`, falling back to the original nonempty string if normalization yields nothing. Return provenance/status with the result. Never raise for an old missing canonical field, drop its alias, or write back simply because a form was opened. Raw-dict readers must use the same helper. A missing character reference remains an orphaned entry for resolution, not an excuse to delete it or attach it to the first option.
- ID safety: [alias.py:35](../data_structures/alias.py#L35) currently derives IDs from book + name, omitting character; [enter_text.py:1044](../pages/enter_text.py#L1044) consequently skips cross-character name collisions. Preserve every old document at its **actual stored ID**. Add optional immutable `storage_id`; loaded objects take the actual snapshot ID, injected into loader input or assigned under `reading_from_db`, never through an unguarded post-load Field assignment. Fall back to the old formula only for older callers. New entries receive a deterministic versioned ID from book reference + character identity + exact raw surface (full tuple hash, checked against the payload on collision). Set it before registration and use create-only conflict protection. The same form under two identities must be representable without overwrite or skip. Canonical edits never rename a document. Repeated same-identity/same-raw input may reuse its entry while retaining provenance; different legacy entries are not deleted.
- Widget/lookup rules: capture `key_suffix = self.document_id` before assignments, as [alias.py:49](../data_structures/alias.py#L49) already does; new value-seeded entity widgets use that suffix, existing keys remain unchanged, and new-entry resets remain in place. All new user text belongs in `text_content`. Preserve the membership guard at alias.py:66 and `.get` at line 93; use equivalent guards for canonical modes, stale references and all touched options. Do not silently default an orphan to another character. These are [CLAUDE.md](../CLAUDE.md)'s Field, widget-key and lookup conventions.

**Backfill: additive, deterministic, $0 model/API cost for all 200 existing aliases.** First produce a local dry-run ledger of actual document ID, original name/reference/timestamps/hash, proposed canonical fields and version. Apply only absent derived fields with the conditional-write protection above after rechecking the source hash and existing override; retain all old names, references, provenance and IDs. Backfill `storage_id` with the existing ID, never a new hash. Make replay idempotent, checkpoint partial progress and compare before/after raw-entry counts/hashes and matchability. Unknown legacy canonical metadata is preserved and flagged, not overwritten. Rollback can ignore the new projection fields; no record deletion is necessary. The 1,494 characters remain readable without a mass rewrite. Historical entry code already stripped some strings: preserve them as **legacy recorded surfaces**, and do not claim to recover original keystrokes or removed articles. New pattern expansion is explicit inference, not recovered archival text. Any future source-assisted enrichment is a separate additive record with provenance, outside this rule-only backfill.

**Review and validation:** [review_my_books.py:70](../pages/review_my_books.py#L70) only routes to editing, so needs **no direct alias-schema change**; regression-check its route through [book_edit_home.py:41](../pages/book_edit_home.py#L41) to text/character management. The alias list at [enter_text.py:767](../pages/enter_text.py#L767) must use the shared fallback and preserve raw display, with optional canonical/provenance detail. The validation page **does need changes**: `_edit_aliases` at [validation.py:543](../pages/validation.py#L543) currently edits only `name` and recreates/deletes a renamed entry at lines 575–583. Replace that destructive correction with a linked corrective entry and retained original/provenance; corrections can mark the earlier entry superseded without erasing it from archive/export. A canonical-only edit writes canonical fields, never creates/deletes an alias. Use `_log` at line 66 before write-through, including override/reset and attachment changes. Load actual snapshot IDs and preserve all new metadata. Guard the character selection at [validation.py:606](../pages/validation.py#L606) with `.get` and an explicit missing-reference state; reuse the page's guarded-index helpers at lines 81–96. Legacy aliases remain visible and valid without a canonical field. Existing review/validation status is not invalidated just because a derived field is absent.

**Identity discrimination, not a merge/split bias.** Use the §4.1 prompt's explicit evidence records and post-process them as a graph of `same_entity`, `distinct_entity`, `member_of` and unresolved links. Explicit naming/apposition supports identity; distinct agents interacting, group/member contrasts and separately attributed speech support distinction. Co-occurrence alone is insufficient because apposition repeats one identity. Grammatical number can distinguish a herd from a member, but collective singulars, generic uses and invariant `reindeer` require syntax/context. Distinct actions can belong to the same actor sequentially; require distinct contemporaneous subjects or other referential evidence. Dialogue contributes attribution cues, not a rule that every quoted turn has a different speaker. Neither shared species nor lack of an overt contrast is a merge warrant; neither descriptive variation nor an unresolved link is a split warrant. Do not replace uncertainty with a new asserted entity for every mention.

Use available text before images. Where locally cached image evidence is available in a later implementation, record page/crop/hash and whether it shows simultaneous distinct actors, an individual within a group, or a visually continuous scene. A repeated character in sequential panels is not two individuals; colour/size alone is not identity, and neither occlusion nor absence from a crop proves nonexistence. Image claims must have evidence provenance and unresolved status if not determinative. No new image-derived identity verdict is used here, no image-only characters are added to the present text detector, and no additional manual image review is assumed. Any automated image-based identity resolver is a separate costed ablation, not an unpriced second call on every book.

Before accepting a merge, check the whole evidence component for incompatible distinctness/number/membership edges; transitivity of `same_entity` must not swallow a known group/member distinction. A conflict remains unresolved. Preserve old IDs and every source record even after supported identity linkage. Resolve occurrence offsets by matching the quote/surface to the stated page, with a reversible normalized-to-raw offset map; multiple compatible locations require more context or remain unresolved. Deduplicate exact occurrence keys, not surface strings. A missing span is reported as evidence unavailable, never as a zero duplicate-assignment success. Only context-resolved occurrences enter exclusive character profiles; unresolved candidate sets remain accessible for sensitivity analysis, without silently crediting every member or selecting one.

**M2/M3 reporting:** retain the historical raw redundancy and cross-record candidate incidences above. M2's adoption gates are **zero lost recorded surfaces, zero duplicate counts for the same source occurrence/identity**, and a single compiled entry per identical pattern/constraint/candidate-set key. There is no target to reduce raw alias entries or modifier diversity. M3 requires zero unhandled flags and zero mutually exclusive double assignments; report handled, unresolved and span-unavailable counts separately, together with their book/occurrence denominators. An unresolved flag is handled operationally but not a correct attribution. Require no unexplained increase in unresolved occurrence coverage at fixed inputs and no loss of known positives; sending everything to uncertainty cannot pass. False merges and false splits have **separate** rates and targets in §4.1.3, never a combined score. The concrete dev20 residue is assessed there.

### 4.1.2 M1 groundedness: the annotation-free output-to-text check

M1 is computable on **all pilot books with pipeline output, without annotation**, including books with no student aliases. It replaces the retired reference-precision diagnostic for checking lexical support, but cannot measure correct attribution or downstream accuracy. It measures agreement with the pipeline's **own extracted text**, not independent correctness or correct attachment. The denominator is **explicit emitted alias surfaces only**, deduplicated identically by entity and exact normalized surface before canonical grouping; it excludes character names from this metric. Preserve the emitted-alias ledger even if a later adapter rejects an alias. Report character-name grounding separately, without claiming the 112-alias baseline applies to it. Report micro counts and book-macro scores with empty-output/failed-book coverage.

The supplied `grounding.txt` result is reproduced by loose case-insensitive substring matching over extracted text: **97/112 = 86.6% verbatim**, eight more after leading determiner removal, **105/112 = 93.8% cumulative**, seven absent (**6.25%, rounded 6.2%**). It is a historical loose-search diagnostic. The operative metric uses the same NFKC/casefold/apostrophe/word-token normalizer as §4.1.1, **contiguous whole-token sequences on one page**, never cross-page concatenation or substring matching inside a token. Search all completed archival page text first; separately record support in the exact main-content input sent to characters. For continuity with the measured M1 baseline, first test the full alias token sequence (`G_exact`); only then remove one leading item from the historical `the|a|an|my|your|his|her|its|our|their` list and test (`G_det`). This is a **frozen permissive grounding diagnostic**, not the new article-only mention matcher or permission to erase owner constraints. Report exact and permissive tiers separately; a possessive-stripped hit cannot establish attribution. No stemming, named-genitive rewriting, modifier deletion, reordering or plural folding is allowed. Empty forms and uncertainty-only tokens never supply grounding.

Recomputation gives **95 exact (84.8%) + 9 determiner-stripped = 104/112 (92.9%)**, with **8 absent (7.1%)**. `the swan` had been credited inside `the swans`; it moves to the stripped tier because a singular `swan` occurs elsewhere. Bear Hunt `us` loses its embedded-substring match and becomes the eighth missing item; it is already invalid as a pronoun under C3. Keep the normalization-version change separate from any pipeline gain.

| Alias absent under the operative rule | Classification from available evidence |
|---|---|
| Santa: `Santa Claus` | Familiar-name expansion not supported by supplied OCR; absence is not photographic proof of invention. |
| Youngest reindeer: `the reindeer (youngest)` | Editorial parenthetical disambiguator, not a verbatim lexical expression. |
| Devon children: `Alfie, Ava, Ben, Brooke, Charlie, Chloe... Zac, Zara, Zybil` | Editorial elision of printed names on pp31/33; components are printed, the concatenation is not one attested alias. |
| Notsobig One: `the Notsobig Crocodile` | Generated expansion; retain source-absence uncertainty. |
| Mom/Dad: `Billy's mom`, `Billy's dad` | Possessive expansions of printed Mom/Dad/his mom/his dad, not exact lexical support. |
| Bear Hunt: `the family` | Unattested collective display construction in supplied text. |
| Bear Hunt: `us` | No whole-token grounding found; independently invalid as a lexical alias. |

**Defined allowance, not a 100% lexical target:** allow separately typed editorial labels for (i) a parenthetical disambiguator constructed from an attested head and identity/modifier evidence for that same referent, or (ii) an elided list whose every retained name has page-local support and whose collective membership is evidenced. Require source spans, construction type and referenced entity IDs; the label is never counted as a printed occurrence. Do not allow cultural expansions or arbitrary possessive rewrites. Define `E` as the subset of **ungrounded entries already in the emitted-alias denominator A** that pass these deterministic evidence checks: `E ⊆ A \ G`. The support tiers are disjoint. Separately emitted `editorial_labels` are outside both A and E and have their own count/support ledger. An unverified claim of an allowed class earns no allowance. The reindeer/list rows are candidates for these two classes, **not an already-validated E=2 baseline**. Report lexical `G=(G_exact+G_det)/A`, editorial `E/A`, explained-support `(G_exact+G_det+E)/A`, and unexplained `(A−G_exact−G_det−E)/A`. Editorial status never waives a wrong referent attachment. New prompts should emit these as editorial labels, with their output counts and the historical alias-to-label movement explicit; never hide a denominator change as a gain.

**Target:** boundary-safe lexical M1 **≥95%**, compared with **92.9%** on dev20, with no decline in known-positive recall or output/failure coverage. Require all editorial allowances to satisfy their typed evidence checks and report the remaining unexplained share; do not require 100% literal grounding or call the unexplained share a hallucination rate. Run the same check on the emitted-surface ledger before post-processing, preventing deletion of failed-evidence aliases from manufacturing success.

**OCR confound:** §5 records four scored failures responsible for 153/234 missing reference tokens; extracted text is not a complete witness. For aliases absent from the character-stage input, report (a) found in cached student/adjudicated text but absent from archival pipeline text, (b) found in archival pipeline text but excluded by classification/projection, (c) validated editorial allowance, and (d) unlocated with available evidence. Tier (b) is archival-grounded for headline M1 but missing from the character input; keep that distinction explicit. These are diagnostic attributions, not new ground truth. M1 keeps its original denominator and does not automatically excuse every alias in an OCR-failed book. Report M1 by failed/partial versus completed OCR coverage, and a fixed-cached-text character-only ablation alongside end-to-end M1. If OCR improves while aliases stay fixed, label the resulting grounding gain as upstream coverage. Self-consistent wrong OCR can ground a wrong alias, and missing OCR can leave a valid one ungrounded; neither direction proves alias precision. M3 separately addresses attachment, which M1 cannot assess.

### 4.1.3 Mention-level evaluation, rule comparison and identity residue

**M4 task estimands.** Define a lexical character mention as a page-local referring span in the extracted main-content text, with independently supported referent identity; exclude pronouns from this lexical denominator and report their coreference coverage separately. With a complete eligible set `D`, report (i) **unique attribution coverage**: spans assigned exactly one ID / `|D|`; (ii) **correct unique attribution recall**: spans assigned exactly their supported ID / `|D|`; (iii) **wrong attribution**: spans assigned exactly one ID that differs from the supported ID / `|D|`; and (iv) ambiguous/unassigned shares, with wrong/assigned also reported as contamination among assigned mentions. Unique coverage equals correct-plus-wrong unique assignments / `|D|`; candidate sets with several IDs are ambiguity, not a committed wrong assignment. A false detected span assigned to a character is contamination too: report spurious assigned spans separately against independently assessed predicted spans, rather than hiding them outside `D`. For T2 additionally report wrongly associated text tokens / all assigned language tokens once the language-span rule and supporting reference exist. For T1 assess speaker and addressee correctness separately on evidenced turns; a correct alias hit does not settle either role.

**Dev20 true mention-level baseline: UNMEASURED.** Cached `predictions.json` and extracted text provide candidate strings and occurrences, not an exhaustive independent mention inventory, identity map, speech/addressee labels or T2 span labels. The student alias list is a set of known positives, not such a reference. Defining true mentions as model-string hits or declaring a unique dictionary candidate correct would be circular; Jill's misattached phrase is a concrete counterexample. No additional annotation programme is commissioned to fill this gap. If existing independently labelled spans later become available, freeze their book/page/text hash, offsets, referent IDs and same/different-identity evidence; enumerate `D` on that text version, align predicted spans by exact boundaries (with an explicitly versioned article-boundary equivalence), count the four outcomes above and bootstrap whole books. Keep unmatched predicted spans and uncertain labels separate, and state reference coverage. Do not extrapolate a few regression cases to full-book truth.

**What can be measured now: fixed-universe dictionary retrieval and ambiguity.** The following local recomputation uses `ART/dev20-run/predictions.json` and `scripts/extraction_eval/normalization.py:tokenize`, selecting **579 pages with `contains_story=true`, all with completed status**, across the 20 books. Use all **105 character names + 112 explicit alias forms**, preserving their existing character-array indices as candidate identities. This deliberately audits historical output, including forms that later type/evidence checks may reject; it is not an endorsement of pronouns, synthetic labels or the predicted main-content scope. On each page, tokenize with NFKC/casefold/apostrophe normalization. For the broad article-retrieval diagnostic, remove at most one initial `a|an|the` from each pattern, skip empty patterns, find contiguous token matches, and extend the span left by one if the immediately preceding page token is an article. This diagnostic applies the option before proper-name/owner/context eligibility checks; production resolution must still apply §4.1.1's constraints.

Build a **fixed universe U of 916 candidate occurrences** from the raw all-form hits: sort half-open token intervals within each page and merge strictly overlapping intervals transitively, never merely adjacent intervals. These are overlap components, not verified noun phrases. Keep the union interval and all nested hits. For each candidate rule, collect the distinct character indices of its hits contained in each fixed component; cardinality 0/1/>1 means missed/single-candidate/ambiguous. No deletion can shrink `U`, so missing a difficult occurrence cannot improve the denominator. Persist raw-offset mappings in implementation; this offline token-index calculation does not invent character offsets or context-resolved IDs.

With casefolded **exact** patterns and no article option, **811/916** components are found: **807 single-candidate + 4 ambiguous**, with **105 missed**. With all raw surfaces and optional articles, **916/916** are found: **894/916 = 97.6% single-candidate**, **22/916 = 2.4% ambiguous**. The article option adds **105** candidate occurrences and **18** ambiguous components. Of the 22, **18 are in Santa** (14 reindeer overlaps and four Santa/Santa-nav tokenization overlaps), and **four are in Crocodile**. No correct-attribution claim follows from 894: unique candidates can be wrong, and these patterns do not find every true mention. Full phrase/context evidence must prevent a `Santa` token inside `Santa-nav` from becoming a character attribution.

**Evaluate canonicalisation at fixed text, matcher, candidate identities and U.** Each destructive counterfactual below keeps only surviving *attested* source strings; the same optional-article matcher is applied to every row. Narrow equality uses the historical article/pronominal-possessive key, keeping the original name if in the group, otherwise the minimum normalized token count, raw length, original order. The named-genitive variant additionally erases the leading named possessive in its grouping key. Broad rules form within-character connected components with an edge for **ordered key equality or strict token-set subset**, not equality of reordered token sets. Shortest/longest representatives use normalized token count, normalized length, then original order, reversing the first two for longest. No row merges characters. These rules reproduce the earlier 9/14/47 alias-row deletions; occurrence effects, not row counts, now drive the choice.

| Rule, same optional-article matcher | Alias rows removed /112 | Found /916 | Single-candidate /916 | Ambiguous /916 | Missed /916 |
|---|---:|---:|---:|---:|---:|
| **Raw-preserving grouping, any display representative (recommended)** | **0** | **916** | **894** | **22** | **0** |
| Destructive narrow determiner/possessive equality | 9 | 916 | 894 | 22 | 0 |
| Also erase named genitives | 14 | 906 | 884 | 22 | 10 |
| Broad subset components, shortest representative | 47 | 901 | 879 | 22 | 15 |
| Same components, longest representative | 47 | 731 | 728 | 3 | 185 |

All counts are dictionary diagnostics, with true correct/wrong attribution **unmeasured for every row**. The narrow deletion has no measured retrieval benefit and irreversibly discards recorded information; a zero loss in this small dictionary universe does not establish universal safety. Named-genitive deletion loses ten candidates; shortest loses 15 and longest loses 185. Longest's apparent ambiguity improvement from 22 to three is mainly suppression, not evidence of better identity reasoning. Keeping all raw patterns makes the result invariant to shortest/longest display choice. Singular/plural folding is **not adopted**: its numerical effect is unmeasured; evaluate it only as candidate discovery with the same fixed-universe/identity checks and separately list newly introduced hits outside U. No row-count result can override group/member evidence.

Retain the previous **known-positive** sensitivity checks as a second axis: under fixed existing entity alignment and lexical scoring, narrow deletion loses **0/6** recovered explicit student aliases and **0/88** credited name variants; named-genitive deletion loses **0/6** and **3/88**; shortest loses **1/6** (`cave dad`) and **8/88**; longest loses **1/6** (`mother`) and **18/88**. There are only **10 explicit student aliases** in dev20, so either broad deletion changes aliases-only recall from **6/10=.600 to 5/10=.500**. These values were recomputed from predictions, `reports/book_scores.json` and `dev20-snap/inventory.json`; they are not mention-frequency effects. Lossless grouping preserves them all. Report full-rematch sensitivity separately because this comparison freezes alignment. Student descriptive practice (134/200 lowercase strings) reinforces the need to retain surfaces but does not replace occurrence evaluation.

**Separate false-merge and false-split metrics.** For a frozen evidence-eligible set of within-book mention pairs, let `D_distinct` contain independently supported different-referent pairs and `D_same` independently supported same-referent pairs. **False-merge rate = pairs asserted same / |D_distinct|; false-split rate = pairs asserted distinct / |D_same|.** Report both numerators/denominators, two separate intervals, and unresolved-decision coverage in each stratum. A local mention ID with unresolved cross-scene linkage is not an asserted split. Targets are separately **0 known false merges** and **0 known false splits** on the existing evidence fixtures, with no paired increase on eligible evidence; neither rate may compensate for the other. A zero denominator is undefined, never zero error. Require resolution of existing explicit naming/group-member cases and report abstentions, so refusing every identity decision cannot pass. **Population dev20 rates are unmeasured** because no complete same/different-identity reference exists; an incomplete student inventory cannot be used to call every extra record a false split. M3's 2/20 flag incidence is not either error rate.

**What the named dev20 cases resolve, and what remains:**

| Case | Cached extracted-text discrimination | Remaining uncertainty |
|---|---|---|
| Santa's three reindeer records | p20 has the youngest look toward the old red-nosed reindeer: two distinct individuals. p25's reindeer pull with “their” might: a collective. p13's wink to “the reindeer” occurs in the youngest's scene, so this occurrence must not route mechanically to the herd. | **Zero unresolved identity distinctions among these three records** from the cited contrasts; occurrence attribution still requires context. Keep herd/member links, and the two individuals separate. Three records are not three duplicates. |
| Crocodile's four overlapping girl forms | p22 explicitly names “one little girl” as Jill; the subsequent “the little girl” also refers to her. Correct the Fair children attachment. p19's older girl asks and “the others” answer: she is a member, not identity-equal to Playground children. | **Two unsupported assertions in one book:** Mary's stored `the girl` has no exact contiguous whole-token occurrence in the extracted text; and the older unnamed girl's cross-scene identity relative to Mary/Jill is not established. |

The absence of Mary's full phrase is an evidence gap, not proof that no girl mention could refer to her. The optional-article hit on generic `girl` cannot supply that proof. Preserve Mary's recorded entry with unsupported-attachment status. Preserve the older girl's local speaker mention and attributes, with unresolved cross-scene identity links; neither forcibly merge her into Mary/Jill nor assert a new globally distinct individual merely to avoid merging. Do not introduce global speaker IDs or exclusive aggregate character profiles from that unresolved link. Source text supports these development diagnoses; no photograph adjudication or exhaustive book-level identity labelling is claimed.

**Residue/workload:** the named audit leaves **two unresolved assertions in one of 20 books**. Scaling only this observed workload gives `2/20×100 = 10 issues` across `1/20×100 = 5 books per 100 books`. Before contextual triage, the structural checker flags `2/20×100 = 10 books per 100`; that is a different, broader workload. Neither extrapolation estimates population error or the total unseen ambiguity queue. **Default: no mandatory human-review queue and no new annotation labour.** Retain/export the unresolved evidence and exclude uncertain cross-identity assignments from exclusive T1/T2 outputs, with coverage reported. If optional review is later requested, an explicitly assumed **3 minutes/issue** means **30 minutes per 100 books** for this observed residue, plus any image retrieval and other unresolved cases; that cost is not a free fallback or an approved work programme. It is only justified if resolving these spans changes the intended analysis enough to warrant it.

M4's computable adoption checks are zero **unexplained** loss of supported occurrences, no extra double-counted occurrences, repair of the existing evidenced misattachments, and no unexplained rise in unresolved coverage at fixed inputs. Typed pronoun/body-part/editorial exclusions are reported as diagnostic population changes with their raw records retained, not disguised as recall gains or losses. Retain both missing and ambiguous counts when comparing rules; do not optimize the single-candidate fraction alone. A full T1/T2 claim remains unavailable without its task reference and language-span definition.

### 4.2 Page transcription

Keep current image resolution, rotation/crop behavior and ordinary-page token limit in the first prompt experiment. The following replaces the contradictory artwork exclusion and clarifies coverage; it does not make text detection more conservative. Proposed additional `regions` are necessary to preserve illustration lettering independently of the analytical convention.

```text
Transcribe the supplied photographed book page or spread. Read the visible book,
not your memory of it. Inspect the whole page, including margins, small isolated
lines, speech bubbles, captions, decorative, angled and curved lettering, and
text printed within illustrations. Include front/back matter and book-authored
signs, labels and sound effects. Ignore the photographed room, hands, library
stickers and later handwritten annotations. Exclude standalone page-number folios;
retain numbers that are part of the content.

Transcribe every visible occurrence once. Preserve spelling, invented words,
punctuation and repeated words/letters as seen. Do not correct the book's wording,
complete a sentence from memory, describe an illustration, or continue beyond
the image. For repeated-letter strings, follow each visible line and stop at its
visible end; do not keep generating the same letter. If uncertain about a span,
use [reading?], or [?] if none of it can be read, retaining readable surroundings.

For facing pages, read the left page fully before the right unless a continuous
text block visibly spans both. Within each page follow its visible reading order.
Before returning, check for a missed final line, second dialogue block, or small
lettering. This check must not add any word you cannot see.

Return JSON only. regions is an ordered list with kind narrative, informational,
illustration_lettering, or paratext and text containing its exact transcription.
text joins those region texts in order with blank lines. has_text is true exactly
when there is transcribable non-folio book text, including an uncertainty marker
for a visibly present unreadable word. With no such text use text="", regions=[]
and has_text=false. A false has_text must never accompany a nonempty text.

Classify page_type independently: cover, title, copyright, dedication, contents,
narrative, informational, wordless_narrative, endpaper, blank, glossary_index,
supplemental_activity, other_back_matter, or uncertain.
is_story_page is true for narrative, informational and wordless_narrative under
the supplied main-content convention. Typography or absence of text alone cannot
decide this. Set story_needs_context=true if sequence context is necessary;
otherwise false. This flag does not suppress transcription.

{"has_text":true,"text":"...","regions":[{"kind":"narrative","text":"..."}],
"is_story_page":true,"page_type":"narrative","story_needs_context":false}

EXAMPLE — The Runaway Pea, photograph 20:
{"has_text":true,"text":"\"Owwww,\" cried the pea, \"my bottom's on fire,\"",
"regions":[{"kind":"narrative","text":"\"Owwww,\" cried the pea, \"my bottom's on fire,\""}],
"is_story_page":true,"page_type":"narrative","story_needs_context":false}
This text is present even though the pilot transcription was empty.

EXAMPLE — How the Grinch Stole Christmas, photograph 46:
Transcription is "MERRY MERRY"; has_text=true; its region kind is
illustration_lettering. Do not turn it into an empty string. Whether the page
participates in the narrative is decided from the page/sequence, not from the
fact that its words are printed on a pennant.

EXAMPLE — It's Christmas, photograph 30:
Transcription is "Wheee!"; has_text=true; region kind=illustration_lettering.
It is a printed sound effect, not a description invented for the picture.

Now transcribe the target image. Examples supply behavior, never missing words.
```

The three example transcriptions above are photograph-adjudicated, development-only examples. *Norman the Slug* p24 (“But he was falling”) and *The Book With No Pictures* p33 (“Oh, is that so?”) are additional locked regression cases, not necessary prompt padding. Do not use the unverified reference's enormous repeated-letter string on p41 as an exact ICL target.

`text` is archival all-print text under C1; derive `analysis_text` from tagged regions for a research convention. Classification never clears either transcription. For character extraction, the proposed default includes narrative/informational text and in-scene lettering on main-content pages, excluding paratext; save the precise projection version. Never silently change research token counts when adding artwork lettering.

### 4.3 Story classification with book context

The page prompt supplies a provisional label. Evaluate one additional **whole-book classification-only pass** over all ordered low-resolution thumbnails and the predicted OCR, using no reference story flags. Start with Sonnet 5; compare Haiku 4.5 as a cost ablation. This tests movement in the 50 raw empty-text disagreements against existing imported labels and addresses the nonfiction inconsistency without rerunning or rewriting OCR. It cannot establish true story-classification accuracy without the annotation that has been cancelled. Keep a page-prompt-only variant in the ablation so the extra call must earn its cost.

```text
Classify each photographed page of this ONE book using the ordered thumbnails
and supplied OCR. OCR may be incomplete; absence of OCR is not absence of story.
Do not transcribe, correct, or delete text in this task.

The research convention is MAIN CONTENT: narrative scenes and the main
informational/instructional work count; a wordless scene continuing that work
also counts. Covers, title/copyright/dedication/contents pages, decorative
endpapers, genuinely blank pages, glossary/index and supplemental activities
after the main work do not count. An illustrated page can be either main content
or endpaper. Decide from its visual content and sequence, not its page number
alone. Do not fill a continuous story range across blank or bibliographic pages.

A back-cover synopsis remains paratext even when it names the characters and
describes a plot. "THE END" alone is a closing marker, not enough to establish a
narrative scene; an actual scene or concluding dialogue can still be main content.
An in-scene sound effect/sign is transcribed elsewhere, and counts as part of
main content if its illustration is a narrative scene. Lettering on decorative
endpapers does not make them narrative. Do not erase wordless narrative scenes.

For nonfiction distinguish the book's main instructional sequence from a
separately introduced appendix of activities. An introduction explaining how to
use the book and a glossary/index are paratext under this convention.

Return JSON only, with exactly one result for every supplied page_number:
{"pages":[{"page_number":1,"contains_story":false,"page_type":"cover",
"decision":"resolved","evidence":"short visible/sequence basis"}]}
decision is resolved or review_required. If evidence cannot resolve a page,
return contains_story=null, decision=review_required; do not invent certainty.

EXAMPLE A — How the Grinch Stole Christmas, photograph 64, excerpt:
"hatches a fiendish plot to steal Christmas from the citizens of Who-ville,
he's in for the surprise of his life!"
With its back-cover placement/presentation this is a synopsis: contains_story=false,
page_type=other_back_matter. Names and plot wording do not make a synopsis story.

EXAMPLE B — All Year Round, photograph 10, excerpt:
"Nature diary ... You can make a nature diary to keep a record of the things
you spot at different times of year"
This is a chapter in the main instructional sequence: contains_story=true,
page_type=informational. Do not require fictional narration.
Photograph 34 starts "Glossary ... Aestivation": contains_story=false,
page_type=glossary_index, even though the imported story range includes it.

EXAMPLE C — Wiggling Worms At Work, photographs 36–37:
36 introduces "FIND OUT MORE ABOUT WORMS" / "A Worm of Your Own".
37 continues "A Worm's Home" and "Do Earthworms Really Help the Soil?".
These are supplemental activities after the main exposition:
contains_story=false, page_type=supplemental_activity.

Now classify the target book's pages under the same convention.
```

Examples A–C have text/sequence support, not a new photographic adjudication by this author; retain them as provisional C2 prompt demonstrations, never independent gold labels. For *Grinch* p46 and *It's Christmas* p30 the transcription is settled; the `contains_story` value still requires the C1/C2 scene convention and page-context review. Proposed value is true if their artwork continues the story. Do not use reference labels alone to decide.

The context result supersedes the provisional label only when structurally valid. Missing/duplicate page outputs invalidate that classification call; keep the provisional labels as an explicitly recorded fallback and count the context-stage failure. A valid unresolved result stays unresolved in the final output and counts as an abstention in end-to-end scoring; it cannot disappear from the denominator. Preserve provisional output for diagnostics. Do not send unresolved pages to characters as definitely story; retain their text as unresolved data and report the resulting character-input coverage; no staffed review queue is presumed. Accept this pass only if the recall/coverage gates in §8 pass; abstaining on difficult pages must not manufacture higher precision.

The frozen inventory has at most 70 photographs/book, below the configured 100-image limit. Use 784-pixel thumbnails, actual page-number labels and all predicted page texts for this pass. Enforce the pinned model's image-count, input-token and 32 MB request limits before dispatch. If a request exceeds a limit, record `context_request_limit` and use the explicit provisional fallback; do not silently truncate the book or introduce an untested chunking algorithm. Report fallback rate and its quality as a subgroup.

### 4.4 Title, copyright and metadata reconciliation

Retain inexpensive page location initially. Read title and copyright together for extraction, adding the cover if distinct and available. The active fill-only merge must be replaced by evidence-ranked field reconciliation; prompt changes alone cannot rescue the garbled but nonempty names it currently preserves.

Locator revision (retain the existing two-key return contract):

```text
Locate the internal title page and the copyright/imprint page among these ordered
photographs. Return the supplied image POSITION, not a printed folio. The title
page normally has the full title and credits; prefer it over a half-title with
only part of the title, or a decorative cover. Use the cover only if no internal
title page is visible. The copyright page contains publication/edition statements,
copyright notices, ISBN or publisher details and can occur near either end.
Both roles may occupy the same photograph. Do not assume they are adjacent.
Return JSON only: {"title_page":integer_or_null,"copyright_page":integer_or_null}.
```

Joint extraction prompt, replacing the two incompatible field-only responses:

```text
Extract bibliographic evidence from the supplied cover/title/copyright photographs
of ONE physical edition. Use only visible evidence. Multiple statements may refer
to different editions, territories or roles; preserve that distinction.

title: the complete work title, including words distributed across different lines
or artwork. Ignore series advertising. Prefer the full internal title, corroborated
by the cover. Report a conflict if visible sources disagree; do not silently choose
the first nonempty string or supply a famous title from memory.

credits: list every visible person and explicit role: author, illustrator, reteller,
translator, original_author, editor, designer, or unspecified. Exclude credits
in advertisements for another book. "Illustrated by", "pictures by" and "art by" establish
illustrator. Text/illustration copyright statements can establish those roles.
An unexplained name or single ambiguous "by" does not establish both roles. A
reteller is retained as reteller and included in the authors projection. Copy names
exactly; use corroborating print on another supplied page to resolve stylized text.
Never invent a missing first name or infer an illustrator from prior knowledge.

publisher_statements: extract each publication statement with publisher/imprint,
year, scope (original, territorial_first, edition, reprint, copyright, or unknown),
and territory if printed. Distinguish an imprint from a parent, printer,
distributor, rights holder or "by arrangement with" organization. Preserve the raw
publisher string and its role. Do not replace the photographed-edition imprint
with a familiar parent company. Copyright year alone is copyright evidence, not
proof of first publication. The earliest printed date may refer to another work
or component; never use min(all years).

isbns: all visible ISBN strings with format/edition qualifiers if present.
Do not repair digits by guessing. Missing facts are null or empty lists.
Give a source page_number and an exact short quote for each value.

Return JSON only:
{"title":{"value":null,"evidence":[]},
"credits":[{"name":"...","role":"author","page_number":1,"quote":"..."}],
"publisher_statements":[{"publisher":"...","publisher_role":"imprint",
"year":2000,"scope":"edition","territory":null,"page_number":2,"quote":"..."}],
"isbns":[{"raw":"...","qualifier":null,"page_number":2,"quote":"..."}],
"conflicts":[]}

title.evidence is a list of {page_number:integer,quote:string} records.
publisher_role is imprint, publisher, parent, printer, distributor, rights_holder,
or unknown. Publisher and year may be null. conflicts is a list of
{field:string,candidates:[{value:any,page_number:integer,quote:string}],reason:string}.
Keep original_author, editor and designer separate from author/reteller projections.

EXAMPLE A — A Squash and a Squeeze, photograph 6, publication-line excerpts:
"First published 1993 by Methuen Children's Books"
"This edition published 2016 by Macmillan Children's Books"
Expected records: original/Methuen Children's Books/1993;
edition/Macmillan Children's Books/2016. Both are evidence; do not collapse them.

EXAMPLE B — The Enormous Crocodile, photograph 2, publication facts:
Jonathan Cape 1978; Picture Puffins 1980.
Expected decisions: retain the separate first-publication and subsequent-edition
statements with their exact source quotations. Do not change the current imprint
to Jonathan Cape merely because it is the original publisher.

EXAMPLE C — Santa is Coming to Devon, photograph 6, credit excerpt:
"Illustrated by Robert Dunn and Amerigo Pinelli"
Expected credits: Robert Dunn/illustrator and Amerigo Pinelli/illustrator,
both with photograph 6 evidence. An earlier title-page credit to one person does
not remove the other explicit credit.

EXAMPLE D — How the Grinch Stole Christmas, photograph 6, excerpt:
"First published in the UK in 1973"
Expected scope=territorial_first, territory=UK, year=1973. Do not produce a
worldwide first-publication year from memory. A separate 2008 edition statement
is retained as an edition event, not a correction of the 1973 statement.

EXAMPLE E — The Ugly Duckling, photograph 5, excerpt:
"Retold and Illustrated by IAN BECK"
Expected credits: IAN BECK/reteller and IAN BECK/illustrator. Do not fabricate
an author quotation; the authors projection includes retellers only under C6.

Now extract the supplied target photographs.
```

Example A is a publication-line excerpt; preserve the exact case/punctuation from the cached page when rendering the final pack. Example B deliberately provides facts/decisions rather than fabricating a verbatim line. All five remain dev examples corroborated by CSV OCR, not independent ground truth. The cancelled annotation round supplies no new target facts.

Implementable reconciliation policy:

1. Always extract both roles even when locator positions coincide; one joint call avoids today's skipped copyright pass in that case. Label images with actual page numbers as well as positions.
2. Union explicitly supported credits by normalized full name and role. Never merge different people on surname alone. Keep unspecified-role credits for review. Do not guess Ian Beck's role without visible evidence.
3. For photographed-edition publisher prefer an explicit “this edition”/publication statement, then a corroborated title-page imprint; never prefer a printer/rights holder. Populate the distinct publication fields specified below. Prefer an explicit work-first event for `first_publication`; otherwise use exactly one consistent territorial-first event with its territory retained. Conflicting events at the chosen scope return null plus conflict records, never the minimum year. A copyright-only date remains evidence in `publication_events`, not asserted first publication. Score each event only against a reference with the same scope.
4. If title/credit strings conflict or remain missing, examine at most two additional candidate pages ranked by predicted OCR containing title/credit/publication cues (then ascending page number to break ties); reuse existing extraction text first, and request image re-reading only where needed. Cap at one additional joint vision call per book. The target book's predictions, never reference title/page labels, select these pages. Run initial metadata extraction as today, but defer this final reconciliation/fallback until page OCR completes; the current metadata-before-OCR stage order otherwise lacks the necessary input.
5. A visible spelling on two pages or an explicit plain-print credit outranks a garbled stylized reading. Unresolved conflicts return null plus candidates, not an arbitrary majority vote. Count unresolved/failed values in operational coverage.
6. ISBN lookup is optional missing-field enrichment, below visible photo evidence. Validate ISBN and retain returned edition identifiers before use. For evaluation, replay frozen lookup results only. A lookup failure remains failure, not evidence that a credit does not exist. Do not expand to live web-title searches in the prompt experiment.
7. Preserve strict raw publisher-string agreement as a diagnostic. Separately report bibliographic equivalence only where existing evidence supports it, with work/edition scope and a versioned table; unknown mappings remain unresolved, without a fresh review requirement. `&`/`and`, legal suffixes and historical imprints require explicit rules; “all Macmillans are equivalent” is not a rule.

### 4.5 Accepted publication schema, migration and scoring

Use `metadata_schema_version="bibliography-v2"`. The two target objects are **both mandatory keys**, with nullable components; they are never alternatives chosen to match the reference:

```json
{
  "metadata_schema_version": "bibliography-v2",
  "photographed_edition": {
    "publisher": null, "publisher_authority_id": null, "year": null, "territory": null,
    "publisher_status": "unresolved", "year_status": "unresolved",
    "evidence": []
  },
  "first_publication": {
    "publisher": null, "publisher_authority_id": null, "year": null,
    "scope": "unknown", "territory": null,
    "publisher_status": "unresolved", "year_status": "unresolved",
    "evidence": []
  },
  "publication_events": [],
  "legacy_publication": {
    "publisher": null, "published_year": null,
    "source": null, "migration_status": "needs_review"
  }
}
```

`publisher` preserves the printed imprint string and `publisher_authority_id` is an optional reviewed authority ID; `year` is an integer or null. `first_publication.scope` is `work_first`, `territorial_first`, or `unknown`; territorial-first requires a territory. An unqualified first-publication statement supports `work_first` as asserted by that source, not independent proof of worldwide priority. Per-component status is `evidenced`, `not_stated`, `unresolved`, or `conflicting`; technical failure is recorded separately at stage level. Evidence records contain field, source page ID, image hash, exact quote and provenance (`photo`, `frozen_lookup`, or `legacy_unverified`). `publication_events` retains every extracted original/territorial/edition/reprint/copyright statement and its imprint/role/territory, including events not selected into either target. Map prompt scope `original` to target scope `work_first`, retaining the raw event scope; all other event scopes retain their explicit meaning. Never pair a publisher from one event with a year from another.

For *Squash*, record photographed edition Macmillan Children's Books/2016 and first publication Methuen Children's Books/1993. For *Grinch*, preserve the evidenced UK-first 1973 and the separate 2008 edition event; do not infer worldwide 1957 solely from familiarity or the imported reference. An edition's year may remain null while its imprint is evidenced, and vice versa. If both a work-first and territorial-first event exist, put the work-first in `first_publication` and retain territorial-first in `publication_events`; if only conflicting territorial events exist, leave the target unresolved. This deterministic rule avoids a further choice of which historical event to score.

**Migration of existing records (future implementation, no writes now):**

1. Preserve old `publisher`/`published_year` values, source IDs, timestamps and record hash verbatim in `legacy_publication`; retain the immutable v1 scoring snapshot. Create both v2 target objects with null values/status unresolved. Never bulk-copy a legacy publisher into both targets or assume its year names a particular event.
2. Reconcile existing cached photo evidence and publication statements first. Populate only components whose event identity is supported; map separately for each field. Values supported only by legacy import/lookup remain unverified candidates in the migration queue until edition/event identity is established. Retain unknown/conflicting values rather than guessing an original publisher from the current imprint.
3. Make the migration idempotent by record ID, original hash and schema version; produce a dry-run field-change ledger, preserve manual verified corrections, and reject conflicting source versions. Reuse supported cached publication events; unknown event identities stay unresolved. No new human publication-event review is required. Any later photo re-extraction is a separately costed inference step.
4. Update persistence, metadata adapters, forms/export columns, snapshot eligibility, adjudication field allowlists and reporting together so extra fields are not silently dropped. Display/export distinct “Photographed edition imprint/year” and “First publication publisher/year/scope”. Existing top-level `publisher` and `published_year` become documented compatibility projections of the evidenced photographed-edition imprint and scoped first-publication year respectively, with `published_year_scope`/territory carried alongside. Preserve old values in `legacy_publication`; a missing verified target projects null, never an unlabelled fallback. Version exports so consumers cannot confuse old and new meanings.

**Scoring:** report four field agreement scores separately where existing event-scoped target facts are available: `photographed_edition.publisher`, `photographed_edition.year`, `first_publication.publisher`, and `first_publication.year`; report scope/territory and joint event agreement on the same available facts. Compare imprint strings strictly, then with a versioned, event-scoped reviewed equivalence table. Year is exact integer agreement **at the same event/scope/territory**; 1973 UK-first does not match 1957 work-first or 2008 edition. Do not globally normalize parent companies to imprints. Use only existing scoped reference facts and supplied adjudications; retained but unverified legacy history is not gold. Where no such v2 target exists, report the field denominator as zero/not estimable and output evidence/coverage only; do not invent a fresh annotation dependency or a v2 accuracy gate. `not_stated` requires existing evidence for absence; otherwise use unresolved. Track assertions conflicting with known facts separately; unknown reference values cannot establish that an assertion is unsupported. Failed/unresolved predictions against a known fact are incorrect and remain in its denominator; genuinely unknown reference values have separate counts and are not automatic model successes. Report field coverage across all books and known-fact conflicts only on eligible existing evidence.

Apply the **same** v2 adapter, event policy and reference to cached baseline and candidate. Baseline outputs lacking event provenance remain unresolved unless their saved inputs/outputs establish it; do not supply candidate-only evidence to the baseline. Report (a) raw v1 agreement, (b) fixed-output schema/reference/scorer repair, and (c) new-inference gain over that repaired baseline. Publisher 0.50 is not a new prompt's starting accuracy or an automatic +.50 opportunity. Both publication targets are accepted; their extraction quality still needs measurement.

## 5. Reliability and OCR's remaining recall gap

Do the following as separately measurable reliability work, after the alias/metadata/classification experiments. None authorizes an inference run now.

* Preserve raw response, provider stop reason, parsed/salvaged status and completeness. `vision_json` currently salvages malformed JSON, and the page adapter forces `text=""` when `has_text=false`. A contradictory nonempty transcription must become `schema_conflict` for review/retry, not disappear. A salvaged prefix is partial output, not a completed page. Count missing reference content and record partial coverage.
* Keep the normal 2,048-token page ceiling initially. One longer response allowance (4,096 tokens) is appropriate only for an ordinary dense-page truncation with a valid, nonrepetitive prefix. *Book With No Pictures* p41 is a repetition failure: increasing its ceiling alone is likely to buy more repeated `e`. For a completed request with malformed or runaway output, permit **one** metered re-extraction using the repetition/visible-line instructions above, retain both responses, and accept only a structurally complete response. Never select the answer by closeness to gold. If it still fails, mark unresolved; a human transcription is a separately attributed repair, not automated credit.
* Detect runaway output diagnostically, for example a repeated single-character run of at least 256 code points or `max_tokens` with no complete closing JSON. It is a flag, never a rule to delete or shorten genuine repeated lettering. Do not use the student string as a repair template. Check the flag on cached p41 output and repeated-letter controls without treating it as proof of a visual error; the unresolved photo-dependent conclusion is listed in the final section.
* Do not automatically replay the three permanent provider content-filter errors or the cover refusal as generic transient failures. Preserve those errors, include them in automated end-to-end denominators and route to a manual exception queue. No prompt can be assumed to remove this coverage limit. Manual completion can make the research corpus complete but must be reported separately from fully automated performance.
* One retry is allowed for a definite pre-response transient transport/provider failure under the metered gateway. Unknown-outcome requests retain their reservation and require reconciliation; a timeout is not permission to spend twice. No silent SDK retry or unmetered fallback.

Do not perform a second expensive vision pass on every page. Evaluate targeted rereading only for explicit schema/coverage errors, unresolved text markers, and identified metadata conflicts; use a fixed trigger rule at inference time, not the list of gold mismatches. Page crop/resolution changes have no independently adjudicated causal subgroup in this study, so remain deferred. Keep the already tested rotation/crop logic fixed; §6 tests only the rotation model under that logic, with a historical-fixture regression guard.

The hard-failure contribution is substantial, but even perfect recovery of those 153 reference tokens would leave 81 reference-token deficits whose truth and scope are uncertain. The seven adjudicated recoveries establish that some apparent extra tokens are successes. OCR near 0.991 may already be close to this reference's noise floor on completed ordinary pages. Retain token recall, CER/WER, coverage, lettering subgroups and source checks; do not chase a cosmetic F1 increase through aggressive normalization or omitted print.

## 6. Model and cost experiments

Use the rates **already verified in** [`scripts/extraction_eval/baseline.json`](../scripts/extraction_eval/baseline.json), timestamp 2026-11-04. No current-price web lookup was performed or is needed for this design. Amounts below are USD, standard token rates, not batch discounts.

| Model | Input / output per million tokens | Cache read / cache write per million |
|---|---:|---:|
| `claude-sonnet-5` | $2 / $10 | $0.20 / $2.50 |
| `claude-sonnet-4-6` | $3 / $15 | $0.30 / $3.75 |
| `claude-haiku-4-5` | $1 / $5 | $0.10 / $1.25 |

Actual cached-call accounting gives $15.045192 total, **$0.752260/book**. These means use 20 books, including the two without eligible character references; the detector nevertheless ran on all 20. All recorded cache token counts are zero. Provider-call counts differ from page counts because the run can reuse identical requests; do not multiply every stage's mean call cost by 772 blindly.

| Stage | Measured cost/book | Model-only fixed-token experiment | Increment/book |
|---|---:|---|---:|
| Characters, Sonnet 5 | $0.010411 | Replace with Sonnet 4.6 | **+$0.005206** |
| Metadata extraction, Sonnet 5, 39 calls total | $0.020216 | Replace with Sonnet 4.6 | **+$0.010108** |
| Key-page locator, Haiku | $0.026991 | Replace with Sonnet 5 / Sonnet 4.6 | +$0.026991 / +$0.053982 |
| Page extraction, Sonnet 5 | $0.459514 | Replace all with Sonnet 4.6 | **+$0.229757** |
| Rotation, Sonnet 4.6 | $0.232912 | Model-only Haiku 4.5 ablation | **−$0.155275** at fixed token use |
| Crop checks, Haiku | $0.002216 | No proposed change | $0 |

For fixed token use the Sonnet 5 → 4.6 change is exactly +50%; that is a price comparison, **not evidence that 4.6 is better**. Changing model can change output lengths, refusals and token accounting; measure actual costs in experiments. Opus or another provider/model has no verified rate in this baseline and no local comparative evidence, so this design does not nominate or price it as an upgrade.

**Rotation is the first cost ablation.** It used **814 completed Sonnet 4.6 subcalls against 772 page records**, costing **$4.658235, or 30.96% of total spend**. The ledger has 750 distinct rotation page-stage IDs (695 with one call, 46 with two, nine with three); generic stage labels do not establish which were triage, binary or spread-probe calls, or imply 42 retries. Model-only Haiku substitution at fixed token use costs $1.552745 per 20 books: **$3.105490 saved (66.7% of rotation, 20.64% of the whole run), $0.155275/book**. Changed branch frequencies, responses and retries can change that saving.

Freeze the production `get_rotation_angle` path, prompt wording, parsing, 16-token response limit, deterministic +90° binary step, 1.15 aspect threshold, SINGLE-only probe override, uncertainty flags and crop inputs. Replace **only** `claude-sonnet-4-6` with `claude-haiku-4-5` for all rotation subcalls. Use identical cached raw/pre-rotation image bytes and image settings; instrument triage/binary/probe separately, plus final applied angle, uncertainty, actual cost and call count. The old `scripts/rotation_prompt_eval/rot_eval.py` is not ready to run unchanged: it embodies older gate behaviour and its cache identity omits model ID. The new ablation must invoke current production logic and key caches by image/prompt/model/settings/parser/replicate hashes.

Run both models once over all dev20 pages, plus three independent replicates of each on frozen historical rotation regressions with available input bytes and expected outcomes. [The July fix](rotation_triage_fix_2026-07-17.md) achieved 26/26 portrait-shot-spread triage verdicts plus 12/12 orientation controls (**38/38 triage, not final-angle accuracy**). The July 18 `rot_eval.py` result records **195/195 final-angle outcomes on 65 images** under the then-current gate; separate prompt comments record probe controls of 36/36 covers and 200/200 folds. These are historical results, not fresh validation of the current path. Preserve their original expected labels and distinguish triage from final-angle tests; do not manufacture labels from the current model. No new orientation annotation is required or planned. Missing local input/expected-output pairs are unavailable tests, not passes.

**Rotation regression guard:** on every available protected fixture and all three replicates, require no new wrong expected final angle, no regression in expected triage/uncertainty behavior and lower measured total cost including extra branches/retries. On unlabelled dev pages report Sonnet/Haiku angle disagreement, uncertainty and failure rates, not orientation accuracy. Require no increase in unresolved/failure counts. If a fixture fails or the protected fixture set cannot be reconstructed, retain Sonnet. Delete the former population orientation-accuracy/noninferiority gate: there is no independent held-out orientation reference. A pass supports only preserved **historical fixture behavior**, not general orientation accuracy. Freeze any passing choice before the holdout, report holdout angle/uncertainty differences, and rerun downstream OCR/characters wherever final image bytes change. Apply the existing-reference OCR checks and already-adjudicated blank/recovery guards in §8; never score a changed orientation using cached Sonnet-oriented downstream outputs.

**Experiments worth running after implementation:**

1. **Characters:** identical revised prompt on Sonnet 5 and Sonnet 4.6, using cached OCR/story inputs. The whole-stage delta is only about half a cent per book at old token use. Consider 4.6 only if known-positive recall, M1–M3 and supported M4 occurrence outcomes improve beyond replicate variation and persist on the untouched subsample; true attachment-accuracy gains cannot be estimated here. More aliases or more conservative output alone is not success. A second whole-book verifier is not the default; it adds about $0.010411/book on Sonnet 5 or $0.015617 on 4.6 at old input/output size and must demonstrate benefit beyond one evidence-rich call.
2. **Metadata:** first test joint evidence extraction and merge with Sonnet 5. Test a single Sonnet 4.6 fallback only on predeclared unresolved/garbled/conflicting fields. An additional average old-size metadata read costs $0.010367 on Sonnet 5 or $0.015551 on 4.6 **per triggered book**; at trigger fraction `f`, multiply by `f`. It earns adoption by correctly reading photo-supported missing title/credits/imprint facts, not by returning remembered reference bibliography. A three-image joint call is larger than the old single-image call: recompute token forecasts rather than using that mean as a guaranteed ceiling.
3. **Story context:** Sonnet 5 is the initial quality experiment; Haiku is a cheaper challenger. Planning assumption: about 30,000 input tokens (all low-resolution thumbnails, OCR and prompt) and 1,000 output tokens/book. Increment is approximately **$0.070/book** on Sonnet 5, **$0.035** on Haiku, or $0.105 on 4.6. The actual locator averaged 26,871 input tokens before added OCR, so this is a plausible budget estimate, not measured new-stage usage. Compare existing-reference agreement, abstentions, input coverage and downstream recall/M1–M3 by book. There is no independent classification superiority gate; any adoption is provisional under §8 and cannot be advertised as verified story-accuracy improvement.
4. **OCR/locator:** no default upgrade. OCR escalation would add $4.60 to an equivalent 20-book run before prompt growth, despite no demonstrated superiority. Locator escalation has no demonstrated need: failure to locate *Worms* copyright does not prove that a usable copyright page exists. Use already verified regression cases to assess targeted changes; without broader independent evidence, keep the default models.

Prompt/evidence growth is real cost. On Sonnet 5, an extra 1,000 uncached input tokens costs **$0.002/request**; at 38.6 pages/book that is about **$0.0772/book** if every page is a cache miss. A character prompt with +1,500 input tokens and +1,000 evidence-output tokens adds about **$0.013/book**. An illustrative metadata change with +1,000 input and +500 output tokens adds $0.007 per request. These are explicitly sized scenarios, not claims about the as-yet-unrendered pack. Keep OCR ICL compact; do not attach the entire example library to every photo.

At fixed usage, the candidate can remain on Sonnet 5 for all extraction stages. More expensive models are justified as low-cost **experiments** on the weakest stages; none has yet earned deployment. Price every rendered request and reserve its configured worst case in the existing spend ledger. Proposed schema growth requires character/context output ceilings of 8,192 and metadata 4,096; extra allowance is not charged unless generated, but it affects reservations and failure policy.

## 7. Crediting `reference_wrong` without rewarding mistakes

Extend the existing local adjudication overlay, not the immutable snapshot and not a second evaluator. Existing `--adjudications` can already produce separate raw/adjudicated reports, but the overlay lacks an explicit error verdict. More seriously, `--compare-run` currently compares **raw OCR only**. Both must change **before any candidate comparison**, including development ranking. In this checkout `cli.py` calls `paired_comparison(..., value="ocr_f1")` near lines 257–259 and supplies the raw manifest even when separate adjudicated reports exist.

Implement a named metric registry in `compare-run`: known-positive variant/alias and character recall; M1 groundedness/allowances, M2 redundancy and M3 structural collision flags; M4 fixed-universe mention retrieval/ambiguity and any evidence-eligible correct/wrong attribution; separate false-merge and false-split rates, unresolved coverage and case-regression outcomes; existing-reference story agreement and OCR recall/F1/CER/WER; each eligible existing v2 publication/credit fact; historical rotation fixture outcomes; blank/recovery regression counts; and output/failure coverage. Reference-precision **0.447 is diagnostic only (48% of the 195 books have zero recorded aliases)**, never a gate. Delete complete-reference precision/F1 endpoints and independently verified population story/OCR/rotation accuracy endpoints from this study. Carry per-book numerators, denominators, eligibility, failure states, stratum and weight into each comparison; recompute ratios inside book resamples. Retain paired book-ID/reference-hash checks and extend them to schema, convention, matching, normalizer and overlay hashes. Route the same adjudicated manifest to both runs. Emit raw→raw and corrected→corrected comparisons plus fixed-baseline measurement-repair deltas; refuse mixed protocols or silently dropped undefined metrics. Implement §8's exploratory labels, intervals and computable gates. Fixtures must show that character-only changes are visible, OCR-only gains cannot substitute for another endpoint, stale overlays are rejected and failures remain in denominators. This is a design prerequisite, not implementation performed here.

Separate the adjudication verdict from eligibility:

```json
{
  "schema_version": "adjudication-v2",
  "convention_version": "research-convention-v1",
  "snapshot_id": "<exact snapshot identity>",
  "records": [{
    "book_id": "the_runaway_pea",
    "page_id": "the_runaway_pea_20",
    "field": "text",
    "original_value_hash": "<hash of original empty field and its provenance>",
    "verdict": "reference_wrong",
    "status": "verified",
    "corrected_value": "\"Owwww,\" cried the pea, \"my bottom's on fire,\"",
    "evidence": {
      "image_sha256": "<snapshot original image hash>",
      "adjudication_source": "planning/wordless_adjudication_2026-11-05.md",
      "method": "photographic_review_reported_in_supplied_adjudication"
    },
    "reviewer": "<actual reviewer identity, or documented unavailable identity>",
    "reason": "Printed text omitted from pilot transcription"
  }]
}
```

This is a schema example, not an overlay created in this design task. Do not fabricate hashes or reviewer identity. Record the supplied adjudication as the authority when importing its seven verdicts; distinguish that provenance from a new reviewer personally viewing a photograph. Other verdicts: `reference_confirmed`, `prediction_wrong`, `both_wrong`, `ambiguous`, `convention_pending`. `both_wrong` also needs a corrected value when resolvable; ambiguity is not automatic prediction credit.

Apply correction semantics as follows:

1. Validate snapshot/original-value/image identity, field type, convention version and provenance; reject stale/conflicting overlay records. Preserve an append-only correction history and overlay hash.
2. For `reference_wrong`, replace the **effective scoring value** with the verified corrected value and mark it eligible. Score every baseline/candidate against that same value. Do not award a blanket perfect page or field: a subsequent model that drops the recovered text loses recall; a partially correct response gets partial OCR credit.
3. For aliases, attach an adjudicated surface to a stable book-local entity ID, correct merges/splits where supported and rematch one-to-one. Adding one missing alias does **not** make a book's whole alias annotation complete. A synthetic display label can match a reviewed entity without becoming an attested lexical variant. Keep incomplete-reference known-positive recall distinct from output groundedness; historical reference-precision is diagnostic only, with the 48%-empty-alias caveat. No complete-reference precision is estimated.
4. For metadata, preserve field semantics and publication event. A reference from a different edition can be marked wrong **for the chosen target**, while its historical fact remains stored. Semantic equivalence is separate from both literal exact match and a factual correction.
5. For story, keep its adjudication independent of text. A text correction never automatically flips `contains_story`. Annotation-policy changes create a new convention/protocol version and corresponding baseline rescore, not an unannounced accuracy gain.
6. Compare raw-to-raw and adjudicated-to-adjudicated for all endpoints. Require identical eligible reference hashes, overlay hash, conventions, normalizers, field scope and book IDs on both sides. Fail comparison rather than silently use different denominators. Also report raw baseline → corrected baseline to show measurement repair separately from extraction improvement.

Define the lexical scoring migration symmetrically. Retain v1 `{name} ∪ aliases` for both runs. For v2, a canonical name is counted as a lexical variant only when text-attested; otherwise it is an entity display label in **both** legacy and new outputs. Determine that from the supplied page text and existing evidence, not solely the candidate's `name_is_label` assertion; retain unresolved cases explicitly. Explicitly emitted aliases remain predictions even when their evidence fails validation: an unsupported alias stays in the M1 denominator as ungrounded, never disappears merely because validation failed; absence from OCR alone does not prove it wrong. Entity-population projections apply identically to both systems and the reference. Explicitly emitted pronoun/body-part aliases remain invalid outputs in the structural audit, even when a parser can recognize them; do not erase those errors through the projection. Legacy responses need no invented page evidence: mark their evidence unavailable, check computable text support using cached input, and keep evidence-compliance separate from the comparable text-grounding endpoint. This ensures removing synthetic labels through a metric/schema correction is shown as a baseline rescore, not credited solely to the new prompt. Report v1 and v2 variant denominators explicitly; neither should be assumed still to equal 197/106.

Known text-recovery correction ledger:

| Book ID | Photo | Recovered tokens |
|---|---:|---:|
| `how_the_grinch_stole_christmas` | 46 | 2 |
| `it's_christmas` | 30 | 1 |
| `norman_the_slug_with_the_silly_shell` | 24 | 4 |
| `santa_is_coming_to_devon` | 24 | 28 |
| `the_book_with_no_pictures` | 33 | 4 |
| `the_runaway_pea` | 20 | 8 |
| `wiggling_worms_at_work` | 29 | 33 |
| **Total** | **7 pages** | **80** |

Copy each complete corrected text from its adjudicated cached output, retaining source hashes. **Audit detail:** the Santa CSV additionally contains `"Everybody, PULL!"`, omitted from the shortened quotation in the adjudication table. The supplied verdict explicitly accepts all seven outputs and all 80 tokens. Record that discrepancy and the full cached text; do not silently truncate it to the table quotation or claim a separate photo inspection here.

Under the proposed all-print convention, these corrections make eligible text pages 451 → 458; matched/reference/predicted token counts become **16,615 / 16,849 / 16,694**, each 80 higher than before. Offline arithmetic gives book-macro F1 about **0.990636**, versus 0.990583 raw. This tiny score change does not diminish the 80 genuine recoveries; it illustrates why F1 movement alone is inadequate. This is reference correction credit, not a new extraction gain.

Add separate `contains_story` corrections for *Book With No Pictures* p6 and p24: true → false, `page_type=blank`, reason `reference_wrong` from all-pages import. Record Chris's p6 photo judgement and the p24 identical-image hash transfer separately; do not alter their empty OCR text. The other 16 empty false negatives stay unresolved; no new photo/sequence review is scheduled (see final open questions). This is reference repair, not a classification-prompt gain.

Keep the original 108-page empty-reference cohort visible as **101 empty/empty agreements, 7 verified recoveries, 0 confirmed hallucinations**. The 101 have not all been photograph-verified; the supplied p6 judgement and p24 hash transfer settle two of them, without a new photo inspection by this author. After correction, 101 remain in the empty-reference subgroup; never report 101/101 as verified wordless accuracy. Rename unreviewed `invented_tokens` to `unreviewed_disagreement_tokens`; separately report `recovered_correct_tokens`, `confirmed_hallucinated_tokens`, `credited_fields`, failure/abstention coverage and unresolved reviews.

Under C1, the three artwork tokens are credited in all-print transcription, with a separate illustration-lettering stratum. If Chris excludes lettering from the analytical text view, score that projection against a correspondingly projected reference and still credit archival recovery. Do not count these real tokens as hallucinations in either convention. Their story labels depend on scene context under C2.

New implementation acceptance fixtures, specified here but not implemented/run: all seven corrected pages credited; omission by a later candidate penalized; one corrected alias leaves `complete_reference=false`; conflicting hashes rejected; both runs receive the same overlay; a wrong candidate value cannot earn credit from the verdict alone; imported non-story empty text stays `not_annotated` until reviewed; failures remain in intended denominators; a missing metadata lookup does not masquerade as reference ineligibility. Publish source-known-reference agreement and operational all-book coverage separately, including the existing author's lookup-failure exclusion.

## 8. Development, existing-reference holdout and publication claims

**No fresh annotation programme.** Chris has cancelled it. There is no new complete character inventory, OCR transcription subset, story/wordless review programme or independent orientation annotation. All computable endpoints below use existing student records, pipeline output, the supplied adjudications or historical rotation fixtures; true-task endpoints without those labels are defined but unmeasured. There is no confirmatory story-accuracy primary endpoint. Alias/entity improvements remain **EXPLORATORY**.

### Allocation and leakage protection

The dev20 manifest is **not** a documented probability sample from the full corpus: its inventory has only those 20 books, inclusion probability 1.0, `challenge_count=0` and `held_out_book_ids=[]`. Its intervals describe that selection; 772 pages are not independent books. The old “full inventory” description conflated two populations:

| Inventory / eligibility computation | Books | What is established locally |
|---|---:|---|
| Full metadata census, `ART/EMPTY_TEXT_DIAG.json` | 276 | 200 `pilot_import` records and 76 other student entries; not a complete image/reference snapshot. |
| Existing exposed set | 33 | dev20 + both Gruffalo books + 11 additional historical rotation books, as below. |
| Full census after those exposures | 243 | `276 − 33`; this is the owner's approximate pool **before** missing-text exclusions. |
| Text-bearing after required exclusions | 235 | `276 − 33 − 8`; none of the eight no-text books overlaps the 33. The five no-S3 metadata shells are among the eight, so subtract them only once. |
| Strict recorded-story-text candidates | 233 | Two further records, `it's_a_no-money_day` and `it's_ok_to_be_different`, have one nonempty page each but no page flagged story. Exclude them if requiring nonempty recorded story text; imported story flags remain imperfect. |
| Cached image-ready pilot reference | 195 | `ART/pilot-v1/inventory.json` filters to `pilot_import`; five shells are separately excluded. This is the source of the 1,494-character/200-alias census, not all 276 records. |
| Cached pool after the mandated 33 | 162 | `195 − 33`; 160 have recorded characters, **90 have explicit student aliases**, 72 have none. |
| Additional incidental exposure during the previous revision | 1 | A schema inspection printed nested page text for `a_letter_to_santa` (34 page records, partly truncated). No examples use it. Quarantine it rather than claim it remained unseen. |
| **Usable, locally verified holdout after all 34 exposures** | **161** | **159 character-recall-eligible; 90 alias-recall-eligible; 71 zero-alias books.** All have nonempty recorded story text in the metadata census. Contains 1,297 recorded characters, 178 explicit aliases and 5,582 image objects. |

Thus the wider metadata-only bounds become **234 text-bearing / 232 strict-story candidates** after the additional quarantine. Their complete S3 availability and character/alias eligibility cannot be established from local artefacts: the 76 other entries have no complete cached character inventory here. **Do not invent a full-276 alias-recall pool size.** The precise runnable pools for this design are **161 operational, 159 character-positive and 90 alias-positive**; extending beyond them would require an existing-data snapshot, not fresh annotation; the approved sample uses this verified pool. Reproduce eligibility by joining inventory `records[].book.id` to the metadata census, filtering `n_story > n_story_empty`, requiring a cached folder/images entry, subtracting the exposure-ID union, and counting nonempty character/alias records without inspecting held-out prose. The two cached character-empty books are `ten_in_the_bed_and_other_counting_rhymes` and `you_choose`; both also have zero recorded aliases. They remain eligible for output-only M1–M3. Zero-alias books may contribute canonical-name/character recall, but never explicit-alias recall.

The active metadata prompt supplies **The Gruffalo**'s title, author, illustrator, publisher and year. Keep both `the_gruffalo` and **`the_gruffalo's_child`** quarantined; inventory credits establish their known example linkage. The historical manifest `scripts/rotation_prompt_eval/sample_manifest.json` exposes 12 pilot books; *The Storm Whale* is already in dev20. The other 11 are `born_to_be_a_butterfly`, `bottoms_up!`, `elmer_on_stilts`, `kipper's_a_to_z`, `knock_knock_dinosaur`, `lenny_makes_a_wish`, `lost_in_snow`, `the_bad-tempered_ladybird`, `the_cave`, `the_smartest_giant_in_town`, `who_will_save_us`. Retain this explicit exposure ledger and the additional `a_letter_to_santa` incident.

Automatic title/image-hash grouping found no multi-book groups among the 195, but cannot exclude differently photographed duplicate editions. Freeze metadata/hash-based work/edition grouping before sampling; quarantine any group linked to an exposure, choose its lexicographically first eligible ID as representative and recompute counts if grouping changes. Never split a group across development and holdout. Do not open remaining page text for example selection. Shared authors/publishers beyond the Gruffalo linkage are possible; this is an unexposed-book test, not an unseen-author study.

**APPROVED IN PRINCIPLE: ~40 books, with execution blocked until subscription credits are linked.** Use **40 total books** from the verified 161: randomly select **27 of the 90 alias-positive books and 13 of the 71 alias-zero books**, seed **20261105**, before opening output or text. This preserves approximately the earlier two-to-one sampling allocation within Chris's approved scale. Oversampling alias-positive books makes the sparse recall denominator useful while retaining output checks on books the students left alias-empty. Use inclusion probabilities `27/90` and `13/71`, weights `90/27` and `71/13`; retain page count/density as reporting strata rather than inventing additional tiny sampling cells. Freeze IDs, reference hashes, group IDs, eligibility counts, seed and weights. The remaining books stay unexposed, with no prearranged second-look or sample expansion after results. This replaces the cancelled annotated allocation; no reserve programme is commissioned.

Keep the challenge manifest entirely within exposed dev20: Grinch/It's Christmas lettering; Grinch/Book With No Pictures failures; All for One/Enormous Crocodile aliases; Squash/Cave Baby metadata; All Year Round/Worms classification. Challenges remain descriptive, never pooled into a probability estimate. Using eligibility counts to sample is permitted; using held-out content to choose prompts is not.

### Ordered experiments using existing evidence

1. Freeze the raw baseline and reference. Preserve §2's census and appendix, import only already-supported §7 corrections, retain unresolved verdicts, and extend `compare-run`. Apply C3/C4 and version the provisional C1/C2/C6 conventions symmetrically. Produce fixed-output measurement-repair scores first; no fresh adjudication is a precondition.
2. At fixed Sonnet 5 and cached current page inputs, compare the new character prompt alone, then joint metadata/reconciliation alone. Include rules-only versus rules-plus-ICL as an explicit ablation. Compare raw output and the same §4.1 raw/canonical adapter and mention matcher on both systems to isolate prompt gains from deterministic representation/matching changes. Type A representative choice must not consume NLP experiments without an occurrence benefit; prioritize Type B evidence discrimination.
3. Compare revised page prompt and context classification. Rerun characters on each system's own final story/OCR output. Retain a diagnostic with identical cached inputs to distinguish character reasoning from upstream changes; it is not end-to-end performance.
4. Test §6's rotation/model ablations independently. Run three response replicates on four fixed dev books: *All for One*, *Enormous Crocodile*, *All Year Round*, *Grinch*. Use distinct replicate IDs/cache keys, report distributions/flips and never select the best replicate. Every configuration inspected is a development trial.
5. Freeze one candidate's prompt/example bytes and hashes, model IDs, adapters, schema, retry rules, generation/image settings, matching, canonicalisation, metric definitions and thresholds. Run baseline and candidate on the **same 40 selected books**, only after the credit-linking precondition in §9 is satisfied, with identical original image bytes and frozen lookup inputs. Recompute dependent stages if rotation changes. Comparing candidate holdout scores directly with dev20 is invalid.
6. Compute reference recall, M1–M3 and the computable M4 diagnostics without new annotation; keep true attribution and false-merge/false-split rates unmeasured wherever independent evidence is absent; retain failures, uncertainty, reference eligibility and support tiers. No tuning or new examples after unblinding. All sampled books then become exposed; do not rerun an edited candidate and call the same sample held out. Human repairs, if later requested, are separate assisted results.

### Existing-artifact safety gates and output monitoring

Freeze the **101 originally empty/empty pages** by ID, original image hash and baseline response. They are a monitoring cohort, **not 101 verified wordless pages**. Record newly nonempty outputs as unreviewed disagreements and count their tokens; do not call them hallucinations, require them to stay empty, or commission photo review as a gate. The genuine-blank evidence is *Book With No Pictures* p6 plus p24's identical-image transfer: **two page records, one independent image**. Require empty OCR, `has_text=false`, `contains_story=false` and completed outputs on both. The seven already-adjudicated recovery pages must retain their accepted text under the same archival normalization; a verified recovered token cannot be dropped to improve empty-page agreement. These are fixed regression gates, not population accuracy estimates.

Require structurally valid page/context outputs, preserved nonempty text on a conflicting `has_text=false`, no new permanently failed/unrun/unresolved pages relative to baseline, and no newly lost known reference field through parsing/projection. Null against a known fact counts as unhandled; unknown target facts cannot be presumed known. Apply §6's historical rotation fixture/cost guard separately; no fresh orientation labels or population accuracy gate survive. Keep the 16 unresolved empty-story cases, remaining source-absence questions and new empty-page disagreements unresolved (final open questions).

### Sample size, estimands and claims

**Targets are operational research defaults (C5), not proven adequacy thresholds.** Apply them on dev20 for candidate selection and once on the untouched sample. Report weighted paired point differences and two-sided 95% intervals, with whole books/groups as clusters; **10,000 stratified paired bootstrap draws, seed 20261105**, recomputing ratio numerators/denominators inside draws. Use the two sampling strata and their weights. For finite-161-population inference use a without-replacement survey variance/FPC or a documented finite-population resampling correction; also report the ordinary with-replacement book-bootstrap interval as a conservative planning comparison. Never treat aliases/pages as independent trials. These intervals are exploratory and unadjusted for development selection, not confirmatory superiority tests.

| Surviving endpoint | Baseline and target / computable check | Interpretation |
|---|---|---|
| Known student name-variant recall, historical v1 | **88/106 = 0.830; target ≥0.90** on dev20; held-out target ≥0.90 and paired point change ≥0. Report aliases-only recall separately (**6/10 = 0.600** dev20) on alias-positive books, with no paired point decline. | Known-positive recovery guard, including names for continuity; it is not a T1/T2 score. Scorer repair and prompt gain are separate. |
| Existing-reference character recall | Historical **82/96 = 0.854** (ambiguity upper bound 83/96); no paired point decline on eligible holdout books. | Known-character coverage, not complete entity recall or entity precision/F1. |
| M1 alias groundedness | Historical loose **105/112 = 93.8%**; corrected boundary-safe **104/112 = 92.9%**; target **≥95% lexical grounding**, no paired point decline, plus separately reported constrained editorial allowance. | Lexical support check replacing reference-precision; neither true precision nor attachment accuracy. |
| M2 Type A redundancy | Retain §4.1 historical incidences. Target **zero lost raw surfaces and zero duplicate occurrence counts**, with identical pattern/constraint/candidate-set keys compiled once. | Raw records/modifier variety have no reduction target. Pattern grouping cannot change occurrence attribution merely by choosing a representative. |
| M3 Type B collision flags | §4.1 gives the structural book-incidence baseline. Target **zero unhandled flags** and zero duplicated source-occurrence assignments to mutually exclusive individuals. | Every overlap is disambiguated by evidence, linked as membership, or explicitly unresolved; raw overlap incidence is reported, never forced to zero by deleting records. |
| M4 mention attribution | True correct-single recall and wrong-attribution baselines **unmeasured**; dictionary diagnostic **894 single-candidate / 916 candidate occurrences**, with **22 ambiguous** under broad optional-article retrieval (§4.1.3). Target no unexplained loss of supported occurrences or increase in unresolved share at fixed inputs, and repair evidenced wrong attachments without new ones; report typed exclusions separately. | Dictionary uniqueness is not correct attribution. Full T1/T2 remain unmeasured. |
| False merges / false splits | Report **two separate rates** on independently evidenced distinct/same mention pairs, both with target **0 known errors** and no paired increase; true dev20 population rates unmeasured. Report each evidence denominator and unresolved share. | Neither error can compensate for the other; zero eligible pairs is undefined, not a pass. Retained local IDs with unresolved cross-scene links are not asserted splits. |
| OCR/story/title/credits and C4 facts | Report paired agreement with existing eligible references, corrected only by §7 evidence; no new failed outputs/known-fact losses and the fixed blank/recovery guards above. | Delete independent-accuracy and complete-credit-set gates. Existing-label improvement cannot settle true story accuracy or bibliographic completeness. |
| Rotation | All available historical protected outcomes preserved across replicates; actual measured cost lower; no increased failure/unresolved count. | Fixture regression claim only. |

The evidence-eligible point targets and non-regression checks are computable adoption conditions; unmeasured true-task endpoints remain limitations, never automatic passes; they do not establish statistical noninferiority. Report the intervals even if a point target passes. Orient differences as improvement (candidate−baseline for recall/grounding, baseline−candidate for error/flag rates). A positive improvement with an interval crossing zero is **unconfirmed**; an interval wholly below zero indicates deterioration on that measured endpoint. Keep output counts, failures, ungrounded emissions and unresolved-collision coverage visible so an empty/truncated response cannot pass by looking tidy. Do not promote a candidate which suppresses aliases to pass M1/M2 while losing recorded-positive recall. These gates do not certify unknown extras or resolve open research conventions.

**What 40 books buys:** these are conditional, two-sided **95% macro-score** planning intervals, not promised micro-ratio or paired-change precision. In these planning formulas `s` denotes an assumed finite-population RMS SD (denominator N); the observed development sample SDs are substituted only as sensitivity values. For aliases-only recall on the 27 selected alias-positive books, half-width is `1.96 × s/√27 × √((90−27)/(90−1))`. Assuming between-book SD `.30` gives **±9.52 percentage points**; `.50` gives **±15.87 points**. The five alias-positive dev20 books have fixed-alignment recalls `1, 1, 0, 0, .75` (Cave Baby, Sharing a Shell, Runaway Pea, Three Little Pigs, Ugly Duckling), micro **6/10**, macro **.55**, sample SD **.5123475**; plugging that unstable five-book estimate into the formula gives **±16.26 points**. Dev20 is not a probability sample, so this is a sensitivity calculation, not an estimated held-out confidence interval.

For book-macro groundedness M1, use `1.96 × sqrt(sum_h W_h² s_h²/n_h × (N_h−n_h)/(N_h−1))`, with `(N_h,n_h)=(90,27),(71,13)` and `W_h=N_h/161`. Recomputing §4.1.2's boundary-safe grounding from cached predictions gives **17 alias-emitting books**, macro **.8806373**, sample SD **.2846208**, and **three undefined empty-output books**. Assuming that SD in each sampling stratum gives **±8.00 points**; sensitivity assumptions `.15` and `.50` give **±4.22** and **±14.06 points**. Do not assume zero variance because all five development alias-positive books happen to have M1=1. These widths assume sampled books have defined scores; output-empty coverage can worsen them. An empty output is undefined grounding, never perfect grounding, and stays visible in coverage/recall.

The reported **micro** ratios and **paired improvements** need their own weighted book-cluster intervals after running both systems; their width cannot be known from alias counts alone. Expected counts are `27 × 178/90 = 53.4` recorded student aliases and, as a dev20-density extrapolation only, `40 × 112/20 = 224` predicted aliases. Do not advertise independent-alias binomial intervals. Forty books can reveal gross overfitting but cannot promise small absolute alias-recall or grounding error bars under these variance assumptions. Precision for a small paired change depends on the as-yet-unmeasured within-book covariance/difference variance. For a finite-pool macro half-width of **±5 points**, the same formula needs **55/90 alias-positive books** at SD .30, or **74/90** at the observed .51235. Groundedness at common SD .28462 would need approximately **71 proportionally sampled books** for ±5 or **111** for ±3 points. Those are sizing calculations, **not** approved sample expansion. More API output alone cannot supply missing true-mention/identity labels, so no sample size in this cancelled-annotation design establishes true wrong-attribution, false-merge or false-split rates. A target miss does not authorize extra books until a favorable result appears.

**Publishable from pilot data alone:** the fixed-output reference/scorer corrections with their provenance; held-out recovery of existing recorded aliases/characters; transparent M1–M3 output-consistency estimates, M4 dictionary retrieval/ambiguity and paired changes; known-reference agreement and coverage; historical regression outcomes; and measured cost. State population, sampling weights, incompleteness and exposure history. **Not publishable as established by this study:** true alias precision/F1, recall of all possible aliases/entities, true mention recall, speaker/addressee or character-language accuracy, wrong-attribution/false-merge/false-split prevalence, complete credit/publication extraction, independent population story/OCR/orientation accuracy, or a causal claim that every ungrounded alias is invented. The annotations needed for those claims are not being done. A larger API-only sample cannot repair missing ground truth.

### API budget

Use the measured dev20 ledger **$15.045192 / 20 = $0.7522596 per book** (772 pages, or 38.6/book), not an assumed free scoring run. The initial `276−33=243` estimate would cost `243 × .7522596 = $182.80` **per system**; excluding the eight no-text records gives `235 × .7522596 = $176.78`. After the incidental quarantine, 234 text-bearing candidates would be **$176.03**, or 232 with recorded story text **$174.52**, before unknown S3/reference exclusions. Those are conditional wider-pool forecasts, not executable allocations.

The actual ledger has **1,669 rows: 1,666 completed and three failed with recorded zero cost**. Summing `calls.json[].cost_usd` as decimal values gives the total above. **40 × $0.7522596 = $30.090384 per baseline-sized full run; two full systems cost $60.180768** before growth. The owner's ~$37 is compatible with an illustrative **single candidate** budget, not a paired comparison:

| 40-book component | Explicit arithmetic | Forecast USD |
|---|---|---:|
| Candidate at old usage | `40 × .7522596` | 30.090384 |
| Context pass scenario (§6) | `40 × .070` | 2.800000 |
| Character evidence growth scenario | `40 × .013` | .520000 |
| OCR prompt growth scenario | `40 × 38.6 pages × 1,000 input tokens × $2/million` | 3.088000 |
| Metadata growth scenario | `40 × (39/20 requests/book) × .007/request` | .546000 |
| **Illustrative candidate** | sum | **37.044384** |
| **Paired baseline + candidate** | `30.090384 + 37.044384` | **67.134768** |
| **Paired with identical candidate rotation/crop reused** | `67.134768 − 40 × (4.658235 + .044312)/20` | **57.729674** |

Reusing exactly identical requests is valid shared computation; no baseline outputs for these unseen books are assumed to exist for free. The reuse saving is **$9.405094**. If changing rotation to Haiku instead, fixed-usage saving is **$6.210980**, and rotation-cache reuse cannot also be counted. The hypothetical growth uses §6's stated token assumptions and historical page/request counts; joint metadata calls, new branches, retries and prompt sizes can change it. Render and meter the frozen request plan before dispatch. A character-only holdout can reuse unchanged stages more extensively, but would test that narrower candidate, not the full OCR/context pipeline.

**Status: APPROVED IN PRINCIPLE, ~40 books; HELD-OUT EXECUTION BLOCKED.** Chris must first link the Claude subscription Console organisation and confirm that the configured run draws on subscription credits. The runner must fail closed while that is unverified; it must not fall back to the production Anthropic key. No call is made to test billing during this design revision. Preserve the approved scale and correct the cost basis transparently: if ~$37 is a hard total ceiling, the full paired experiment does not fit it; the new budget question is in the final section. Do not silently reduce the comparison to one system or run early on the production key.

**Dev20 tuning is not blocked by the held-out credit-linking condition.** Offline adapter/scorer work and preparation can continue; any separately requested inference still uses the existing spend ledger, worst-case reservations and retry accounting. A dev20 Haiku rotation sweep is about **$1.55** at recorded fixed usage; Sonnet controls and three-replicate historical fixtures add metered cost without an invented fixture forecast. Check any surviving $200 allowance against the actual ledger before separately authorized execution. This document revision itself runs no inference and commissions no annotation labour.

## 9. What not to change, and implementation deliverables

Do **not** make OCR more reluctant to find text, use empty student transcription as a negative example, suppress artwork lettering to match the old reference, or reinterpret all 80 recovered tokens as hallucinations. Do not add dictionary correction, stopword removal, aggressive fuzzy matching or cross-page pooling to improve OCR scores. They could erase meaningful child-language forms, repetitions and misplaced text.

Do **not** globally reduce aliases, cap alias counts, require proper names, force familiar-name expansions, or make models reproduce 106 reference variants. Retain evidenced descriptions and unnamed entities. Do not infer image-only characters with the present text detector; report their scope/coverage separately if Chris later requests visual character analysis.

Do **not** globally upgrade OCR, metadata location or crop models; rotation has the explicit cheaper-model ablation in §6. There is no paired evidence that the more expensive configured model is better. Keep rotation/crop settings fixed while testing language prompts. Do not tune unlabelled themes or demographic attributes against imported defaults; no independent baseline for them was supplied. Do not add fine-tuning, retrieval from held-out books, per-page character calls or repeated-model voting before the inexpensive evidence-rich whole-book prompt has been tested.

Do **not** silently replace publisher strings with parent companies, assume a copyright year is first publication, or use an ISBN lookup to overwrite a conflicting photo-supported value. Current rates can support inexpensive stage comparisons, but model price is not a quality ranking.

Implementation, when separately requested, should produce these reviewable artifacts in this order:

1. Versioned C1–C6 status (C3/C4 accepted; research defaults marked provisional), corrected development census and separate entity alignment, metadata/event migration, `reference_wrong` support and extended field-aware `compare-run`. Preserve the immutable baseline and raw reports.
2. A frozen dev-only example manifest containing the cited book IDs/photo numbers, source text/image hashes, exact prompt text and approval status. Refuse to load an example from a held-out group. Label unverified text-derived examples as provisional behavioral demonstrations, not gold; only already-adjudicated examples carry verified status.
3. The §4 evidence-preserving schemas/adapters and prompt variants: character identity / raw-plus-canonical alias entries / mention patterns, occurrence evidence, the single-form manual path, read fallback, additive $0-API backfill and safe alias IDs. Update the named text-entry and validation paths, verify review-my-books navigation, and preserve Field descriptors, document-ID widget keys and guarded lookups. Deliver a dry-run ledger and regression checks for old/missing/null canonical fields, one-string entry, overrides/reset, shared forms under different characters, repeated saves, raw-preserving corrections and no read-time writes. Preserve field reconciliation and unresolved states; no silent dropping of model fields or partial responses. Separate schema/matching gains from inference gains.
4. Independent staged ablations, the 101-page monitoring cohort and verified blank/recovery gates, historical rotation regression/cost guards, token/cost/coverage reports and the four-book language-prompt replicate study. Use request/parser/reference versioning so only changed requests and dependent stages rerun.
5. The frozen **40-book allocation (27 alias-positive + 13 alias-zero)** with both Gruffalo books, historical rotation exposures and the incidental exposure quarantined; eligibility, exploratory labels, weighted book-cluster intervals and the corrected paired-run forecast. Compare baseline with one frozen candidate on the same books using known positives, M1–M3, M4 diagnostics and separate evidence-eligible false-merge/false-split outcomes. Keep true task metrics explicitly unmeasured where labels are absent.

Held-out execution checklist (implementation must enforce these before its first paid request):

- [ ] **BLOCKING PRECONDITION — Chris has linked the Claude subscription Console organisation.** Record confirmation of the intended organisation/project and subscription-credit funding, without exposing credentials; verify the runner is configured to charge that credit-linked account. If unverified, stop held-out dispatch. **Never use or fall back to the production Anthropic key.** Approval in principle for ~40 books does not satisfy this prerequisite.
- [ ] Freeze the candidate, 40-book IDs/weights, reference and request hashes, and the actual rendered-request forecast/reservations; reconcile the corrected paired cost with the spend ceiling (§8 and final question). Do not treat an illustrative ~$37 single candidate estimate as funding for two systems.
- [ ] Retain the no-new-annotation protocol, supported-case regressions, raw/derived compatibility checks and all §5–§7 failure/rotation/adjudication guards. Store per-book occurrence numerators, ambiguity, undefined-metric coverage and separate identity-error denominators.

**Dev20 tuning is not blocked by the held-out billing prerequisite.** This checklist schedules no extraction during this design-only task.

Publish corrected raw counts, reference incompleteness, field-specific coverage, effect sizes, uncertainty and evidence provenance with the final scores. The publishable claims are limited to recovery of existing recorded positives, output consistency, dictionary-candidate retrieval and fixed regression behavior (§8); true alias precision, complete mention recall, population attachment accuracy and end-to-end T1/T2 performance remain unmeasured.

## Appendix: corrected accounting for all 109 uncredited variants

`S` = supported reference omission; `A` = `scorer_artefact`; `P` = `prediction_wrong` audit flag; `Q` = other unresolved. A and P are **orthogonal flags**, with three overlaps; each row totals `S + A + P + Q − overlap`. For disjoint accounting give P priority: S45, scorer-only21, P30, Q13. Strings are scorer-normalized, not proposed raw transcription. P is not automatically a verified `prediction_wrong` overlay: photo review remains required for source-absence assertions. Enumerate from `reports/book_scores.json`, by book, predicted entity index and sorted variant difference, using §2's algorithm.

| Book ID | S | `scorer_artefact` A | `prediction_wrong` P | Q | A∩P | Total |
|---|---:|---:|---:|---:|---:|---:|
| `a_squash_and_a_squeeze` | 0 | 0 | 0 | 0 | 0 | 0 |
| `all_for_one` | 2 | 0 | 0 | 0 | 0 | 2 |
| `cave_baby` | 3 | 2 | 4 | 3 | 0 | 12 |
| `how_the_grinch_stole_christmas` | 6 | 5 | 2 | 2 | 0 | 15 |
| `it's_christmas` | 0 | 0 | 0 | 0 | 0 | 0 |
| `norman_the_slug_with_the_silly_shell` | 1 | 0 | 0 | 0 | 0 | 1 |
| `room_on_the_broom` | 1 | 0 | 0 | 1 | 0 | 2 |
| `santa_is_coming_to_devon` | 7 | 1 | 3 | 1 | 0 | 12 |
| `sharing_a_shell` | 0 | 0 | 0 | 0 | 0 | 0 |
| `the_enormous_crocodile` | 11 | 0 | 8 | 0 | 0 | 19 |
| `the_little_bully` | 1 | 0 | 3 | 0 | 0 | 4 |
| `the_runaway_pea` | 4 | 0 | 2 | 2 | 0 | 8 |
| `the_same_but_different_too` | 0 | 0 | 2 | 0 | 0 | 2 |
| `the_storm_whale` | 4 | 1 | 0 | 1 | 0 | 6 |
| `the_three_little_pigs` | 2 | 4 | 3 | 3 | 2 | 10 |
| `the_ugly_duckling` | 3 | 6 | 1 | 0 | 1 | 9 |
| `we're_going_on_a_bear_hunt` | 0 | 0 | 2 | 0 | 0 | 2 |
| `wiggling_worms_at_work` | 0 | 5 | 0 | 0 | 0 | 5 |
| **Total** | **45** | **24** | **30** | **13** | **3** | **109** |

* **All for One:** S: `marlon's mum`; `marlon's granny`.
* **Cave Baby:** S: `baby`; `cave mum`; `woolly mammoth`. A: `big brown bear`; `small brown bear`. P: `mammoth's family`; `he`; `long grey trunk`; `they`. Q: `grey woolly mammoth`; `five legged tiger`; `sabre toothed tiger`.
* **Grinch:** S: `fake santa claus`; `old liar`; `santa claus`; `tall and the small`; `dog`; `old max`. A: `child`; `cindy lou who`; `small who`; `sweet little tot`; `tiny daughter`. P: `saint nick`; `who ville`. Q: `every who`; `who girls and boys`.
* **Norman:** S: `slug`.
* **Room on the Broom:** S: `green bird`. Q: `beast`.
* **Santa:** S: `little old elf`; `young reindeer`; `children from devon`; `children of devon`; `good children of devon`; `all the reindeer`; `reindeer`. A: `old reindeer with the red nose`. P: `santa claus`; `reindeer youngest`; `alfie ava ben brooke charlie chloe zac zara zybil`. Q: `bossy lady's voice`. The concatenated names are synthetic **as one referring expression**, while the individual names are printed on pp31/33.
* **Enormous Crocodile:** S: `crocky`; `crocodile`; `hippopotamus`; `elephant`; `monkey`; `boy`; `sister`; `little girl`; `all sorts of children`; `two boys and two girls`; `man who was working the roundabout`. P: `notsobig crocodile`; `playground children`; `fair children`; `picnic family children`; `roundabout man`; `girl`; `girl who was older than the others`; `one little girl`. The final three retain their historical attachment audit flags. The older-girl and Jill phrases explicitly belong to individuals, not their assigned collectives; the normalized `girl` flag does not establish an occurrence of Mary's raw `the girl` or its referent. That attachment remains unsupported as specified in §4.1.3, not a new verified absence/identity verdict.
* **Little Bully:** S: `lot of friends`. P: `billy's mom`; `billy's dad`; `billy's friends`.
* **Runaway Pea:** S: `little pea`; `runaway pea`; `new peas`; `party of new peas`. P: `banana and grapes`; `he`. Q: `dog`; `cat`.
* **Same but Different Too:** P: `me`; `you`.
* **Storm Whale:** S: `noi's dad`; `friend`; `little whale`; `storm whale`. A: `six cats`. Q: `nbi`.
* **Three Little Pigs:** S: `mother`; `happy little pig`. A: `man who was carrying a bundle of sticks`; `stick man`; `man who was pulling a cart laden with heavy bricks and bags of cement`; `brick man`. P: `straw man`; `stick man`; `brick man`. Q: `growly voice`; `gruff voice`; `sly voice`. A∩P: `stick man`; `brick man`.
* **Ugly Duckling:** S: `swan`; `young swan`; `beautiful white birds`. A: `other ducklings`; `six ducklings`; `six of them`; `ducks on the other side of the river`; `other big ducks`; `old cat`. P: `six ducklings`. A∩P: `six ducklings`.
* **Bear Hunt:** P: `family`; `us`. `us` is independently invalid as a pronoun alias, regardless of whether photo review finds it printed; it was already inside the old 19-string problem bucket, so is not added again to the six newly moved errors.
* **Wiggling Worms:** A: `nature's plows`; `wiggling worms`; `worm`; `wormlets`; `worms` (conservative alignment ambiguity, not an exact-overlap failure).

The nine exact-overlap-blocked entities carry 19 variants: Cave bear (2), Cindy-Lou (5), Santa old reindeer (1), Storm cats (1), Pigs stick/brick men (4), Ugly ducklings/ducks/cat (6). Worms contributes the remaining five scorer flags. A scorer flag is not an identity adjudication: scope/attachment questions remain for `small brown bear`, `six of them` and the worm population. Preserve these secondary uncertainties even though their primary census mechanism is matching.

C3's **descriptive-alias scope is settled**, and pronouns/body parts are no longer unresolved alias-scope questions. Remaining research ontology issues concern depicted/dreamed/hypothetical characters, mere comparators, generic species, subset groups and narrator/reader roles. Preserve categorized mentions and unresolved identity, scoring literal pronouns as coreference evidence rather than valid lexical aliases. Do not merge solely on a body part, voice or shared species. If a voice is attributed to an existing actor, attach that evidence without inventing another person. `Nbi` appears in both text streams at *Storm Whale* p27; inspect the photograph before calling it a true name variant. These text-audit uncertainties cannot be resolved by raising a prompt's alias count.


## Open questions for Chris

The four questions answered this round are closed: task-driven duplication handling, raw + canonical storage with one-form/legacy support, no preferred identity-error bias, and approval in principle for ~40 held-out books. No fresh annotation is scheduled or reopened.

- **T2 output unit:** should a character's language profile include only their speech, narration describing their actions/attributes, language addressed to them, or separately labelled views of these? Default preparation preserves these separate relations and unresolved states. T1 already requires directed speaker→addressee relations; implicit addressees remain unknown unless context supports them. The remaining T2 scope determines its task metric, not a choice of collapse strength.
- **Corrected paired-run ceiling:** the ledger gives ~$30.09 for one baseline-sized 40-book run. The explicit growth scenario gives ~$37.04 for the candidate alone and ~$57.73 for the pair with identical rotation/crop reuse (~$67.13 without reuse). Is the earlier ~$37 a hard total cap or a rough candidate estimate? This is a newly exposed cost-basis question, not a request to reapprove the 40-book scale. Execution remains blocked until Chris links the subscription Console organisation, regardless of the answer.
- **Remaining research conventions:** retain provisional C1 all-print archival text with separate analytical views, C2 main-content scope and C6 retellers-as-authors? Generic species and discourse-role inclusion remain ontology choices; C3 descriptive aliases and C4's publication targets are settled. Unresolved cross-scene identity is retained without an asserted merge/split and excluded from exclusive totals by default.
- **Claim limits:** are the exploratory C5 targets and pilot-only claims sufficient for the intended publication/analysis, given unmeasured true mention/identity and T1/T2 accuracy? Source-absence questions (`Nbi`, Mary's `the girl`), the older girl's cross-scene identity, worm/small-bear/duckling scope, uncertain publisher/credit equivalences, the 16 empty-story cases, context-example layouts and OCR residuals remain unresolved; no new review labour is assumed. Use the verified 161-book pool for the approved sample; any future expansion to the wider metadata census needs an offline existing-data export, not fresh labels.
