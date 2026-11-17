# Baseline runs from colour scans (16 November 2026)

Step 1 of the tuning loop (`planning/tuning_process_design_2026-11-10.md` §5):
run the old prompts and the new on 30 Iterate books, then carry the better set
to the other 25 Iterate books and the 20 Check books. Chris authorised the probe
and both parts on 16 November (`AFFIRMATIVE`), about $80 in all, inside the $150
monthly evaluation cap.

## Set-up

- **Code:** `claude-dev` at `8643d68`, checked out as a detached worktree in
  `evaluation_runs/code-8643d68` so the runs do not see later edits.
- **Arms:** `baseline.json` (old prompts, `legacy-v1`) and
  `candidate_pipeline_v2.json` (new prompts, `rules-plus-icl`); both use Sonnet 5
  for transcription, metadata and characters.
- **Inputs:** original colour scans (`--inputs scans`) paired by
  `inputs_manifest_tune.json`; all 75 tuning books are `ready` with Word-export
  references.
- **Ledger and caps:** a new ledger, `evaluation_runs/spend.sqlite` (ignored). The
  evidence ledger is left untouched, because opening it would migrate it. The
  evidence ledger records $15.05 of evaluation spend this calendar month (1,669
  calls on 5 November), so the Anthropic cycle cap on the new ledger is lowered
  to $134 (`--cycle-cap-usd 134`), keeping the month inside $150. Cycle start day
  is still the placeholder 1.

## Which 30 books

The design names no rule for the 30, so they are drawn before any run. The pool
is the 45 Iterate books that supplied no prompt example. Leave-one-out is wired,
so a source book would only lose its own examples, but drawing outside the
sources means both arms see identical example packs on every compared book.
Within each alias stratum, take books in ascending SHA-256 of
`tuning-baseline|20261116|<stratum>|<id>` (ties by UTF-8 id), allocated in
proportion to the pool: 16 of 24 `alias_zero`, 14 of 21 `alias_positive`.

Part 1 (30): born_to_be_a_butterfly, bottoms_up!, elmer_and_the_wind,
elmer_on_stilts, father_christmas_needs_a_wee, giraffe_is_left_out,
jesus'_christmas_party, kipper's_a_to_z, lenny_makes_a_wish,
little_red_riding_hood, lost_in_snow, norman_the_slug_with_the_silly_shell,
sharing_a_shell, supertato, tabby_mctat, ten_in_the_bed_and_other_counting_rhymes,
ten_tall_giraffes, the_bad-tempered_ladybird, the_book_with_no_pictures, the_cave,
the_little_bully, the_monstrous_tale_of_celery_crumble, the_pirates_next_door,
the_rescue_party, the_same_but_different_too, the_three_little_pigs,
the_wheels_on_the_bus, what_the_ladybird_heard_next, who_will_save_us, you_choose.

Part 2: the other 25 Iterate books, including all ten example sources, and the
20 Check books, with the better arm.

## Results

Recorded below as each run finishes. One book's scores say nothing about
quality; the probes check the path, the accounting and the scoring.

### Probes (`the_three_little_pigs`, 28 scan pages)

| Run | Code | Paid | Outcome |
|---|---|---|---|
| Old prompts | `8643d68` | $0.51 (estimate $0.64) | 60 calls, all `end_turn`; served models as requested |
| New prompts | `8643d68` | $0.59 | Metadata and character calls stopped at `max_tokens` with every output token spent thinking (4,096 and 8,192); no characters returned |
| Old prompts | `e01cd1a` | $0.33 | All `end_turn`; character call 842 output tokens, 323 thinking |
| New prompts | `e01cd1a` | $0.65 | All `end_turn`; character call 12,171 output tokens, 8,491 thinking; one page rejected by the strict parser as `invalid_response` (an invented "no text" region beside the real text), a genuine new-prompt failure |

The second probe exposed a defect, not a prompt effect: Sonnet 5 thinks by
default and `max_tokens` caps thinking and answer together. Commit `e01cd1a`
(branch `impl/output-ceilings`) raises the whole-book stages to 20,000 output
tokens and page transcription to 8,192 for both arms, so part 1 runs from
`e01cd1a`, not `8643d68`. Character recall on the probe book moved from 4 of 8
to 8 of 8 between the two old-prompt runs, whose page requests differed only in
the output ceiling: single-book figures are noisy, which the noise calibration
(design §4) exists to measure.

Full-book cost from the probes: old prompts $0.51, new prompts $0.83, both arms
$1.16 with rotation shared. Projected by pages: part 1 (1,130 pages, both arms)
about $47; part 2 (1,593 pages) about $48 with the new prompts or $29 with the
old. Probes so far $2.09. Part 1 per-run caps: $30 old, $36 new.

### Part 1: old against new prompts on the 30 drawn books (code `e01cd1a`)

Both arms completed every book. Cost $46.59: old prompts $21.37 ($0.71 a
book), new prompts $25.23 ($0.84 a book, reusing the old arm's rotation calls).
Paired new-minus-old equal-book differences with 95% book-bootstrap ranges
(20,000 draws, seed 20261110); character measures exclude the three books with
no reference characters.

| Measure | Books | Old | New | New − old [95% range] |
|---|---:|---:|---:|---|
| Text accuracy | 30 | 0.791 | 0.732 | −0.059 [−0.100, −0.016] |
| Unsuccessful pages per 100 | 30 | 0.0 | 3.2 | +3.2 [+2.3, +4.2] |
| Inserted text per 100 reference tokens | 30 | 15.7 | 13.3 | −2.4 [−6.2, +0.9] |
| Character recall | 27 | 0.918 | 0.522 | −0.396 [−0.564, −0.235] |
| Gender | 27 | 0.874 | 0.487 | −0.387 [−0.551, −0.230] |
| Human | 27 | 0.787 | 0.439 | −0.348 [−0.528, −0.175] |
| Protagonist | 27 | 0.873 | 0.500 | −0.372 [−0.541, −0.208] |

Why the new prompts lose. Their character call ran out of output on 14 of 30
books even at 20,000 tokens: the model was still thinking (median 19,996
thinking tokens on those books, 7,056 where it finished, against 194 under the
old prompts), so those books scored zero on every character measure. The SDK
refuses longer non-streaming requests, so the ceiling cannot simply rise again.
On the 16 books where the new call finished, it is still no better: character
recall −0.044 [−0.106, 0.000], gender −0.052 [−0.119, +0.013], human +0.001,
protagonist −0.017. Separately, the strict parser rejected 36 new-prompt pages
as `invalid_response`.

Decision under the approved rule ("carry the better to the other 25 and the
Check set"): the old prompts, better on all five primaries and cheaper. Part 2
runs `baseline.json` on the other 25 Iterate books and the 20 Check books,
cap $38. The new prompts' rules and examples remain candidate changes for the
tuning loop, one at a time.

Consequence for the `main` merge: the app on `claude-dev` defaults to the new
prompts (`pipeline_config.PIPELINE_SETTINGS_DEFAULTS['prompt_variant'] =
'rules-plus-icl'`). Until that changes, merging `claude-dev` into `main` would
move the live app to the losing prompts (DECISIONS 022 holds the merge).

Text accuracy from colour scans, about 0.79 under the old prompts, is the real
starting point; the 0.99 measured on stored page images was void, because those
images are renders of the reference text.
