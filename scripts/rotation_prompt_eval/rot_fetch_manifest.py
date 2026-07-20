"""Re-download the EXACT labelled eval images from the committed
``sample_manifest.json`` (by stored s3_key), rather than re-sampling — the
bucket contents change over time, so the seeded sampler no longer reproduces
the labelled set. READ-ONLY on S3. Missing keys are reported, not fatal.

Usage: PYTHONPATH=. .venv/bin/python scripts/rotation_prompt_eval/rot_fetch_manifest.py
"""
import json
import os
import sys
import tomllib

import s3fs

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)
from image_processing import downscale_for_vision  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WORKDIR = os.environ.get("ROT_EVAL_WORKDIR", os.path.join(HERE, "work"))
OUT = os.path.join(WORKDIR, "sample")
os.makedirs(OUT, exist_ok=True)

with open(os.path.join(REPO, ".streamlit/secrets.toml"), "rb") as fh:
    secrets = tomllib.load(fh)
fs = s3fs.S3FileSystem(key=secrets["AWS_ACCESS_KEY_ID"],
                       secret=secrets["AWS_SECRET_ACCESS_KEY"])

manifest = json.load(open(os.path.join(HERE, "sample_manifest.json")))
missing = []
for img in manifest["images"]:
    raw_path = os.path.join(OUT, img["id"] + ".jpg")
    prev_path = os.path.join(OUT, img["id"] + "_preview.jpg")
    if os.path.exists(prev_path):
        continue
    try:
        with fs.open(img["s3_key"], "rb") as h:
            data = h.read()
    except FileNotFoundError:
        missing.append(img["id"])
        continue
    with open(raw_path, "wb") as f:
        f.write(data)
    with open(prev_path, "wb") as f:
        f.write(downscale_for_vision(data))
    print(" ", img["id"])
print(f"\nfetched; missing keys: {missing}")
