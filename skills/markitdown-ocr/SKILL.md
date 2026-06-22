---
name: markitdown-ocr
description: Backfill Markdown for scanned PDFs, image-only pages, and standalone images after local routing identifies missing embedded text. Use for needs_ocr files, failed or empty conversions, and explicit OCR requests. Prefer local PaddleOCR first and use an OpenAI-compatible vision model only for a small validated subset.
---

# MarkItDown OCR

Use the shared engine at `~/.codex/skills/markitdown-document-converter/scripts`. Baseline conversion must create `manifest.jsonl` before backfill.

Set `SCRIPTS` to that directory and use the converter environment's Python as `PYTHON`.

## Local first

Install the optional OCR environment once if needed:

```powershell
python "$SCRIPTS/bootstrap_env.py" --with-paddleocr
```

Dry-run the target set, then process a small sample before the full routed bucket:

```powershell
& $PYTHON "$SCRIPTS/paddleocr_backfill.py" --kb "<kb>" --dry-run
& $PYTHON "$SCRIPTS/paddleocr_backfill.py" --kb "<kb>" --limit 3
& $PYTHON "$SCRIPTS/paddleocr_backfill.py" --kb "<kb>" --rebuild-chunks
```

## Vision fallback

Use `ocr_backfill.py` only when local OCR is materially inadequate. Validate one file first, cap pages and file count, and stop on narration, authentication errors, or malformed output.

After OCR, inspect empty/short-output QA rows and rebuild chunks. Do not OCR text-based PDFs or files routed to table repair.
