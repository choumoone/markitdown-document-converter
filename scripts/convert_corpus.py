from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from postprocess_markdown import clean_markdown, strip_frontmatter, write_chunks


DEFAULT_SOURCE = Path.cwd()
DEFAULT_OUTPUT = Path.cwd() / "markdown_output"
SKILL_ENV_HOME = Path.home() / ".codex" / "skill-envs" / "markitdown-document-converter"

ARCHIVE_EXTS = {".zip", ".rar"}
CONVERT_EXTS = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
LEGACY_OFFICE_EXTS = {".doc", ".xls", ".ppt"}

DOC_KIND_BY_EXT = {
    ".pdf": "pdf",
    ".docx": "word",
    ".doc": "word",
    ".xlsx": "spreadsheet",
    ".xls": "spreadsheet",
    ".csv": "spreadsheet",
    ".pptx": "presentation",
    ".ppt": "presentation",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".webp": "image",
    ".html": "html",
    ".htm": "html",
    ".txt": "text",
    ".md": "markdown",
    ".json": "structured-text",
    ".xml": "structured-text",
}


@dataclass
class SourceItem:
    path: Path
    source_path: Path
    origin_archive: str = ""
    archive_member_path: str = ""


def sha(text: str, length: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def safe_str(path: Path) -> str:
    return str(path)


def slugify(name: str, max_len: int = 90) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return (name or "document")[:max_len]


def is_office_temp(path: Path) -> bool:
    return path.name.startswith("~$") or "/~$" in path.as_posix()


def document_kind(path: Path) -> str:
    return DOC_KIND_BY_EXT.get(path.suffix.lower(), "document")


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def assert_under(path: Path, parent: Path) -> None:
    path.resolve().relative_to(parent.resolve())


def safe_rmtree(path: Path, allowed_parent: Path) -> None:
    if not path.exists():
        return
    assert_under(path, allowed_parent)
    shutil.rmtree(path)


def find_7zip() -> Path | None:
    candidates = [
        os.environ.get("SEVEN_ZIP_EXE"),
        str(SKILL_ENV_HOME / "tools" / "7zip-portable" / "full" / "7z.exe"),
        str(SKILL_ENV_HOME / "tools" / "7zip" / "7z.exe"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for item in candidates:
        if not item:
            continue
        path = Path(item)
        if path.exists():
            return path
    return None


def find_soffice() -> Path | None:
    candidates = [
        os.environ.get("SOFFICE_EXE"),
        shutil.which("soffice"),
        shutil.which("soffice.exe"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for item in candidates:
        if item and Path(item).exists():
            return Path(item)
    return None


def decode_zip_member_name(member: zipfile.ZipInfo) -> str:
    name = member.filename
    if member.flag_bits & 0x800:
        return name
    try:
        fixed = name.encode("cp437").decode("gbk")
    except UnicodeError:
        return name
    if any("\u4e00" <= ch <= "\u9fff" for ch in fixed):
        return fixed
    return name


def extract_zip(archive: Path, dest: Path) -> list[tuple[Path, str]]:
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    extracted: list[tuple[Path, str]] = []
    with zipfile.ZipFile(archive) as zf:
        members = [
            (member, decode_zip_member_name(member).replace("\\", "/"))
            for member in zf.infolist()
        ]
        file_names = [name for _member, name in members if name and not name.endswith("/")]
        parts = [Path(name).parts for name in file_names]
        common_root = parts[0][0] if parts and all(len(item) > 1 and item[0] == parts[0][0] for item in parts) else ""
        for member, raw_name in members:
            if not raw_name or raw_name.endswith("/"):
                continue
            relative_name = Path(*Path(raw_name).parts[1:]) if common_root else Path(raw_name)
            target = (dest / relative_name).resolve()
            assert_under(target, dest_resolved)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            extracted.append((target, raw_name))
    return extracted


def extract_rar(archive: Path, dest: Path, sevenzip: Path | None) -> tuple[bool, str]:
    if sevenzip is None:
        return False, "7-Zip not found; RAR extraction skipped."
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(sevenzip), "x", "-y", f"-o{dest}", str(archive)],
        capture_output=True,
    )
    if result.returncode != 0:
        error_bytes = result.stderr or result.stdout or b""
        return False, error_bytes.decode("utf-8", errors="replace").strip()
    return True, ""


def iter_source_files(source: Path, output: Path) -> Iterable[Path]:
    if source.is_file():
        yield source
        return
    for path in sorted(source.rglob("*")):
        if not path.is_file() or is_office_temp(path):
            continue
        if is_under(path, output):
            continue
        yield path


def discover(source: Path, output: Path, keep_work: bool = False) -> tuple[list[SourceItem], list[dict[str, Any]]]:
    work_root = output / "work" / "extracted"
    if not keep_work:
        safe_rmtree(work_root, output)
    work_root.mkdir(parents=True, exist_ok=True)
    sevenzip = find_7zip()
    items: list[SourceItem] = []
    records: list[dict[str, Any]] = []
    queue = [SourceItem(path=p, source_path=p) for p in iter_source_files(source, output)]

    while queue:
        item = queue.pop(0)
        ext = item.path.suffix.lower()
        source_ref = item.archive_member_path or safe_str(item.path)
        base_record = {
            "source_path": safe_str(item.source_path),
            "working_path": safe_str(item.path),
            "origin_archive": item.origin_archive,
            "archive_member_path": item.archive_member_path,
            "extension": ext,
            "document_kind": document_kind(item.path),
            "source_size": item.path.stat().st_size if item.path.exists() else 0,
        }

        if ext in ARCHIVE_EXTS:
            dest = work_root / f"{slugify(item.path.stem)}--{sha(source_ref, 10)}"
            if not keep_work:
                safe_rmtree(dest, output)
            if ext == ".zip":
                try:
                    zip_members = extract_zip(item.path, dest)
                    ok, error = True, ""
                except Exception as exc:  # noqa: BLE001
                    zip_members = []
                    ok, error = False, str(exc)
            else:
                ok, error = extract_rar(item.path, dest, sevenzip)
            records.append(
                {
                    **base_record,
                    "conversion_status": "archive_extracted" if ok else "archive_failed",
                    "quality_status": "ok" if ok else "unresolved",
                    "error": error,
                }
            )
            if not ok:
                continue
            if ext == ".zip":
                for child, member in zip_members:
                    queue.append(
                        SourceItem(
                            path=child,
                            source_path=item.source_path,
                            origin_archive=item.origin_archive or safe_str(item.path),
                            archive_member_path=member,
                        )
                    )
                continue
            for child in sorted(dest.rglob("*")):
                if child.is_file() and not is_office_temp(child):
                    member = child.relative_to(dest).as_posix()
                    queue.append(
                        SourceItem(
                            path=child,
                            source_path=item.source_path,
                            origin_archive=item.origin_archive or safe_str(item.path),
                            archive_member_path=member,
                        )
                    )
            continue

        if ext in CONVERT_EXTS:
            items.append(item)
            records.append({**base_record, "conversion_status": "discovered", "quality_status": "", "error": ""})
        else:
            records.append(
                {
                    **base_record,
                    "conversion_status": "unsupported",
                    "quality_status": "unresolved",
                    "error": f"Unsupported extension: {ext}",
                }
            )
    return items, records


def convert_legacy_office(path: Path, work_dir: Path) -> tuple[Path | None, str]:
    if platform.system() != "Windows":
        return None, "Legacy Office conversion currently requires Windows."
    target_ext = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}.get(path.suffix.lower())
    if target_ext:
        soffice = find_soffice()
        if soffice:
            lo_dir = work_dir / f"libreoffice--{sha(str(path), 10)}"
            lo_dir.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                [str(soffice), "--headless", "--convert-to", target_ext.lstrip("."), "--outdir", str(lo_dir), str(path)],
                capture_output=True,
                timeout=180,
            )
            output = lo_dir / f"{path.stem}{target_ext}"
            if result.returncode == 0 and output.exists():
                return output, ""
            lo_message = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace").strip()
            libreoffice_error = f"LibreOffice conversion failed: {lo_message or 'output not created'}"
        else:
            libreoffice_error = "LibreOffice not found"
    else:
        libreoffice_error = "Unsupported legacy Office extension"
    try:
        import win32com.client  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return None, f"{libreoffice_error}; pywin32 not available: {exc}"

    work_dir.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    if ext == ".doc":
        out = work_dir / f"{path.stem}--{sha(str(path), 8)}.docx"
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            doc = word.Documents.Open(str(path))
            doc.SaveAs(str(out), FileFormat=16)
            doc.Close(False)
            word.Quit()
            return out, ""
        except Exception as exc:  # noqa: BLE001
            try:
                word.Quit()
            except Exception:
                pass
            return None, f"{libreoffice_error}; Word COM conversion failed: {exc}"
    if ext == ".xls":
        out = work_dir / f"{path.stem}--{sha(str(path), 8)}.xlsx"
        try:
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Visible = False
            wb = excel.Workbooks.Open(str(path))
            wb.SaveAs(str(out), FileFormat=51)
            wb.Close(False)
            excel.Quit()
            return out, ""
        except Exception as exc:  # noqa: BLE001
            try:
                excel.Quit()
            except Exception:
                pass
            return None, f"{libreoffice_error}; Excel COM conversion failed: {exc}"
    if ext == ".ppt":
        out = work_dir / f"{path.stem}--{sha(str(path), 8)}.pptx"
        try:
            ppt = win32com.client.DispatchEx("PowerPoint.Application")
            deck = ppt.Presentations.Open(str(path), WithWindow=False)
            deck.SaveAs(str(out), 24)
            deck.Close()
            ppt.Quit()
            return out, ""
        except Exception as exc:  # noqa: BLE001
            try:
                ppt.Quit()
            except Exception:
                pass
            return None, f"{libreoffice_error}; PowerPoint COM conversion failed: {exc}"
    return None, "Unsupported legacy Office extension."


def build_converter(ocr_model: str | None, enable_plugins: bool = True):
    try:
        from markitdown import MarkItDown
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("MarkItDown is not installed. Run scripts/bootstrap_env.py first.") from exc

    kwargs: dict[str, Any] = {"enable_plugins": enable_plugins}
    if ocr_model:
        try:
            from openai import OpenAI
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("OpenAI client is not installed; run bootstrap_env.py.") from exc
        base_url = os.environ.get("OPENAI_BASE_URL")
        client = OpenAI(base_url=base_url) if base_url else OpenAI()
        kwargs.update(
            {
                "llm_client": client,
                "llm_model": ocr_model,
                "llm_prompt": (
                    "Extract visible text from this document page. Preserve headings, tables, "
                    "dates, amounts, IDs, and punctuation. Do not summarize or invent content."
                ),
            }
        )
    return MarkItDown(**kwargs)


def pdf_text_density(path: Path) -> tuple[str, str]:
    if path.suffix.lower() != ".pdf":
        return "", ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = len(reader.pages)
        samples: list[str] = []
        for page in reader.pages[: min(pages, 10)]:
            page_text = (page.extract_text() or "").strip()
            if len(page_text) >= 30:
                samples.append(page_text)
            if len(samples) >= 3:
                break
        if not samples:
            return "needs_ocr", f"low embedded text density: no text-bearing pages found in first {min(pages, 10)} pages"
        text = "\n".join(samples)
        sample = len(samples)
        per_page = len(text.strip()) / sample if sample else 0
        if per_page < 30:
            return "needs_ocr", f"low embedded text density: {per_page:.1f} chars/page in first {sample} pages"
        if per_page < 180:
            return "needs_review", f"low embedded text density: {per_page:.1f} chars/page in first {sample} pages"
        return "text_ok", ""
    except Exception as exc:  # noqa: BLE001
        return "needs_review", f"pdf text probe failed: {exc}"


def convert_item(
    item: SourceItem,
    converter: Any,
    output: Path,
    ocr_model: str | None,
    dry_run: bool = False,
    skip_existing: bool = False,
) -> dict[str, Any]:
    ext = item.path.suffix.lower()
    title = item.path.stem
    source_ref = item.archive_member_path or safe_str(item.path)
    doc_id = sha(f"{item.source_path}|{source_ref}", 14)
    kind = document_kind(item.path)
    out_dir = output / "documents" / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slugify(title)}--{doc_id}.md"
    record: dict[str, Any] = {
        "file_id": doc_id,
        "source_path": safe_str(item.source_path),
        "working_path": safe_str(item.path),
        "origin_archive": item.origin_archive,
        "archive_member_path": item.archive_member_path,
        "extension": ext,
        "document_kind": kind,
        "source_size": item.path.stat().st_size if item.path.exists() else 0,
        "output_markdown": safe_str(out_path),
        "doc_title": title,
        "conversion_status": "",
        "extraction_method": "",
        "ocr_status": "",
        "quality_status": "",
        "error": "",
    }
    if skip_existing and out_path.exists():
        meta, _ = strip_frontmatter(out_path.read_text(encoding="utf-8", errors="replace"))
        record.update(
            {
                "conversion_status": "skipped_existing",
                "extraction_method": meta.get("extraction_method", "existing_markdown"),
                "ocr_status": meta.get("ocr_status", "unknown"),
                "quality_status": meta.get("quality_status", "existing"),
                "doc_title": meta.get("doc_title", title),
            }
        )
        return record
    if ext not in CONVERT_EXTS:
        record.update(
            {
                "conversion_status": "unsupported",
                "quality_status": "unresolved",
                "error": f"Unsupported extension: {ext}",
            }
        )
        return record
    if dry_run:
        record.update({"conversion_status": "dry_run", "quality_status": "not_converted"})
        return record

    conversion_path = item.path
    method = "markitdown"
    if ext in LEGACY_OFFICE_EXTS:
        converted, error = convert_legacy_office(item.path, output / "work" / "office_converted")
        if converted:
            conversion_path = converted
            method = f"office_to_{converted.suffix.lower().lstrip('.')}+markitdown"
        else:
            method = "markitdown_direct_legacy_fallback"
            record["legacy_conversion_error"] = error

    pdf_ocr_status, pdf_probe_note = pdf_text_density(conversion_path)
    try:
        result = converter.convert(str(conversion_path))
        text = getattr(result, "text_content", None) or getattr(result, "markdown", None) or str(result)
    except Exception as exc:  # noqa: BLE001
        record.update(
            {
                "conversion_status": "failed",
                "quality_status": "unresolved",
                "extraction_method": method,
                "ocr_status": pdf_ocr_status,
                "error": str(exc),
            }
        )
        return record

    ocr_status = pdf_ocr_status
    if ext in IMAGE_EXTS and not ocr_model:
        ocr_status = "needs_ocr"
    elif ext in IMAGE_EXTS and ocr_model:
        ocr_status = "vision_ocr_attempted"
    elif pdf_ocr_status == "needs_ocr" and ocr_model:
        ocr_status = "vision_ocr_attempted"
    elif not ocr_status:
        ocr_status = "not_required"

    quality = "ok"
    if len(text.strip()) < 80:
        quality = "needs_review"
    if ocr_status in {"needs_ocr", "needs_review"}:
        quality = "needs_review"

    meta = {
        "doc_id": doc_id,
        "doc_title": title,
        "source_path": safe_str(item.source_path),
        "origin_archive": item.origin_archive,
        "archive_member_path": item.archive_member_path,
        "document_kind": kind,
        "source_extension": ext,
        "source_size": record["source_size"],
        "extraction_method": method,
        "ocr_status": ocr_status,
        "quality_status": quality,
    }
    out_path.write_text(clean_markdown(text, meta), encoding="utf-8", newline="\n")
    record.update(
        {
            "conversion_status": "converted",
            "extraction_method": method,
            "ocr_status": ocr_status,
            "quality_status": quality,
            "probe_note": pdf_probe_note,
        }
    )
    return record


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_unresolved(output: Path, records: list[dict[str, Any]]) -> None:
    unresolved = [
        r
        for r in records
        if r.get("conversion_status") in {"failed", "unsupported", "archive_failed"}
        or r.get("quality_status") in {"unresolved", "needs_review"}
        or r.get("ocr_status") in {"needs_ocr", "needs_review"}
    ]
    path = output / "qa" / "unresolved.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Unresolved and Review Items", ""]
    if not unresolved:
        lines.append("No unresolved items found by automated checks.")
    for record in unresolved:
        lines.append(f"- `{record.get('conversion_status', '')}` `{record.get('quality_status', '')}` `{record.get('ocr_status', '')}`")
        lines.append(f"  - Source: {record.get('source_path', '')}")
        if record.get("archive_member_path"):
            lines.append(f"  - Archive member: {record.get('archive_member_path')}")
        if record.get("error"):
            lines.append(f"  - Error: {record.get('error')}")
        if record.get("probe_note"):
            lines.append(f"  - Probe: {record.get('probe_note')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_report(output: Path, records: list[dict[str, Any]], chunks_count: int) -> None:
    stats: dict[str, int] = {}
    for record in records:
        key = str(record.get("conversion_status", "unknown"))
        stats[key] = stats.get(key, 0) + 1
    lines = [
        "# Conversion Report",
        "",
        "## Summary",
        "",
        f"- Manifest records: {len(records)}",
        f"- Retrieval chunks: {chunks_count}",
    ]
    for key in sorted(stats):
        lines.append(f"- {key}: {stats[key]}")
    lines.extend(
        [
            "",
            "## Acceptance Checks",
            "",
            "- Open several converted Markdown files and compare headings, tables, and visible text against the source.",
            "- Review `qa/unresolved.md` before using the corpus for search, RAG, or formal answers.",
            "- Treat `needs_ocr`, `needs_review`, and `unresolved` records as not fully reliable until checked.",
            "- Confirm archive member paths and source paths are preserved in frontmatter and `manifest.jsonl`.",
            "",
            "## Notes",
            "",
            "- Low-text PDFs and image files may require vision OCR credentials.",
            "- Automated conversion can miss layout semantics, merged cells, handwritten notes, seals, and scanned text.",
        ]
    )
    path = output / "qa" / "conversion_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert documents, archives, or folders into Markdown with MarkItDown.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Source file or folder.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output folder.")
    parser.add_argument("--ocr-model", default=os.environ.get("MARKITDOWN_OCR_MODEL"), help="OpenAI-compatible vision model for OCR.")
    parser.add_argument("--no-plugins", action="store_true", help="Disable MarkItDown plugins.")
    parser.add_argument("--dry-run", action="store_true", help="Discover files and write manifest without conversion.")
    parser.add_argument("--keep-work", action="store_true", help="Keep previous extracted work files.")
    parser.add_argument("--limit", type=int, default=0, help="Convert only the first N discovered leaf files.")
    parser.add_argument("--start-index", type=int, default=1, help="1-based index of the first discovered leaf file to convert.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip conversion when the expected Markdown output already exists.")
    parser.add_argument("--no-chunks", action="store_true", help="Do not rebuild chunks.jsonl at the end of this run.")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not source.exists():
        print(f"Source not found: {source}", file=sys.stderr)
        return 2
    output.mkdir(parents=True, exist_ok=True)
    (output / "documents").mkdir(exist_ok=True)
    (output / "qa").mkdir(exist_ok=True)

    items, records = discover(source, output, keep_work=args.keep_work)
    if args.limit:
        start = max(args.start_index, 1) - 1
        items = items[start : start + args.limit]
    elif args.start_index > 1:
        items = items[args.start_index - 1 :]
    converter = None if args.dry_run else build_converter(args.ocr_model, enable_plugins=not args.no_plugins)
    for index, item in enumerate(items, 1):
        print(f"[{index}/{len(items)}] {item.path.name}")
        record = convert_item(
            item,
            converter,
            output,
            args.ocr_model,
            dry_run=args.dry_run,
            skip_existing=args.skip_existing,
        )
        records.append(record)

    manifest_path = output / "manifest.jsonl"
    write_jsonl(manifest_path, records)
    chunks_count = 0
    if not args.dry_run and not args.no_chunks:
        chunks_count = write_chunks(output / "documents", output / "chunks.jsonl")
    write_unresolved(output, records)
    write_report(output, records, chunks_count)
    print(f"Manifest: {manifest_path}")
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
