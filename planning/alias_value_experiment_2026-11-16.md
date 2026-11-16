# Does denser alias coding improve language analysis? — 16 November 2026

Pipeline coding finds 12.5% more unique mentions, but only 8.3% more attributed
verbs—falling to 3.6% without one ambiguous pairing—and resolves fewer speech
labels. Extra aliases help some characters; this does not establish better
language analysis overall.

**Method.** A: evidence `dev20-snap/manifest.json`; B: `dev20-run/predictions.json`.
`score_character_recall` pairs 90 of 96 reference characters with 105 predictions.
Identical Word-export pages pass through the tune loader and `reference_inputs`;
whitespace is collapsed. `alias_pipeline` uses optional articles, preserved
possessives and shared overlapping occurrences. Mentions/verbs cover pairs;
full casts remain ambiguity competitors. spaCy counts lexical verbs with direct
nominal subjects, unique matches and recorded gender; auxiliaries and
coordination propagation are excluded. No pronouns are resolved: standalone
personal-pronoun forms and pronominal subjects are excluded symmetrically.
Speech matches raw endpoint strings. Seed: **20261116**.

| Paired-character measure | A | B | B names only |
|---|---:|---:|---:|
| Mentions found | 708 | 813 | 621 |
| Unique / ambiguous mentions | 704 / 4 | 792 / 21 | 607 / 14 |
| Characters with no mention / no unique mention | 7 / 8 | 2 / 3 | 10 / 12 |
| Attributed verbs | 373 | 404 | 333 |

Aliases help within B; A/B also changes primary names. Verbs change for only
20 pairs. Largest changes:

| Character, A name | Mentions A → B | Verbs A → B |
|---|---:|---:|
| worm | 26 → 56 | 14 → 32 |
| the Enormous Crocodile | 44 → 68 | 35 → 47 |
| youngest reindeer | 10 → 13 | 7 → 0 |
| third little pig | 12 → 3 | 6 → 3 |

`worm`/`Worms` is ambiguous with the original `worms`. Excluding it: unique
mentions **678 → 736**, verbs **359 → 372**; the Crocodile supplies 12 of the
net 13-verb gain. All B youngest-reindeer mentions collide with another character.

| Recorded gender | A verbs (share) | B verbs (share) | Top ten lemmas retained |
|---|---:|---:|---:|
| Female | 60 (16.1%) | 59 (14.6%) | 10/10 |
| Male | 255 (68.4%) | 267 (66.1%) | 9/10 |
| Non-specific | 58 (15.5%) | 78 (19.3%) | 5/10 |

`say` stays first. Male `run` gives way to `think`; non-specific
`laugh, sit, agree, begin, bend` to `eat, help, wiggle, call, have`.
Ties are alphabetical. Without the worm pair, non-specific shares are
**12.3% → 12.4%**: most of the gender shift comes from merging identities.

| Speech: 284 sections, 16 books; full casts | A | B |
|---|---:|---:|
| Speaker: unique / ambiguous / unresolved | 263 / 3 / 18 | 255 / 3 / 26 |
| Recipient: unique / ambiguous / unresolved | 212 / 16 / 56 | 195 / 25 / 64 |
| Unique speaker / recipient within paired cast | 261 / 210 | 255 / 194 |

B gains 1 speaker and 2 recipient resolutions but loses 9 and 19; one changes
identity per role. Sixteen validation rows join by row id; 15 have accepted
identities: A **15/15** correct, B **14/15**, one unresolved. The rejected match
lacks a corrected identity. The 650 detection labels lack a sentence-to-section
crosswalk: no join. Unique substring matches are not proof of identity or a
speech-detection measure.

| Contextual review by this agent, not human labels | Sample / pool | Right | Wrong | Unclear |
|---|---:|---:|---:|---:|
| New B-only unique mentions, no A hit at that occurrence | 50 / 88 | 40 | 8 | 2 |
| New B-only speech resolutions | 3 / 3 | 3 | 0 | 0 |

Errors include locations, generic species and noun modifiers; body-part/voice
descriptions are unclear. Two new speech resolutions concern a paired character,
one an unpaired group.

**Limits.** Twenty exposed books, 18 with recorded characters; **5 November
prompts on typeset renders**. Front/back matter and extraction noise remain;
parsing is unvalidated. SQLite agrees on 94/96 rows: the first and second little
pigs each have one alias missing from A's manifest. Casts/names/aliases differ;
no causal density claim, corpus generalisation or pronoun resolution.

Run `../fair-tales-language-analysis/.venv-mig/bin/python scripts/alias_value_experiment.py`
(spaCy 3.8.2, `en_core_web_lg` 3.8.0). All 90 character totals, lemma frequencies,
speech results and reviewed samples: `.cache/alias_value_experiment/`,
byte-identical on replay. Count/matcher checks passed. The loader's local guard
uses repository ids-only selections. No sealed paths, network, APIs,
installations, spending or git commands.

## Open questions for Chris

None.
