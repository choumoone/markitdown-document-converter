from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
PAGE_SECTION_RE = re.compile(r"^## Source Page (\d+)\s*$", re.M)
TABLE_HEADING_RE = re.compile(r"^### Source PDF page (\d+) table (\d+)\s*$")


class HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_cell = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and tag == "tr":
            self._current_row = []
        elif self._in_table and tag in {"td", "th"}:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._in_table and tag in {"td", "th"} and self._in_cell:
            text = re.sub(r"\s+", " ", "".join(self._current_cell)).strip()
            self._current_row.append(text)
            self._in_cell = False
        elif self._in_table and tag == "tr":
            if any(cell.strip() for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = []
        elif tag == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False
            self._current_table = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def parse_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta


def extract_markdown_tables(section: str) -> list[list[str]]:
    tables: list[list[str]] = []
    current: list[str] = []
    for line in section.splitlines():
        if line.strip().startswith("|"):
            current.append(line.rstrip())
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    markdown_tables = [table for table in tables if len(table) >= 2]
    if markdown_tables:
        return markdown_tables

    parser = HtmlTableParser()
    parser.feed(section)
    html_tables: list[list[str]] = []
    for rows in parser.tables:
        width = max((len(row) for row in rows), default=0)
        if not rows or width == 0:
            continue
        normalized = [(row + [""] * width)[:width] for row in rows]
        escaped = [[cell.replace("|", r"\|") for cell in row] for row in normalized]
        lines = [
            "| " + " | ".join(escaped[0]) + " |",
            "| " + " | ".join(["---"] * width) + " |",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in escaped[1:])
        html_tables.append(lines)
    return html_tables


def extract_enhanced_pages(path: Path) -> tuple[dict[str, str], dict[int, list[list[str]]]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = parse_frontmatter(text)
    matches = list(PAGE_SECTION_RE.finditer(text))
    pages: dict[int, list[list[str]]] = {}
    for index, match in enumerate(matches):
        page_no = int(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        tables = extract_markdown_tables(text[start:end])
        if tables:
            pages[page_no] = tables
    return meta, pages


def find_source_markdown(kb: Path, doc_id: str, source_roots: list[Path] | None = None) -> tuple[Path, Path]:
    roots = source_roots or [
        kb / "documents_page_aware",
        kb / "documents_llm_ready" / "documents",
        kb / "documents",
    ]
    for root in roots:
        if not root.exists():
            continue
        matches = sorted(root.rglob(f"*--{doc_id}.md"))
        if matches:
            return matches[0], root
    raise FileNotFoundError(f"Cannot find markdown for doc_id={doc_id}")


def table_block_end(lines: list[str], start_index: int) -> int:
    index = start_index
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("|") or not stripped:
            index += 1
            continue
        break
    return index


def replace_page_tables(
    text: str,
    enhanced_pages: dict[int, list[list[str]]],
) -> tuple[str, list[dict[str, Any]]]:
    lines = text.splitlines()
    replacements: list[dict[str, Any]] = []
    output: list[str] = []
    index = 0
    page_table_seen: dict[int, int] = {}

    while index < len(lines):
        line = lines[index]
        match = TABLE_HEADING_RE.match(line.strip())
        if not match:
            output.append(line)
            index += 1
            continue

        page_no = int(match.group(1))
        table_no = int(match.group(2))
        page_table_seen[page_no] = page_table_seen.get(page_no, 0) + 1
        table_index = page_table_seen[page_no] - 1
        end = table_block_end(lines, index + 1)
        original_rows = sum(1 for item in lines[index + 1 : end] if item.strip().startswith("|"))

        output.append(line)
        output.append("")
        enhanced_tables = enhanced_pages.get(page_no, [])
        if table_index < len(enhanced_tables):
            output.append(
                f"<!-- table_repair: minimax_m3; source_page: {page_no}; table: {table_no}; status: visual_rebuild_needs_spotcheck -->"
            )
            output.extend(enhanced_tables[table_index])
            replacements.append(
                {
                    "page": page_no,
                    "table": table_no,
                    "status": "replaced",
                    "original_rows": original_rows,
                    "enhanced_rows": len(enhanced_tables[table_index]),
                }
            )
        else:
            output.extend(lines[index + 1 : end])
            replacements.append(
                {
                    "page": page_no,
                    "table": table_no,
                    "status": "kept_missing_enhanced_table",
                    "original_rows": original_rows,
                    "enhanced_rows": 0,
                }
            )
        index = end

    for page_no, tables in enhanced_pages.items():
        seen = page_table_seen.get(page_no, 0)
        if seen != len(tables):
            replacements.append(
                {
                    "page": page_no,
                    "table": "",
                    "status": f"table_count_mismatch_original_{seen}_enhanced_{len(tables)}",
                    "original_rows": "",
                    "enhanced_rows": "",
                }
            )
    return "\n".join(output) + "\n", replacements


def write_report(kb: Path, records: list[dict[str, Any]]) -> None:
    qa = kb / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    csv_path = qa / "minimax_table_repair_apply_report.csv"
    json_path = qa / "minimax_table_repair_apply_report.json"
    md_path = qa / "minimax_table_repair_apply_report.md"
    fieldnames = [
        "doc_id",
        "source_markdown",
        "output_markdown",
        "page",
        "table",
        "status",
        "original_rows",
        "enhanced_rows",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# MiniMax Table Repair Apply Report",
        "",
        "Visual table rebuilds were inserted only at existing page-aware table headings.",
        "Generated Markdown still requires human spot-check before formal citation.",
        "",
    ]
    by_status: dict[str, int] = {}
    for record in records:
        by_status[record["status"]] = by_status.get(record["status"], 0) + 1
    for status, count in sorted(by_status.items()):
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Records", ""])
    for record in records:
        lines.append(
            f"- `{record['doc_id']}` p{record['page']} t{record['table']}: {record['status']} "
            f"({record['original_rows']} -> {record['enhanced_rows']})"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply MiniMax table-enhancement sidecars back into page-aware Markdown table positions."
    )
    parser.add_argument("--kb", required=True, help="Converted knowledge-base output folder.")
    parser.add_argument(
        "--enhanced-file",
        action="append",
        default=[],
        help="A table_enhanced/*.tables.md file. Can be repeated. Defaults to all files.",
    )
    parser.add_argument(
        "--out-root",
        default="documents_minimax_repaired",
        help="Output folder under the KB root.",
    )
    parser.add_argument(
        "--enhanced-dir",
        action="append",
        default=[],
        help="A folder containing *.tables.md files. Can be repeated. Defaults to table_enhanced when --enhanced-file is omitted.",
    )
    parser.add_argument(
        "--source-root",
        action="append",
        default=[],
        help="Source Markdown root to repair. Can be repeated; defaults to documents_page_aware, documents_llm_ready/documents, documents.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing repaired Markdown.")
    args = parser.parse_args()

    kb = Path(args.kb).resolve()
    enhanced_files = [Path(item).resolve() for item in args.enhanced_file]
    if not enhanced_files:
        enhanced_dirs = [kb / item for item in args.enhanced_dir] if args.enhanced_dir else [kb / "table_enhanced"]
        enhanced_files = sorted(path for root in enhanced_dirs for path in root.glob("*.tables.md"))
    source_roots = [Path(item).resolve() for item in args.source_root] or None
    out_root = kb / args.out_root
    records: list[dict[str, Any]] = []

    for enhanced_file in enhanced_files:
        meta, pages = extract_enhanced_pages(enhanced_file)
        doc_id = meta.get("doc_id")
        if not doc_id:
            raise ValueError(f"{enhanced_file} is missing doc_id frontmatter")
        source_md, source_root = find_source_markdown(kb, doc_id, source_roots)
        rel = source_md.relative_to(source_root)
        output_md = out_root / rel
        if output_md.exists() and not args.force:
            raise FileExistsError(f"{output_md} exists; pass --force to overwrite")
        output_md.parent.mkdir(parents=True, exist_ok=True)
        repaired, replacements = replace_page_tables(
            source_md.read_text(encoding="utf-8", errors="replace"),
            pages,
        )
        output_md.write_text(repaired, encoding="utf-8", newline="\n")
        shutil.copystat(source_md, output_md)
        for replacement in replacements:
            record = {
                "doc_id": doc_id,
                "source_markdown": str(source_md),
                "output_markdown": str(output_md),
                **replacement,
            }
            records.append(record)

    write_report(kb, records)
    print(json.dumps({"processed_docs": len(enhanced_files), "records": len(records), "out_root": str(out_root)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
