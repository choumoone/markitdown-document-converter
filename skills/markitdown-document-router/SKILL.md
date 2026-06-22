---
name: markitdown-document-router
description: Classify mixed document folders locally before conversion and route each bucket to the smallest MarkItDown specialist skill. Use for broad, heterogeneous, unknown, or expensive corpora where simple files should convert directly while scans, image-only PDFs, table-heavy PDFs, archives, and unsupported files are separated before work begins. Automatically process only the cheap local path; report complex buckets and obtain explicit user authorization before invoking their specialist workflows.
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

## Authorization gate

Automatically run classification and the default cheap buckets. If `needs_ocr`, `pdf_table`, `archive`, or `manual_review` is non-empty, stop after the cheap pass and ask before invoking a specialist.

The authorization request must state:

- bucket and file count;
- specialist skill to invoke;
- whether the next stage is local or uses an external provider;
- estimated file/page/API-call scope when known;
- why the stage is necessary and what output it will modify.

Wait for an explicit user reply before continuing. Approval covers only the described scope. Ask again if the scope grows, an external or paid model becomes necessary, credentials will be used, or destructive cleanup is proposed. A direct user request to use a named specialist counts as approval for that specialist's local deterministic steps, but never for paid/external calls.

## Route

Execute buckets in this order:

1. Send `simple_direct`, `pdf_text`, and `legacy_office` to `$markitdown-document-converter`.
2. After authorization, send `needs_ocr` to `$markitdown-ocr`.
3. After authorization, send `pdf_table` to `$markitdown-pdf-table-repair`.
4. After authorization, expand `archive` with the core converter, then classify extracted leaf files if specialist work remains.
5. Report `manual_review` and `unsupported` without guessing.
6. Invoke `$markitdown-corpus-audit` only when the user requests an accepted LLM-ready corpus.
7. Invoke `$markitdown-publisher` only when HTML or DOCX output is requested.

The plan records a `skill` and `reason` for each file. Treat classification as triage, not final QA.

## Context budget

- Do not list every source file in chat.
- Do not open the full plan when counts are enough.
- Process simple files before specialist buckets.
- Keep paid OCR and vision work limited to explicitly routed files.
