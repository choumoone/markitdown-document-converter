# MarkItDown Skill Pack

A modular Codex skill pack for converting documents to clean, traceable Markdown without loading the full OCR, PDF-table, audit, and publishing workflow for every task.

## Skills

| Skill | Purpose |
| --- | --- |
| `markitdown-document-router` | Classify mixed folders locally and choose the smallest required workflow. |
| `markitdown-document-converter` | Direct conversion and deterministic Markdown cleanup. |
| `markitdown-ocr` | Scanned PDF and image OCR backfill. |
| `markitdown-pdf-table-repair` | Page-aware PDF table repair and source-recall QA. |
| `markitdown-corpus-audit` | Final LLM-ready assembly, chunks, and acceptance. |
| `markitdown-publisher` | Markdown/HTML/DOCX publishing. |

The converter at the repository root remains the shared engine and backward-compatible skill. Specialist skills under `skills/` reuse its scripts instead of maintaining copies.

## Install

Install or update the complete pack on Windows:

```powershell
.\install_skill_pack.ps1
python "$HOME\.codex\skills\markitdown-document-converter\scripts\bootstrap_env.py"
```

Install local PaddleOCR only when needed:

```powershell
python "$HOME\.codex\skills\markitdown-document-converter\scripts\bootstrap_env.py" --with-paddleocr
```

## Fast Mixed-Folder Workflow

Classify without an LLM:

```powershell
$python = "$HOME\.codex\skill-envs\markitdown-document-converter\.venv\Scripts\python.exe"
$router = "$HOME\.codex\skills\markitdown-document-router"
& $python "$router\scripts\classify_documents.py" --source "C:\documents" --output "C:\markdown-output\route-plan.json"
```

Convert only the cheap buckets first. The default selection is `simple_direct`, `pdf_text`, and `legacy_office`:

```powershell
$scripts = "$HOME\.codex\skills\markitdown-document-converter\scripts"
& $python "$scripts\convert_corpus.py" --source "C:\documents" --output "C:\markdown-output" --route-plan "C:\markdown-output\route-plan.json" --quiet
```

The route plan sends only exceptional files to OCR or PDF-table repair. Final corpus audit and publishing are separate, opt-in stages.

Complex buckets are authorization-gated. Codex must report the proposed scope before invoking OCR, PDF-table repair, archive expansion, or final cleanup. Paid/external vision calls always require a separate explicit approval, even when local specialist work was already approved.

## Direct Markdown Cleanup

```powershell
& $python "$scripts\postprocess_markdown.py" --input "C:\markdown"
```

This is a deterministic single-command path. It does not require OCR, vision, or a final corpus audit.

## Verification

```powershell
& $python -m unittest discover -s tests -p "test_*.py"
& $python -m unittest discover -s skills\markitdown-document-router\tests -p "test_*.py"
```

Publishing scripts and templates are adapted from `alchaincyf/huashu-md-html`; see `references/huashu-md-html-LICENSE.txt`.
