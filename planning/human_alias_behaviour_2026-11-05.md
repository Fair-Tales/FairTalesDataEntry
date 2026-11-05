# What the students actually did with aliases (measured, 2026-11-05)

Chris asked that the alias convention be decided empirically — by assessing what
the human annotators were doing and comparing the pipeline to it — rather than
chosen a priori. Measured from the captured pilot snapshot (195 books). No
Firestore reads; all figures from the local inventory.

| Measure | Value |
|---|---|
| Characters recorded | 1,494 |
| Alias records | **200** (0.13 per character) |
| Books with at least one alias | 102 of 195 (52%) |
| Books with **zero** aliases | **93 of 195 (48%)** |
| Median aliases in books that have any | **1** (max 11) |
| Alias strings that are multi-word | 81 of 200 (40%) |
| Alias strings beginning lower-case | **134 of 200 (67%)** |

Actual recorded strings include: `the brown bear`, `the littlest one`, `the
small one`, `the white bear`, `her daughter`, `the animal`, `each bird`,
`scary monster`, `the monster`, `everyone`, `mum`, `cave dad`, `chief`,
alongside proper names (`Josh`, `Alexander`, `Biff`, `Eldo`, `T-Rex`).

## Conclusion

**The students' own working convention already included descriptive referring
expressions, not just proper names.** Two thirds of what they recorded is
lower-case descriptive text of exactly the kind the pipeline produces
(`the old liar`, `the fake Santa Claus`, `Marlon's granny`). The pipeline is
doing the same KIND of task the humans were doing — it is simply doing it far
more thoroughly and far more consistently.

Consequences:

1. **The alias convention question is largely settled by evidence, not
   preference.** Descriptive references belong in scope, because that is what
   the reference annotation itself contains. This is independent of pipeline
   behaviour, which answers Fable's circularity objection to convention C3.
2. **This reference cannot support an alias PRECISION measurement.** With 48%
   of books recording no aliases at all and a median of one elsewhere, almost
   any correct alias the pipeline finds will be uncredited. The reported 0.447
   is a coverage artefact. Precision requires fresh annotation on held-out
   books, double-annotated, which is the dominant cost of the next round — not
   API spend.
3. **Recall against this reference remains meaningful** (0.830), because a
   recorded alias is positive evidence that the string belongs.
4. The genuine pipeline faults identified by audit — unprinted synthetic labels,
   pronouns and body parts emitted as aliases, and wrong attachment such as
   `Who-ville` to the Whos — are real and worth fixing, but they are roughly 30
   cases, not 109.
