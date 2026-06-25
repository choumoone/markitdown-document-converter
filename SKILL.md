---
name: markitdown-document-converter
description: Convert ordinary documents and clean existing Markdown with a fast MarkItDown-centered path. Use for direct conversion of text, Markdown, HTML, DOCX, XLSX, PPTX, text-based PDF, and legacy Office files, or for deterministic Markdown cleanup and chunk rebuilding. For mixed corpora classify first with markitdown-document-router; use the separate OCR, PDF-table-repair, corpus-audit, or publisher skills only when their specialist work is actually required.
---

# MarkItDown Document Converter

Use this skill for the cheap baseline pass. Keep OCR, table repair, final corpus audit, and publishing out of this context unless the task explicitly needs them.

## Paths

```text
SKILL_DIR=~/.codex/skills/markitdown-document-converter
SCRIPTS=~/.codex/skills/markitdown-document-converter/scripts
PYTHON=~/.codex/skill-envs/markitdown-document-converter/.venv/Scripts/python.exe
```

On non-Windows systems use the environment's `bin/python`. If the environment is missing, run `python scripts/bootstrap_env.py` once.

## Fast path

### Clean Markdown only

Run one deterministic command. Do not inspect every heading, paragraph, or chunk unless the command reports a failure.

```powershell
& $PYTHON "$SCRIPTS/postprocess_markdown.py" --input "<file-or-folder>"
```

Add `--chunks-out <chunks.jsonl>` when chunks are required. Use `--chunks-only` to rebuild chunks without rewriting Markdown.

### Convert a file or folder

```powershell
& $PYTHON "$SCRIPTS/convert_corpus.py" --source "<source>" --output "<output>" --quiet
```

Use `--dry-run` for discovery, `--limit N` for a sample, and `--skip-existing` when resuming. The converter writes `documents/`, `manifest.jsonl`, QA reports, and optionally `chunks.jsonl`.

When a router plan exists, add `--route-plan <route-plan.json>` to batch-convert only the default cheap buckets. Repeat `--route-bucket <name>` to override that selection.

## Escalation

- Use `$markitdown-document-router` before converting a broad or mixed folder.
- Automatically delegate scanned pages and image-only documents to the local Ollama `qwen3.6:27b` vision worker through `$markitdown-ocr`.
- Automatically run local archive expansion, local PDF-table repair, and local QA when they stay inside the requested source and output paths.
- Ask only before using a paid/external provider, expanding beyond the requested paths, overwriting accepted deliverables, or deleting files.
- Use `$markitdown-ocr` only for scans, images, or low-text PDFs.
- Use `$markitdown-pdf-table-repair` only when PDF tables require page-aware repair or source recall checks.
- Use `$markitdown-corpus-audit` only for final LLM-ready assembly and acceptance.
- Use `$markitdown-publisher` for Markdown/HTML/DOCX publishing.

## Context budget

- For a single Markdown cleanup, target one execution and one compact verification.
- Read summaries and error rows, not complete manifests or file trees.
- Do not print full Markdown, chunks, or per-file success logs into the conversation.
- Escalate only the files named by the router or QA reports.
- Use local Qwen for visual transcription and first-pass review; keep ordinary cleanup, path repair, and chunk generation deterministic.
