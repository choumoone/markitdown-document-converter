---
name: markitdown-document-converter
description: Convert documents, archives, URLs, HTML, and Markdown through a MarkItDown-centered pipeline. Use when Codex needs to batch convert PDF, Word, Excel, PowerPoint, image, HTML, text, ZIP, or RAR inputs to clean traceable Markdown; preserve source metadata; generate manifest/chunks/QA reports; identify files needing OCR/manual review; backfill scanned PDFs/images with local PaddleOCR or OpenAI-compatible vision OCR; enhance complex tables/forms with MiniMax-M3; convert Markdown to polished HTML with bundled Pandoc themes; convert HTML/URL articles back to Markdown with trafilatura/html-to-markdown; or export Markdown to styled DOCX. Do not add domain-specific classification assumptions unless the user explicitly asks for them.
---

# MarkItDown Document Converter

## Purpose

Use this skill to convert individual documents, archives, URLs, or mixed folders into Markdown files with source traceability, then optionally publish that Markdown to HTML or DOCX. The base intake converter is Microsoft MarkItDown; the bundled scripts add batch discovery, archive extraction, Markdown cleanup, frontmatter metadata, retrieval chunks, QA reports, optional OCR backfill, optional table/form enhancement, HTML publishing themes, HTML-to-Markdown recovery, and styled DOCX export.

For scanned PDFs/images, prefer local PaddleOCR when available; use MiniMax-M3 or another OpenAI-compatible vision model for pages where table/form structure matters.

Default behavior is general document conversion. Do not infer business-domain categories unless the user explicitly asks for those labels.

## Decision Tree

- For files/folders/archives that need traceable Markdown for RAG, search, or audit: use `scripts/convert_corpus.py`.
- For PDF corpora where tables may exist, scan first with `scripts/pdf_table_preflight.py` so table-bearing files/pages are known before conversion QA.
- For PDFs whose tables must be usable in the main Markdown, run page-aware table repair with `scripts/pdf_page_table_repair.py` after base conversion and before treating the corpus as verified.
- For final LLM import after table repair, build a merged import directory with `scripts/build_llm_ready_corpus.py` instead of pointing users at the raw `documents/` folder.
- For scanned PDFs/images after initial conversion: use `scripts/paddleocr_backfill.py` first, then `scripts/ocr_backfill.py` only when local OCR is unsuitable.
- For flattened or missing complex table/form structure: use `scripts/minimax_table_enhance.py`.
- For Markdown to readable or publishable HTML: use `scripts/md_to_html.py`.
- For a blog/news/article URL or local HTML back to Markdown: use `scripts/html_to_md.py`.
- For Markdown to a styled Word/DOCX deliverable: use `scripts/md_to_docx.py`.

## Intake Workflow

1. Bootstrap the isolated environment if dependencies are missing:
   `python scripts/bootstrap_env.py`
   If `python` is not on `PATH`, use the Codex bundled Python or another known Python executable.
2. For PDF-heavy folders, scan table-bearing PDFs before conversion QA:
   `python scripts/pdf_table_preflight.py --source "<input_folder>" --output "<output_folder>\qa"`
   This writes `qa/pdf_table_preflight.*` and identifies PDFs/pages that must not be accepted from plain text extraction alone.
3. Convert a file or folder:
   `python scripts/convert_corpus.py --source "<input_file_or_folder>" --output "<output_folder>"`
4. For PDF corpora where tables matter, build page-aware PDF Markdown and chunks:
   `python scripts/pdf_page_table_repair.py --kb "<output_folder>" --rebuild-chunks`
   This writes `documents_page_aware/`, `chunks_page_aware.jsonl`, and `qa/pdf_page_table_repair_report.*`. It uses PDF page coordinates to skip text blocks that overlap detected table bounding boxes and inserts Markdown tables at their page positions. Treat files with detected tables as `needs_human_spotcheck` until source-page QA is complete.
5. Build the importable LLM corpus after PDF table repair:
   `python scripts/build_llm_ready_corpus.py --kb "<output_folder>"`
   This writes `documents_llm_ready/documents/`, `documents_llm_ready/chunks_llm_ready.jsonl`, and `qa/llm_ready_corpus_report.md`, using page-aware PDF Markdown where available and original non-PDF Markdown otherwise.
6. Re-clean Markdown or rebuild chunks when needed:
   `python scripts/postprocess_markdown.py --input "<output_folder>\documents" --chunks-out "<output_folder>\chunks.jsonl"`
7. For scanned PDFs/images, prefer local PaddleOCR:
   `python scripts/bootstrap_env.py --with-paddleocr`
   On Windows CPU, set `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0` before running PaddleOCR if PaddlePaddle raises a oneDNN/PIR error.
8. Backfill records marked `needs_ocr`, `needs_review`, or partial OCR with local PaddleOCR:
   `python scripts/paddleocr_backfill.py --kb "<output_folder>" --rebuild-chunks`
9. Configure OCR credentials only when image files or scanned PDFs need vision-model OCR. Copy the API key to the Windows clipboard, then run:
   `powershell -ExecutionPolicy Bypass -File scripts/set_ocr_secret_from_clipboard.ps1`
10. Backfill OCR with an OpenAI-compatible vision model when PaddleOCR is unsuitable or unavailable:
   `python scripts/ocr_backfill.py --kb "<output_folder>" --model qwen-vl-ocr-latest --rebuild-chunks`
11. For complex Chinese scanned tables/forms, configure MiniMax credentials by copying the MiniMax API key to the Windows clipboard, then run:
   `powershell -ExecutionPolicy Bypass -File scripts/set_minimax_secret_from_clipboard.ps1`
12. Enhance candidate table/form pages with MiniMax-M3:
   `python scripts/minimax_table_enhance.py --kb "<output_folder>" --priority high --select-pages --zip`
   If the page selector misses a known table page, force it with `--manual-page "<file_id>:9,10"`.
13. Inspect `qa\conversion_report.md`, `qa\unresolved.md`, `qa\pdf_table_preflight.md`, `qa\pdf_page_table_repair_report.md`, `qa\llm_ready_corpus_report.md`, `qa\Table_Enhancement_Candidates.md`, and `table_enhanced\00_Table_Enhancement_Index.md` before relying on the converted corpus.

## Publishing Workflow

- Convert Markdown to self-contained HTML:
  `python scripts/md_to_html.py "<input.md>" --theme article -o "<output.html>"`
- Choose `--theme article` for essays/blogs, `--theme report` for reports and tables, `--theme reading` for narrow reading pages, `--theme interactive` for long documents with a table of contents, and `--theme wechat` for WeChat-style article transfer.
- Use `--inline-images` when a single portable HTML file is required. Use `--copy-images` when keeping external image files beside the HTML is better.
- Convert local HTML or article-like URLs back to Markdown:
  `python scripts/html_to_md.py "<input.html-or-url>" -o "<output.md>"`
- For URLs, use `html_to_md.py` for articles/blogs/news where navigation should be removed. Use `convert_corpus.py` or MarkItDown for structured pages where metadata, field values, links, and hierarchy matter.
- Convert Markdown to DOCX:
  `python scripts/md_to_docx.py "<input.md>" -o "<output.docx>"`
- For multi-file manuscripts, use:
  `python scripts/md_to_docx.py ch01.md ch02.md --book --title "<title>" -o "<book.docx>"`

## Publishing Dependencies

- `scripts/bootstrap_env.py` installs Python dependencies for HTML recovery and DOCX export: `html-to-markdown`, `trafilatura`, `markdownify`, `python-docx`, and `Pillow`.
- `scripts/md_to_html.py` requires Pandoc as a system executable. On Windows, install it with `winget install JohnMacFarlane.Pandoc` or use an existing Pandoc installation on `PATH`.
- HTML themes live under `templates/`.
- Detailed publishing guidance lives in `references/html-to-md-cookbook.md`, `references/md-to-html-themes.md`, `references/md-to-docx-cookbook.md`, `references/design-tokens.md`, and `references/anti-ai-slop.md`.
- The added publishing scripts/templates are adapted from `alchaincyf/huashu-md-html`; keep `references/huashu-md-html-LICENSE.txt` with redistributed files.

## Conversion Rules

- Preserve the original source folder. Write generated files under the output folder.
- Expand ZIP recursively with Python. Expand RAR with 7-Zip only; do not rely on Windows `tar` for RAR because Chinese filenames can become mojibake.
- Convert PDF, DOCX, XLSX, PPTX, images, HTML, CSV, TXT, and similar document formats through MarkItDown where possible.
- Convert legacy `.doc`, `.xls`, and `.ppt` through local Microsoft Office automation first when available, then run MarkItDown on the modern copy.
- Enable MarkItDown plugins and an OpenAI-compatible client only when the user has configured `OPENAI_API_KEY` and `MARKITDOWN_OCR_MODEL`, or has passed an OCR model explicitly.
- Use local PaddleOCR before large vision-language models for Chinese scanned PDFs when batch stability matters. It is usually steadier for OCR-only work and keeps credentials local.
- If PaddleOCR or vision OCR is unavailable, keep the file in the manifest and mark `ocr_status=needs_ocr` rather than pretending the text extraction succeeded.
- PaddleOCR-backed files should use `ocr_status=paddleocr_completed` or `paddleocr_partial` and `quality_status=needs_human_spotcheck`.
- For MiniMax official-site `sk-cp-...` keys, use the domestic OpenAI-compatible endpoint `https://api.minimaxi.com/v1` with model `MiniMax-M3`; `https://api.minimax.io/v1` can return 401 for these keys.
- MiniMax-M3 table enhancement must write separate `.tables.md` files under `table_enhanced/` rather than overwriting the main converted Markdown. Keep `quality_status=needs_human_spotcheck` because vision models can still miss rows, merge cells incorrectly, or be blocked by provider safety filters.
- MarkItDown 0.1.6 improves PDF table extraction, but it still does not guarantee correct table placement for complex Chinese policy PDFs. When tables are important, do not mark a PDF corpus verified from `documents/` alone; use `documents_page_aware/` plus source-page QA or a manually verified `documents_verified/` set.
- Do not repair PDF tables by appending sidecar tables to the end of the Markdown or moving them to the top. That destroys source position. Use page-aware repair or keep sidecars clearly separate.
- Do not call a table-heavy corpus "fully verified" from count reconciliation alone. Distinguish batch repair, count reconciliation, spot-checking, and cell-level verification.
- On Windows, console mojibake is not proof that generated Markdown is corrupted. Verify file contents as UTF-8 and compare rendered source pages before deciding OCR is needed.
- For Alibaba Cloud Bailian/DashScope OCR, use `OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1` and model `qwen-vl-ocr-latest` unless the user specifies another supported Qwen OCR/VL model.
- Prefer storing OCR credentials in `%USERPROFILE%\.codex\secrets\markitdown-document-converter.env` on Windows, or `~/.codex/secrets/markitdown-document-converter.env` on Unix-like systems. Do not store API keys in `SKILL.md`, scripts, generated Markdown, manifests, reports, or logs.

## Output Contract

- `documents/**/*.md`: one traceable Markdown file per converted source document, with YAML frontmatter.
- `documents_llm_ready/documents/**/*.md`: optional merged import corpus, using page-aware PDF Markdown where available and original non-PDF Markdown otherwise.
- `documents_page_aware/**/*.md`: optional page-aware PDF Markdown with `source_page` markers and coordinate-placed Markdown tables.
- `manifest.jsonl`: one JSON object per discovered file, archive action, or conversion attempt.
- `chunks.jsonl`: retrieval chunks with file and section citations.
- `chunks_page_aware.jsonl`: optional retrieval chunks from `documents_page_aware/`.
- `documents_llm_ready/chunks_llm_ready.jsonl`: optional retrieval chunks from the merged import corpus.
- `qa/unresolved.md`: files that failed, were unsupported, or need OCR/manual review.
- `qa/conversion_report.md`: run summary and acceptance checks.
- `qa/pdf_page_table_repair_report.*`: page-aware PDF table repair summary, including table counts and files needing source-page spotcheck.
- `qa/pdf_table_preflight.*`: pre-conversion/source scan of PDFs with detected tables and table pages.
- `qa/llm_ready_corpus_report.md`: merged import corpus summary.
- `qa/Table_Enhancement_Candidates.*`: candidate files whose tables/forms may need structure enhancement.
- `table_enhanced/*.tables.md`: MiniMax-M3 table/form enhancement outputs for selected source pages.
- `table_enhanced/00_Table_Enhancement_All.md`: combined table/form enhancement output for import.
- `table_enhanced/00_Table_Enhancement_Index.md`: enhancement run index and provider page errors.
- HTML publishing outputs are written wherever `-o <output.html>` points; they should not overwrite source Markdown.
- DOCX publishing outputs are written wherever `-o <output.docx>` points; they are final deliverables for human review, not source-of-truth files.

## Quality Rules

- Keep citations at file plus chapter/section level by default.
- Preserve visible headings, tables, dates, amounts, IDs, filenames, and source paths.
- Prefer Markdown tables over HTML tables when OCR or cleanup can reasonably produce them.
- For PDFs with detected tables, preserve page-level traceability. A table is not considered verified merely because it exists in Markdown; source page, table shape, and placement must be spot-checked for high-value files.
- Do not summarize source documents during conversion.
- For low-text PDFs, images, and very short extracted text, mark the item for OCR or human spot-checking.
- Keep both the converted Markdown and the manifest; the manifest is the audit trail for source paths, archive members, conversion status, and OCR status.

## Resources

- Use `scripts/bootstrap_env.py` instead of installing MarkItDown into the system Python.
- Use `scripts/convert_corpus.py` for batch conversion and source traceability.
- Use `scripts/pdf_table_preflight.py` before or during intake to identify PDFs and pages with tables.
- Use `scripts/pdf_page_table_repair.py` after `convert_corpus.py` when PDF table placement matters.
- Use `scripts/build_llm_ready_corpus.py` after PDF table repair to create a single import target that does not rely on raw `documents/` for table-bearing PDFs.
- Read `references/pdf-table-lessons.md` when handling PDF table corpora or when a user challenges table accuracy.
- Use `scripts/postprocess_markdown.py` to clean Markdown and rebuild chunks.
- Use `scripts/paddleocr_backfill.py` for local scanned PDF/image OCR after installing PaddleOCR with `scripts/bootstrap_env.py --with-paddleocr`.
- Use `scripts/ocr_backfill.py` only when a vision/OCR model is configured or PaddleOCR is not suitable.
- Use `scripts/set_minimax_secret_from_clipboard.ps1` to store MiniMax API credentials locally without putting keys in source or generated outputs.
- Use `scripts/minimax_table_enhance.py` for MiniMax-M3 table/form enhancement after candidate QA identifies pages with flattened or missing table structure.
- Use `scripts/md_to_html.py` for Markdown to styled HTML with bundled Pandoc themes.
- Use `scripts/html_to_md.py` for article-like HTML or URL recovery back to Markdown.
- Use `scripts/md_to_docx.py` for Markdown to styled DOCX export.
