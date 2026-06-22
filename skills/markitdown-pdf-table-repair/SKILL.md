---
name: markitdown-pdf-table-repair
description: Repair table-heavy PDFs after baseline MarkItDown conversion with page-aware extraction, local quality gates, source-content recall checks, and optional targeted MiniMax vision repair. Use only for PDFs routed as pdf_table or for explicit requests to preserve and verify complex tables, forms, merged cells, or page-level traceability.
---

# MarkItDown PDF Table Repair

Use the shared engine at `~/.codex/skills/markitdown-document-converter/scripts`. Start only after baseline conversion has produced a knowledge-base folder with `manifest.jsonl`.

Set `SCRIPTS` to that directory and use the converter environment's Python as `PYTHON`.

## Authorization gate

When the router selects this skill, report the number of PDFs and sampled table pages, describe the local repair outputs, and wait for explicit authorization. An explicit table-repair request in the current user message authorizes local preflight, page-aware repair, and local audits for the stated scope.

Never treat local repair approval as MiniMax or other vision-model approval. Before external table repair, report the provider/model, exact candidate files/pages, estimated maximum calls, and why local extraction failed; wait for separate explicit authorization.

## Local repair

```powershell
& $PYTHON "$SCRIPTS/pdf_table_preflight.py" --source "<source>" --output "<kb>/qa/preflight"
& $PYTHON "$SCRIPTS/pdf_page_table_repair.py" --kb "<kb>" --rebuild-chunks
& $PYTHON "$SCRIPTS/pdf_table_quality_audit.py" --kb "<kb>"
```

Build the page-aware result into the final document tree only after reviewing hard audit flags:

```powershell
& $PYTHON "$SCRIPTS/build_llm_ready_corpus.py" --kb "<kb>"
& $PYTHON "$SCRIPTS/source_table_content_audit.py" --kb "<kb>" --require-clean
```

## Vision escalation

Use `dual_engine_table_prefilter.py` to narrow candidates. Call `minimax_table_enhance.py` and `minimax_apply_table_repair.py` only for confirmed table blocks that local extraction cannot preserve. Never send the entire corpus to vision repair.

After applying repairs, rerun the local quality audit, source-content audit, and chunk rebuild. Counts alone are not acceptance: hard audit flags and source recall must pass.

## Context budget

Read QA summaries and flagged rows only. Keep page images, full Markdown, and successful records out of chat unless a flagged table needs visual inspection.
