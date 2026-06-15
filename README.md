# MarkItDown Document Converter

Codex skill for converting mixed document folders into clean, traceable Markdown, then optionally publishing that Markdown to HTML or DOCX.

## What It Does

- Batch converts PDF, Word, Excel, PowerPoint, images, HTML, text, ZIP, and RAR inputs to Markdown.
- Preserves source metadata in frontmatter and `manifest.jsonl`.
- Generates retrieval chunks and QA reports for RAG/knowledge-base workflows.
- Flags low-text PDFs, images, failed conversions, and unsupported files for review.
- Backfills scanned PDFs/images with PaddleOCR or an OpenAI-compatible vision model.
- Preflights PDF table pages, repairs table placement with page-aware extraction, audits table risk, and can apply MiniMax-rebuilt tables back into their original Markdown positions.
- Enhances complex scanned tables/forms with MiniMax-M3.
- Publishes Markdown to themed HTML via Pandoc.
- Converts article-like HTML or URLs back to Markdown.
- Exports Markdown to styled DOCX.

## Install

Copy this folder into your Codex skills directory:

```powershell
Copy-Item -Recurse . "$env:USERPROFILE\.codex\skills\markitdown-document-converter"
```

Then bootstrap the isolated Python environment:

```powershell
python scripts/bootstrap_env.py
```

For local scanned-document OCR:

```powershell
python scripts/bootstrap_env.py --with-paddleocr
```

## Basic Usage

Convert a folder into Markdown:

```powershell
python scripts/convert_corpus.py --source "C:\path\to\documents" --output "C:\path\to\markdown-output"
```

For PDF-heavy corpora where tables matter, do not rely on the raw `documents/` folder alone:

```powershell
python scripts/pdf_table_preflight.py --source "C:\path\to\documents" --output "C:\path\to\markdown-output\qa"
python scripts/pdf_page_table_repair.py --kb "C:\path\to\markdown-output" --rebuild-chunks
python scripts/build_llm_ready_corpus.py --kb "C:\path\to\markdown-output"
python scripts/pdf_table_quality_audit.py --kb "C:\path\to\markdown-output"
```

Use `documents_llm_ready\documents` as the import target after reviewing the QA reports.
If MiniMax table rebuilds are accepted for main Markdown, apply them in-place instead of replacing whole pages:

```powershell
python scripts/minimax_apply_table_repair.py --kb "C:\path\to\markdown-output" --enhanced-file "C:\path\to\markdown-output\table_enhanced\example.tables.md" --force
```

Rebuild chunks:

```powershell
python scripts/postprocess_markdown.py --input "C:\path\to\markdown-output\documents" --chunks-out "C:\path\to\markdown-output\chunks.jsonl"
```

Publish Markdown to HTML:

```powershell
python scripts/md_to_html.py "article.md" --theme article -o "article.html"
```

Convert HTML or a URL back to Markdown:

```powershell
python scripts/html_to_md.py "https://example.com/article" -o "article.md"
```

Export Markdown to DOCX:

```powershell
python scripts/md_to_docx.py "article.md" -o "article.docx"
```

## Notes

- `md_to_html.py` requires Pandoc on `PATH`.
- OCR credentials are loaded from local env files under `~/.codex/secrets`; never commit those files.
- Keep generated outputs outside this skill folder.
- For PDF tables, read `references/pdf-table-lessons.md`; table sidecars must not be appended to the end of converted Markdown as a "fix", and MiniMax page output should not replace original prose when only table blocks need repair.
- The publishing scripts and templates are adapted from `alchaincyf/huashu-md-html`; see `references/huashu-md-html-LICENSE.txt`.
