from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PUNCT_RE = re.compile(r"[\s\u3000,，.。;；:：!！?？()（）\[\]【】<>《》\"'“”‘’、/\\|-]+")
DOC_ID_RE = re.compile(r"--([0-9a-f]{10,})$")
HARD_AUDIT_REASONS = [
    "mostly_blank_header",
    "too_many_empty_cells",
    "table_too_small_or_parse_failed",
    "table_count_mismatch",
    "missing_table_pages",
    "missing_repaired_markdown",
]


@dataclass
class ExtractedTable:
    engine: str
    page: int
    index: int
    rows: list[list[str]]
    flavor: str = ""
    accuracy: float | None = None


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for part in re.split(r"[,;，；\s]+", value or ""):
        part = part.strip()
        if part.isdigit():
            pages.add(int(part))
    return sorted(pages)


def doc_id_from_md(path: str) -> str:
    stem = Path(path).stem
    match = DOC_ID_RE.search(stem)
    return match.group(1) if match else stem.rsplit("--", 1)[-1]


def resolve_docs_root(kb: Path, value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    candidates = [
        kb / "documents_llm_ready_quality_first" / "documents",
        kb / "documents_llm_ready" / "documents",
        kb / "documents_page_aware",
        kb / "documents",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def build_md_index(docs_root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    if not docs_root.exists():
        return index
    for path in docs_root.rglob("*.md"):
        index[doc_id_from_md(str(path))] = path
    return index


def repair_rows_by_source(kb: Path) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    rows = read_csv(kb / "qa" / "pdf_page_table_repair_report.csv")
    by_source = {Path(row.get("source_path", "")).name: row for row in rows if row.get("source_path")}
    by_id = {row.get("file_id", ""): row for row in rows if row.get("file_id")}
    return by_source, by_id


def manifest_sources(kb: Path) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for row in read_jsonl(kb / "manifest.jsonl"):
        source = Path(str(row.get("source_path", "")))
        if source.suffix.lower() == ".pdf":
            sources[source.name] = source
    return sources


def source_from_markdown(path: Path) -> Path | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^source_path:\s*"?(.+?)"?\s*$', text, re.M)
    if not match:
        return None
    raw = match.group(1).strip().strip('"')
    source = Path(raw)
    return source if source.exists() else None


def choose_markdown(audit_path: str, docs_index: dict[str, Path]) -> Path | None:
    if audit_path:
        path = Path(audit_path)
        if path.exists():
            return path
        doc_id = doc_id_from_md(audit_path)
        if doc_id in docs_index:
            return docs_index[doc_id]
    return None


def choose_source(
    audit_row: dict[str, str],
    markdown: Path | None,
    repair_by_source: dict[str, dict[str, str]],
    repair_by_id: dict[str, dict[str, str]],
    manifest_by_name: dict[str, Path],
) -> Path | None:
    source_name = audit_row.get("source_name", "")
    doc_id = doc_id_from_md(str(markdown)) if markdown else ""
    for candidate in [
        Path(repair_by_id.get(doc_id, {}).get("source_path", "")),
        Path(repair_by_source.get(source_name, {}).get("source_path", "")),
        manifest_by_name.get(source_name, Path()),
    ]:
        if candidate and str(candidate) != "." and candidate.exists():
            return candidate
    if markdown:
        return source_from_markdown(markdown)
    return None


def clean_cell(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n+", " ", text)
    return text.strip()


def normalize_rows(rows: list[list[object]]) -> list[list[str]]:
    normalized = [[clean_cell(cell) for cell in row] for row in rows if row is not None]
    while normalized and not any(cell for cell in normalized[-1]):
        normalized.pop()
    return normalized


def numeric_value(text: str) -> float | None:
    value = text.strip()
    value = value.replace(",", "").replace("，", "").replace("%", "")
    value = value.replace("￥", "").replace("¥", "")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def relaxed_text(text: str) -> str:
    return PUNCT_RE.sub("", text).lower()


def compare_cell(left: str, right: str) -> bool:
    left_num = numeric_value(left)
    right_num = numeric_value(right)
    if left_num is not None or right_num is not None:
        return left_num is not None and right_num is not None and abs(left_num - right_num) <= 1e-9
    return relaxed_text(left) == relaxed_text(right)


def shape(rows: list[list[str]]) -> tuple[int, int]:
    return (len(rows), max((len(row) for row in rows), default=0))


def pad_rows(rows: list[list[str]]) -> list[list[str]]:
    width = shape(rows)[1]
    return [(row + [""] * width)[:width] for row in rows]


def compare_tables(pdfplumber_table: ExtractedTable | None, camelot_table: ExtractedTable | None) -> tuple[str, str]:
    if pdfplumber_table is None and camelot_table is None:
        return "conflict", "no_table_from_either_engine"
    if pdfplumber_table is None:
        return "conflict", "pdfplumber_missing_table"
    if camelot_table is None:
        return "conflict", "camelot_missing_table"
    left_shape = shape(pdfplumber_table.rows)
    right_shape = shape(camelot_table.rows)
    if left_shape != right_shape:
        return "conflict", f"shape_mismatch:{left_shape[0]}x{left_shape[1]}!={right_shape[0]}x{right_shape[1]}"
    mismatches = 0
    for left_row, right_row in zip(pad_rows(pdfplumber_table.rows), pad_rows(camelot_table.rows)):
        for left, right in zip(left_row, right_row):
            if not compare_cell(left, right):
                mismatches += 1
                if mismatches >= 3:
                    return "conflict", "cell_mismatch:3_or_more"
    if mismatches:
        return "conflict", f"cell_mismatch:{mismatches}"
    return "high", "engines_agree_structure_and_cells"


def hard_audit_reasons(reason_text: str, page: int) -> list[str]:
    reasons: list[str] = []
    for reason in HARD_AUDIT_REASONS:
        if reason not in reason_text:
            continue
        page_scoped = re.search(rf"(?:^|;)p{page}t\d+:{re.escape(reason)}(?:;|$)", reason_text)
        doc_scoped = reason in {"table_count_mismatch", "missing_table_pages", "missing_repaired_markdown"}
        if page_scoped or doc_scoped:
            reasons.append(reason)
    return reasons


def extract_pdfplumber(source: Path, pages: list[int]) -> dict[int, list[ExtractedTable]]:
    try:
        import pdfplumber
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("pdfplumber is missing. Run bootstrap_env.py or install pdfplumber.") from exc

    result: dict[int, list[ExtractedTable]] = defaultdict(list)
    with pdfplumber.open(str(source)) as pdf:
        for page_no in pages:
            if page_no < 1 or page_no > len(pdf.pages):
                continue
            try:
                tables = pdf.pages[page_no - 1].extract_tables() or []
            except Exception:
                tables = []
            for index, rows in enumerate(tables, 1):
                normalized = normalize_rows(rows)
                if normalized:
                    result[page_no].append(ExtractedTable("pdfplumber", page_no, index, normalized))
    return result


def camelot_available() -> tuple[bool, str]:
    try:
        import camelot  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"camelot_unavailable:{exc.__class__.__name__}"
    ghostscript = shutil.which("gswin64c") or shutil.which("gswin32c") or shutil.which("gs")
    note = "ghostscript_found" if ghostscript else "ghostscript_not_found_lattice_may_fail"
    return True, note


def extract_camelot(source: Path, pages: list[int]) -> tuple[dict[int, list[ExtractedTable]], str]:
    available, note = camelot_available()
    if not available:
        return {}, note
    import camelot

    result: dict[int, list[ExtractedTable]] = defaultdict(list)
    errors: list[str] = []
    for page_no in pages:
        page_tables: list[ExtractedTable] = []
        for flavor in ["lattice", "stream"]:
            try:
                tables = camelot.read_pdf(str(source), pages=str(page_no), flavor=flavor)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"p{page_no}:{flavor}:{exc.__class__.__name__}")
                continue
            for index, table in enumerate(tables, 1):
                rows = normalize_rows(table.df.values.tolist())
                if not rows:
                    continue
                accuracy = None
                try:
                    accuracy = float(table.parsing_report.get("accuracy", 0))
                except Exception:
                    accuracy = None
                page_tables.append(ExtractedTable("camelot", page_no, index, rows, flavor, accuracy))
            if page_tables:
                break
        result[page_no] = page_tables
    if errors and not any(result.values()):
        note = "camelot_errors:" + ";".join(errors[:8])
    elif errors:
        note = note + "; partial_errors:" + ";".join(errors[:5])
    return result, note


def candidate_rows(kb: Path, docs_root: Path, levels: set[str]) -> list[dict[str, Any]]:
    audit_path = kb / "qa" / "pdf_table_quality_audit_files.csv"
    audit = read_csv(audit_path)
    if not audit:
        raise FileNotFoundError(f"{audit_path} not found. Run pdf_table_quality_audit.py first.")

    docs_index = build_md_index(docs_root)
    repair_by_source, repair_by_id = repair_rows_by_source(kb)
    manifest_by_name = manifest_sources(kb)

    rows: list[dict[str, Any]] = []
    for audit_row in audit:
        level = (audit_row.get("level") or "").lower()
        pages = parse_pages(audit_row.get("table_pages", ""))
        if level not in levels or not pages:
            continue
        markdown = choose_markdown(audit_row.get("markdown_path", ""), docs_index)
        source = choose_source(audit_row, markdown, repair_by_source, repair_by_id, manifest_by_name)
        doc_id = doc_id_from_md(str(markdown or audit_row.get("markdown_path", "")))
        rows.append(
            {
                "file_id": doc_id,
                "title": audit_row.get("title") or Path(audit_row.get("source_name", "")).stem or doc_id,
                "level": level,
                "reason": audit_row.get("reasons", ""),
                "pages": pages,
                "markdown": markdown,
                "source": source,
                "source_name": audit_row.get("source_name", ""),
            }
        )
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def write_candidates(
    csv_path: Path,
    selection_path: Path,
    docs: dict[str, dict[str, Any]],
    conflict_pages: dict[str, dict[int, list[str]]],
) -> None:
    candidate_fields = ["file_id", "title", "md_path", "priority", "reason"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=candidate_fields)
        writer.writeheader()
        for file_id in sorted(conflict_pages):
            doc = docs[file_id]
            reasons = sorted({reason for values in conflict_pages[file_id].values() for reason in values})
            writer.writerow(
                {
                    "file_id": file_id,
                    "title": doc.get("title", file_id),
                    "md_path": str(doc.get("markdown") or ""),
                    "priority": "high",
                    "reason": "dual_engine_conflict:" + ";".join(reasons[:8]),
                }
            )
    selection: list[dict[str, Any]] = []
    for file_id in sorted(conflict_pages):
        doc = docs[file_id]
        pages = []
        for page_no, reasons in sorted(conflict_pages[file_id].items()):
            reason_text = "dual_engine_conflict:" + ";".join(sorted(set(reasons))[:5])
            pages.append({"page": page_no, "reason": reason_text, "type": "table"})
        selection.append(
            {
                "file_id": file_id,
                "title": doc.get("title", file_id),
                "pages": pages,
                "source_path": str(doc.get("source") or ""),
            }
        )
    selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def write_markdown_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]], camelot_note: str) -> None:
    conflict_docs = sorted({row["file_id"] for row in rows if row["confidence"] == "conflict"})
    lines = [
        "# Dual Engine Table Prefilter",
        "",
        "This is a local risk prefilter. `high` means the two local engines agree on shape and cell text for the selected table, not full source verification.",
        "",
        "## Summary",
        "",
        f"- Documents scanned: {summary['documents_scanned']}",
        f"- Pages scanned: {summary['pages_scanned']}",
        f"- Tables compared: {summary['tables_compared']}",
        f"- Low-risk local agreements: {summary['high']}",
        f"- Conflicts queued for MiniMax/manual review: {summary['conflict']}",
        f"- Camelot status: {camelot_note}",
        f"- MiniMax candidate CSV: `{summary['candidate_csv']}`",
        f"- MiniMax selection JSON: `{summary['selection_json']}`",
        "",
        "## Conflict Documents",
        "",
    ]
    if not conflict_docs:
        lines.append("- none")
    for file_id in conflict_docs[:80]:
        doc_rows = [row for row in rows if row["file_id"] == file_id and row["confidence"] == "conflict"]
        title = doc_rows[0].get("title", file_id)
        pages = sorted({int(row["page"]) for row in doc_rows})
        reasons = sorted({row["reason"] for row in doc_rows})
        lines.append(f"- `{file_id}` {title}: pages={','.join(map(str, pages))}; {('; '.join(reasons))[:260]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare pdfplumber and Camelot on risky PDF table pages before paid repair.")
    parser.add_argument("--kb", required=True, help="Converted corpus output folder.")
    parser.add_argument("--docs-root", help="Markdown documents root to match audit rows. Defaults to LLM-ready/page-aware/documents.")
    parser.add_argument("--levels", default="red,yellow", help="Comma-separated audit levels to scan, default red,yellow.")
    parser.add_argument("--limit-files", type=int, default=0, help="Maximum documents to scan.")
    parser.add_argument("--limit-pages", type=int, default=0, help="Maximum pages to scan across documents.")
    parser.add_argument("--output-prefix", default="dual_engine_table_prefilter", help="qa output filename prefix.")
    parser.add_argument(
        "--ignore-audit-hard-flags",
        action="store_true",
        help="Allow local engine agreement to clear hard audit flags. Not recommended for quality-first corpora.",
    )
    args = parser.parse_args()

    kb = Path(args.kb).expanduser().resolve()
    qa = kb / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    docs_root = resolve_docs_root(kb, args.docs_root)
    levels = {level.strip().lower() for level in args.levels.split(",") if level.strip()}

    targets = candidate_rows(kb, docs_root, levels)
    if args.limit_files:
        targets = targets[: args.limit_files]

    report_rows: list[dict[str, Any]] = []
    docs: dict[str, dict[str, Any]] = {}
    conflict_pages: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    pages_scanned = 0
    camelot_notes: list[str] = []

    for doc in targets:
        file_id = doc["file_id"]
        docs[file_id] = doc
        source = doc.get("source")
        markdown = doc.get("markdown")
        selected_pages: list[int] = []
        for page in doc["pages"]:
            if args.limit_pages and pages_scanned >= args.limit_pages:
                break
            selected_pages.append(page)
            pages_scanned += 1
        if not selected_pages:
            continue
        if not source or not Path(source).exists():
            for page in selected_pages:
                row = {
                    "file_id": file_id,
                    "title": doc["title"],
                    "source_path": str(source or ""),
                    "markdown_path": str(markdown or ""),
                    "page": page,
                    "table_index": 0,
                    "pdfplumber_shape": "",
                    "camelot_shape": "",
                    "confidence": "conflict",
                    "risk": "high",
                    "reason": "source_pdf_missing",
                    "camelot_flavor": "",
                    "camelot_accuracy": "",
                }
                report_rows.append(row)
                conflict_pages[file_id][page].append(row["reason"])
            continue

        pdf_tables = extract_pdfplumber(Path(source), selected_pages)
        camelot_tables, camelot_note = extract_camelot(Path(source), selected_pages)
        camelot_notes.append(camelot_note)

        for page in selected_pages:
            left_tables = pdf_tables.get(page, [])
            right_tables = camelot_tables.get(page, [])
            max_count = max(len(left_tables), len(right_tables), 1)
            for index in range(max_count):
                left = left_tables[index] if index < len(left_tables) else None
                right = right_tables[index] if index < len(right_tables) else None
                confidence, reason = compare_tables(left, right)
                hard_reasons = [] if args.ignore_audit_hard_flags else hard_audit_reasons(doc.get("reason", ""), page)
                if confidence == "high" and hard_reasons:
                    confidence = "conflict"
                    reason = "audit_hard_flag:" + ",".join(hard_reasons)
                row = {
                    "file_id": file_id,
                    "title": doc["title"],
                    "source_path": str(source),
                    "markdown_path": str(markdown or ""),
                    "page": page,
                    "table_index": index + 1,
                    "pdfplumber_shape": "x".join(map(str, shape(left.rows))) if left else "",
                    "camelot_shape": "x".join(map(str, shape(right.rows))) if right else "",
                    "confidence": confidence,
                    "risk": "low" if confidence == "high" else "high",
                    "reason": reason,
                    "camelot_flavor": right.flavor if right else "",
                    "camelot_accuracy": right.accuracy if right and right.accuracy is not None else "",
                }
                report_rows.append(row)
                if confidence == "conflict":
                    conflict_pages[file_id][page].append(reason)
        if args.limit_pages and pages_scanned >= args.limit_pages:
            break

    report_jsonl = qa / f"{args.output_prefix}_report.jsonl"
    report_md = qa / f"{args.output_prefix}_report.md"
    candidate_csv = qa / "minimax_dual_engine_candidates.csv"
    selection_json = qa / "minimax_dual_engine_selection.json"
    write_jsonl(report_jsonl, report_rows)
    write_candidates(candidate_csv, selection_json, docs, conflict_pages)

    camelot_note = "; ".join(dict.fromkeys(camelot_notes)) if camelot_notes else camelot_available()[1]
    summary = {
        "documents_scanned": len({row["file_id"] for row in report_rows}),
        "pages_scanned": len({(row["file_id"], int(row["page"])) for row in report_rows}),
        "tables_compared": len(report_rows),
        "high": sum(1 for row in report_rows if row["confidence"] == "high"),
        "conflict": sum(1 for row in report_rows if row["confidence"] == "conflict"),
        "report_jsonl": str(report_jsonl),
        "report_md": str(report_md),
        "candidate_csv": str(candidate_csv),
        "selection_json": str(selection_json),
        "docs_root": str(docs_root),
        "levels": sorted(levels),
        "camelot_status": camelot_note,
    }
    (qa / f"{args.output_prefix}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    write_markdown_report(report_md, summary, report_rows, camelot_note)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
