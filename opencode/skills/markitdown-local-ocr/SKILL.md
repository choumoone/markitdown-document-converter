---
name: markitdown-local-ocr
description: OCR authorized scanned images or PDFs with the local Ollama Qwen3.6 vision model and return traceable Markdown. Use only after a user explicitly approves the file/page scope. Supports standalone files and manifest-based MarkItDown corpora; never uses a paid or external provider.
---

# MarkItDown Local OCR

Require explicit authorization for the named files and page limit. Never infer authorization from a general conversion request.

## Standalone file

```powershell
& "$HOME\.codex\skill-envs\markitdown-document-converter\.venv\Scripts\python.exe" `
  "$HOME\.config\opencode\skills\markitdown-local-ocr\scripts\run_local_ocr.py" `
  --authorized --input "<image-or-pdf>" --output "<output.md>" --max-pages <N>
```

## Existing corpus

```powershell
& "$HOME\.codex\skill-envs\markitdown-document-converter\.venv\Scripts\python.exe" `
  "$HOME\.config\opencode\skills\markitdown-local-ocr\scripts\run_local_ocr.py" `
  --authorized --kb "<kb>" --limit <files> --max-pages <pages-per-file>
```

Use model `qwen3.6:27b` by default. The wrapper calls only `http://localhost:11434/v1`.

Report elapsed time, pages processed, output path, and that human spot-checking remains required. Do not inspect every output page or rerun successful OCR. Stop on an error or if the requested scope exceeds the authorized limits.
