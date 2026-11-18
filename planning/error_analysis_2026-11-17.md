# Error analysis of the old prompts on the 55 Iterate books (17 November 2026)

Astra (gpt-6-astra), read-only, step A of Fable's plan (`review_fable_plan_2026-11-17.md`), from the old-prompt runs rescored at `4c3e055` (`reports-2026-11-17`). Verbatim. The totals (309/345 characters, 46/74 aliases, 82 extra characters, 14 duplicate forms) were rechecked against the scores. No Check record was opened. Gain figures are ceilings, not forecasts.

The largest useful targets are **alias coverage, character identity/grouping, and page classification**. Many reported disagreements should not be “fixed” by making the pipeline imitate the reference.

I reviewed all 55 Iterate books, including every unmatched character, attribute disagreement, missing recorded alias, and story-text difference. No files changed; no git, network or API calls. Check entries were not decoded.

Counts below are observed; classifications are my reading. **Gain estimates are optimistic ceilings**, not forecasts: percentage-point changes in equal-book scores if the assigned errors disappeared, without regressions. Text uses 55 books, character measures 51 character-positive books, and alias coverage 35 eligible books. Without inspecting scans, some transcription-versus-reference diagnoses remain provisional.

**1. Characters and aliases**

The baseline finds **309/345 reference characters**; equal-book recall is **90.36%**.

The 36 misses, classified by primary kind, are:

| Kind | Misses | Books |
|---|---:|---:|
| Group or collective | 22 | 11 |
| Unnamed individual | 7 | 7 |
| Named/labelled only once | 3 | 3 |
| Other minor character | 2 | 2 |
| Other named individual | 2 | 1 |
| Named in front matter | 0 | 0 |

These kinds overlap conceptually; the table assigns each miss once. Their more useful **causal classification** is:

| Cause | Misses / books | Reach and estimated recall gain |
|---|---:|---|
| Omitted incidental individuals/groups | 15 / 7 | Rule + example: include textually mentioned entities, not only active speakers; ≤2.96 points |
| Merged individuals, groups or subgroups | 9 / 6 | Separate identity step + example; ≤2.55 points, including questionable reference distinctions |
| Missing identifying name/alias | 7 / 7 | Rule + example for printed short forms and descriptions; ≤2.26 points |
| Apparent reference/naming artefacts | 5 / 4 | Nothing appropriate in the prompt; nominal 1.87-point deficit |

Nine of the 15 omissions occur in *The Detective Dog*: incidental animals and entities mentioned through other books. Grouping failures include combining parents, combining two named individuals, and attaching a subgroup to its larger collective.

The **82 unmatched predictions** are not 82 invented characters:

| Kind | Extras | Books |
|---|---:|---:|
| Characters in books with empty reference casts | 38 | 3 |
| Additional text-supported collectives | 18 | 14 |
| Additional individuals, including toys | 15 | 11 |
| Counterparts of differently labelled reference characters | 9 | 9 |
| Combined named pair | 1 | 1 |
| Reader role | 1 | 1 |

Suppressing these indiscriminately would lose valid characters. Extra-character counts mainly diagnose reference coverage, identity and population conventions; lowering them does not itself improve recall.

Alias coverage is **46/74 forms**, or **70.48% equal-book**.

| Alias problem | Forms / books | Reach / estimated movement |
|---|---:|---|
| Omitted usable forms | 20 / 12 | Rule + examples; ≤20.95 coverage points |
| Reference shortening/description not independently supported | 5 / 4 | Nothing: do not invent wording to match labels |
| Form absent from character-stage transcription | 2 / 2 | Repair upstream text; ≤2.38 coverage points |
| Reference form assigned to an individual instead of a collective | 1 / 1 | Resolve ownership/reference convention; nominal 0.71 point |
| Individual–collective ownership conflations | 26 / 5 | Identity rule/example; reduce these suspect attachments |
| Utterances, number labels, location/shop names or sounds used as aliases | 38 / 5 | Rule separating referring expressions from other text |
| Duplicate forms | 14 / 9 | Deterministic normalization; remove 14 duplicates |

The 26 ownership cases include individual names attached to collectives and collective expressions attached to individuals. These are manual diagnoses, not an existing scored ownership measure.

**2. Attributes among found characters**

There are **24 gender disagreements across 17 books**.

| Available cue | Errors / books | Interpretation and reach |
|---|---:|---|
| Pronoun | 6 / 4 | Four character-prompt misses; one upstream omission; one reference contradiction |
| Kinship or gendered title/address | 4 / 4 | Three identity/group confounds; one reference-policy disagreement |
| Sex-specific animal noun | 2 / 1 | Rule/example after cue review; ≤0.65 agreement point |
| Name only | 7 / 4 | No justified gender inference; includes two apparently reversed reference labels |
| No usable cue | 5 / 5 | Remove two unsupported assignments; three reference genders remain unsupported |

Together, **four missed pronoun cues, two animal-noun cues and two unsupported assignments** offer a ceiling of **2.83 gender-agreement points**.

Beyond pronouns, the disagreement cases contain **parental kinship (1), sibling kinship (1), gendered title/address (2), and sex-specific animal nouns (2)**. Egg-laying also accompanies one animal-noun case; it supplies no additional independent target. Names alone are not evidence.

There are **33 human/non-human disagreements across 13 books**:

| Kind | Errors / books | Reach |
|---|---:|---|
| Talking/anthropomorphic animals | 21 / 6 | Picture-based structural step; ≤8.06 agreement points |
| Fantasy beings | 6 / 4 | Picture/context step; ≤2.78 points |
| Ambiguous identity/species | 2 / 2 | Resolve before tuning |
| Text contradicts reference coding | 4 / 1 | Reference correction, not prompt correction |
| Toys / objects | 0 / 0 | No observed target |

Pictures must inform **human/non-human only**, never gender.

There are **14 protagonist disagreements across seven books**: two missing output fields in one book, and twelve salience/ensemble disagreements in six. Required-field validation reaches the two missing values (**≤0.98 point**); one clear omitted co-protagonist offers **≤0.65 point** through an example. The other eleven involve identity or inconsistent protagonist conventions, including incidental characters marked as protagonists. Do not tune those blindly.

**3. Story-page text**

Equal-book story accuracy is **89.98%**, versus **77.40% all-pages**. I reconstructed and inspected all **5,963 edits**: 281 substitutions, 3,853 deletions and 1,829 insertions.

Here, S/D/I are scored token edits—not independent incidents. Each edit has one primary diagnostic category.

| Category | S / D / I | Books | Reach; accuracy ceiling |
|---|---:|---:|---|
| Main-content/ancillary-page scope disagreement | 43 / 2,911 / 994 | 9 | Classification rule + structural separation; ≤3.33 points |
| Omitted passages on otherwise completed output | 6 / 605 / 0 | 18 | Completeness example/check; ≤1.54 |
| Reading/page-order effects | 85 / 124 / 130 | 14 | Ordering rule/example; ≤0.85 |
| Picture lettering: labels, signs, sound effects | 0 / 47 / 200 | 18 | Separate tagging/projection; ≤0.73 |
| Apparent speech-bubble/caption differences | 20 / 0 / 229 | 8 | Region distinction; ≤0.49, reference-dependent |
| Repeated lines/refrains | 1 / 3 / 137 | 2 | Faithful repetition rule/example; ≤0.13 |
| Spelling, punctuation, elongation/tokenization conventions | 33 / 16 / 21 | 16 | Mostly normalization/reference policy; ≤0.26 |
| Apparent reference transcription debris/errors | 71 / 37 / 2 | 31 | Nothing appropriate in prompt |
| Coherent passages apparently absent from reference | 0 / 0 / 86 | 5 | Verify reference; do not suppress automatically |
| Reference includes non-story material | 0 / 9 / 0 | 1 | Reference flag correction |
| Recorded blocked/refused pages | 2 / 91 / 0 | 2 | Failure recovery; ≤0.14 |
| Unresolved lexical differences | 20 / 10 / 30 | 18 | Source verification before choosing a lever |

*All Year Round* dominates omissions because most informational content is excluded as non-story. That is a classification/convention problem, not thousands of misread words.

Four books supply **1,254/1,829 insertions (68.6%)**:

| Book | Insertions |
|---|---:|
| *You Choose* | 789 |
| *Snow White, Star Striker* | 202 |
| *Ten in the Bed and Other Counting Rhymes* | 137 |
| *Knock Knock Dinosaur* | 126 |

Decorative/front-matter content, dialogue and repeated verses dominate these cases. Decision 025 requires retaining and tagging lettering, not deleting it to improve agreement. Ceilings account for the score’s zero floor and are not additive.

The **five largest reachable targets**, ordered by their own metric’s ceiling rather than incomparable raw token/character counts, are:

1. Missing usable aliases — **20 forms; ≤20.95 coverage points**.
2. Animal/fantasy human classification — **27 characters; ≤10.84 agreement points**, requiring pictures.
3. Main-content versus ancillary-page classification — **nine books; ≤3.33 text points**, after resolving reference scope.
4. Omitted incidental characters/groups — **15 characters; ≤2.96 recall points**.
5. Supported gender cues and unsupported assignments — **eight characters; ≤2.83 agreement points**, subject to cue approval.

**Open questions for Chris**

- May prompt rules explicitly use **parental/sibling kinship and gendered titles/address**, alongside the already-approved sex-specific animal nouns? Approving these permits textual evidence beyond pronouns; deferring keeps conflicting cases for review. Names alone would remain insufficient.
- Should **protagonist** include every central member of an ensemble, or only one principal lead? The first supports multiple central characters; the second requires choosing one. Either needs consistent reference treatment before the disputed protagonist cases become tuning targets.
