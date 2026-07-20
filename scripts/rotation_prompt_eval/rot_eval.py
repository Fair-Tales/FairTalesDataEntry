"""Rotation-prompt evaluation harness (#217 follow-up, 2026-07-18).

Read-only on production data; makes Anthropic vision calls only. Replicates
``get_rotation_angle``'s two-step logic (triage -> aspect gate -> +90-in-code
-> binary) over the labelled random sample in ``rot_labels.json`` for one or
more triage-prompt VARIANTS, N runs per image to expose non-determinism.
Results are cached in ``<workdir>/rot_eval_cache.json`` keyed by
(prompt-hash, image, run) so re-runs and shared-prompt variants are free.

Also runs a synthetic 4-orientation regression: known-upright images rotated
0/90/180/270 in code, expecting correction (360-k)%360. NOTE: in that check a
portrait single page rotated 90/270 becomes LANDSCAPE, so the production
aspect gate fires and returns (0, uncertain) BY DESIGN even when the model's
triage verdict was correct — the report separates "triage correct" from
"pipeline correct" for that reason.

Final 2026-07-18 result (claude-sonnet-4-6, labelled sample of 65 scoreable
images from 18 random books, 2-3 runs each):
    baseline (4c15e2e line-direction prompt)   126/130 = 96.9%
    winner   ("current" == the committed one)  195/195 = 100.0%, 0 flips

Usage (from the repo root; sample must exist — run rot_sample.py first):
  .venv/bin/python scripts/rotation_prompt_eval/rot_eval.py real  current 2
  .venv/bin/python scripts/rotation_prompt_eval/rot_eval.py synth current 2
Variants: current | baseline_4c15e2e ; optional 4th arg selects the binary
prompt (current | b2).
"""
import base64
import hashlib
import io
import json
import os
import re
import sys
import tomllib
from collections import defaultdict

import anthropic
from PIL import Image

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)
from image_processing import LANDSCAPE_SPREAD_MIN_ASPECT  # noqa: E402
from text_content import AIPrompts  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WORKDIR = os.environ.get("ROT_EVAL_WORKDIR", os.path.join(HERE, "work"))
MODEL = "claude-sonnet-4-6"

with open(os.path.join(REPO, ".streamlit/secrets.toml"), "rb") as fh:
    secrets = tomllib.load(fh)
client = anthropic.Anthropic(api_key=secrets["ANTHROPIC_API_KEY"])

# ---------------------------------------------------------------------------
# Triage prompt variants. "current" is whatever is committed in text_content.
# "baseline_4c15e2e" preserves the pre-iteration prompt for comparison.
# ---------------------------------------------------------------------------
BASELINE_4C15E2E = (
    "This is a photo of one book page or a two-page spread. Find the lines "
    "of printed text and decide how they are oriented.\n"
    "- UPRIGHT: the lines of text run HORIZONTALLY (left to right) and the "
    "letters are the right way up and readable.\n"
    "- UPSIDEDOWN: the lines of text still run HORIZONTALLY, but every letter "
    "is inverted — you would rotate the whole page a HALF turn (180 degrees) "
    "to read it.\n"
    "- SIDEWAYS: the lines of text run VERTICALLY (up and down the page); you "
    "would rotate the page a QUARTER turn (90 degrees) so the lines become "
    "horizontal and readable.\n"
    "The key cue is the DIRECTION the lines of text run: horizontal means "
    "UPRIGHT or UPSIDEDOWN, vertical means SIDEWAYS. If there is no text, use "
    "the picture (people/objects upright, sky at the top).\n"
    "Answer with exactly one word: UPRIGHT, UPSIDEDOWN, or SIDEWAYS."
)

VARIANTS = {
    "current": AIPrompts.rotation_triage,
    "baseline_4c15e2e": BASELINE_4C15E2E,
}

# Rejected binary variant kept for reference: letters-emphasis binary. Did not
# clearly beat the production binary (real sample tied at 100%; small synth
# gain within run-to-run noise), so the production binary stays.
BINARY_B2 = (
    "This is a photo of a book page or a two-page spread. Is it the RIGHT "
    "WAY UP or UPSIDE DOWN? Judge from the letters themselves: if the "
    "letters are the right way up and readable, answer UPRIGHT; if the "
    "letters are inverted — you would rotate the page a half turn to read "
    "them — answer UPSIDEDOWN. If there is no text, use the picture "
    "(people/objects upright, sky at the top).\n"
    "Answer with exactly one word: UPRIGHT or UPSIDEDOWN."
)

BINARIES = {"current": None, "b2": BINARY_B2}

TRIAGE_WORDS = ("UPRIGHT", "UPSIDEDOWN", "SIDEWAYS")
BINARY_WORDS = ("UPRIGHT", "UPSIDEDOWN")

CACHE_PATH = os.path.join(WORKDIR, "rot_eval_cache.json")
cache = json.load(open(CACHE_PATH)) if os.path.exists(CACHE_PATH) else {}


def phash(prompt):
    return hashlib.sha1(prompt.encode()).hexdigest()[:10]


def ask(image_bytes, prompt, allowed, ckey):
    if ckey in cache:
        return cache[ckey]
    b64 = base64.standard_b64encode(image_bytes).decode()
    resp = client.messages.create(
        # Matches production image_processing._CLASSIFY_MAX_TOKENS: 5 clipped long
        # answer words (FOLDHORIZONTAL) mid-word, causing truncation/parse misses.
        model=MODEL, max_tokens=16,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": prompt},
        ]}],
    )
    word = re.sub(r"[^A-Z]", "", (resp.content[0].text or "").upper())
    out = word if word in allowed else f"?{word}"
    cache[ckey] = out
    os.makedirs(WORKDIR, exist_ok=True)
    json.dump(cache, open(CACHE_PATH, "w"))
    return out


def rotate(image_bytes, deg_cw):
    with Image.open(io.BytesIO(image_bytes)) as im:
        out = im.rotate(-deg_cw, expand=True).convert("RGB")
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=85)
        return buf.getvalue()


def pipeline(image_bytes, triage_prompt, ckey_base, binary_prompt=None):
    """Replicate get_rotation_angle. Returns (angle, uncertain, triage, binary)."""
    triage = ask(image_bytes, triage_prompt, TRIAGE_WORDS, ckey_base + "|t")
    if triage == "UPRIGHT":
        return 0, False, triage, None
    if triage == "UPSIDEDOWN":
        return 180, False, triage, None
    if triage == "SIDEWAYS":
        with Image.open(io.BytesIO(image_bytes)) as im:
            aspect = im.width / im.height
        if aspect >= LANDSCAPE_SPREAD_MIN_ASPECT:
            return 0, True, triage, "GATED"
        bp = binary_prompt or AIPrompts.rotation_binary
        bsuf = "|b" if binary_prompt is None else f"|b:{phash(bp)}"
        binary = ask(rotate(image_bytes, 90), bp, BINARY_WORDS, ckey_base + bsuf)
        if binary == "UPRIGHT":
            return 90, False, triage, binary
        if binary == "UPSIDEDOWN":
            return 270, False, triage, binary
        return 0, True, triage, binary
    return 0, True, triage, None


def load_preview(image_id):
    with open(os.path.join(WORKDIR, "sample", image_id + "_preview.jpg"), "rb") as f:
        return f.read()


def load_labels():
    return json.load(open(os.path.join(HERE, "rot_labels.json")))["labels"]


def eval_real(variant, runs=2, binary="current"):
    prompt = VARIANTS[variant]
    bprompt = BINARIES[binary]
    ph = phash(prompt)
    labels = load_labels()
    scoreable = {k: v for k, v in labels.items() if not v.get("excluded")}
    stats = defaultdict(lambda: [0, 0])
    flips = 0
    wrong = []
    for image_id, lab in sorted(scoreable.items()):
        img = load_preview(image_id)
        angles = []
        for r in range(runs):
            ckey = f"{ph}|{image_id}|{r}"
            angle, unc, triage, bword = pipeline(img, prompt, ckey, bprompt)
            angles.append(angle)
            ok = (angle == lab["angle"])
            for cat in ("ALL", f"layout:{lab['layout']}", f"text:{lab['text']}",
                        f"angle:{lab['angle']}"):
                stats[cat][0] += ok
                stats[cat][1] += 1
            if not ok:
                wrong.append((image_id, r, lab["angle"], angle, triage, bword, unc))
        if len(set(angles)) > 1:
            flips += 1
    print(f"\n== REAL SAMPLE  variant={variant}  binary={binary}  runs={runs}"
          f"  model={MODEL} ==")
    for cat in sorted(stats):
        c, t = stats[cat]
        print(f"  {cat:18} {c:>3}/{t:<3} = {100*c/t:.1f}%")
    print(f"  images with run-to-run flips: {flips}/{len(scoreable)}")
    for w in wrong:
        print(f"    {w[0]} run{w[1]}: want {w[2]}, got {w[3]} "
              f"(triage={w[4]}, binary={w[5]}, uncertain={w[6]})")


#: Diverse known-upright bases for the synthetic 4-orientation regression.
SYNTH_BASES = [
    "Bea's_Bad_Day__page_1",               # cover, decorative
    "Who_Will_Save_Us__page_46",           # dense text single
    "Lost_in_Snow__page_22",               # wordless illustration
    "If_I_had_a_Kangaroo__page_2",         # landscape spread, upright
    "Bottoms_Up!__page_6",                 # decorative banners
    "The_Smartest_Giant_in_Town__page_8",  # normal text single
]


def eval_synth(variant, runs=1, binary="current"):
    prompt = VARIANTS[variant]
    bprompt = BINARIES[binary]
    ph = phash(prompt)
    ok = tot = triage_ok = gated = 0
    fails = []
    expect_triage = {0: "UPRIGHT", 90: "SIDEWAYS", 180: "UPSIDEDOWN", 270: "SIDEWAYS"}
    for base in SYNTH_BASES:
        img0 = load_preview(base)
        for deg in (0, 90, 180, 270):
            img = rotate(img0, deg) if deg else img0
            want = (360 - deg) % 360
            for r in range(runs):
                ckey = f"{ph}|SYN:{base}:{deg}|{r}"
                angle, unc, triage, bword = pipeline(img, prompt, ckey, bprompt)
                tot += 1
                triage_ok += (triage == expect_triage[deg])
                if bword == "GATED":
                    gated += 1  # aspect gate fired: by-design uncertain
                if angle == want:
                    ok += 1
                else:
                    fails.append((base, deg, want, angle, triage, bword))
    print(f"\n== SYNTHETIC 4-ORIENTATION  variant={variant}  binary={binary}"
          f"  runs={runs} ==")
    print(f"  pipeline correct: {ok}/{tot} = {100*ok/tot:.1f}%   "
          f"(aspect-gate uncertain, by design: {gated})")
    print(f"  triage correct:   {triage_ok}/{tot} = {100*triage_ok/tot:.1f}%")
    for f in fails:
        print(f"    {f[0]} rot{f[1]}: want {f[2]}, got {f[3]} "
              f"(triage={f[4]}, binary={f[5]})")


if __name__ == "__main__":
    mode, variant = sys.argv[1], sys.argv[2]
    runs = int(sys.argv[3]) if len(sys.argv) > 3 else (2 if mode == "real" else 1)
    binary = sys.argv[4] if len(sys.argv) > 4 else "current"
    if mode == "real":
        eval_real(variant, runs, binary)
    else:
        eval_synth(variant, runs, binary)
