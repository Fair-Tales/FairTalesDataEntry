#!/usr/bin/env python3
"""Quality eval: local (Ollama) vs Claude for the importer's clean+judge pass.

Before we trust a free local model (Gemma 3 27B on the user's GPU) with the
importer's expensive clean + coherence-judge pass (~67% of API cost), this
harness validates it against Claude on real pilot-corpus pages — the same rigor
as the earlier OCR eval.

What it does
------------
1. Samples ``N`` STORY pages from the pilot PDFs that have a TRUSTWORTHY text
   layer (reusing the importer's ``analyse_pdf`` text extraction), so the input
   to clean+judge is real, clean-ish book text with genuine neighbour context.
2. Runs the SAME clean+judge prompt/schema through BOTH backends: Claude
   (``--clean-model``) and the local model (``--ollama-clean-model``).
3. Scores, per page and in aggregate:
     (a) does the local output pass the importer's token-subset guard
         (``clean_kept`` — cleaned text may only REMOVE words, never add/alter);
     (b) cleaned-text similarity/containment of local vs Claude and vs the
         original extracted text;
     (c) coherence-judgement agreement — do local ``makes_sense`` /
         ``fits_context`` match Claude's on the same page (agreement rate +
         disagreement examples).
   A clear report is printed and per-page results are written as JSON + CSV.

If Ollama is unreachable or the model is missing, prints a "start Ollama and
``ollama pull <model>`` first" message and exits cleanly (never crashes).

Run
---
    ./.venv/bin/python scripts/eval_local_vs_claude.py \
        --methods-dir ../fair-tales-language-analysis \
        --n 40 \
        --ollama-clean-model gemma3:27b \
        --out /tmp/eval_local_vs_claude

Point at a remote GPU with ``--ollama-url http://<host>:11434/v1``. Requires
``.streamlit/secrets.toml`` (for the Claude side), so run it from the main
checkout where the real secrets exist — an isolated worktree has none.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timezone

# The importer + local client live at repo root / scripts/; add both to the path.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
for _p in (_REPO_ROOT, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import local_models  # noqa: E402
import import_pilot_data as imp  # noqa: E402

# Minimum non-whitespace chars for a page's text layer to count as a TRUSTWORTHY
# clean+judge input (stricter than the importer's TEXT_LAYER_MIN_CHARS=20 so we
# eval on pages with real, substantive text rather than a stray word).
EVAL_TEXT_MIN_CHARS = 60


def _clean_context_for_page(page_texts: list, story_idx: list, pos: int) -> tuple[str, str]:
    """Build the (prev_ctx, next_ctx) the importer's pass 2 would use for a page.

    ``story_idx`` is the list of physical page indices that are story pages;
    ``pos`` is this page's position within it. Uses the neighbours' raw text-layer
    text and the importer's position-derived ``neighbour_context`` (#73 M4).
    """
    last = len(story_idx) - 1
    prev_raw = page_texts[story_idx[pos - 1]] if pos > 0 else ""
    next_raw = page_texts[story_idx[pos + 1]] if pos < last else ""
    prev_ctx = imp.neighbour_context(prev_raw, is_previous=True, at_book_edge=(pos == 0))
    next_ctx = imp.neighbour_context(next_raw, is_previous=False, at_book_edge=(pos == last))
    return prev_ctx, next_ctx


def collect_samples(methods_dir: str, excel_path: str, pdf_dir: str, n: int, seed: int) -> list:
    """Return up to ``n`` sampled clean+judge inputs from the pilot corpus.

    Each item: ``{title, page_number, text, prev_ctx, next_ctx}``. Pages are
    STORY pages (per the Excel page range) whose OWN text layer is trustworthy
    (>= ``EVAL_TEXT_MIN_CHARS``). Sampling is seeded for reproducibility.
    """
    excel_books = imp.load_excel_books(excel_path)
    range_by_norm = {b.norm: b.page_range for b in excel_books}
    title_by_norm = {b.norm: b.title for b in excel_books}
    pdf_map, _ = imp.load_pdfs(pdf_dir)

    candidates: list = []
    for norm, pdf_path in sorted(pdf_map.items()):
        page_range = range_by_norm.get(norm)
        title = title_by_norm.get(norm) or os.path.basename(pdf_path)
        try:
            analysis = imp.analyse_pdf(pdf_path)
        except Exception as exc:  # noqa: BLE001 - a bad PDF just contributes no samples
            print(f"  (skip {os.path.basename(pdf_path)}: {type(exc).__name__}: {exc})",
                  file=sys.stderr)
            continue
        page_texts = analysis["page_texts"]
        story_idx = [i for i in range(len(page_texts)) if imp.is_story_page(i + 1, page_range)]
        for pos, i in enumerate(story_idx):
            text = (page_texts[i] or "").strip()
            if len(text.replace(" ", "")) < EVAL_TEXT_MIN_CHARS:
                continue
            prev_ctx, next_ctx = _clean_context_for_page(page_texts, story_idx, pos)
            candidates.append({
                "title": title,
                "page_number": i + 1,
                "text": text,
                "prev_ctx": prev_ctx,
                "next_ctx": next_ctx,
            })

    rng = random.Random(seed)
    rng.shuffle(candidates)
    return candidates[:n]


def _run_claude(client, text: str, context_block: str, model: str) -> dict:
    """Claude clean+judge -> the raw parsed reply merged with post-guard status."""
    content = [
        {"type": "text", "text": imp.CLEAN_STATIC_PROMPT, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": context_block},
    ]
    data, _raw = imp._ai_json_call(
        client, model=model, max_tokens=2048, content_blocks=content, schema=imp.CLEAN_SCHEMA
    )
    return _shape(data, text)


def _run_local(url: str, model: str, text: str, context_block: str) -> dict:
    """Local clean+judge -> the raw parsed reply merged with post-guard status."""
    data = local_models.chat_json(
        url, model, imp.CLEAN_STATIC_PROMPT, context_block, schema=imp.CLEAN_SCHEMA,
        max_tokens=2048,
    )
    return _shape(data, text)


def _shape(data, text: str) -> dict:
    """Normalise a parsed clean+judge reply into the fields the eval compares."""
    final_text, status, needs_review, priority, note = imp._finalise_clean_judge(data, text)
    cleaned_raw = str((data or {}).get("cleaned_text") or "").strip()
    return {
        "makes_sense": imp._as_bool((data or {}).get("makes_sense", True), True),
        "fits_context": imp._as_bool((data or {}).get("fits_context", True), True),
        "cleaned_raw": cleaned_raw,      # exactly what the model returned
        "final_text": final_text,        # after the token-subset guard
        "status": status,                # cleaned/unchanged/rejected/failed
        "guard_passed": status in ("cleaned", "unchanged"),
        "note": note,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--methods-dir", default="../fair-tales-language-analysis",
                   help="Pilot-corpus checkout (holds the xlsx + text_pdfs/).")
    p.add_argument("--excel", help="Override path to Book-List-Final-NONA.xlsx.")
    p.add_argument("--pdf-dir", help="Override path to text_pdfs/.")
    p.add_argument("--secrets", default=imp.DEFAULT_SECRETS,
                   help=f"secrets.toml path (default: {imp.DEFAULT_SECRETS}).")
    p.add_argument("--n", type=int, default=40, help="Number of pages to sample (default 40).")
    p.add_argument("--seed", type=int, default=1234, help="Sampling seed (default 1234).")
    p.add_argument("--clean-model", default=imp.DEFAULT_CLEAN_MODEL,
                   help=f"Claude clean+judge model (default: {imp.DEFAULT_CLEAN_MODEL}).")
    p.add_argument("--ollama-url", default=imp.DEFAULT_OLLAMA_URL,
                   help=f"Ollama OpenAI-compatible base URL (default: {imp.DEFAULT_OLLAMA_URL}).")
    p.add_argument("--ollama-clean-model", default=imp.DEFAULT_OLLAMA_CLEAN_MODEL,
                   help=f"Local clean+judge model (default: {imp.DEFAULT_OLLAMA_CLEAN_MODEL}).")
    p.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "eval_local_vs_claude"),
                   help="Output dir for the per-page JSON + CSV report.")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    methods = args.methods_dir
    excel_path = args.excel or os.path.join(methods, "Book-List-Final-NONA.xlsx")
    pdf_dir = args.pdf_dir or os.path.join(methods, "text_pdfs")

    for label, path in [("excel", excel_path), ("pdf dir", pdf_dir)]:
        if not os.path.exists(path):
            print(f"ERROR: {label} not found: {path}", file=sys.stderr)
            return 2

    # 1. Ollama reachable + model present? Skip cleanly if not.
    if not local_models.ping(args.ollama_url, args.ollama_clean_model):
        print("=" * 78)
        print("Ollama endpoint or model not available — nothing to eval.")
        print(f"  endpoint : {args.ollama_url}")
        print(f"  model    : {args.ollama_clean_model}")
        print("  Start Ollama and pull the model first, e.g.:")
        print("      ollama serve   # if not already running")
        print(f"      ollama pull {args.ollama_clean_model}")
        print("  (Point at a remote GPU with --ollama-url http://<host>:11434/v1.)")
        print("=" * 78)
        return 0

    # 2. Claude client (needed for the reference side).
    if not os.path.exists(args.secrets):
        print(f"ERROR: secrets file not found: {args.secrets} — the Claude side "
              "needs it. Run from the main checkout where secrets exist.", file=sys.stderr)
        return 2
    secrets = imp.load_secrets(args.secrets)
    if "ANTHROPIC_API_KEY" not in secrets:
        print("ERROR: no ANTHROPIC_API_KEY in secrets; cannot run the Claude side.",
              file=sys.stderr)
        return 2
    import anthropic

    client = anthropic.Anthropic(api_key=secrets["ANTHROPIC_API_KEY"])

    # 3. Sample pages.
    samples = collect_samples(methods, excel_path, pdf_dir, args.n, args.seed)
    if not samples:
        print("No trustworthy-text-layer story pages found to sample.", file=sys.stderr)
        return 1

    print("=" * 78)
    print("EVAL — local (Ollama) vs Claude clean+judge")
    print("=" * 78)
    print(f"  claude model : {args.clean_model}")
    print(f"  local model  : {args.ollama_clean_model} @ {args.ollama_url}")
    print(f"  sampled pages: {len(samples)} (seed {args.seed})")
    print()

    rows: list = []
    for idx, s in enumerate(samples, start=1):
        context_block = imp._build_clean_context_block(s["text"], s["prev_ctx"], s["next_ctx"])
        try:
            claude = _run_claude(client, s["text"], context_block, args.clean_model)
        except Exception as exc:  # noqa: BLE001 - record + skip this page; don't abort the eval
            print(f"  [{idx}/{len(samples)}] {s['title']} p{s['page_number']}: "
                  f"Claude call failed ({type(exc).__name__}: {exc}); skipping", file=sys.stderr)
            continue
        try:
            loc = _run_local(args.ollama_url, args.ollama_clean_model, s["text"], context_block)
            local_error = ""
        except local_models.LocalModelError as exc:
            print(f"  [{idx}/{len(samples)}] {s['title']} p{s['page_number']}: "
                  f"local call failed ({exc})", file=sys.stderr)
            loc = None
            local_error = str(exc)

        row = {
            "title": s["title"],
            "page_number": s["page_number"],
            "original_chars": len(s["text"]),
            "local_error": local_error,
            "claude_status": claude["status"],
            "claude_makes_sense": claude["makes_sense"],
            "claude_fits_context": claude["fits_context"],
        }
        if loc is not None:
            row.update({
                "local_status": loc["status"],
                "local_guard_passed": loc["guard_passed"],
                "local_makes_sense": loc["makes_sense"],
                "local_fits_context": loc["fits_context"],
                "makes_sense_agree": loc["makes_sense"] == claude["makes_sense"],
                "fits_context_agree": loc["fits_context"] == claude["fits_context"],
                # Similarity/containment of the cleaned outputs.
                "sim_local_vs_claude": round(
                    imp.text_similarity(loc["final_text"], claude["final_text"]), 4),
                "sim_local_vs_original": round(
                    imp.text_similarity(loc["final_text"], s["text"]), 4),
                "sim_claude_vs_original": round(
                    imp.text_similarity(claude["final_text"], s["text"]), 4),
                # How much of Claude's cleaned text the local output captured.
                "contain_claude_in_local": round(
                    imp.text_containment(loc["final_text"], claude["final_text"]), 4),
            })
        rows.append(row)
        print(f"  [{idx}/{len(samples)}] {s['title'][:40]:40} p{s['page_number']:<3} "
              f"claude[ms={claude['makes_sense']:d} fc={claude['fits_context']:d} "
              f"{claude['status']:9}] "
              + ("local[FAILED]" if loc is None else
                 f"local[ms={loc['makes_sense']:d} fc={loc['fits_context']:d} "
                 f"{loc['status']:9} guard={loc['guard_passed']:d}]"))

    # 4. Aggregate report.
    scored = [r for r in rows if not r["local_error"]]
    n_scored = len(scored)
    print()
    print("=" * 78)
    print("RESULTS")
    print("=" * 78)
    print(f"  pages scored (local produced usable JSON) : {n_scored} / {len(rows)}")
    n_local_failed = sum(1 for r in rows if r["local_error"])
    if n_local_failed:
        print(f"  local calls that failed / gave no JSON    : {n_local_failed} "
              "(the importer would FALL BACK to Claude for these)")
    if n_scored:
        guard = sum(1 for r in scored if r["local_guard_passed"])
        ms_agree = sum(1 for r in scored if r["makes_sense_agree"])
        fc_agree = sum(1 for r in scored if r["fits_context_agree"])
        mean_sim = sum(r["sim_local_vs_claude"] for r in scored) / n_scored
        mean_contain = sum(r["contain_claude_in_local"] for r in scored) / n_scored
        mean_sim_c_o = sum(r["sim_claude_vs_original"] for r in scored) / n_scored
        mean_sim_l_o = sum(r["sim_local_vs_original"] for r in scored) / n_scored
        print(f"  local passes token-subset guard           : {guard}/{n_scored} "
              f"({guard / n_scored:.0%})")
        print(f"  makes_sense agreement with Claude         : {ms_agree}/{n_scored} "
              f"({ms_agree / n_scored:.0%})")
        print(f"  fits_context agreement with Claude        : {fc_agree}/{n_scored} "
              f"({fc_agree / n_scored:.0%})")
        print(f"  mean similarity (local cleaned vs Claude) : {mean_sim:.0%}")
        print(f"  mean containment (Claude words in local)  : {mean_contain:.0%}")
        print(f"  mean similarity vs original — claude {mean_sim_c_o:.0%} / "
              f"local {mean_sim_l_o:.0%}")
        print()
        disagreements = [r for r in scored
                         if not r["makes_sense_agree"] or not r["fits_context_agree"]]
        if disagreements:
            print(f"  JUDGE DISAGREEMENTS ({len(disagreements)}):")
            for r in disagreements:
                print(f"    - {r['title'][:44]} p{r['page_number']}: "
                      f"makes_sense claude={r['claude_makes_sense']:d}/local={r['local_makes_sense']:d}  "
                      f"fits_context claude={r['claude_fits_context']:d}/local={r['local_fits_context']:d}")
        guard_fails = [r for r in scored if not r["local_guard_passed"]]
        if guard_fails:
            print(f"  LOCAL GUARD FAILURES ({len(guard_fails)}) — importer would re-do on Claude:")
            for r in guard_fails:
                print(f"    - {r['title'][:44]} p{r['page_number']}: status={r['local_status']}")
    print()

    # 5. Write per-page JSON + CSV.
    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = os.path.join(args.out, f"eval_local_vs_claude_{stamp}.json")
    csv_path = os.path.join(args.out, f"eval_local_vs_claude_{stamp}.csv")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({
            "claude_model": args.clean_model,
            "local_model": args.ollama_clean_model,
            "ollama_url": args.ollama_url,
            "seed": args.seed,
            "n_requested": args.n,
            "rows": rows,
        }, fh, indent=2)
    fieldnames = sorted({k for r in rows for k in r})
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"  wrote {json_path}")
    print(f"  wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
