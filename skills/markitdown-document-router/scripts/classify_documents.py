#!/usr/bin/env python3
"""Classify documents into deterministic MarkItDown processing routes."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SIMPLE_EXTENSIONS = {
    ".csv",
    ".docx",
    ".htm",
    ".html",
    ".json",
    ".md",
    ".pptx",
    ".txt",
    ".xlsx",
    ".xml",
}
LEGACY_OFFICE_EXTENSIONS = {".doc", ".ppt", ".xls"}
IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
ARCHIVE_EXTENSIONS = {".rar", ".zip"}
SKILL_BY_BUCKET = {
    "simple_direct": "markitdown-document-converter",
    "pdf_text": "markitdown-document-converter",
    "legacy_office": "markitdown-document-converter",
    "needs_ocr": "markitdown-ocr",
    "pdf_table": "markitdown-pdf-table-repair",
    "archive": "markitdown-document-converter",
    "manual_review": "markitdown-document-router",
    "unsupported": "markitdown-document-router",
}
RECOMMENDED_ORDER = [
    "simple_direct",
    "pdf_text",
    "legacy_office",
    "needs_ocr",
    "pdf_table",
    "archive",
    "manual_review",
    "unsupported",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Source file or folder.")
    parser.add_argument("--output", required=True, help="Route-plan JSON path.")
    parser.add_argument("--sample-pages", type=int, default=8, help="Maximum PDF pages to sample.")
    parser.add_argument("--low-text-chars", type=int, default=40, help="Low-text threshold per sampled PDF page.")
    parser.add_argument("--include-hidden", action="store_true", help="Include hidden files and folders.")
    parser.add_argument("--verbose", action="store_true", help="Print routed file paths after the compact summary.")
    return parser.parse_args()


def is_hidden(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith(".") for part in parts)


def discover(source: Path, output: Path, include_hidden: bool) -> list[Path]:
    if source.is_file():
        return [source]
    files = []
    for path in source.rglob("*"):
        if not path.is_file() or path.resolve() == output.resolve():
            continue
        if not include_hidden and is_hidden(path, source):
            continue
        files.append(path)
    return sorted(files, key=lambda item: str(item).lower())


def sampled_page_indexes(page_count: int, maximum: int) -> list[int]:
    if page_count <= 0:
        return []
    count = min(page_count, max(1, maximum))
    if count == page_count:
        return list(range(page_count))
    return sorted({min(page_count - 1, math.floor(index * (page_count - 1) / (count - 1))) for index in range(count)})


def classify_pdf(path: Path, sample_pages: int, low_text_chars: int) -> tuple[str, str, dict[str, Any]]:
    details: dict[str, Any] = {}
    try:
        import fitz  # type: ignore
    except ImportError:
        return "manual_review", "PyMuPDF is unavailable; PDF type was not guessed", details

    try:
        document = fitz.open(path)
    except Exception as exc:
        return "manual_review", f"PDF could not be opened: {type(exc).__name__}", details

    try:
        indexes = sampled_page_indexes(document.page_count, sample_pages)
        text_counts: list[int] = []
        image_pages = 0
        table_pages: list[int] = []
        table_detection_available = True
        for index in indexes:
            page = document.load_page(index)
            text_counts.append(len(page.get_text("text").strip()))
            if page.get_images(full=False):
                image_pages += 1
            finder = getattr(page, "find_tables", None)
            if finder is None:
                table_detection_available = False
                continue
            try:
                if finder().tables:
                    table_pages.append(index + 1)
            except Exception:
                table_detection_available = False

        sampled = len(indexes)
        low_text_pages = sum(count < low_text_chars for count in text_counts)
        average_text = round(sum(text_counts) / sampled, 1) if sampled else 0.0
        details = {
            "pages": document.page_count,
            "sampled_pages": [index + 1 for index in indexes],
            "average_text_chars": average_text,
            "low_text_pages": low_text_pages,
            "image_pages": image_pages,
            "table_pages": table_pages,
            "table_detection_available": table_detection_available,
        }

        if table_pages:
            return "pdf_table", f"tables detected on sampled pages {table_pages}", details
        if sampled and low_text_pages / sampled >= 0.7 and image_pages / sampled >= 0.5:
            return "needs_ocr", "most sampled pages contain images but little embedded text", details
        if sampled and low_text_pages == sampled:
            return "manual_review", "all sampled pages have little text but do not look consistently image-based", details
        if not table_detection_available:
            return "manual_review", "text exists but local PDF table detection was incomplete", details
        return "pdf_text", "embedded text found and no tables detected in sampled pages", details
    finally:
        document.close()


def classify(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    extension = path.suffix.lower()
    details: dict[str, Any] = {}
    if extension in SIMPLE_EXTENSIONS:
        bucket, reason = "simple_direct", "supported direct-conversion format"
    elif extension in LEGACY_OFFICE_EXTENSIONS:
        bucket, reason = "legacy_office", "legacy Office format may require LibreOffice normalization"
    elif extension in IMAGE_EXTENSIONS:
        bucket, reason = "needs_ocr", "image input has no embedded document text"
    elif extension in ARCHIVE_EXTENSIONS:
        bucket, reason = "archive", "archive must be expanded before leaf-file routing"
    elif extension == ".pdf":
        bucket, reason, details = classify_pdf(path, args.sample_pages, args.low_text_chars)
    else:
        bucket, reason = "unsupported", "extension is not covered by the current conversion pipeline"
    return {
        "path": str(path.resolve()),
        "extension": extension or None,
        "bytes": path.stat().st_size,
        "bucket": bucket,
        "skill": SKILL_BY_BUCKET[bucket],
        "reason": reason,
        "details": details,
    }


def main() -> int:
    args = parse_args()
    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not source.exists():
        print(f"Source does not exist: {source}", file=sys.stderr)
        return 2

    files = discover(source, output, args.include_hidden)
    entries = [classify(path, args) for path in files]
    counts = Counter(entry["bucket"] for entry in entries)
    buckets = {
        bucket: [entry for entry in entries if entry["bucket"] == bucket]
        for bucket in RECOMMENDED_ORDER
        if counts[bucket]
    }
    plan = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "file_count": len(entries),
        "counts": {bucket: counts[bucket] for bucket in RECOMMENDED_ORDER if counts[bucket]},
        "recommended_order": [bucket for bucket in RECOMMENDED_ORDER if counts[bucket]],
        "buckets": buckets,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = ", ".join(f"{bucket}={counts[bucket]}" for bucket in RECOMMENDED_ORDER if counts[bucket]) or "no files"
    print(f"Classified {len(entries)} files: {summary}")
    print(f"Route plan: {output}")
    if args.verbose:
        for entry in entries:
            print(f"[{entry['bucket']}] {entry['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
