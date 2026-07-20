"""Spine / single-vs-spread probe evaluation (#217 follow-up, 2026-07-20).

Tests the hypothesis behind refining the landscape aspect gate: can the vision
model reliably tell ONE single page/cover (no central fold) from TWO pages of
an open book meeting at a fold, and report the fold's direction? If yes, a
SIDEWAYS triage verdict on a landscape image that the probe says is a SINGLE
(or a horizontally-folded spread) can be TRUSTED and rotated via the +90
binary step, instead of being gated to (0, rotation_uncertain).

READ-ONLY on production data; Anthropic vision calls only; results cached in
the shared rot_eval cache. Reuses the labelled sample from rot_eval/rot_sample
(fetch with rot_fetch_manifest.py when the bucket has drifted).

Modes (run from the repo root, PYTHONPATH implied by sys.path insert):
  .venv/bin/python scripts/rotation_prompt_eval/rot_spine_probe.py layout 2
      Probe accuracy on all 72 labelled previews as-is (single/cover ->
      SINGLE; spread with true angle 0/180 -> FOLDVERTICAL; 90/270 ->
      FOLDHORIZONTAL).
  .venv/bin/python scripts/rotation_prompt_eval/rot_spine_probe.py synth 2
      Probe accuracy under code rotation: all spreads x 0/90/180/270 (fold
      direction must track the rotation), all covers and singles x 90/270
      (the landscape-single failure class: probe must say SINGLE).
  .venv/bin/python scripts/rotation_prompt_eval/rot_spine_probe.py pipeline 2
      Full refined-gate pipeline (triage -> gate[probe] -> +90 binary) on the
      real sample AND the 6-base synthetic 4-orientation check, side by side
      with the current always-gate behaviour.
"""
import sys
from collections import defaultdict

from rot_eval import (
    VARIANTS, TRIAGE_WORDS, BINARY_WORDS, SYNTH_BASES,
    ask, rotate, load_preview, load_labels, phash,
)
from text_content import AIPrompts

PROBE_WORDS = ("SINGLE", "FOLDVERTICAL", "FOLDHORIZONTAL")

#: Candidate probe prompt (would live in text_content.AIPrompts if adopted).
PROBE_PROMPT = (
    "This is a photo of a book. Decide what the photo shows:\n"
    "- SINGLE: ONE single page, or the front or back cover, on its own — "
    "there is NO central fold or gutter where two facing pages meet.\n"
    "- FOLDVERTICAL: TWO facing pages of an open book, side by side LEFT and "
    "RIGHT, meeting at a central fold/gutter that runs VERTICALLY (top to "
    "bottom) down the middle of the photo.\n"
    "- FOLDHORIZONTAL: TWO facing pages of an open book, stacked TOP and "
    "BOTTOM, meeting at a central fold/gutter that runs HORIZONTALLY (left "
    "to right) across the middle of the photo.\n"
    "Look for the physical fold: a crease or shadow line where the paper "
    "bends at the book's spine, with the page surface usually curving "
    "slightly on each side of it. Ignore the outer edges of the book and the "
    "background.\n"
    "Reply with exactly one word and no explanation: SINGLE, FOLDVERTICAL, "
    "or FOLDHORIZONTAL."
)

PPH = phash(PROBE_PROMPT)


def expected_probe(layout, angle):
    """Ground-truth probe word from the labelled layout + true angle."""
    if layout in ("single", "cover"):
        return "SINGLE"
    # spread: upright/inverted spread shows a vertical fold; a quarter-turned
    # spread shows a horizontal fold.
    return "FOLDVERTICAL" if angle in (0, 180) else "FOLDHORIZONTAL"


def rotated_expectation(base_word, deg):
    """How the expected probe word transforms under a code rotation."""
    if base_word == "SINGLE" or deg in (0, 180):
        return base_word
    return ("FOLDHORIZONTAL" if base_word == "FOLDVERTICAL" else "FOLDVERTICAL")


def probe(img, ckey):
    return ask(img, PROBE_PROMPT, PROBE_WORDS, ckey)


def eval_layout(runs=2):
    labels = load_labels()
    stats = defaultdict(lambda: [0, 0])
    wrong, flips = [], 0
    for image_id, lab in sorted(labels.items()):
        img = load_preview(image_id)
        want = expected_probe(lab["layout"], lab["angle"])
        got = []
        for r in range(runs):
            w = probe(img, f"{PPH}|{image_id}|{r}|p")
            got.append(w)
            ok = (w == want)
            for cat in ("ALL", f"layout:{lab['layout']}", f"want:{want}"):
                stats[cat][0] += ok
                stats[cat][1] += 1
            if not ok:
                wrong.append((image_id, r, want, w, lab.get("excluded", False)))
        flips += (len(set(got)) > 1)
    print(f"\n== PROBE ON REAL SAMPLE (as-is)  runs={runs} ==")
    for cat in sorted(stats):
        c, t = stats[cat]
        print(f"  {cat:20} {c:>3}/{t:<3} = {100*c/t:.1f}%")
    print(f"  images with run-to-run flips: {flips}")
    for w in wrong:
        print(f"    {w[0]} run{w[1]}: want {w[2]}, got {w[3]}"
              f"{'  [excluded page]' if w[4] else ''}")


def eval_synth(runs=2):
    labels = load_labels()
    stats = defaultdict(lambda: [0, 0])
    wrong = []
    for image_id, lab in sorted(labels.items()):
        if lab.get("excluded"):
            continue
        base_want = expected_probe(lab["layout"], lab["angle"])
        degs = (0, 90, 180, 270) if lab["layout"] == "spread" else (90, 270)
        img0 = load_preview(image_id)
        for deg in degs:
            img = rotate(img0, deg) if deg else img0
            want = rotated_expectation(base_want, deg)
            for r in range(runs):
                w = probe(img, f"{PPH}|SYN:{image_id}:{deg}|{r}|p")
                ok = (w == want)
                for cat in ("ALL", f"layout:{lab['layout']}", f"want:{want}"):
                    stats[cat][0] += ok
                    stats[cat][1] += 1
                if not ok:
                    wrong.append((image_id, deg, r, want, w))
    print(f"\n== PROBE UNDER CODE ROTATION  runs={runs} ==")
    print("  (spreads x 0/90/180/270; singles & covers x 90/270 —")
    print("   the landscape-single failure class)")
    for cat in sorted(stats):
        c, t = stats[cat]
        print(f"  {cat:20} {c:>3}/{t:<3} = {100*c/t:.1f}%")
    for w in wrong:
        print(f"    {w[0]} rot{w[1]} run{w[2]}: want {w[3]}, got {w[4]}")


def refined_pipeline(image_bytes, triage_prompt, ckey_base):
    """get_rotation_angle with the REFINED aspect gate: on SIDEWAYS +
    landscape, a spine probe arbitrates — ONLY a positive SINGLE verdict
    (no central fold, so the image cannot be a spread) trusts SIDEWAYS and
    proceeds to the +90 binary step; FOLDVERTICAL, FOLDHORIZONTAL and
    unparseable replies all keep the protective gate (uncertain)."""
    import io
    from PIL import Image
    triage = ask(image_bytes, triage_prompt, TRIAGE_WORDS, ckey_base + "|t")
    if triage == "UPRIGHT":
        return 0, False, triage, None, None
    if triage == "UPSIDEDOWN":
        return 180, False, triage, None, None
    if triage == "SIDEWAYS":
        from rot_eval import LANDSCAPE_SPREAD_MIN_ASPECT
        with Image.open(io.BytesIO(image_bytes)) as im:
            aspect = im.width / im.height
        pword = None
        if aspect >= LANDSCAPE_SPREAD_MIN_ASPECT:
            pword = probe(image_bytes, ckey_base + f"|g:{PPH}")
            if pword != "SINGLE":
                # Any fold claim or unparseable: keep the protective gate.
                return 0, True, triage, "GATED", pword
        binary = ask(rotate(image_bytes, 90), AIPrompts.rotation_binary,
                     BINARY_WORDS, ckey_base + "|b")
        if binary == "UPRIGHT":
            return 90, False, triage, binary, pword
        if binary == "UPSIDEDOWN":
            return 270, False, triage, binary, pword
        return 0, True, triage, binary, pword
    return 0, True, triage, None, None


def eval_pipeline(runs=2):
    prompt = VARIANTS["current"]
    ph = phash(prompt)
    labels = load_labels()
    scoreable = {k: v for k, v in labels.items() if not v.get("excluded")}

    print(f"\n== REFINED-GATE PIPELINE, REAL SAMPLE  runs={runs} ==")
    ok = tot = probed = 0
    wrong = []
    for image_id, lab in sorted(scoreable.items()):
        img = load_preview(image_id)
        for r in range(runs):
            ckey = f"{ph}|{image_id}|{r}"
            angle, unc, triage, bword, pword = refined_pipeline(img, prompt, ckey)
            tot += 1
            probed += (pword is not None)
            if angle == lab["angle"]:
                ok += 1
            else:
                wrong.append((image_id, r, lab["angle"], angle, triage, bword,
                              pword, unc))
    print(f"  pipeline correct: {ok}/{tot} = {100*ok/tot:.1f}%"
          f"   (gate probe invoked on {probed} runs)")
    for w in wrong:
        print(f"    {w[0]} run{w[1]}: want {w[2]}, got {w[3]} "
              f"(triage={w[4]}, binary={w[5]}, probe={w[6]}, uncertain={w[7]})")

    print(f"\n== REFINED-GATE PIPELINE, SYNTHETIC 4-ORIENTATION  runs={runs} ==")
    ok = tot = probed = gated = 0
    fails = []
    for base in SYNTH_BASES:
        img0 = load_preview(base)
        for deg in (0, 90, 180, 270):
            img = rotate(img0, deg) if deg else img0
            want = (360 - deg) % 360
            for r in range(runs):
                ckey = f"{ph}|SYN:{base}:{deg}|{r}"
                angle, unc, triage, bword, pword = refined_pipeline(
                    img, prompt, ckey)
                tot += 1
                probed += (pword is not None)
                gated += (bword == "GATED")
                if angle == want:
                    ok += 1
                else:
                    fails.append((base, deg, r, want, angle, triage, bword, pword))
    print(f"  pipeline correct: {ok}/{tot} = {100*ok/tot:.1f}%"
          f"   (probe invoked {probed}, still gated {gated})")
    for f in fails:
        print(f"    {f[0]} rot{f[1]} run{f[2]}: want {f[3]}, got {f[4]} "
              f"(triage={f[5]}, binary={f[6]}, probe={f[7]})")


if __name__ == "__main__":
    mode = sys.argv[1]
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    if mode == "layout":
        eval_layout(runs)
    elif mode == "synth":
        eval_synth(runs)
    elif mode == "pipeline":
        eval_pipeline(runs)
    else:
        raise SystemExit(f"unknown mode {mode}")
