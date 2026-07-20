# Rotation triage fix — portrait-shot spreads left sideways (2026-07-17)

## Symptom (user, live production, #217 already deployed)
"Many double-spread pages with clear text are displayed rotated 90° to the left
— portrait, where it should be a landscape double spread." Reproduced on the book
**"The Play Date"** (14 pages, all shot in portrait on a phone).

## Diagnosis (from the real S3 images + live API calls)
- Every raw photo is portrait (3000×4000). Most pages are landscape double
  spreads → correct output is landscape; the pipeline must detect "sideways,
  rotate a quarter turn."
- Pulled the pages and looked at them: pages 3, 10 stored correct (landscape);
  pages 2, 7, 8 stored **sideways/portrait** (text running vertically) = the bug.
- Re-ran the live rotation logic on the raws: **the triage verdict is non-
  deterministic.** The same spread returns `SIDEWAYS` (→ rotated to landscape,
  correct) on one call and `UPSIDEDOWN` (→ 180°, a portrait stays portrait =
  left sideways, wrong) on another. Page 7 flipped to correct on re-run; page 10
  flipped to wrong; pages 2/8 were consistently wrong.
- **Root cause:** for portrait-shot spreads the model cannot reliably separate
  `SIDEWAYS` (quarter turn, 90°) from `UPSIDEDOWN` (half turn, 180°). This is the
  TRIAGE step, not the chirality/binary step #217 fixed. The #217 landscape
  aspect gate gives no protection here because the raw input is portrait
  (aspect 0.75 < 1.15) — there is no safety net for exactly this case.

## Fix
Reframe `AIPrompts.rotation_triage` to decide on the **direction the text lines
run** rather than the abstract turn amount:
- horizontal lines, letters upright → UPRIGHT
- horizontal lines, letters inverted (half turn to read) → UPSIDEDOWN
- **vertical lines → SIDEWAYS**

Prompt-only change; the triage→binary→aspect-gate logic and angle mapping are
unchanged.

## Measurement (Claude Sonnet, model id `claude-sonnet-4-6`, 2 samples/page)
- On "The Play Date" text pages, expected verdict SIDEWAYS: **current 19/26 →
  improved 26/26** (deterministic; pages 2/8/9/10 fixed).
- Four-orientation regression (known-upright images rotated 0/90/180/270):
  **current 10/12 → improved 12/12** — no regression on UPRIGHT/UPSIDEDOWN.
- Combined: **current 29/38 → improved 38/38.**

## Follow-up (not done here)
Deterministic Tesseract OSD (`--psm 0`) as a primary/secondary orientation
signal for text pages would remove the residual model dependence entirely — the
code already carries this as a TODO in `get_rotation_angle`. It needs
`pytesseract` + the `tesseract-ocr` system package (`packages.txt` on Streamlit
Cloud) and a redeploy, so it is deliberately separate from this prompt hotfix.
Diagnostic scripts used: `scratchpad/rot_diag.py`, `rot_verdicts.py`,
`rot_prompt_experiment.py`, `rot_regression.py`.
