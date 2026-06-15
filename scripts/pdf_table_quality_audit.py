from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


TABLE_HEADING_RE = re.compile(r"^### Source PDF page (\d+) table (\d+)\s*$", re.M)
PAGE_MARKER_RE = re.compile(r"<!-- source_page: (\d+) -->")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def table_pages(value: str) -> set[int]:
    pages: set[int] = set()
    for part in (value or "").split(","):
        part = part.strip()
        if part.isdigit():
            pages.add(int(part))
    return pages


def parse_markdown_tables(text: str) -> list[dict[str, Any]]:
    matches = list(TABLE_HEADING_RE.finditer(text))
    tables: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end]
        rows = []
        for line in block.splitlines():
            if not line.strip().startswith("|"):
                if rows:
                    break
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells):
                continue
            rows.append(cells)
        width = max((len(row) for row in rows), default=0)
        non_empty = sum(1 for row in rows for cell in row if cell.strip())
        total = max(sum(len(row) for row in rows), 1)
        header = rows[0] if rows else []
        tables.append(
            {
                "page": int(match.group(1)),
                "table": int(match.group(2)),
                "rows": len(rows),
                "cols": width,
                "empty_ratio": round(1 - non_empty / total, 4),
                "blank_header_cells": sum(1 for cell in header if not cell.strip()),
                "header_cells": len(header),
                "long_cell_count": sum(1 for row in rows for cell in row if len(cell) > 120),
                "line": text[: match.start()].count("\n") + 1,
            }
        )
    return tables


def classify_table(table: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    level = "green"
    rows = int(table["rows"])
    cols = int(table["cols"])
    empty_ratio = float(table["empty_ratio"])
    blank_header = int(table["blank_header_cells"])
    header_cells = int(table["header_cells"])
    long_cells = int(table["long_cell_count"])

    if rows < 2 or cols < 2:
        reasons.append("table_too_small_or_parse_failed")
        level = "red"
    if header_cells and blank_header / max(header_cells, 1) >= 0.5:
        reasons.append("mostly_blank_header")
        level = "red"
    if empty_ratio >= 0.65:
        reasons.append("too_many_empty_cells")
        level = "red"
    elif empty_ratio >= 0.42:
        reasons.append("many_empty_cells")
        level = max(level, "yellow", key={"green": 0, "yellow": 1, "red": 2}.get)
    if cols >= 8:
        reasons.append("wide_table")
        level = max(level, "yellow", key={"green": 0, "yellow": 1, "red": 2}.get)
    if rows >= 20:
        reasons.append("long_table")
        level = max(level, "yellow", key={"green": 0, "yellow": 1, "red": 2}.get)
    if long_cells:
        reasons.append("long_wrapped_cells")
        level = max(level, "yellow", key={"green": 0, "yellow": 1, "red": 2}.get)
    return level, reasons


def worse(a: str, b: str) -> str:
    order = {"green": 0, "yellow": 1, "red": 2}
    return a if order[a] >= order[b] else b


def audit(kb: Path, docs_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    preflight = read_csv(kb / "qa" / "pdf_table_preflight.csv")
    repair = read_csv(kb / "qa" / "pdf_page_table_repair_report.csv")
    repair_by_source = {Path(row.get("source_path", "")).name: row for row in repair}

    file_rows: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []
    md_by_id = {path.stem.rsplit("--", 1)[-1]: path for path in docs_root.rglob("*.md")}
    md_by_name = {path.name: path for path in docs_root.rglob("*.md")}

    for row in preflight:
        source_name = Path(row.get("source_path", "")).name
        source_stem = Path(source_name).stem
        expected_tables = int(row.get("tables") or 0)
        expected_pages = table_pages(row.get("table_pages", ""))
        repair_row = repair_by_source.get(source_name)
        md_path: Path | None = None
        if repair_row:
            candidate = Path(repair_row.get("output_markdown", ""))
            doc_id = str(repair_row.get("file_id", ""))
            md_path = md_by_id.get(doc_id)
            if md_path is None and candidate.exists():
                md_path = candidate
        if md_path is None:
            for name, path in md_by_name.items():
                if source_stem and source_stem in name:
                    md_path = path
                    break

        level = "green"
        reasons: list[str] = []
        markdown_tables: list[dict[str, Any]] = []
        markdown_pages: set[int] = set()
        if expected_tables > 0 and md_path is None:
            level = "red"
            reasons.append("missing_repaired_markdown")
        elif md_path:
            text = md_path.read_text(encoding="utf-8", errors="replace")
            markdown_tables = parse_markdown_tables(text)
            markdown_pages = {int(page) for page in PAGE_MARKER_RE.findall(text)}
            if expected_tables != len(markdown_tables):
                level = "red"
                reasons.append(f"table_count_mismatch:{expected_tables}!={len(markdown_tables)}")
            table_pages_found = {int(table["page"]) for table in markdown_tables}
            missing_table_pages = expected_pages - table_pages_found
            if missing_table_pages:
                level = "red"
                reasons.append("missing_table_pages:" + ",".join(map(str, sorted(missing_table_pages))))
            if markdown_pages and sorted(markdown_pages) != list(range(1, max(markdown_pages) + 1)):
                level = worse(level, "yellow")
                reasons.append("non_contiguous_page_markers")

            page_table_counts: dict[int, int] = defaultdict(int)
            for table in markdown_tables:
                t_level, t_reasons = classify_table(table)
                level = worse(level, t_level)
                page_table_counts[int(table["page"])] += 1
                if t_reasons:
                    reasons.extend(f"p{table['page']}t{table['table']}:{reason}" for reason in t_reasons)
                table_rows.append(
                    {
                        "source_name": source_name,
                        "markdown_path": str(md_path),
                        "page": table["page"],
                        "table": table["table"],
                        "level": t_level,
                        "reasons": ";".join(t_reasons),
                        "rows": table["rows"],
                        "cols": table["cols"],
                        "empty_ratio": table["empty_ratio"],
                        "line": table["line"],
                    }
                )
            if any(count > 1 for count in page_table_counts.values()):
                level = worse(level, "yellow")
                reasons.append("multiple_tables_on_page")
            if expected_tables >= 10:
                level = worse(level, "yellow")
                reasons.append("many_tables_in_document")

        if expected_tables == 0:
            level = "green"
            reasons = ["no_detected_tables"]
        file_rows.append(
            {
                "source_name": source_name,
                "title": row.get("title", ""),
                "level": level,
                "reasons": ";".join(dict.fromkeys(reasons)),
                "expected_tables": expected_tables,
                "markdown_tables": len(markdown_tables),
                "table_pages": ",".join(map(str, sorted(expected_pages))),
                "markdown_path": str(md_path or ""),
            }
        )
    return file_rows, table_rows


def write_reports(kb: Path, file_rows: list[dict[str, Any]], table_rows: list[dict[str, Any]]) -> None:
    qa = kb / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    file_csv = qa / "pdf_table_quality_audit_files.csv"
    table_csv = qa / "pdf_table_quality_audit_tables.csv"
    with file_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(file_rows[0].keys()) if file_rows else [])
        writer.writeheader()
        writer.writerows(file_rows)
    with table_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(table_rows[0].keys()) if table_rows else [])
        writer.writeheader()
        writer.writerows(table_rows)
    summary = {
        "files": {level: sum(1 for row in file_rows if row["level"] == level) for level in ["green", "yellow", "red"]},
        "tables": {level: sum(1 for row in table_rows if row["level"] == level) for level in ["green", "yellow", "red"]},
        "file_csv": str(file_csv),
        "table_csv": str(table_csv),
    }
    (qa / "pdf_table_quality_audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    lines = [
        "# PDF Table Quality Audit",
        "",
        "This is a risk audit, not a guarantee of cell-level correctness.",
        "",
        "## Summary",
        "",
        f"- Files green: {summary['files']['green']}",
        f"- Files yellow: {summary['files']['yellow']}",
        f"- Files red: {summary['files']['red']}",
        f"- Tables green: {summary['tables']['green']}",
        f"- Tables yellow: {summary['tables']['yellow']}",
        f"- Tables red: {summary['tables']['red']}",
        "",
        "## Red Files",
        "",
    ]
    red = [row for row in file_rows if row["level"] == "red"]
    if not red:
        lines.append("- none")
    for row in red[:80]:
        lines.append(f"- `{row['source_name']}`: {row['reasons']}")
    lines.extend(["", "## Yellow Files With Most Tables", ""])
    yellow = [row for row in file_rows if row["level"] == "yellow"]
    yellow.sort(key=lambda item: int(item["expected_tables"]), reverse=True)
    for row in yellow[:50]:
        lines.append(f"- `{row['source_name']}`: tables={row['expected_tables']}; {row['reasons'][:240]}")
    (qa / "pdf_table_quality_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Risk-audit page-aware PDF table Markdown quality.")
    parser.add_argument("--kb", required=True, help="Converted knowledge-base output folder.")
    parser.add_argument(
        "--docs-root",
        help="Markdown documents root to audit. Defaults to documents_llm_ready/documents.",
    )
    args = parser.parse_args()
    kb = Path(args.kb).expanduser().resolve()
    docs_root = Path(args.docs_root).expanduser().resolve() if args.docs_root else kb / "documents_llm_ready" / "documents"
    file_rows, table_rows = audit(kb, docs_root)
    write_reports(kb, file_rows, table_rows)
    print(f"Files audited: {len(file_rows)}")
    print(f"Tables audited: {len(table_rows)}")
    print(f"Report: {kb / 'qa' / 'pdf_table_quality_audit.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
