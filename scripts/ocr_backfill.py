from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from convert_corpus import write_report, write_unresolved
from postprocess_markdown import clean_markdown, strip_frontmatter, write_chunks


DEFAULT_KB = Path.cwd() / "markdown_output"
DEFAULT_MODEL = "qwen-vl-ocr-latest"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_ENV_FILE = Path.home() / ".codex" / "secrets" / "markitdown-document-converter.env"
TARGET_STATUSES = {"needs_ocr", "needs_review", "vision_ocr_partial"}


def sha(text: str, length: int = 14) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def slugify(name: str, max_len: int = 90) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return (name or "document")[:max_len]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.lstrip("\ufeff").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


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


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def output_path(kb: Path, record: dict[str, Any]) -> Path:
    existing = record.get("output_markdown")
    if existing:
        return Path(existing)
    kind = record.get("document_kind") or "ocr"
    title = record.get("doc_title") or Path(record.get("working_path") or record.get("source_path") or "document").stem
    doc_id = record.get("file_id") or sha(f"{record.get('source_path')}|{record.get('archive_member_path')}")
    return kb / "documents" / str(kind) / f"{slugify(title)}--{doc_id}.md"


def resolve_source(record: dict[str, Any]) -> Path | None:
    for key in ["working_path", "source_path"]:
        value = record.get(key)
        if value and Path(value).exists() and Path(value).is_file():
            return Path(value)
    return None


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def pdf_pages_to_pngs(path: Path, max_pages: int, dpi: int, page_from: int = 1, page_to: int = 0) -> list[tuple[int, Path]]:
    import fitz

    doc = fitz.open(str(path))
    total = len(doc)
    start = max(page_from, 1)
    end = total if page_to <= 0 else min(page_to, total)
    page_numbers = list(range(start, end + 1))
    if max_pages > 0:
        page_numbers = page_numbers[:max_pages]
    tempdir = Path(tempfile.mkdtemp(prefix="markitdown_ocr_"))
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[tuple[int, Path]] = []
    for page_no in page_numbers:
        page = doc.load_page(page_no - 1)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        out = tempdir / f"page_{page_no:04d}.png"
        pix.save(str(out))
        pages.append((page_no, out))
    doc.close()
    return pages


def source_to_images(path: Path, max_pages: int, dpi: int, page_from: int = 1, page_to: int = 0) -> list[tuple[int, Path]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return pdf_pages_to_pngs(path, max_pages=max_pages, dpi=dpi, page_from=page_from, page_to=page_to)
    if ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}:
        return [(1, path)]
    return []


def source_page_count(record: dict[str, Any]) -> int | None:
    source = resolve_source(record)
    if source is None:
        return None
    if source.suffix.lower() == ".pdf":
        import fitz

        doc = fitz.open(str(source))
        try:
            return len(doc)
        finally:
            doc.close()
    if source.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}:
        return 1
    return None


def extract_existing_ocr_pages(out: Path) -> dict[int, str]:
    if not out.exists():
        return {}
    meta, body = strip_frontmatter(out.read_text(encoding="utf-8", errors="replace"))
    if not str(meta.get("extraction_method", "")).startswith("vision_ocr:"):
        return {}
    pages: dict[int, str] = {}
    pattern = re.compile(r"^## Page (\d+)\s*\n(.*?)(?=^## Page \d+\s*\n|\Z)", re.M | re.S)
    for match in pattern.finditer(body):
        page_no = int(match.group(1))
        text = match.group(2).strip()
        if text:
            pages[page_no] = text
    return pages


def write_ocr_markdown(kb: Path, record: dict[str, Any], model: str, pages: dict[int, str], status: str) -> Path:
    source = resolve_source(record)
    title = record.get("doc_title") or (source.stem if source else "document")
    meta = {
        "doc_id": record.get("file_id") or sha(str(source)),
        "doc_title": title,
        "source_path": record.get("source_path", str(source or "")),
        "origin_archive": record.get("origin_archive", ""),
        "archive_member_path": record.get("archive_member_path", ""),
        "document_kind": record.get("document_kind", "ocr"),
        "source_extension": record.get("extension", source.suffix.lower() if source else ""),
        "source_size": record.get("source_size", ""),
        "extraction_method": f"vision_ocr:{model}",
        "ocr_status": status,
        "quality_status": "needs_human_spotcheck",
    }
    body = "\n\n".join(f"## Page {page_no}\n\n{pages[page_no]}".strip() for page_no in sorted(pages))
    out = output_path(kb, record)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(clean_markdown(body, meta), encoding="utf-8", newline="\n")
    return out


def clean_ocr_response(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json|markdown|md)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            payload = json.loads(match.group(0))
            for key in ("markdown", "ocr_markdown", "text", "content"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    text = value.strip()
                    break
        except json.JSONDecodeError:
            pass

    commentary_patterns = [
        r"^\s*The user wants\b.*$",
        r"^\s*Let me\b.*$",
        r"^\s*I can see\b.*$",
        r"^\s*I see\b.*$",
        r"^\s*The page appears\b.*$",
        r"^\s*The document appears\b.*$",
        r"^\s*The document is\b.*$",
        r"^\s*The rest of the page\b.*$",
        r"^\s*Starting from\b.*$",
        r"^\s*Body text starts\b.*$",
        r"^\s*Header:\s*.*$",
        r"^\s*Document number:\s*.*$",
        r"^\s*Title:\s*.*$",
        r"^\s*Signature area\b.*$",
        r"^\s*Page number:\s*.*$",
        r"^\s*At the (top|bottom),\b.*$",
        r"^\s*And the page number\b.*$",
        r"^\s*I'll mark\b.*$",
        r"^\s*我看到.*$",
        r"^\s*让我.*$",
        r"^\s*下面是.*$",
        r"^\s*以下是.*转写.*$",
    ]
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        if any(re.match(pattern, line, flags=re.I) for pattern in commentary_patterns):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned or "[blank page]"


def ocr_image(client: Any, model: str, image_path: Path, page_label: str, retries: int = 3) -> str:
    prompt = (
        "你是严格的 OCR 转写引擎。请只转写图片页面中肉眼可见的原文内容。\n"
        '返回格式必须是一个 JSON 对象：{"markdown":"页面原文 Markdown"}。\n'
        "硬性规则：\n"
        "1. JSON 的 markdown 字段里只放页面原文，不要放解释、分析、摘要、翻译、复核过程或对用户意图的描述。\n"
        "2. 不要出现类似 'The user wants', 'Let me', 'I can see', '我看到', '让我' 的自述句。\n"
        "3. 保留标题、编号层级、条款序号、表格结构、日期、金额、文件编号、组织名称、印章文字和页码。\n"
        "4. 能识别为表格的内容必须用 Markdown 表格表示；不要改写、合并或省略单元格文字。\n"
        "5. 不要输出 HTML、XML、代码块或 ``` 包裹；不要输出 JSON 以外的任何文字。\n"
        "6. 无法确定的字符标为 [uncertain:原样]；空白页只输出 [blank page]。"
    )
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "Output clean OCR transcription only. No reasoning, no commentary, no summaries.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_data_url(image_path)}},
                        ],
                    }
                ],
                temperature=0,
                stream=False,
            )
            text = response.choices[0].message.content or ""
            return clean_ocr_response(text)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(min(2 * attempt, 8))
    raise RuntimeError(f"OCR failed for {page_label}: {last_error}")


def ocr_record(
    client: Any,
    model: str,
    kb: Path,
    record: dict[str, Any],
    max_pages: int,
    dpi: int,
    page_from: int,
    page_to: int,
) -> tuple[dict[str, Any], bool]:
    source = resolve_source(record)
    updated = dict(record)
    if source is None:
        updated["ocr_error"] = "source file not found"
        return updated, False
    images = source_to_images(source, max_pages=max_pages, dpi=dpi, page_from=page_from, page_to=page_to)
    if not images:
        updated["ocr_error"] = f"unsupported source for OCR: {source.suffix}"
        return updated, False

    title = record.get("doc_title") or source.stem
    out = output_path(kb, record)
    page_texts = extract_existing_ocr_pages(out)
    total_pages = source_page_count(record) or len(images)
    for current, (page_no, image) in enumerate(images, 1):
        if page_no in page_texts:
            print(f"Skip existing OCR {title} page {page_no}/{total_pages}")
            continue
        print(f"OCR {title} page {page_no}/{total_pages} ({current}/{len(images)})")
        text = ocr_image(client, model, image, f"{title} page {page_no}")
        page_texts[page_no] = text
        write_ocr_markdown(kb, record, model, page_texts, "vision_ocr_partial")

    complete = all(page_no in page_texts for page_no in range(1, total_pages + 1))
    status = "vision_ocr_completed" if complete else "needs_ocr"
    out = write_ocr_markdown(kb, record, model, page_texts, status)
    updated.update(
        {
            "output_markdown": str(out),
            "extraction_method": f"vision_ocr:{model}",
            "ocr_status": status,
            "quality_status": "needs_human_spotcheck",
            "ocr_pages": len(page_texts),
            "ocr_error": "" if complete else f"partial OCR saved: {len(page_texts)}/{total_pages} pages",
        }
    )
    return updated, complete


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill OCR for low-text PDFs and image documents.")
    parser.add_argument("--kb", default=str(DEFAULT_KB), help="Converted Markdown output folder.")
    parser.add_argument("--env-file", default=os.environ.get("MARKITDOWN_ENV_FILE", str(DEFAULT_ENV_FILE)), help="Optional local env file for OCR credentials.")
    parser.add_argument("--model", help="OCR vision model.")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL.")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable containing the API key.")
    parser.add_argument("--status", action="append", choices=sorted(TARGET_STATUSES), help="OCR statuses to process.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of files to OCR.")
    parser.add_argument("--max-pages", type=int, default=0, help="Maximum pages per PDF, 0 means all pages.")
    parser.add_argument("--page-from", type=int, default=1, help="First PDF page to OCR, 1-based.")
    parser.add_argument("--page-to", type=int, default=0, help="Last PDF page to OCR, 0 means through the end.")
    parser.add_argument("--dpi", type=int, default=180, help="PDF render DPI.")
    parser.add_argument("--title-contains", help="Only process records whose doc_title contains this string.")
    parser.add_argument("--include-kind", action="append", default=[], help="Only process records with this document_kind.")
    parser.add_argument("--exclude-kind", action="append", default=[], help="Skip records with this document_kind.")
    parser.add_argument("--skip-over-pages", type=int, default=0, help="Skip source files over this page count, 0 means no page-count filter.")
    parser.add_argument("--rebuild-chunks", action="store_true", help="Rebuild chunks.jsonl after OCR.")
    parser.add_argument("--refresh-only", action="store_true", help="Refresh chunks and QA reports without calling OCR.")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))
    kb = Path(args.kb).expanduser().resolve()
    manifest = kb / "manifest.jsonl"
    records = load_jsonl(manifest)
    if args.refresh_only:
        chunks = 0
        if args.rebuild_chunks:
            chunks = write_chunks(kb / "documents", kb / "chunks.jsonl")
            print(f"Rebuilt chunks: {chunks}")
        else:
            chunks_path = kb / "chunks.jsonl"
            if chunks_path.exists():
                chunks = sum(1 for _ in chunks_path.open("r", encoding="utf-8"))
        write_unresolved(kb, records)
        write_report(kb, records, chunks)
        print("Refreshed QA reports")
        return 0

    args.model = args.model or os.environ.get("MARKITDOWN_OCR_MODEL", DEFAULT_MODEL)
    args.base_url = args.base_url or os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL)
    key = os.environ.get(args.api_key_env)
    if not key:
        raise SystemExit(f"Missing API key env var: {args.api_key_env}")

    from openai import OpenAI

    statuses = set(args.status or TARGET_STATUSES)
    targets = [r for r in records if r.get("ocr_status") in statuses]
    if args.title_contains:
        targets = [r for r in targets if args.title_contains in str(r.get("doc_title", ""))]
    if args.include_kind:
        included = set(args.include_kind)
        targets = [r for r in targets if r.get("document_kind") in included]
    if args.exclude_kind:
        excluded = set(args.exclude_kind)
        targets = [r for r in targets if r.get("document_kind") not in excluded]
    if args.skip_over_pages:
        kept = []
        for record in targets:
            try:
                pages = source_page_count(record)
            except Exception as exc:  # noqa: BLE001
                record["ocr_error"] = f"page count failed: {exc}"
                continue
            if pages is None or pages <= args.skip_over_pages:
                kept.append(record)
            else:
                record["ocr_error"] = f"skipped by --skip-over-pages={args.skip_over_pages}; source pages={pages}"
        targets = kept
    if args.limit:
        targets = targets[: args.limit]

    client = OpenAI(api_key=key, base_url=args.base_url)
    by_key = {(r.get("source_path"), r.get("archive_member_path"), r.get("doc_title")): i for i, r in enumerate(records)}
    done = 0
    for record in targets:
        updated, ok = ocr_record(
            client,
            args.model,
            kb,
            record,
            max_pages=args.max_pages,
            dpi=args.dpi,
            page_from=args.page_from,
            page_to=args.page_to,
        )
        key_tuple = (record.get("source_path"), record.get("archive_member_path"), record.get("doc_title"))
        if key_tuple in by_key:
            records[by_key[key_tuple]] = updated
        done += int(ok)
    write_jsonl(manifest, records)
    chunks = 0
    if args.rebuild_chunks:
        chunks = write_chunks(kb / "documents", kb / "chunks.jsonl")
        print(f"Rebuilt chunks: {chunks}")
    else:
        chunks_path = kb / "chunks.jsonl"
        if chunks_path.exists():
            chunks = sum(1 for _ in chunks_path.open("r", encoding="utf-8"))
    write_unresolved(kb, records)
    write_report(kb, records, chunks)
    print(f"OCR completed records: {done}/{len(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
