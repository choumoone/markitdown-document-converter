from __future__ import annotations

import argparse
import csv
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from postprocess_markdown import make_frontmatter, section_chunks, strip_frontmatter


@dataclass
class TableBlock:
    page: int
    index: int
    bbox: tuple[float, float, float, float]
    rows: list[list[str]]


@dataclass
class TextBlock:
    bbox: tuple[float, float, float, float]
    text: str


def sha_text(text: str, length: int = 12) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def slugify(name: str, max_len: int = 90) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return (name or "document")[:max_len]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def yaml_quote(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def parse_markdown_meta(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    meta, _ = strip_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    return meta


def overlap_ratio(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area = max((ax1 - ax0) * (ay1 - ay0), 1.0)
    return inter / area


def clean_text_block(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_cell(value: object) -> str:
    text = "" if value is None else str(value)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "<br>", text.strip())
    text = text.replace("|", r"\|")
    return text


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max((len(row) for row in rows), default=0)
    if width == 0:
        return ""
    normalized = [(row + [""] * width)[:width] for row in rows]
    normalized = [[clean_cell(cell) for cell in row] for row in normalized]
    header = normalized[0]
    body = normalized[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def table_density(rows: list[list[str]]) -> int:
    return sum(1 for row in rows for cell in row if clean_cell(cell))


def extract_pdf_pages(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("PyMuPDF is required. Run scripts/bootstrap_env.py first.") from exc

    doc = fitz.open(path)
    page_markdowns: list[str] = []
    table_records: list[dict[str, Any]] = []
    for page_index, page in enumerate(doc, 1):
        tables: list[TableBlock] = []
        try:
            found = page.find_tables().tables
        except Exception:
            found = []
        for table_index, table in enumerate(found, 1):
            rows = table.extract()
            if not rows or table_density(rows) < 2:
                continue
            bbox = tuple(float(v) for v in table.bbox)
            tables.append(TableBlock(page_index, table_index, bbox, rows))
            table_records.append(
                {
                    "page": page_index,
                    "table": table_index,
                    "rows": len(rows),
                    "cols": max((len(row) for row in rows), default=0),
                    "bbox": list(bbox),
                    "non_empty_cells": table_density(rows),
                }
            )

        text_blocks: list[TextBlock] = []
        for block in page.get_text("blocks"):
            if len(block) < 5:
                continue
            x0, y0, x1, y1, text = block[:5]
            block_type = block[6] if len(block) > 6 else 0
            if block_type != 0:
                continue
            cleaned = clean_text_block(str(text))
            if not cleaned:
                continue
            bbox = (float(x0), float(y0), float(x1), float(y1))
            if any(overlap_ratio(bbox, table.bbox) > 0.18 for table in tables):
                continue
            text_blocks.append(TextBlock(bbox, cleaned))

        items: list[tuple[float, float, str, str]] = []
        for block in text_blocks:
            x0, y0, _x1, _y1 = block.bbox
            items.append((y0, x0, "text", block.text))
        for table in tables:
            x0, y0, _x1, _y1 = table.bbox
            md = markdown_table(table.rows)
            if not md:
                continue
            content = "\n".join(
                [
                    f"### Source PDF page {table.page} table {table.index}",
                    "",
                    md,
                ]
            )
            items.append((y0, x0, "table", content))

        items.sort(key=lambda item: (round(item[0], 1), round(item[1], 1), 0 if item[2] == "text" else 1))
        lines = [f"<!-- source_page: {page_index} -->", ""]
        for _y, _x, kind, content in items:
            if kind == "table":
                lines.append(content)
            else:
                lines.append(content)
            lines.append("")
        page_markdowns.append("\n".join(lines).strip())
    return page_markdowns, table_records


def make_repaired_markdown(record: dict[str, Any], source_path: Path, original_md: Path | None) -> tuple[str, list[dict[str, Any]]]:
    page_markdowns, table_records = extract_pdf_pages(source_path)
    meta = parse_markdown_meta(original_md) if original_md else {}
    doc_id = record.get("file_id") or meta.get("doc_id") or sha_text(str(source_path), 14)
    title = record.get("doc_title") or meta.get("doc_title") or source_path.stem
    merged_meta: dict[str, object] = {
        **meta,
        "doc_id": doc_id,
        "doc_title": title,
        "source_path": record.get("source_path") or str(source_path),
        "origin_archive": record.get("origin_archive", ""),
        "archive_member_path": record.get("archive_member_path", ""),
        "document_kind": "pdf",
        "source_extension": ".pdf",
        "source_size": record.get("source_size") or source_path.stat().st_size,
        "extraction_method": "pymupdf_page_text_blocks_plus_find_tables",
        "ocr_status": meta.get("ocr_status", record.get("ocr_status", "text_ok")),
        "quality_status": "needs_human_spotcheck" if table_records else "ok",
        "page_aware_tables": len(table_records),
    }
    body_lines = [f"# {title}", ""]
    body_lines.extend(page_markdowns)
    return make_frontmatter(merged_meta) + "\n\n".join(body_lines).strip() + "\n", table_records


def iter_pdf_records(kb: Path) -> Iterable[dict[str, Any]]:
    manifest = kb / "manifest.jsonl"
    records = read_jsonl(manifest)
    seen: set[str] = set()
    for record in records:
        if record.get("extension") != ".pdf":
            continue
        file_id = str(record.get("file_id", ""))
        status = record.get("conversion_status", "")
        if not file_id or file_id in seen or status not in {"converted", "skipped_existing"}:
            continue
        seen.add(file_id)
        yield record


def find_original_md(record: dict[str, Any]) -> Path | None:
    value = record.get("output_markdown")
    if value:
        path = Path(str(value))
        if path.exists():
            return path
    return None


def resolve_pdf_source(record: dict[str, Any]) -> Path:
    """Return the actual PDF bytes while keeping archive provenance in metadata."""
    candidates = [record.get("working_path"), record.get("source_path")]
    checked: list[str] = []
    for value in candidates:
        if not value:
            continue
        path = Path(str(value))
        checked.append(str(path))
        if path.suffix.lower() == ".pdf" and path.exists():
            return path
    raise FileNotFoundError("PDF source not found; checked: " + "; ".join(checked))


def write_chunks(md_root: Path, chunks_out: Path) -> int:
    chunks_out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with chunks_out.open("w", encoding="utf-8", newline="\n") as handle:
        for md_path in sorted(md_root.rglob("*.md")):
            for chunk in section_chunks(md_path):
                handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                count += 1
    return count


def write_reports(kb: Path, report_rows: list[dict[str, Any]], out_root: Path, chunk_count: int) -> None:
    qa = kb / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    json_path = qa / "pdf_page_table_repair_report.json"
    json_path.write_text(json.dumps(report_rows, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    csv_path = qa / "pdf_page_table_repair_report.csv"
    fieldnames = [
        "file_id",
        "title",
        "status",
        "pages",
        "tables",
        "output_markdown",
        "source_path",
        "note",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in fieldnames} for row in report_rows)

    converted = [row for row in report_rows if row.get("status") == "converted"]
    errors = [row for row in report_rows if row.get("status") == "error"]
    with_tables = [row for row in converted if int(row.get("tables", 0) or 0) > 0]
    lines = [
        "# PDF Page Table Repair Report",
        "",
        f"- Output root: `{out_root}`",
        f"- Converted PDFs: {len(converted)}",
        f"- PDFs with detected tables: {len(with_tables)}",
        f"- Errors: {len(errors)}",
        f"- Repaired chunks: {chunk_count}",
        "",
        "## Notes",
        "",
        "- This output is page-aware: each page has a `source_page` marker.",
        "- Text blocks overlapping detected table bounding boxes are skipped, then Markdown tables are inserted at their page positions.",
        "- Files with tables remain `needs_human_spotcheck` until source-page QA is complete.",
        "",
        "## Errors",
        "",
    ]
    if not errors:
        lines.append("- none")
    for row in errors:
        lines.append(f"- `{row.get('file_id')}` {row.get('title')}: {row.get('note')}")
    (qa / "pdf_page_table_repair_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create page-aware PDF Markdown with PyMuPDF table placement.")
    parser.add_argument("--kb", required=True, help="Existing converted knowledge-base folder.")
    parser.add_argument("--output-subdir", default="documents_page_aware", help="Output subdirectory under KB.")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N PDF records.")
    parser.add_argument("--file-id", action="append", default=[], help="Only process selected file_id; can be repeated.")
    parser.add_argument("--rebuild-chunks", action="store_true", help="Write chunks_page_aware.jsonl.")
    args = parser.parse_args()

    kb = Path(args.kb).expanduser().resolve()
    out_root = kb / args.output_subdir
    out_root.mkdir(parents=True, exist_ok=True)
    wanted = set(args.file_id)
    report_rows: list[dict[str, Any]] = []
    records = list(iter_pdf_records(kb))
    if wanted:
        records = [record for record in records if str(record.get("file_id")) in wanted]
    if args.limit:
        records = records[: args.limit]

    for index, record in enumerate(records, 1):
        file_id = str(record.get("file_id"))
        title = str(record.get("doc_title") or file_id)
        print(f"[{index}/{len(records)}] {title}")
        try:
            source_path = resolve_pdf_source(record)
        except FileNotFoundError:
            source_path = Path(str(record.get("working_path") or record.get("source_path", "")))
        output_path = out_root / "pdf" / f"{slugify(title)}--{file_id}.md"
        row = {
            "file_id": file_id,
            "title": title,
            "status": "",
            "pages": "",
            "tables": "",
            "output_markdown": str(output_path),
            "source_path": str(source_path),
            "note": "",
        }
        try:
            if not source_path.exists():
                raise FileNotFoundError(f"source not found: {source_path}")
            repaired, tables = make_repaired_markdown(record, source_path, find_original_md(record))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(repaired, encoding="utf-8", newline="\n")
            row.update(
                {
                    "status": "converted",
                    "pages": len(re.findall(r"<!-- source_page:", repaired)),
                    "tables": len(tables),
                    "note": "needs_human_spotcheck" if tables else "",
                }
            )
        except Exception as exc:  # noqa: BLE001
            row.update({"status": "error", "note": str(exc)})
        report_rows.append(row)

    chunk_count = 0
    if args.rebuild_chunks:
        chunk_count = write_chunks(out_root, kb / "chunks_page_aware.jsonl")
    write_reports(kb, report_rows, out_root, chunk_count)
    print(f"Output: {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
