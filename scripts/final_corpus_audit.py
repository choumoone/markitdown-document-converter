from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from build_llm_ready_corpus import inspect_markdown


def read_chunks(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        return rows, ["chunks_file_missing"]
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                errors.append(f"invalid_chunk_json_line:{line_no}")
    return rows, errors


def audit(documents: Path, chunks_path: Path | None, expected_documents: int | None) -> dict[str, Any]:
    files = sorted(documents.rglob("*.md"))
    document_rows: list[dict[str, Any]] = []
    doc_ids: list[str] = []
    source_paths: list[str] = []
    source_keys: list[str] = []
    for path in files:
        raw = path.read_bytes()
        encoding_issues: list[str] = []
        if raw.startswith(b"\xef\xbb\xbf"):
            encoding_issues.append("utf8_bom")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            encoding_issues.append("invalid_utf8")
        meta, body_chars, issues = inspect_markdown(path)
        issues = list(dict.fromkeys(encoding_issues + issues))
        doc_id = meta.get("doc_id", "")
        source_path = meta.get("source_path", "")
        archive_member_path = meta.get("archive_member_path", "")
        if not doc_id:
            issues.append("missing_doc_id")
        if not source_path:
            issues.append("missing_source_path")
        elif not Path(source_path).exists():
            issues.append("source_path_not_found")
        if doc_id:
            doc_ids.append(doc_id)
        if source_path:
            resolved_source = str(Path(source_path).resolve()) if Path(source_path).exists() else source_path
            source_paths.append(resolved_source)
            source_keys.append(f"{resolved_source}::{archive_member_path}" if archive_member_path else resolved_source)
        document_rows.append(
            {
                "path": str(path),
                "doc_id": doc_id,
                "source_path": source_path,
                "body_chars": body_chars,
                "issues": issues,
            }
        )

    duplicate_doc_ids = sorted(key for key, count in Counter(doc_ids).items() if count > 1)
    duplicate_sources = sorted(key for key, count in Counter(source_keys).items() if count > 1)
    chunks: list[dict[str, Any]] = []
    chunk_errors: list[str] = []
    duplicate_chunk_ids: list[str] = []
    missing_chunk_markdown_paths = 0
    missing_chunk_source_paths = 0
    chunk_doc_ids: set[str] = set()
    if chunks_path is not None:
        chunks, chunk_errors = read_chunks(chunks_path)
        chunk_ids = [str(row.get("chunk_id", "")) for row in chunks if row.get("chunk_id")]
        duplicate_chunk_ids = sorted(key for key, count in Counter(chunk_ids).items() if count > 1)
        missing_chunk_markdown_paths = sum(
            1 for row in chunks if not row.get("markdown_path") or not Path(row["markdown_path"]).exists()
        )
        missing_chunk_source_paths = sum(
            1 for row in chunks if not row.get("source_path") or not Path(row["source_path"]).exists()
        )
        chunk_doc_ids = {str(row.get("doc_id", "")) for row in chunks if row.get("doc_id")}

    global_issues: list[str] = []
    if expected_documents is not None and len(files) != expected_documents:
        global_issues.append(f"document_count_mismatch:{len(files)}!={expected_documents}")
    if duplicate_doc_ids:
        global_issues.append(f"duplicate_doc_ids:{len(duplicate_doc_ids)}")
    if duplicate_sources:
        global_issues.append(f"duplicate_source_paths:{len(duplicate_sources)}")
    if chunks_path is not None:
        if duplicate_chunk_ids:
            global_issues.append(f"duplicate_chunk_ids:{len(duplicate_chunk_ids)}")
        if chunk_errors:
            global_issues.extend(chunk_errors)
        if missing_chunk_markdown_paths:
            global_issues.append(f"missing_chunk_markdown_paths:{missing_chunk_markdown_paths}")
        if missing_chunk_source_paths:
            global_issues.append(f"missing_chunk_source_paths:{missing_chunk_source_paths}")
        if set(doc_ids) != chunk_doc_ids:
            global_issues.append("chunk_document_coverage_mismatch")

    failed_documents = [row for row in document_rows if row["issues"]]
    return {
        "summary": {
            "documents": len(files),
            "failed_documents": len(failed_documents),
            "unique_doc_ids": len(set(doc_ids)),
            "unique_source_paths": len(set(source_keys)),
            "chunks": len(chunks),
            "unique_chunk_doc_ids": len(chunk_doc_ids),
            "global_issues": len(global_issues),
            "clean": not failed_documents and not global_issues,
        },
        "global_issues": global_issues,
        "duplicate_doc_ids": duplicate_doc_ids,
        "duplicate_source_paths": duplicate_sources,
        "duplicate_chunk_ids": duplicate_chunk_ids,
        "failed_documents": failed_documents,
    }


def write_report(report_path: Path, result: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.suffix.lower() == ".json":
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        return
    summary = result["summary"]
    lines = [
        "# Final Corpus Audit",
        "",
        f"- Documents: {summary['documents']}",
        f"- Failed documents: {summary['failed_documents']}",
        f"- Unique doc IDs: {summary['unique_doc_ids']}",
        f"- Unique source paths: {summary['unique_source_paths']}",
        f"- Chunks: {summary['chunks']}",
        f"- Chunk document coverage: {summary['unique_chunk_doc_ids']}",
        f"- Global issues: {summary['global_issues']}",
        f"- Clean: `{str(summary['clean']).lower()}`",
        "",
        "## Global Issues",
        "",
    ]
    lines.extend(f"- {issue}" for issue in result["global_issues"])
    if not result["global_issues"]:
        lines.append("- none")
    lines.extend(["", "## Failed Documents", ""])
    lines.extend(
        f"- `{row['path']}`: {', '.join(row['issues'])}" for row in result["failed_documents"]
    )
    if not result["failed_documents"]:
        lines.append("- none")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the actual final Markdown and chunks after delivery staging.")
    parser.add_argument("--documents", required=True, help="Final Markdown documents directory.")
    parser.add_argument("--chunks", help="Optional final chunks.jsonl path.")
    parser.add_argument("--expected-documents", type=int, help="Expected Markdown document count.")
    parser.add_argument("--report", required=True, help="Output .json or .md report path.")
    parser.add_argument("--require-clean", action="store_true", help="Exit 2 when any document or global issue remains.")
    args = parser.parse_args()
    documents = Path(args.documents).expanduser().resolve()
    chunks = Path(args.chunks).expanduser().resolve() if args.chunks else None
    result = audit(documents, chunks, args.expected_documents)
    write_report(Path(args.report).expanduser().resolve(), result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if args.require_clean and not result["summary"]["clean"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
