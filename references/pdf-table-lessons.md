# PDF Table Conversion Lessons

## What Went Wrong

- Plain MarkItDown PDF output can flatten complex tables into ordinary text, especially in policy PDFs with merged cells and multi-page tables.
- Appending extracted table sidecars to the end of Markdown is not a repair. It loses source position and confuses retrieval.
- Moving extracted tables to the top of a document is also not a repair. Tables must be inserted near their original source-page location.
- A table-count match is necessary but not sufficient. It proves coverage, not cell-level accuracy.
- Console mojibake on Windows does not necessarily mean the Markdown file is corrupted. Verify by reading files as UTF-8 and comparing rendered source pages.
- Vision/OCR models are not a bulk replacement by default. Use samples first, then apply selectively to pages where coordinate extraction fails.

## Correct Workflow

1. Run `pdf_table_preflight.py` before accepting PDF conversion quality.
2. Run `convert_corpus.py` for the base corpus.
3. Run `pdf_page_table_repair.py` for page-aware PDF Markdown.
4. Run `build_llm_ready_corpus.py` to create a single import target.
5. Inspect QA reports before using the corpus:
   - `qa/pdf_table_preflight.*`
   - `qa/pdf_page_table_repair_report.*`
   - `qa/llm_ready_corpus_report.md`
6. For high-value documents, compare table pages against rendered source images.
7. Use MiniMax-M3 or another vision model only for pages whose table structure is missing or obviously wrong after page-aware repair.

## Acceptance Language

Use precise status wording:

- "Batch repair completed" means every PDF was processed and counted.
- "Count reconciliation passed" means source preflight and repaired output table counts match.
- "Spot-check passed" means selected documents/pages were manually inspected.
- "Fully verified" should be used only after required source-page or cell-level review is complete.

## Final Import Target

For PDF-heavy corpora, do not import raw `documents/` directly. Use:

`documents_llm_ready/documents/`

This directory uses page-aware PDF Markdown where available and original non-PDF Markdown otherwise.
