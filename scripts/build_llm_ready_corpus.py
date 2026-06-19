from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any

from postprocess_markdown import section_chunks, strip_frontmatter


OCR_BACKFILL_STATUSES = {
    "paddleocr_completed",
    "paddleocr_partial",
    "vision_ocr_completed",
    "vision_ocr_partial",
}

UNRESOLVED_OCR_STATUSES = {"needs_ocr", "paddleocr_partial", "vision_ocr_partial"}
REVIEW_QUALITY_STATUSES = {"needs_review", "needs_human_spotcheck"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def assert_under(path: Path, parent: Path) -> None:
    path.resolve().relative_to(parent.resolve())


def safe_rmtree(path: Path, allowed_parent: Path) -> None:
    if not path.exists():
        return
    assert_under(path, allowed_parent)
    shutil.rmtree(path)


def md_doc_id(path: Path) -> str:
    meta, _body = strip_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    return meta.get("doc_id") or path.stem.rsplit("--", 1)[-1]


def is_pdf_markdown(meta: dict[str, str], rel: Path) -> bool:
    kind = meta.get("document_kind", "")
    source_extension = meta.get("source_extension", "")
    source_path = meta.get("source_path", "")
    if kind == "pdf" or source_extension.lower() == ".pdf":
        return True
    if source_path.lower().endswith(".pdf"):
        return True
    return bool(rel.parts and rel.parts[0].lower() == "pdf")


def copy_markdown(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def collect_page_aware(page_aware_root: Path) -> dict[str, Path]:
    by_id: dict[str, Path] = {}
    if not page_aware_root.exists():
        return by_id
    for path in sorted(page_aware_root.rglob("*.md")):
        by_id[md_doc_id(path)] = path
    return by_id


def inspect_markdown(path: Path) -> tuple[dict[str, str], int, list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = strip_frontmatter(text)
    without_comments = re.sub(r"<!--[\s\S]*?-->", "", body)
    compact = re.sub(r"[#*|:`_\-\s]", "", without_comments)
    issues: list[str] = []
    if "\ufffd" in text:
        issues.append("replacement_character")
    lowered = text.lower()
    if any(term in lowered for term in ("<think>", "invalid api key", "sensitive image", "server error")):
        issues.append("provider_error_text")
    content_lines = [
        re.sub(r"[#*|:`_\-\s]", "", line)
        for line in without_comments.splitlines()
    ]
    content_lines = [line for line in content_lines if line]
    tiny_lines = sum(1 for line in content_lines if len(line) <= 2)
    if len(content_lines) >= 20 and tiny_lines / len(content_lines) >= 0.65:
        issues.append("garbled_or_fragmented")
    return meta, len(compact), issues


def build(kb: Path, output_subdir: str, page_aware_subdir: str) -> dict[str, Any]:
    documents = kb / "documents"
    page_aware = kb / page_aware_subdir
    out_root = kb / output_subdir
    safe_rmtree(out_root, kb)
    out_docs = out_root / "documents"
    out_docs.mkdir(parents=True, exist_ok=True)
    page_aware_by_id = collect_page_aware(page_aware)

    copied = 0
    replaced_pdf = 0
    ocr_pdf = 0
    missing_page_aware_pdf = 0
    seen_ids: set[str] = set()
    rows: list[dict[str, str]] = []

    for src in sorted(documents.rglob("*.md")):
        rel = src.relative_to(documents)
        meta, _body = strip_frontmatter(src.read_text(encoding="utf-8", errors="replace"))
        doc_id = meta.get("doc_id") or md_doc_id(src)
        kind = meta.get("document_kind") or (rel.parts[0] if rel.parts else "")
        if is_pdf_markdown(meta, rel):
            if meta.get("ocr_status", "") in OCR_BACKFILL_STATUSES:
                copy_markdown(src, out_docs / rel)
                ocr_pdf += 1
                copied += 1
                seen_ids.add(doc_id)
                rows.append({"doc_id": doc_id, "kind": "pdf", "source": str(src), "status": "ocr_backfill"})
                continue
            replacement = page_aware_by_id.get(doc_id)
            if replacement:
                rel = replacement.relative_to(page_aware)
                copy_markdown(replacement, out_docs / rel)
                replaced_pdf += 1
                copied += 1
                seen_ids.add(doc_id)
                rows.append({"doc_id": doc_id, "kind": "pdf", "source": str(replacement), "status": "page_aware"})
            else:
                copy_markdown(src, out_docs / rel)
                missing_page_aware_pdf += 1
                copied += 1
                seen_ids.add(doc_id)
                rows.append({"doc_id": doc_id, "kind": "pdf", "source": str(src), "status": "original_pdf_fallback"})
            continue
        copy_markdown(src, out_docs / rel)
        copied += 1
        seen_ids.add(doc_id)
        rows.append({"doc_id": doc_id, "kind": kind, "source": str(src), "status": "original_non_pdf"})

    for doc_id, src in sorted(page_aware_by_id.items()):
        if doc_id in seen_ids:
            continue
        rel = src.relative_to(page_aware)
        copy_markdown(src, out_docs / rel)
        copied += 1
        replaced_pdf += 1
        rows.append({"doc_id": doc_id, "kind": "pdf", "source": str(src), "status": "page_aware_extra"})

    chunks_path = out_root / "chunks_llm_ready.jsonl"
    chunk_count = 0
    with chunks_path.open("w", encoding="utf-8", newline="\n") as handle:
        for md_path in sorted(out_docs.rglob("*.md")):
            for chunk in section_chunks(md_path):
                handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                chunk_count += 1

    unresolved_ocr: list[dict[str, str]] = []
    near_empty: list[dict[str, str]] = []
    suspicious_content: list[dict[str, str]] = []
    needs_spotcheck: list[dict[str, str]] = []
    for md_path in sorted(out_docs.rglob("*.md")):
        meta, body_chars, issues = inspect_markdown(md_path)
        item = {
            "doc_id": meta.get("doc_id", ""),
            "title": meta.get("doc_title", md_path.stem),
            "path": str(md_path),
            "ocr_status": meta.get("ocr_status", ""),
            "quality_status": meta.get("quality_status", ""),
            "body_chars": str(body_chars),
            "issues": ",".join(issues),
        }
        if item["ocr_status"] in UNRESOLVED_OCR_STATUSES:
            unresolved_ocr.append(item)
        if body_chars < 30:
            near_empty.append(item)
        if issues:
            suspicious_content.append(item)
        if item["quality_status"] in REVIEW_QUALITY_STATUSES:
            needs_spotcheck.append(item)

    ready_for_import = not unresolved_ocr and not near_empty and not suspicious_content

    qa = kb / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    (qa / "llm_ready_corpus_manifest.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    lines = [
        "# LLM Ready Corpus Report",
        "",
        f"- Output documents: `{out_docs}`",
        f"- Markdown files copied: {copied}",
        f"- PDFs replaced with page-aware version: {replaced_pdf}",
        f"- PDFs using OCR backfill: {ocr_pdf}",
        f"- PDF fallbacks to original conversion: {missing_page_aware_pdf}",
        f"- Chunks: {chunk_count}",
        f"- Unresolved OCR documents: {len(unresolved_ocr)}",
        f"- Near-empty Markdown documents: {len(near_empty)}",
        f"- Documents with suspicious content: {len(suspicious_content)}",
        f"- Documents needing human spot-check: {len(needs_spotcheck)}",
        f"- Ready for import: `{str(ready_for_import).lower()}`",
        "",
        "## Rule",
        "",
        "- OCR-backfilled PDF files use the OCR Markdown from `documents/`.",
        "- Other PDF files use `documents_page_aware/` when available.",
        "- Non-PDF files use the original `documents/` conversion.",
        "- This directory is the preferred import target after table repair QA.",
    ]
    report = qa / "llm_ready_corpus_report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    unresolved_report = qa / "llm_ready_unresolved.md"
    unresolved_lines = [
        "# LLM Ready Unresolved Items",
        "",
        f"- Unresolved OCR documents: {len(unresolved_ocr)}",
        f"- Near-empty Markdown documents: {len(near_empty)}",
        f"- Documents with suspicious content: {len(suspicious_content)}",
        "",
        "## Unresolved OCR",
        "",
    ]
    unresolved_lines.extend(
        f"- `{item['doc_id']}` {item['title']} - `{item['ocr_status']}` - `{item['path']}`"
        for item in unresolved_ocr
    )
    unresolved_lines.extend(["", "## Near-Empty Markdown", ""])
    unresolved_lines.extend(
        f"- `{item['doc_id']}` {item['title']} - {item['body_chars']} chars - `{item['path']}`"
        for item in near_empty
    )
    unresolved_lines.extend(["", "## Suspicious Content", ""])
    unresolved_lines.extend(
        f"- `{item['doc_id']}` {item['title']} - `{item['issues']}` - `{item['path']}`"
        for item in suspicious_content
    )
    unresolved_report.write_text("\n".join(unresolved_lines) + "\n", encoding="utf-8", newline="\n")
    return {
        "output_documents": str(out_docs),
        "copied": copied,
        "replaced_pdf": replaced_pdf,
        "ocr_pdf": ocr_pdf,
        "missing_page_aware_pdf": missing_page_aware_pdf,
        "chunks": chunk_count,
        "unresolved_ocr": len(unresolved_ocr),
        "near_empty": len(near_empty),
        "suspicious_content": len(suspicious_content),
        "needs_spotcheck": len(needs_spotcheck),
        "ready_for_import": ready_for_import,
        "report": str(report),
        "unresolved_report": str(unresolved_report),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a single LLM-ready corpus from base docs plus page-aware PDFs.")
    parser.add_argument("--kb", required=True, help="Converted knowledge-base output folder.")
    parser.add_argument("--output-subdir", default="documents_llm_ready", help="Output subdirectory under KB.")
    parser.add_argument("--page-aware-subdir", default="documents_page_aware", help="Page-aware PDF Markdown subdirectory.")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit with status 2 when unresolved OCR, near-empty Markdown, or suspicious content remains.",
    )
    args = parser.parse_args()

    summary = build(Path(args.kb).expanduser().resolve(), args.output_subdir, args.page_aware_subdir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.require_ready and not summary["ready_for_import"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
