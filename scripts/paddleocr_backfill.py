from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from convert_corpus import write_report, write_unresolved
from postprocess_markdown import clean_markdown, strip_frontmatter, write_chunks


TARGET_STATUSES = {"needs_ocr", "needs_review", "vision_ocr_partial", "paddleocr_partial"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def resolve_source(record: dict[str, Any]) -> Path | None:
    for key in ["working_path", "source_path"]:
        value = record.get(key)
        if value and Path(value).exists() and Path(value).is_file():
            return Path(value)
    return None


def output_path(kb: Path, record: dict[str, Any]) -> Path | None:
    value = record.get("output_markdown")
    if value:
        return Path(value)
    return None


def source_page_count(source: Path) -> int:
    if source.suffix.lower() == ".pdf":
        import fitz

        with fitz.open(str(source)) as doc:
            return doc.page_count
    return 1


def source_to_page_images(source: Path, tmp: Path, page_from: int, page_to: int, dpi: int) -> list[tuple[int, Path]]:
    if source.suffix.lower() == ".pdf":
        import fitz

        pages: list[tuple[int, Path]] = []
        with fitz.open(str(source)) as doc:
            total = doc.page_count
            start = max(page_from, 1)
            end = total if page_to <= 0 else min(page_to, total)
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            for page_no in range(start, end + 1):
                image = tmp / f"page_{page_no:04d}.png"
                pix = doc.load_page(page_no - 1).get_pixmap(matrix=matrix, alpha=False)
                pix.save(str(image))
                pages.append((page_no, image))
            return pages
    if source.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}:
        return [(1, source)]
    return []


def extract_existing_paddleocr_pages(out: Path) -> dict[int, str]:
    if not out.exists():
        return {}
    meta, body = strip_frontmatter(out.read_text(encoding="utf-8", errors="replace"))
    if not str(meta.get("extraction_method", "")).startswith("paddleocr:"):
        return {}
    pages: dict[int, str] = {}
    pattern = re.compile(r"^## Page (\d+)\s*\n(.*?)(?=^## Page \d+\s*\n|\Z)", re.M | re.S)
    for match in pattern.finditer(body):
        page_no = int(match.group(1))
        text = match.group(2).strip()
        if text:
            pages[page_no] = text
    return pages


def predict_page_text(ocr: Any, image: Path) -> str:
    result = ocr.predict(str(image))
    if not result:
        return ""
    item = result[0]
    if isinstance(item, dict):
        data = item
    elif hasattr(item, "json"):
        data = item.json
    else:
        data = item.to_dict()
    texts = data.get("rec_texts") or data.get("res", {}).get("rec_texts") or []
    return "\n".join(str(text).strip() for text in texts if str(text).strip())


def write_paddleocr_markdown(kb: Path, record: dict[str, Any], pages: dict[int, str], status: str) -> Path:
    source = resolve_source(record)
    out = output_path(kb, record)
    if out is None:
        raise ValueError("record has no output_markdown path")
    title = record.get("doc_title") or (source.stem if source else "document")
    meta = {
        "doc_id": record.get("file_id", ""),
        "doc_title": title,
        "source_path": record.get("source_path", str(source or "")),
        "origin_archive": record.get("origin_archive", ""),
        "archive_member_path": record.get("archive_member_path", ""),
        "document_kind": record.get("document_kind", "ocr"),
        "source_extension": record.get("extension", source.suffix.lower() if source else ""),
        "source_size": record.get("source_size", ""),
        "extraction_method": "paddleocr:PP-OCRv5",
        "ocr_status": status,
        "quality_status": "needs_human_spotcheck",
    }
    body = "\n\n".join(f"## Page {page_no}\n\n{pages[page_no]}".strip() for page_no in sorted(pages))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(clean_markdown(body, meta), encoding="utf-8", newline="\n")
    return out


def ocr_record(ocr: Any, kb: Path, record: dict[str, Any], dpi: int, page_from: int, page_to: int) -> tuple[dict[str, Any], bool]:
    source = resolve_source(record)
    updated = dict(record)
    if source is None:
        updated["ocr_error"] = "source file not found"
        return updated, False
    out = output_path(kb, record)
    if out is None:
        updated["ocr_error"] = "output_markdown not found"
        return updated, False
    pages = extract_existing_paddleocr_pages(out)
    total_pages = source_page_count(source)
    with tempfile.TemporaryDirectory(prefix="paddleocr_pages_") as temp:
        images = source_to_page_images(source, Path(temp), page_from=page_from, page_to=page_to, dpi=dpi)
        if not images:
            updated["ocr_error"] = f"unsupported source for PaddleOCR: {source.suffix}"
            return updated, False
        title = record.get("doc_title") or source.stem
        for current, (page_no, image) in enumerate(images, 1):
            if page_no in pages:
                print(f"Skip existing PaddleOCR {title} page {page_no}/{total_pages}", flush=True)
                continue
            print(f"PaddleOCR {title} page {page_no}/{total_pages} ({current}/{len(images)})", flush=True)
            pages[page_no] = predict_page_text(ocr, image)
            write_paddleocr_markdown(kb, record, pages, "paddleocr_partial")
    complete = all(page_no in pages for page_no in range(1, total_pages + 1))
    status = "paddleocr_completed" if complete else "paddleocr_partial"
    out = write_paddleocr_markdown(kb, record, pages, status)
    updated.update(
        {
            "output_markdown": str(out),
            "extraction_method": "paddleocr:PP-OCRv5",
            "ocr_status": status,
            "quality_status": "needs_human_spotcheck",
            "ocr_pages": len(pages),
            "ocr_error": "" if complete else f"partial OCR saved: {len(pages)}/{total_pages} pages",
        }
    )
    return updated, complete


def make_paddleocr(lang: str, det_model: str | None, rec_model: str | None) -> Any:
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    try:
        from paddleocr import PaddleOCR
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "PaddleOCR is not installed in this Python environment. "
            "Run `python scripts/bootstrap_env.py --with-paddleocr` first."
        ) from exc

    kwargs: dict[str, Any] = {
        "lang": lang,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
    }
    if det_model:
        kwargs["text_detection_model_name"] = det_model
    if rec_model:
        kwargs["text_recognition_model_name"] = rec_model
    return PaddleOCR(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill low-text PDF/image Markdown files with local PaddleOCR.")
    parser.add_argument("--kb", required=True, help="Converted Markdown output folder.")
    parser.add_argument("--status", action="append", choices=sorted(TARGET_STATUSES), help="OCR statuses to process.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of files to OCR.")
    parser.add_argument("--dpi", type=int, default=144, help="PDF render DPI.")
    parser.add_argument("--page-from", type=int, default=1, help="First PDF page to OCR, 1-based.")
    parser.add_argument("--page-to", type=int, default=0, help="Last PDF page to OCR, 0 means through the end.")
    parser.add_argument("--lang", default="ch", help="PaddleOCR language code.")
    parser.add_argument("--det-model", help="Optional PaddleOCR text detection model name.")
    parser.add_argument("--rec-model", help="Optional PaddleOCR text recognition model name.")
    parser.add_argument("--title-contains", help="Only process records whose doc_title contains this string.")
    parser.add_argument("--dry-run", action="store_true", help="List target records without loading PaddleOCR or writing files.")
    parser.add_argument("--rebuild-chunks", action="store_true", help="Rebuild chunks.jsonl after OCR.")
    args = parser.parse_args()

    kb = Path(args.kb).expanduser().resolve()
    manifest = kb / "manifest.jsonl"
    records = load_jsonl(manifest)
    statuses = set(args.status or TARGET_STATUSES)
    targets = [record for record in records if record.get("ocr_status") in statuses]
    if args.title_contains:
        targets = [record for record in targets if args.title_contains in str(record.get("doc_title", ""))]
    if args.limit:
        targets = targets[: args.limit]

    print(json.dumps({"targets": len(targets)}, ensure_ascii=False), flush=True)
    if args.dry_run:
        for record in targets:
            print(record.get("doc_title") or record.get("source_path") or "<untitled>", flush=True)
        return 0
    if not targets:
        return 0

    ocr = make_paddleocr(args.lang, args.det_model, args.rec_model)
    by_key = {(r.get("source_path"), r.get("archive_member_path"), r.get("doc_title")): i for i, r in enumerate(records)}
    done = 0
    results: list[dict[str, Any]] = []
    start = time.time()
    for record in targets:
        item_start = time.time()
        updated, ok = ocr_record(ocr, kb, record, dpi=args.dpi, page_from=args.page_from, page_to=args.page_to)
        key_tuple = (record.get("source_path"), record.get("archive_member_path"), record.get("doc_title"))
        if key_tuple in by_key:
            records[by_key[key_tuple]] = updated
        done += int(ok)
        results.append(
            {
                "doc_title": record.get("doc_title", ""),
                "ok": ok,
                "ocr_status": updated.get("ocr_status", ""),
                "ocr_pages": updated.get("ocr_pages", 0),
                "seconds": round(time.time() - item_start, 1),
                "error": updated.get("ocr_error", ""),
            }
        )
        (kb / "qa").mkdir(parents=True, exist_ok=True)
        (kb / "qa" / "paddleocr_backfill_results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    write_jsonl(manifest, records)
    chunks = 0
    if args.rebuild_chunks:
        chunks = write_chunks(kb / "documents", kb / "chunks.jsonl")
        print(f"Rebuilt chunks: {chunks}", flush=True)
    else:
        chunks_path = kb / "chunks.jsonl"
        if chunks_path.exists():
            chunks = sum(1 for _ in chunks_path.open("r", encoding="utf-8"))
    write_unresolved(kb, records)
    write_report(kb, records, chunks)
    print(f"PaddleOCR completed records: {done}/{len(targets)} in {round(time.time() - start, 1)}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
