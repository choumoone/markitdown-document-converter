---
name: markitdown-corpus-audit
description: Assemble and verify the final LLM-ready Markdown corpus after conversion, OCR, and any PDF table repair are complete. Use for final acceptance, deliverable cleanup, manifest-to-document reconciliation, chunk rebuilding, expected-count validation, or requests to confirm that a corpus is genuinely ready for import.
---

# MarkItDown Corpus Audit

Use the shared engine at `~/.codex/skills/markitdown-document-converter/scripts`. Do not invoke this skill for a routine file conversion or Markdown cleanup.

Set `SCRIPTS` to that directory and use the converter environment's Python as `PYTHON`.

## Authorization gate

When this stage was not explicitly requested, report the candidate document count, target output directory, expected chunk rebuild, and validation artifacts, then wait for authorization. An explicit request to build or verify the final corpus authorizes non-destructive local assembly and audit for the stated paths.

Deleting process outputs, replacing an accepted final folder, or removing QA evidence requires separate explicit authorization after the clean audit result is shown.

## Assemble

```powershell
& $PYTHON "$SCRIPTS/build_llm_ready_corpus.py" --kb "<kb>" --require-ready
```

This merges baseline, OCR, and page-aware repaired outputs according to the manifest. Preserve source metadata and repair markers.

## Verify

```powershell
& $PYTHON "$SCRIPTS/postprocess_markdown.py" --input "<documents>" --chunks-out "<chunks.jsonl>" --chunks-only
& $PYTHON "$SCRIPTS/final_corpus_audit.py" --documents "<documents>" --chunks "<chunks.jsonl>" --report "<FINAL_VALIDATION.md>" --require-clean
```

Provide `--expected-documents N` when a source inventory establishes an exact count. For PDF-table corpora, require the specialist table audit to pass before final acceptance.

Keep only verified final deliverables when cleanup is requested: final `documents/`, `chunks.jsonl`, validation report, and any traceability metadata the report references.
