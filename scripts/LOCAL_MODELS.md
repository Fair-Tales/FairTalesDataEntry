# Local models (Ollama) for the pilot importer's text stages

The pilot importer's **clean + coherence-judge pass** (`ai_clean_and_judge`) is
~67% of the run's Claude API cost, yet it is a pure **text** task: strip OCR /
extraction junk while preserving verbatim (odd/invented) spelling, then judge
`makes_sense` / `fits_context`. That work can be offloaded to a **free local
model** (e.g. Gemma 3 27B) running on the user's GPU via
[Ollama](https://ollama.com), keeping Claude only for the actual **vision OCR**
(`ai_ocr_page`, which stays on Claude).

The local path is **strictly opt-in**. With no `--*-backend ollama` flag the
importer behaves exactly as before (Claude everywhere). On **any** local failure
it automatically falls back to Claude, so enabling it can never lose quality —
at worst it costs the same Claude call it would have made anyway.

Everything goes through one shared, Streamlit-free client, `local_models.py`
(repo root), which talks to Ollama's **OpenAI-compatible** endpoint. It never
downloads a model — you run `ollama pull` yourself.

## 1. Install the models (you do this, once)

Requires Ollama **0.6+**. The importer/eval never pull models for you.

```bash
# Text: the clean+judge and (optional) continuity judge.
ollama pull gemma3:27b

# Vision (for later app use only — page rotation / crop / wordless detection;
# the importer keeps Claude for OCR). Qwen3-VL is on Ollama; pick a size that
# fits your VRAM (e.g. 32b for a 24 GB card, 8b for less):
ollama pull qwen3-vl:32b
# Fallback if you prefer the previous generation (note the tag has no dash):
ollama pull qwen2.5vl:32b
```

Verified tags (Ollama registry, checked 2026-07):
`gemma3:27b` (17 GB, 128K ctx, text+image), `qwen3-vl` (library offers
`2b/4b/8b/30b/32b/235b`; `qwen3-vl:32b` for a 24 GB card), fallback
`qwen2.5vl`. See <https://ollama.com/library/gemma3/tags> and
<https://ollama.com/library/qwen3-vl/tags>.

**RTX 3090 (24 GB) fit:** at Q4 quantization Gemma 3 27B and a Qwen3 ~32B VLM
both fit comfortably in 24 GB. Gemma 3 27B is ~17 GB at Ollama's default
quant; a `q4_K_M`/`q4_0` variant of a 32B VLM lands around 18–20 GB.

## 2. Point the importer at the local model

Only the **text** stages have a backend switch; OCR is always Claude.

```bash
PY=./.venv/bin/python

# Offload just the clean+judge pass (the big cost item) to local Gemma:
$PY scripts/import_pilot_data.py --methods-dir ../fair-tales-language-analysis \
    --execute \
    --clean-backend ollama --ollama-clean-model gemma3:27b

# Also offload the (cheaper) neighbour-continuity judge:
$PY scripts/import_pilot_data.py --methods-dir ../fair-tales-language-analysis \
    --execute \
    --clean-backend ollama --continuity-backend ollama \
    --ollama-clean-model gemma3:27b --ollama-continuity-model gemma3:27b
```

### Remote GPU (the 3090 is a different machine)

Ollama listens on `11434`. Start it on the GPU host bound to the network
(`OLLAMA_HOST=0.0.0.0:11434 ollama serve`) and point the importer at it:

```bash
$PY scripts/import_pilot_data.py --methods-dir ../fair-tales-language-analysis \
    --execute --clean-backend ollama \
    --ollama-url http://<gpu-host>:11434/v1 \
    --ollama-clean-model gemma3:27b
```

### Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--clean-backend {claude,ollama}` | `claude` | Backend for the clean+judge pass. |
| `--continuity-backend {claude,ollama}` | `claude` | Backend for the continuity judge. |
| `--ollama-url URL` | `http://localhost:11434/v1` | Ollama OpenAI-compatible base URL. |
| `--ollama-clean-model NAME` | `gemma3:27b` | Local model for clean+judge. |
| `--ollama-continuity-model NAME` | `gemma3:27b` | Local model for the continuity judge. |

### Fallback behaviour (why this is safe)

* **clean+judge:** the SAME prompt, schema and token-subset guard run on local
  output. If the local call errors (connection/timeout/bad JSON →
  `LocalModelError`) **or** its clean fails the token-subset guard (it added or
  altered a word), the page is **re-done on Claude** and the fallback is logged.
  So local output is only ever accepted when it is at least as safe as Claude's.
* **continuity judge:** any local failure degrades to the existing safe verdict
  that **forces OCR** (never a false skip / never lost text), exactly like a
  Claude-judge failure.
* Result-cache keys include the backend, so switching backends between runs
  never returns a stale cross-backend result.

## 3. Validate quality first (recommended)

Before trusting local output on a real import, run the eval — it compares local
vs Claude clean+judge on real pilot pages with a trustworthy text layer:

```bash
./.venv/bin/python scripts/eval_local_vs_claude.py \
    --methods-dir ../fair-tales-language-analysis \
    --n 40 \
    --ollama-clean-model gemma3:27b \
    --out /tmp/eval_local_vs_claude
```

It reports the local token-subset-guard pass rate, `makes_sense` /
`fits_context` agreement with Claude, cleaned-text similarity/containment, and
lists every judge disagreement and guard failure; per-page results are written
as JSON + CSV to `--out`. If Ollama is unreachable or the model isn't pulled, it
prints a "start Ollama / `ollama pull`" message and exits cleanly.
