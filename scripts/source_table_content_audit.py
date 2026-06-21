from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from postprocess_markdown import strip_frontmatter


TABLE_HEADING_RE = re.compile(r"^### Source PDF page (\d+) table (\d+)\s*$", re.M)


def normalize(value: object) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKC", str(value or "")) if ch.isalnum()
    )


def char_recall(source: str, target: str) -> float:
    source_count = Counter(normalize(source))
    target_count = Counter(normalize(target))
    total = sum(source_count.values())
    if not total:
        return 1.0
    matched = sum(min(count, target_count.get(char, 0)) for char, count in source_count.items())
    return matched / total


def page_body(text: str, page: int) -> str:
    match = re.search(
        rf"(?s)<!--\s*source_page:\s*{page}\s*-->\s*(.*?)(?=<!--\s*source_page:\s*\d+\s*-->|\Z)",
        text,
    )
    return match.group(1) if match else ""


def pdf_rows(md_path: Path, text: str, source: Path) -> list[dict[str, Any]]:
    import pdfplumber

    markers = [(int(page), int(table)) for page, table in TABLE_HEADING_RE.findall(text)]
    rows: list[dict[str, Any]] = []
    if not markers:
        return rows
    with pdfplumber.open(source) as document:
        cache: dict[int, list[list[list[str | None]]]] = {}
        for page, table_no in markers:
            base = {
                "document": md_path.name,
                "source": str(source),
                "page": page,
                "table": table_no,
                "kind": "pdf",
            }
            if page < 1 or page > len(document.pages):
                rows.append({**base, "status": "source_page_out_of_range", "cells": 0, "exact_cell_rate": 0.0, "char_recall": 0.0})
                continue
            if page not in cache:
                cache[page] = document.pages[page - 1].extract_tables() or []
            source_tables = cache[page]
            if table_no < 1 or table_no > len(source_tables):
                rows.append({**base, "status": "source_table_not_redetected", "cells": 0, "exact_cell_rate": 0.0, "char_recall": 0.0})
                continue
            cells = [
                normalize(cell)
                for source_row in source_tables[table_no - 1]
                for cell in source_row
                if normalize(cell)
            ]
            target = normalize(page_body(text, page))
            exact = sum(cell in target for cell in cells)
            rows.append(
                {
                    **base,
                    "status": "checked",
                    "cells": len(cells),
                    "exact_cell_rate": round(exact / len(cells), 6) if cells else 1.0,
                    "char_recall": round(char_recall("".join(cells), target), 6),
                }
            )
    return rows


def word_row(md_path: Path, text: str, source: Path) -> dict[str, Any]:
    from docx import Document

    document = Document(source)
    cells = [
        normalize(cell.text)
        for table in document.tables
        for row in table.rows
        for cell in row.cells
        if normalize(cell.text)
    ]
    target = normalize(text)
    exact = sum(cell in target for cell in cells)
    unique_cells = list(dict.fromkeys(cells))
    return {
        "document": md_path.name,
        "source": str(source),
        "page": "",
        "table": "all",
        "kind": "word",
        "status": "checked",
        "cells": len(cells),
        "exact_cell_rate": round(exact / len(cells), 6) if cells else 1.0,
        "char_recall": round(char_recall("".join(unique_cells), target), 6),
    }


def manifest_sources(kb: Path | None) -> dict[str, Path]:
    if kb is None:
        return {}
    manifest = kb / "manifest.jsonl"
    if not manifest.exists():
        return {}
    sources: dict[str, Path] = {}
    with manifest.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            file_id = str(record.get("file_id", ""))
            if not file_id or record.get("conversion_status") not in {"converted", "skipped_existing"}:
                continue
            for key in ("working_path", "source_path"):
                value = record.get(key)
                if value and Path(str(value)).is_file():
                    source = Path(str(value))
                    if source.suffix.lower() == ".doc":
                        digest = hashlib.sha1(str(source).encode("utf-8", errors="ignore")).hexdigest()[:10]
                        converted = kb / "work" / "office_converted" / f"libreoffice--{digest}" / f"{source.stem}.docx"
                        if converted.is_file():
                            source = converted
                    sources[file_id] = source
                    break
    return sources


def audit(docs_root: Path, kb: Path | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_by_id = manifest_sources(kb)
    for md_path in sorted(docs_root.rglob("*.md")):
        text = md_path.read_text(encoding="utf-8", errors="replace")
        meta, _body = strip_frontmatter(text)
        source_value = meta.get("source_path", "")
        source = Path(source_value) if source_value else None
        doc_id = str(meta.get("doc_id", ""))
        manifest_source = source_by_id.get(doc_id)
        if manifest_source is not None:
            source = manifest_source
        if source is None or not source.exists():
            rows.append(
                {
                    "document": md_path.name,
                    "source": source_value,
                    "page": "",
                    "table": "",
                    "kind": "unknown",
                    "status": "source_missing",
                    "cells": 0,
                    "exact_cell_rate": 0.0,
                    "char_recall": 0.0,
                }
            )
            continue
        suffix = source.suffix.lower()
        if suffix == ".pdf":
            rows.extend(pdf_rows(md_path, text, source))
        elif suffix == ".docx":
            rows.append(word_row(md_path, text, source))
        elif suffix == ".doc":
            rows.append(
                {
                    "document": md_path.name,
                    "source": str(source),
                    "page": "",
                    "table": "all",
                    "kind": "legacy_word",
                    "status": "legacy_word_requires_converted_docx_or_visual_review",
                    "cells": 0,
                    "exact_cell_rate": 0.0,
                    "char_recall": 0.0,
                }
            )
    return rows


def load_attestations(path: Path | None) -> dict[tuple[str, str, str], dict[str, str]]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        records = json.loads(path.read_text(encoding="utf-8-sig"))
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            records = list(csv.DictReader(handle))
    allowed = {"source_page_verified", "false_positive_removed", "converted_docx_verified"}
    attestations: dict[tuple[str, str, str], dict[str, str]] = {}
    for record in records:
        status = str(record.get("status", "")).strip()
        if status not in allowed:
            raise ValueError(f"Unsupported attestation status: {status!r}")
        key = (
            str(record.get("document", "")).strip(),
            str(record.get("page", "")).strip(),
            str(record.get("table", "")).strip(),
        )
        if not key[0]:
            raise ValueError("Attestation record is missing document")
        attestations[key] = {"status": status, "note": str(record.get("note", "")).strip()}
    return attestations


def apply_attestations(rows: list[dict[str, Any]], attestations: dict[tuple[str, str, str], dict[str, str]]) -> None:
    for row in rows:
        keys = [
            (str(row["document"]), str(row["page"]), str(row["table"])),
            (str(row["document"]), "", str(row["table"])),
            (str(row["document"]), "", ""),
        ]
        attestation = next((attestations[key] for key in keys if key in attestations), None)
        if attestation:
            row["original_status"] = row["status"]
            row["status"] = f"attested:{attestation['status']}"
            row["attestation_note"] = attestation["note"]


def write_reports(kb: Path, rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    qa = kb / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    csv_path = qa / "source_table_content_audit.csv"
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    checked = [row for row in rows if row["status"] == "checked"]
    below = [row for row in checked if float(row["char_recall"]) < threshold]
    attested = [row for row in rows if str(row["status"]).startswith("attested:")]
    review = [row for row in rows if row["status"] != "checked" and not str(row["status"]).startswith("attested:")]
    summary = {
        "rows": len(rows),
        "checked": len(checked),
        "below_threshold": len(below),
        "attested_manual_reviews": len(attested),
        "manual_review": len(review),
        "threshold": threshold,
        "minimum_char_recall": min((float(row["char_recall"]) for row in checked), default=1.0),
        "report_csv": str(csv_path),
    }
    (qa / "source_table_content_audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    lines = [
        "# Source Table Content Audit",
        "",
        "This compares source table cell text with the Markdown on the same source page. It is a coverage gate, not proof of merged-cell geometry.",
        "",
        f"- Checked rows: {len(checked)}",
        f"- Below {threshold:.0%} character recall: {len(below)}",
        f"- Attested manual reviews: {len(attested)}",
        f"- Manual review queue: {len(review)}",
        f"- Minimum checked character recall: {summary['minimum_char_recall']:.2%}",
        "",
        "## Below Threshold",
        "",
    ]
    lines.extend(
        f"- `{row['document']}` page {row['page']} table {row['table']}: {float(row['char_recall']):.2%}"
        for row in below
    )
    if not below:
        lines.append("- none")
    lines.extend(["", "## Manual Review Queue", ""])
    lines.extend(
        f"- `{row['document']}` page {row['page']}: `{row['status']}`" for row in review
    )
    if not review:
        lines.append("- none")
    (qa / "source_table_content_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare source table cell text with final Markdown page content.")
    parser.add_argument("--kb", required=True, help="Converted knowledge-base output folder.")
    parser.add_argument("--docs-root", help="Markdown root. Defaults to documents_llm_ready/documents.")
    parser.add_argument("--min-char-recall", type=float, default=0.90, help="Minimum accepted source-table character recall.")
    parser.add_argument(
        "--review-attestations",
        help="Optional CSV/JSON with document,page,table,status,note. Allowed statuses: source_page_verified, false_positive_removed, converted_docx_verified.",
    )
    parser.add_argument("--require-clean", action="store_true", help="Exit 2 when any checked table is below threshold or any item needs manual review.")
    args = parser.parse_args()
    kb = Path(args.kb).expanduser().resolve()
    docs_root = Path(args.docs_root).expanduser().resolve() if args.docs_root else kb / "documents_llm_ready" / "documents"
    rows = audit(docs_root, kb)
    attestations = load_attestations(Path(args.review_attestations).expanduser().resolve() if args.review_attestations else None)
    apply_attestations(rows, attestations)
    summary = write_reports(kb, rows, args.min_char_recall)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.require_clean and (summary["below_threshold"] or summary["manual_review"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
