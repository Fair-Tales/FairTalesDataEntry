# Commit ids before and after the history rewrite of 11 November 2026

On 11 November the history of `claude-dev` was rewritten from the first commit of
4 November onward, to remove a test fixture that held near-complete text of 20
in-copyright books and to mask three student usernames in one planning note
(DECISIONS 017 item 5). Nothing else changed: the tips before and after differ only
in those three masked lines, and every commit kept its message, author and dates.

Rewriting changes commit ids. Documents and commit messages written before the
rewrite cite the old ids; this table resolves them. Commits earlier than the first
row from the bottom are unchanged.

| Old id | New id | Subject |
|---|---|---|
| `a58d559` | `132952f` | Score attributes on found characters, and branch the work in six groups |
| `8ff4eeb` | `c9b1627` | Order the work between the approved designs and the first paid call |
| `40e8b7d` | `17237dd` | Approve the tuning design with Chris's acceptance rule |
| `93d8594` | `3e6fc18` | Settle how characters and aliases are scored, and tighten the tuning loop |
| `0bcd4b1` | `a3ce86d` | Astra's review of the tuning design, with both credit balances in use |
| `ede253d` | `af00c99` | Log three conventions Chris set for tuning and scoring (018) |
| `555b5fa` | `38c32b4` | Design the tuning process around what the baseline actually gets wrong |
| `8c1cfd2` | `328a0e6` | Record the plan's ratification and how tuning is to proceed |
| `9463637` | `24e9c9b` | Draft the analysis plan that replaces the gates: measure, then judge |
| `183fd50` | `50e5241` | Log the rulings of 10 November (017) |
| `7fb876c` | `a5e36d4` | Take the dev20 prediction file out of the repository |
| `87b2e7f` | `df17e7a` | Correct the review: the original books' text is human-entered |
| `25430b1` | `cb2a7bf` | Record the Fable 5.2 review of the work since 4 November |
| `a89fd46` | `a4ce68a` | Let the evaluation harness call OpenAI |
| `5f676d6` | `1937866` | Record either provider's spend honestly, at current frontier prices |
| `f100772` | `b5c059f` | Refuse held-out books at the point tuning loads them |
| `6c79d76` | `3fb639e` | Give agent sessions standing instructions instead of repeating them |
| `40138cf` | `bb5bf10` | Cut the freeze down to one agent and two ordinary folders |
| `cc4a3aa` | `4a0044c` | Make the holdout freeze executable, and give it somewhere durable to live |
| `4927465` | `40fc009` | Cover the photo-upload flow, and unblock it locally |
| `3347f0a` | `118e7c4` | Repair the browser suite so its selectors match what the app renders |
| `8ef5c57` | `493e833` | Log the autonomy validation decisions |
| `d871aac` | `52c86cc` | Split the imported books evenly between holdout and tuning |
| `0e44a5a` | `7e913cc` | Hold imported books out of tuning rather than transcribing unfinished ones |
| `ad33253` | `f1dcfa2` | Prove on the human-typed pilot cohort and measure the AI's edge instead of assuming it |
| `dab63a8` | `5f23558` | Replace the unreachable autonomy gates with ones the evidence can settle |
| `402093a` | `3b71a34` | Design the path to running the pipeline without human review |
| `33542bc` | `3390d6d` | Plan recurring use of both expiring credit balances |
| `d20b9a6` | `3fa3aaa` | Keep the stock camera and automate the move instead |
| `9881364` | `0625029` | Assess the OpenAI credit options and recommend declining them |
| `d75829f` | `1d72d67` | Build the prompt, schema and billing half of the pipeline improvement |
| `5e2eaa3` | `03e5217` | Design photo ingestion around a no-selection constraint |
| `37481ad` | `ee5c258` | Implement the alias identity model, raw/canonical storage and M1-M4 |
| `41109f8` | `e2734db` | Log the alias design and measurement decisions (014) |
| `5c3b673` | `beb1005` | Design the alias pipeline around downstream NLP, not tidiness |
| `5e228f8` | `b8aba57` | Measure alias duplication and groundedness on the dev20 output |
| `0af1fa4` | `c214499` | Measure what the students actually did with aliases |
| `c925c25` | `6bbc170` | Record adjudication of the 7 disputed wordless pages: zero hallucinations |
| `0130eb7` | `5945a25` | Stop ISBN lookup failures masquerading as missing metadata |
| `4581496` | `8231493` | Stop the snapshot reading the corpus three times (Firestore quota incident) |
| `d061e1a` | `8d73cc9` | Add durable bulk batch photo upload with full-pipeline processing |
| `0e73006` | `7a9a10d` | Correct the harness README note on Sonnet 5 pricing |
| `0aeb768` | `9c34df3` | Correct and verify the evaluation baseline's model pricing |
| `2d2887c` | `602d633` | Restore extraction rationale, the shared error constant and strict settings |
| `27198ae` | `384560b` | Add the pilot extraction-evaluation harness (snapshot / extract / score) |
| `ae4a12c` | `b3c2325` | Add astra designs for the batch upload and pilot validation harness |

The old history is kept locally, never pushed: the branch
`backup/claude-dev-before-purge` and a bundle in the evidence folder beside the
freeze. The local branches `e2e-photo-upload` and `fix-deploy-blockers-full` still
carry the old commits and must not be pushed.
