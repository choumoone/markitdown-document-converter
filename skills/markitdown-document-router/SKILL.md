---
name: markitdown-document-router
description: Classify mixed document folders locally before conversion and route each bucket to the smallest MarkItDown specialist skill. Use for broad, heterogeneous, unknown, or expensive corpora where simple files convert directly while scans, image-only PDFs, table-heavy PDFs, archives, and unsupported files are separated. Automatically use local deterministic tools and the local Ollama Qwen3.6 vision worker; request authorization only for paid/external services, destructive cleanup, or work outside the requested paths.
---

# MarkItDown Document Router

Classify once without an LLM, then load only the skills needed by the resulting buckets.

## Classify

```powershell
$ROUTER = "$HOME/.codex/skills/markitdown-document-router"
$PYTHON = "$HOME/.codex/skill-envs/markitdown-document-converter/.venv/Scripts/python.exe"
& $PYTHON "$ROUTER/scripts/classify_documents.py" --source "<source>" --output "<output>/route-plan.json"
```

Read only `counts`, `recommended_order`, and non-empty bucket names first. Inspect individual entries only for a bucket that needs action or manual review.

Convert the cheap buckets in one batch before loading specialist skills:

```powershell
$SCRIPTS = "$HOME/.codex/skills/markitdown-document-converter/scripts"
& $PYTHON "$SCRIPTS/convert_corpus.py" --source "<source>" --output "<output>" --route-plan "<output>/route-plan.json" --quiet
```

The default route-plan filter converts only `simple_direct`, `pdf_text`, and `legacy_office`. Add repeated `--route-bucket <name>` only when intentionally processing another bucket.

## Delegation policy

Automatically run classification and the default cheap buckets. Continue local branches without asking:

- send `needs_ocr` to the local `qwen3.6:27b` worker through `$markitdown-ocr`;
- send `pdf_table` to local page-aware repair and local audits;
- expand `archive`, then classify extracted leaves;
- use local Qwen for first-pass visual transcription or semantic spot-checking;
- keep deterministic scripts authoritative for conversion, metadata, paths, and chunks.

Ask before using a paid/external provider, reading outside the requested source scope, overwriting an accepted final directory, or deleting files. Local Qwen/Ollama calls do not require a separate authorization prompt.

## Route

Execute buckets in this order:

1. Send `simple_direct`, `pdf_text`, and `legacy_office` to `$markitdown-document-converter`.
2. Send `needs_ocr` automatically to `$markitdown-ocr`.
3. Send `pdf_table` automatically to `$markitdown-pdf-table-repair`.
4. Expand `archive` automatically with the core converter, then classify extracted leaf files.
5. Report `manual_review` and `unsupported` without guessing.
6. Invoke `$markitdown-corpus-audit` only when the user requests an accepted LLM-ready corpus.
7. Invoke `$markitdown-publisher` only when HTML or DOCX output is requested.

The plan records a `skill` and `reason` for each file. Treat classification as triage, not final QA.

## Context budget

- Do not list every source file in chat.
- Do not open the full plan when counts are enough.
- Process simple files before specialist buckets.
- Keep paid OCR and vision work limited to explicitly routed files.
