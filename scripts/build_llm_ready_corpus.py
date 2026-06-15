from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from postprocess_markdown import section_chunks, strip_frontmatter


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
    missing_page_aware_pdf = 0
    seen_ids: set[str] = set()
    rows: list[dict[str, str]] = []

    for src in sorted(documents.rglob("*.md")):
        rel = src.relative_to(documents)
        meta, _body = strip_frontmatter(src.read_text(encoding="utf-8", errors="replace"))
        doc_id = meta.get("doc_id") or md_doc_id(src)
        kind = meta.get("document_kind") or rel.parts[0] if rel.parts else ""
        if kind == "pdf":
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
        f"- PDF fallbacks to original conversion: {missing_page_aware_pdf}",
        f"- Chunks: {chunk_count}",
        "",
        "## Rule",
        "",
        "- PDF files use `documents_page_aware/` when available.",
        "- Non-PDF files use the original `documents/` conversion.",
        "- This directory is the preferred import target after table repair QA.",
    ]
    report = qa / "llm_ready_corpus_report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {
        "output_documents": str(out_docs),
        "copied": copied,
        "replaced_pdf": replaced_pdf,
        "missing_page_aware_pdf": missing_page_aware_pdf,
        "chunks": chunk_count,
        "report": str(report),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a single LLM-ready corpus from base docs plus page-aware PDFs.")
    parser.add_argument("--kb", required=True, help="Converted knowledge-base output folder.")
    parser.add_argument("--output-subdir", default="documents_llm_ready", help="Output subdirectory under KB.")
    parser.add_argument("--page-aware-subdir", default="documents_page_aware", help="Page-aware PDF Markdown subdirectory.")
    args = parser.parse_args()

    summary = build(Path(args.kb).expanduser().resolve(), args.output_subdir, args.page_aware_subdir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
