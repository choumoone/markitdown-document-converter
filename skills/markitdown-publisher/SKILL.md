---
name: markitdown-publisher
description: Publish clean Markdown to polished HTML or styled DOCX, and convert HTML pages or URLs back to Markdown. Use when the requested deliverable is presentation-ready HTML, a Word document, an HTML-to-Markdown extraction, or a URL article conversion rather than corpus conversion or repair.
---

# MarkItDown Publisher

Use the shared engine at `~/.codex/skills/markitdown-document-converter/scripts` after Markdown content is clean and accepted.

Set `SCRIPTS` to that directory and use the converter environment's Python as `PYTHON`.

## Authorization gate

An explicit request for HTML, DOCX, or HTML-to-Markdown output authorizes local publishing to the stated destination. If publishing is suggested automatically by another skill, report the input count, output format, destination, and overwrite behavior, then wait for authorization. Never overwrite source Markdown without explicit approval.

## Markdown to HTML

```powershell
& $PYTHON "$SCRIPTS/md_to_html.py" "<input.md>" --output "<output.html>"
```

Use the script's theme and Pandoc options only when the user requests a specific presentation style.

## Markdown to DOCX

```powershell
& $PYTHON "$SCRIPTS/md_to_docx.py" "<input.md>" --output "<output.docx>"
```

Use `--book` and its title metadata only for book-like output. Render and inspect the DOCX when layout matters.

## HTML or URL to Markdown

```powershell
& $PYTHON "$SCRIPTS/html_to_md.py" "<html-file-or-url>" --output "<output.md>"
```

Verify that navigation, ads, and unrelated page chrome were removed. Do not run corpus audit unless the output is joining a larger knowledge base.
