---
name: markitdown-fast-convert
description: Classify and convert ordinary documents to Markdown through one deterministic local command. Use for TXT, Markdown, HTML, DOCX, XLSX, PPTX, text PDFs, and legacy Office inputs. Process only cheap buckets; stop and report when OCR, PDF-table repair, archive expansion, or manual review requires authorization.
---

# MarkItDown Fast Convert

Run exactly one command. Do not inspect the repository, enumerate skill files, bootstrap an existing environment, or read full manifests.

```powershell
& "$HOME\.codex\skill-envs\markitdown-document-converter\.venv\Scripts\python.exe" `
  "$HOME\.config\opencode\skills\markitdown-fast-convert\scripts\run_fast_convert.py" `
  --source "<source>" --output "<output>"
```

Read the compact JSON printed by the command.

- If `status` is `completed`, report elapsed time and document, manifest, and chunk counts.
- If `needs_authorization` is true, report `complex_buckets` and stop. Do not invoke OCR, table repair, archive expansion, or external services.
- Do not rerun successful commands for verification. The wrapper performs output checks.
