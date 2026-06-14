# MarkItDown Document Converter

Codex skill for converting mixed document folders into clean, traceable Markdown, then optionally publishing that Markdown to HTML or DOCX.

## What It Does

- Batch converts PDF, Word, Excel, PowerPoint, images, HTML, text, ZIP, and RAR inputs to Markdown.
- Preserves source metadata in frontmatter and `manifest.jsonl`.
- Generates retrieval chunks and QA reports for RAG/knowledge-base workflows.
- Flags low-text PDFs, images, failed conversions, and unsupported files for review.
- Backfills scanned PDFs/images with PaddleOCR or an OpenAI-compatible vision model.
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
- The publishing scripts and templates are adapted from `alchaincyf/huashu-md-html`; see `references/huashu-md-html-LICENSE.txt`.

