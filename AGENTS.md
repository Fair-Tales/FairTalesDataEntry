# Notes for agent sessions

This file is read automatically by `codex` sessions in this repository, so the
standing instructions below do not need repeating in a prompt. A brief should
carry the *task*; the constraints live here.

`CLAUDE.md` holds the code conventions (user-facing strings in `text_content`,
write-through `Field` descriptors, stable widget keys, `FieldFilter` queries,
shared helpers) and the safety rules. `DECISIONS.md` records significant
decisions and why — check it before proposing a change to tooling, layout or
data handling. Planning documents live in `planning/`, dated and descriptive.

## Simplicity is binding (Chris, 2026-11-07)

**As simple and concise as possible. No bloat, no unnecessary roles,
infrastructure or process.** This applies to designs and to code.

- **Be proportionate to the real risk.** For every requirement, state what
  failure it actually prevents and how likely that failure is here. Delete it if
  the honest answer is "none" or "vanishingly unlikely". Defences that only make
  sense against a deliberate adversary are waste on a university research
  project: the realistic failures are accidents, confusion and lost work.
- **An agent may do a job end to end** with no human in the loop unless a human
  judgement is genuinely required. Do not invent custody roles, sign-offs,
  witnesses or approval gates. Where Chris has said "security by trust", trust.
- **Prefer ordinary files and existing tools** over new services, accounts,
  volumes or layers. Propose no purchase and no new infrastructure.
- A design that is correct but unusable has failed. Shorter is better; say what
  you cut.

This came from a freeze procedure that specified two named custodians,
cryptographic counter-signatures, separate OS accounts and a demonstrated
restore, to protect a holdout draw of children's books against an adversary who
does not exist. Chris: "far too complicated and overengineered."

## Design rounds

- **Design only unless told otherwise**: revise the named files in place, change
  no others, run no git commands, no API calls, no live services, and spend
  nothing.
- **Argue back.** Where you think a brief, a review finding or an earlier round
  is wrong, say so and make the case. Do not capitulate quietly, and do not
  reinstate something you were asked to cut without arguing for it.
- **Distinguish what is measured from what is assumed.** Prospective counts,
  planned artifacts and expected results must be labelled as such. Never present
  a projection as an observation.
- **End with "Open questions for Chris"**, and only genuine decisions of his.
  He answers from a terminal with none of these documents open, so each question
  must be self-contained: define any term he has not used himself, give the
  trade-off in plain words and the concrete consequence of each option. If
  nothing is genuinely his, leave the section empty and say so.

## Version control

Trunk is `claude-dev`; topic branches off it, merged back when done. Commit each
atomic change with a why-focused message. **Never merge into or push `main`** —
it is the production deploy branch and Chris handles it himself. Never push
automatically. No co-author trailer.
