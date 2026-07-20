"""Quantify the refined-gate risk case (#217 follow-up, 2026-07-20).

The refined aspect gate would trust a SIDEWAYS triage verdict on a landscape
image when the spine probe says SINGLE. The only way that can go wrong is:
an UPRIGHT (or 180) landscape SPREAD where (a) triage wrongly says SIDEWAYS
AND (b) the probe wrongly says SINGLE. The probe fails on whitened scan
spreads (no fold shadow), so measure how often triage actually says SIDEWAYS
on exactly those images — upright landscape spreads, photos and scans — with
extra runs. Also: probe stability on the same set.

Extra realistic upright landscape spreads are manufactured by rotating the 18
sideways-shot (angle 90/270) real spreads upright in code.

Usage: .venv/bin/python scripts/rotation_prompt_eval/rot_gate_risk.py 4
"""
import sys
from collections import Counter

from rot_eval import VARIANTS, TRIAGE_WORDS, ask, rotate, load_preview, load_labels, phash
from rot_spine_probe import probe, PPH

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 4
TRIAGE = VARIANTS["current"]
TPH = phash(TRIAGE)

labels = load_labels()
cases = []  # (case_id, image_bytes, kind)
for image_id, lab in sorted(labels.items()):
    if lab["layout"] != "spread":
        continue
    img0 = load_preview(image_id)
    if lab["angle"] in (0, 180) and not lab.get("excluded"):
        cases.append((f"{image_id}@as-is", img0 if lab["angle"] == 0
                      else rotate(img0, 180), "labelled-upright"))
    if lab["angle"] in (90, 270):
        # Rotate the sideways-shot spread upright in code -> realistic
        # upright landscape spread photo.
        cases.append((f"{image_id}@uprighted", rotate(img0, lab["angle"]),
                      "uprighted-photo"))
# The whitened scan spreads with no defined upright are the probe's known
# blind spot; include them as-is (they are landscape-ish spreads).
for image_id in ("The_Storm_Whale__page_5", "The_Storm_Whale__page_33"):
    cases.append((f"{image_id}@as-is", load_preview(image_id), "scan-excluded"))

triage_bad = probe_bad = both_bad = 0
n = 0
for case_id, img, kind in cases:
    tv = Counter()
    pv = Counter()
    for r in range(RUNS):
        tv[ask(img, TRIAGE, TRIAGE_WORDS, f"{TPH}|RISK:{case_id}|{r}|t")] += 1
        pv[probe(img, f"{PPH}|RISK:{case_id}|{r}|p")] += 1
    n += RUNS
    t_sw = tv.get("SIDEWAYS", 0)
    p_sg = pv.get("SINGLE", 0)
    triage_bad += t_sw
    probe_bad += p_sg
    # Joint per-run pessimistic pairing: a run is jointly bad only if both
    # fire; report min as the co-occurrence bound per image.
    both_bad += min(t_sw, p_sg)
    flag = " <-- triage SIDEWAYS" if t_sw else ""
    print(f"{kind:16} {case_id:55} triage={dict(tv)} probe={dict(pv)}{flag}")

print(f"\nupright-landscape-spread runs: {n}")
print(f"  triage said SIDEWAYS (gate would engage):   {triage_bad}/{n}")
print(f"  probe said SINGLE (would un-gate):          {probe_bad}/{n}")
print(f"  worst-case joint (silent wrong rotation):   {both_bad}/{n}")
