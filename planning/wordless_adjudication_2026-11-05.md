# Adjudication: the 7 "disputed wordless" pages (2026-11-05)

The first harness run flagged 7 pages where the pilot reference recorded a
wordless (`contains_story=True`, empty text) page but the pipeline produced
text — reported as **80 "invented tokens"**, the hallucination signal.

**All 7 were adjudicated by eye against the original photographs. All 7 are
real printed text that the student entry MISSED. Zero hallucinations.**

| Book | Page | Pipeline output | Verdict |
|---|---|---|---|
| Santa is Coming to Devon | 24 | "Finally, when the snow had died down and the clouds parted, Santa discovered exactly where they were, stuck right at the very top of Exeter Cathedral!" | Correct, verbatim |
| The Runaway Pea | 20 | `"Owwww," cried the pea, "my bottom's on fire,"` | Correct, verbatim incl. punctuation |
| The Book With No Pictures | 33 | "Oh, is that so?" | Correct, verbatim |
| Norman The Slug... | 24 | "But he was falling" | Correct, verbatim |
| Wiggling Worms At Work | 29 | "In a few minutes the cocoon slips off the worm's head, just as the ring would slip off your finger. The ends of the cocoon close. Inside the cocoon the eggs are fertilized." | Correct, verbatim |
| How The Grinch Stole Christmas | 46 | "MERRY MERRY" | Correct — but it is lettering INSIDE the illustration (a pennant on the sleigh) |
| It's Christmas | 30 | "Wheee!" | Correct — but it is sound lettering inside the illustration |

## Consequences

**1. The 80 "invented" tokens are RECOVERED tokens.** The pipeline is not
fabricating text on wordless pages; it is finding text humans skipped. This is
the single strongest argument for the automated pipeline: it is more complete
than the student baseline on this evidence.

**2. The metric is mis-framed.** `disputed_wordless` / `invented_tokens` reads
as a failure count. After adjudication it is a *recovery* count. Scoring should
let an adjudicated page be marked `reference_wrong` and credited to the
pipeline, not held against it — otherwise improving the pipeline means making
it worse at finding text.

**3. This caps achievable "agreement".** The reference contains human misses, so
100% agreement would require reproducing those misses. Agreement is a floor on
pipeline quality, not a ceiling to chase.

**4. An open annotation-convention question.** Two of the seven (MERRY MERRY,
Wheee!) are lettering drawn inside artwork rather than set narrative text. Both
are genuinely on the page, but whether they belong in the transcription is a
research decision, not a model defect. It needs a documented convention before
it can be scored either way — and the same question governs whether a page with
only such lettering counts as `contains_story`.
