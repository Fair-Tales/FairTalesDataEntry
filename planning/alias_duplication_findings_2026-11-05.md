# Alias duplication, articles and groundedness — measured on dev20

Date: 5 November 2026
Source: `predictions.json` from the 20-book / 772-page baseline run
(`variant=production-baseline-with-strict-evaluation-parser-v1`).
Totals: **105 predicted characters, 112 predicted aliases.**

Written in response to Chris's instruction of 5 November: no fresh annotation
round, work with the pilot data we have, and "one thing we need to handle ... is
duplication and articles e.g. the littlest penguin versus little penguin".

## 1. The duplication problem is two problems

### Type A — intra-character redundancy
Several surface forms of one referent inside a single character record. Loses no
information; it is untidiness, and a deterministic rule can collapse it.

| Pattern | Count | Examples |
| --- | --- | --- |
| Alias equals its own character's name modulo article/case | 5 | `Mother Pig` / `the mother pig`; `First Little Pig` / `the first little pig`; `Santa-nav` / `the Santa-nav` |
| Two aliases of one character differing only by determiner | 9 | `Mum` / `his mum`; `Dad` / `his dad`; `Granny` / `his granny` |
| Modifier variants of one referent (**Chris's example type**) | see below | `the whale` / `the little whale` / `storm whale`; `the pea` / `the little pea`; `the bird` / `the green bird`; `the mammoth` / `woolly mammoth` / `grey woolly mammoth` |

### Type B — inter-character referent collision
The same or overlapping surface form attached to **different** character records,
or two records that are plausibly one referent. This is a correctness fault, not
untidiness: a researcher counting mentions of a surface form would attribute them
to the wrong character.

- `santa_is_coming_to_devon` holds `The reindeer`, `The youngest reindeer` and
  `The old reindeer with the red nose` as three separate character records, with
  `young reindeer` and `the reindeer (youngest)` as aliases of one of them.
- `the_enormous_crocodile` attaches `the girl` to **Mary**, `the little girl` to
  **Jill**, `one little girl` to **Fair children** and `a girl who was older than
  the others` to **Playground children**; `the children` belongs to
  **Playground children** while `Fair children` and `Picnic family children` are
  separate records.

## 2. Groundedness — a precision proxy that needs no annotation

Each predicted alias was tested against the pipeline's own extracted page text
for the same book.

| Result | Count | Share |
| --- | --- | --- |
| Appears **verbatim** in extracted text | 97 | 86.6% |
| Appears after determiner stripping | 8 | 93.8% cumulative |
| **Not found at all** | 7 | 6.2% |

The seven ungrounded aliases are `Santa Claus` and `the reindeer (youngest)` and
the elided name list `Alfie, Ava, ... Zac, Zara, Zybil` (santa), `the Notsobig
Crocodile`, `Billy's mom` and `Billy's dad`, and `the family`
(we're_going_on_a_bear_hunt). Several are defensible editorial constructions — an
elision of a genuinely printed list, a parenthetical disambiguator — rather than
inventions, so the metric needs a defined allowance for those classes instead of
a naive 100% target.

**Why this matters.** Alias precision against the student reference (0.447) is a
coverage artefact: 48% of pilot books record no aliases at all, so a correct alias
is routinely uncredited, and no pipeline improvement can raise it. Groundedness
measures the thing precision was meant to measure — is this alias real? — and it
is computable on every pilot book with no annotation at all.

**Known confound:** an alias can be ungrounded because the *text* is missing
rather than because the alias is wrong (see §5 of the improvement design on the
OCR recall gap). Groundedness must be reported against that gap, not alone.

## 3. The tension any canonicalisation rule must face

67% of the aliases the students themselves recorded are lowercase descriptive
forms (`the brown bear`, `the littlest one`, `her daughter`) — see
[human alias behaviour](human_alias_behaviour_2026-11-05.md). Alias recall against
that reference is the one alias metric that survives the cancellation of the
annotation round. Aggressive collapsing of modifier variants therefore trades
directly against it: the forms a tidiness rule would delete are the same kind of
forms the students were recording.

Reproduction: `scripts/` has no committed harness for this yet; the census was
computed from `predictions.json` with determiner stripping over
`(the|a|an|his|her|their|its|my|all the)` plus case folding and token-subset
comparison. The improvement design specifies the rule to be implemented.
