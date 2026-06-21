# PDF Table Conversion Lessons

## What Went Wrong

- Plain MarkItDown PDF output can flatten complex tables into ordinary text, especially in policy PDFs with merged cells and multi-page tables.
- Appending extracted table sidecars to the end of Markdown is not a repair. It loses source position and confuses retrieval.
- Moving extracted tables to the top of a document is also not a repair. Tables must be inserted near their original source-page location.
- A table-count match is necessary but not sufficient. It proves coverage, not cell-level accuracy.
- Console mojibake on Windows does not necessarily mean the Markdown file is corrupted. Verify by reading files as UTF-8 and comparing rendered source pages.
- Vision/OCR models are not a bulk replacement by default. Use samples first, then apply selectively to pages where coordinate extraction fails.
- `ocr_status=completed` can still hide a title-only or partial document. Reconcile source PDF page count with contiguous `source_page` markers and check per-page text density.
- Coordinate detectors can mistake page headers, highlighted sentences, ruled boxes, and flowchart nodes for tables. Count parity can therefore preserve false positives.
- A flowchart with all labels present but arrows/branches lost is still incorrectly converted.
- A model-rebuilt table can look cleaner while dropping cells or changing responsibility/approval placement. Validate source text agreement before accepting it.

## Correct Workflow

1. Run `pdf_table_preflight.py` before accepting PDF conversion quality.
2. Run `convert_corpus.py` for the base corpus.
3. Run `pdf_page_table_repair.py` for page-aware PDF Markdown.
4. Run `build_llm_ready_corpus.py` to create a single import target.
5. Run `source_table_content_audit.py` against the repaired candidate. Review all low-coverage and non-redetected rows.
6. Render and inspect high-risk pages: scans, forms, wide/long tables, merged cells, cross-page tables, flowcharts, amounts, permissions, dates, and approval symbols.
7. Stage the exact final directory, rebuild chunks with `--chunks-only`, then run `final_corpus_audit.py --require-clean` on those final paths.
8. Inspect QA reports before using the corpus:
   - `qa/pdf_table_preflight.*`
   - `qa/pdf_page_table_repair_report.*`
   - `qa/llm_ready_corpus_report.md`
9. Use MiniMax-M3 or another vision model only for pages whose table structure is missing or obviously wrong after page-aware repair. Accept only high-agreement table overlays; otherwise keep deterministic extraction and queue the page for manual reconstruction.

## Acceptance Language

Use precise status wording:

- "Batch repair completed" means every PDF was processed and counted.
- "Count reconciliation passed" means source preflight and repaired output table counts match.
- "Spot-check passed" means selected documents/pages were manually inspected.
- "Fully verified" should be used only after required source-page or cell-level review is complete.
- "No known table issue after automated coverage checks and targeted source-page review" is the preferred wording when every cell was not manually checked.

## Acceptance Gates

- Counts: source files, Markdown files, unique `doc_id`, and final chunk document coverage reconcile.
- Encoding: UTF-8 valid, no BOM/replacement characters/provider error text.
- OCR: completed status plus page-count/marker/density validation.
- Structure: Markdown table separators and column widths are valid; no orphan fragments.
- Content: source table character coverage passes the chosen threshold; unresolved extraction conflicts remain in a manual-review queue.
- Geometry: targeted visual review confirms merged-cell ownership, cross-page continuity, flowchart order, amounts, dates, permission symbols, and responsibility lanes.
- Delivery: the post-copy final directory passes `final_corpus_audit.py`; only then may temporary evidence be removed.

## Final Import Target

For PDF-heavy corpora, do not import raw `documents/` directly. Use:

`documents_llm_ready/documents/`

This directory uses page-aware PDF Markdown where available and original non-PDF Markdown otherwise.
