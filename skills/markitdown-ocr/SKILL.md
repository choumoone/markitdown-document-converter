---
name: markitdown-ocr
description: Backfill Markdown for scanned PDFs, image-only pages, and standalone images with the local Ollama Qwen3.6 vision model after routing identifies missing embedded text. Use automatically for needs_ocr files, failed or empty conversions, visual table transcription, and OCR requests. Keep outputs traceable and require Codex spot-checking; ask only before switching to a paid or external provider.
---

# MarkItDown OCR

Use the shared engine at `~/.codex/skills/markitdown-document-converter/scripts`. Baseline conversion must create `manifest.jsonl` before backfill.

Set `SCRIPTS` to that directory and use the converter environment's Python as `PYTHON`.

## Local Qwen worker

Use local Ollama `qwen3.6:27b` as the default vision worker. Do not route through an OpenCode agent; call the compact wrapper directly to avoid agent startup and repeated context.

For a standalone image or PDF:

```powershell
& $PYTHON "$SCRIPTS/local_qwen_ocr.py" --input "<source>" --output "<output.md>" --max-pages <N>
```

For an existing converted corpus:

```powershell
& $PYTHON "$SCRIPTS/local_qwen_ocr.py" --kb "<kb>" --limit <files> --max-pages <pages-per-file>
```

Process one representative page first, compare visible text, numbers, dates, and table shape, then continue the routed batch when the sample is sound. Local Qwen calls inside the requested paths do not require a separate user prompt.

## Quality boundary

- Preserve headings, identifiers, dates, amounts, page order, and Markdown table cells.
- Mark local vision output `needs_human_spotcheck`.
- Let Codex inspect representative pages and all flagged anomalies; do not ask Qwen to certify its own work.
- Use deterministic scripts for metadata, source paths, manifests, and chunks.

Use PaddleOCR as a secondary local comparison when useful. Ask before switching to any paid/external OCR or vision provider.

After OCR, inspect empty/short-output QA rows and rebuild chunks. Do not OCR text-based PDFs or files routed to table repair.
