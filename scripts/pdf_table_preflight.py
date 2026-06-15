from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


def slugify(name: str, max_len: int = 90) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return (name or "document")[:max_len]


def table_density(rows: list[list[Any]]) -> int:
    total = 0
    for row in rows or []:
        for cell in row or []:
            if cell is not None and str(cell).strip():
                total += 1
    return total


def scan_pdf(path: Path) -> dict[str, Any]:
    try:
        import fitz
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("PyMuPDF is required. Run scripts/bootstrap_env.py first.") from exc

    result: dict[str, Any] = {
        "source_path": str(path),
        "title": path.stem,
        "status": "scanned",
        "pages": 0,
        "tables": 0,
        "table_pages": [],
        "max_cols": 0,
        "max_rows": 0,
        "needs_page_aware_repair": False,
        "error": "",
    }
    try:
        with fitz.open(path) as doc:
            result["pages"] = len(doc)
            table_pages: list[int] = []
            for page_no, page in enumerate(doc, 1):
                try:
                    found = page.find_tables().tables
                except Exception:
                    found = []
                page_table_count = 0
                for table in found:
                    rows = table.extract()
                    if not rows or table_density(rows) < 2:
                        continue
                    result["tables"] += 1
                    page_table_count += 1
                    result["max_rows"] = max(int(result["max_rows"]), len(rows))
                    result["max_cols"] = max(int(result["max_cols"]), max((len(row) for row in rows), default=0))
                if page_table_count:
                    table_pages.append(page_no)
            result["table_pages"] = table_pages
            result["needs_page_aware_repair"] = bool(table_pages)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)
    return result


def iter_pdfs(source: Path, exclude: list[Path]) -> list[Path]:
    source = source.resolve()
    excludes = [item.resolve() for item in exclude if item.exists()]
    if source.is_file():
        return [source] if source.suffix.lower() == ".pdf" else []
    pdfs: list[Path] = []
    for path in sorted(source.rglob("*.pdf")):
        resolved = path.resolve()
        if any(resolved == ex or ex in resolved.parents for ex in excludes):
            continue
        pdfs.append(path)
    return pdfs


def write_reports(rows: list[dict[str, Any]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_rows = []
    for row in rows:
        json_rows.append({**row, "table_pages": list(row.get("table_pages") or [])})
    (out_dir / "pdf_table_preflight.json").write_text(
        json.dumps(json_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )

    csv_path = out_dir / "pdf_table_preflight.csv"
    fieldnames = [
        "title",
        "status",
        "pages",
        "tables",
        "table_pages",
        "max_rows",
        "max_cols",
        "needs_page_aware_repair",
        "source_path",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            flat["table_pages"] = ",".join(str(v) for v in row.get("table_pages") or [])
            writer.writerow({key: flat.get(key, "") for key in fieldnames})

    scanned = [row for row in rows if row.get("status") == "scanned"]
    errors = [row for row in rows if row.get("status") == "error"]
    with_tables = [row for row in scanned if int(row.get("tables") or 0) > 0]
    total_tables = sum(int(row.get("tables") or 0) for row in scanned)
    lines = [
        "# PDF Table Preflight Report",
        "",
        f"- PDFs scanned: {len(rows)}",
        f"- PDFs with detected tables: {len(with_tables)}",
        f"- Total detected tables: {total_tables}",
        f"- Errors: {len(errors)}",
        "",
        "## High Table Count Files",
        "",
    ]
    for row in sorted(with_tables, key=lambda item: int(item.get("tables") or 0), reverse=True)[:30]:
        pages = ",".join(str(v) for v in row.get("table_pages") or [])
        lines.append(f"- `{row.get('title')}`: tables={row.get('tables')}, pages={pages}")
    lines.extend(["", "## Errors", ""])
    if not errors:
        lines.append("- none")
    for row in errors:
        lines.append(f"- `{row.get('title')}`: {row.get('error')}")
    (out_dir / "pdf_table_preflight.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan source PDFs for table pages before conversion.")
    parser.add_argument("--source", required=True, help="Source folder or PDF file.")
    parser.add_argument("--output", required=True, help="QA output folder.")
    parser.add_argument("--exclude", action="append", default=[], help="Path to exclude from recursive scan.")
    parser.add_argument("--limit", type=int, default=0, help="Scan only first N PDFs.")
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    exclude = [Path(item).expanduser() for item in args.exclude]
    pdfs = iter_pdfs(source, exclude)
    if args.limit:
        pdfs = pdfs[: args.limit]

    rows: list[dict[str, Any]] = []
    for index, pdf in enumerate(pdfs, 1):
        print(f"[{index}/{len(pdfs)}] {pdf.name}", flush=True)
        rows.append(scan_pdf(pdf))
    write_reports(rows, Path(args.output).expanduser())
    print(f"Report: {Path(args.output).expanduser() / 'pdf_table_preflight.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
