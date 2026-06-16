from __future__ import annotations

import argparse
import ast
import base64
import csv
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import fitz
from openai import OpenAI
from PIL import Image, ImageDraw


DEFAULT_ENV_FILE = Path.home() / ".codex" / "secrets" / "minimax.env"
DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M3"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def get_client(env_file: Path, base_url: str | None, model: str | None, timeout: float = 120.0) -> tuple[OpenAI, str]:
    file_env = load_env_file(env_file)
    api_key = (
        os.environ.get("MINIMAX_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or file_env.get("MINIMAX_API_KEY")
        or file_env.get("OPENAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "MiniMax API key is missing. Run scripts/set_minimax_secret_from_clipboard.ps1 first."
        )
    resolved_base_url = (
        base_url
        or os.environ.get("OPENAI_BASE_URL")
        or file_env.get("OPENAI_BASE_URL")
        or DEFAULT_BASE_URL
    )
    resolved_model = (
        model
        or os.environ.get("MARKITDOWN_OCR_MODEL")
        or file_env.get("MARKITDOWN_OCR_MODEL")
        or DEFAULT_MODEL
    )
    return OpenAI(api_key=api_key, base_url=resolved_base_url, timeout=timeout), resolved_model


def parse_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    meta: dict[str, str] = {}
    if not lines or lines[0].strip() != "---":
        return meta
    for line in lines[1:120]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta


def source_from_markdown(markdown: Path) -> Path | None:
    text = markdown.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^source_path:\s*(".*")\s*$', text, re.M)
    if not match:
        return None
    try:
        return Path(ast.literal_eval(match.group(1)))
    except Exception:
        return None


def escape_yaml_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def strip_think(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def image_data_url(path: Path, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def render_page(source: Path, page_no: int, output: Path, dpi: int) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.stat().st_size > 0:
        return output
    doc = fitz.open(source)
    try:
        page = doc[page_no - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
        pix.save(str(output))
    finally:
        doc.close()
    return output


def make_contact_sheet(source: Path, output: Path, width: int = 520) -> tuple[Path, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.stat().st_size > 0:
        doc = fitz.open(source)
        try:
            return output, doc.page_count
        finally:
            doc.close()

    doc = fitz.open(source)
    pages: list[Image.Image] = []
    try:
        for index in range(doc.page_count):
            pix = doc[index].get_pixmap(matrix=fitz.Matrix(0.75, 0.75), alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            scale = width / image.width
            image = image.resize((width, max(1, int(image.height * scale))))
            canvas = Image.new("RGB", (width, image.height + 34), "white")
            canvas.paste(image, (0, 34))
            draw = ImageDraw.Draw(canvas)
            draw.text((8, 8), f"PAGE {index + 1}", fill=(255, 0, 0))
            pages.append(canvas)
    finally:
        doc.close()

    columns = 2
    gap = 20
    rows = (len(pages) + columns - 1) // columns
    row_heights = [
        max(img.height for img in pages[row * columns : (row + 1) * columns])
        for row in range(rows)
    ]
    sheet = Image.new(
        "RGB",
        (columns * width + gap, sum(row_heights) + gap * max(0, rows - 1)),
        "white",
    )
    y = 0
    for row in range(rows):
        x = 0
        for image in pages[row * columns : (row + 1) * columns]:
            sheet.paste(image, (x, y))
            x += width + gap
        y += row_heights[row] + gap
    sheet.save(output, quality=86)
    return output, len(pages)


def load_candidates(kb: Path, priorities: set[str], candidate_csv: Path | None) -> list[dict[str, str]]:
    candidates = candidate_csv or (kb / "qa" / "Table_Enhancement_Candidates.csv")
    if not candidates.exists():
        raise FileNotFoundError(
            f"{candidates} not found. Create it during QA, or pass --candidate-csv."
        )
    rows: list[dict[str, str]] = []
    with candidates.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("priority", "").lower() in priorities:
                rows.append(row)
    return rows


def load_page_selection(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    selected: dict[str, list[dict[str, Any]]] = {}
    for item in data:
        file_id = item.get("file_id")
        if not file_id:
            continue
        selected[file_id] = item.get("pages", [])
    return selected


def parse_manual_pages(values: list[str]) -> dict[str, list[int]]:
    manual: dict[str, list[int]] = {}
    for value in values:
        if ":" not in value:
            raise ValueError(f"manual page must look like file_id:1,2,3, got {value!r}")
        file_id, pages = value.split(":", 1)
        parsed = [int(page.strip()) for page in pages.split(",") if page.strip()]
        manual.setdefault(file_id.strip(), []).extend(parsed)
    return manual


def select_pages_with_minimax(
    client: OpenAI,
    model: str,
    source: Path,
    file_id: str,
    work: Path,
    force: bool,
) -> list[dict[str, Any]]:
    contact, page_count = make_contact_sheet(
        source, work / "contact_sheets" / f"{file_id}_contact.jpg"
    )
    cache = work / "page_selection_cache" / f"{file_id}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8")).get("pages", [])

    prompt = (
        "You are selecting pages from a scanned Chinese policy document for table/form reconstruction.\n"
        "The image is a contact sheet. Each page is labeled PAGE N in red.\n"
        "Return ONLY JSON, no markdown, no explanation.\n"
        'Schema: {"pages":[{"page":number,"reason":"short reason","type":"table|form|checklist|structured_list"}]}\n'
        "Select pages that contain visible tables, form boxes, attachment forms, checklists, "
        "or dense structured lists where row/column structure matters. Do not select ordinary prose pages."
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url(contact, "image/jpeg")}},
                ],
            }
        ],
        temperature=0,
        max_tokens=1200,
    )
    content = strip_think(response.choices[0].message.content or "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        parsed = json.loads(match.group(0)) if match else {"pages": []}

    pages: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in parsed.get("pages", []):
        try:
            page_no = int(item.get("page"))
        except Exception:
            continue
        if 1 <= page_no <= page_count and page_no not in seen:
            seen.add(page_no)
            pages.append(
                {
                    "page": page_no,
                    "reason": str(item.get("reason", ""))[:160],
                    "type": str(item.get("type", ""))[:40],
                }
            )
    cache.write_text(json.dumps({"pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")
    return pages


def enhance_page(
    client: OpenAI,
    model: str,
    source: Path,
    file_id: str,
    page_no: int,
    work: Path,
    dpi: int,
    force: bool,
) -> tuple[str | None, str | None]:
    raw = work / "raw_pages" / f"{file_id}_p{page_no}_minimax_m3.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    if raw.exists() and raw.stat().st_size > 0 and not force:
        content = strip_think(raw.read_text(encoding="utf-8"))
        return (content if content and content != "NO_TABLE" else None, None)

    image = render_page(source, page_no, work / "page_images" / f"{file_id}_p{page_no}.png", dpi)
    prompt = (
        "You are extracting tables/forms from a scanned Chinese policy page for a knowledge base.\n"
        "Output ONLY Markdown. Do not output <think>, analysis, code fences, or commentary.\n"
        "Preserve visible Chinese text exactly. Do not summarize or invent content.\n"
        "If the page contains a table or form, reconstruct it as Markdown tables as far as possible.\n"
        "If a cell is intentionally blank, leave it blank. If text is unreadable, write [unreadable].\n"
        "Keep titles, attachment numbers, page labels, row numbers, column headers, dates, amounts, "
        "departments, signatures, and remarks.\n"
        "For dense structured lists without grid lines, preserve hierarchy with headings and numbered lists.\n"
        "If there is no table/form/structured list worth extracting, output exactly: NO_TABLE"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url(image)}},
                    ],
                }
            ],
            temperature=0,
            max_tokens=6000,
        )
    except Exception as exc:
        return None, str(exc)[:1200]

    content = strip_think(response.choices[0].message.content or "")
    raw.write_text(content, encoding="utf-8")
    if not content or content == "NO_TABLE":
        return None, None
    return content, None


def write_outputs(
    kb: Path,
    summary: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    zip_output: bool,
    out_dir: Path,
    zip_name: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    index_lines = ["# MiniMax Table Enhancement Index", ""]
    all_lines = ["# MiniMax Table Enhancement All", ""]

    for item in summary:
        index_lines.append(
            f"- `{item['status']}` `{item['file_id']}` {item.get('title', '')} pages={item.get('pages', [])}"
        )
        output = item.get("output")
        if output:
            index_lines.append(f"  - output: `{output}`")
            all_lines.append(f"<!-- {item['file_id']} -->")
            all_lines.append(Path(output).read_text(encoding="utf-8"))
            all_lines.append("")
    if errors:
        index_lines.extend(["", "## Page Errors"])
        for error in errors:
            index_lines.append(
                f"- `{error['file_id']}` page {error['page']}: {error['error'][:180]}"
            )

    (out_dir / "00_Table_Enhancement_Index.md").write_text(
        "\n".join(index_lines), encoding="utf-8"
    )
    (out_dir / "00_Table_Enhancement_All.md").write_text(
        "\n".join(all_lines), encoding="utf-8"
    )
    if zip_output:
        zip_base = kb / zip_name
        zip_path = zip_base.with_suffix(".zip")
        if zip_path.exists():
            zip_path.unlink()
        shutil.make_archive(str(zip_base), "zip", out_dir)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enhance selected PDF table/form pages with MiniMax-M3 vision."
    )
    parser.add_argument("--kb", required=True, help="Converted knowledge-base output folder.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="MiniMax env file.")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL.")
    parser.add_argument("--model", help="Vision model, default MiniMax-M3.")
    parser.add_argument("--candidate-csv", help="Table enhancement candidate CSV.")
    parser.add_argument("--priority", action="append", default=["high"], help="Candidate priority to process.")
    parser.add_argument("--select-pages", action="store_true", help="Use MiniMax contact sheets to select table/form pages.")
    parser.add_argument("--selection-json", help="Existing MiniMax page-selection JSON.")
    parser.add_argument("--manual-page", action="append", default=[], help="Force pages, format file_id:1,2,3.")
    parser.add_argument("--limit-files", type=int, default=0, help="Maximum files to process.")
    parser.add_argument("--limit-pages", type=int, default=0, help="Maximum pages to process.")
    parser.add_argument("--dpi", type=int, default=220, help="Page render DPI.")
    parser.add_argument("--request-timeout", type=float, default=120.0, help="Per-page MiniMax request timeout in seconds.")
    parser.add_argument("--force", action="store_true", help="Re-run cached selections and pages.")
    parser.add_argument("--dry-run", action="store_true", help="Print selected targets without calling page enhancement.")
    parser.add_argument("--zip", action="store_true", help="Create Table_Enhanced_MD.zip.")
    parser.add_argument("--out-dir", default="table_enhanced", help="Output folder under the KB root for *.tables.md files.")
    parser.add_argument("--work-dir", default="qa/minimax_table_enhancement", help="Cache/work folder under the KB root.")
    parser.add_argument("--zip-name", default="Table_Enhanced_MD", help="Zip basename under the KB root when --zip is used.")
    args = parser.parse_args()

    kb = Path(args.kb).resolve()
    work = kb / args.work_dir
    work.mkdir(parents=True, exist_ok=True)
    priorities = {priority.lower() for priority in args.priority}
    candidates = load_candidates(kb, priorities, Path(args.candidate_csv) if args.candidate_csv else None)
    if args.limit_files:
        candidates = candidates[: args.limit_files]

    client, model = get_client(Path(args.env_file), args.base_url, args.model, args.request_timeout)
    manual_pages = parse_manual_pages(args.manual_page)
    selection_path = Path(args.selection_json) if args.selection_json else work / "page_selection_minimax_m3.json"
    existing_selection = load_page_selection(selection_path)

    targets: list[dict[str, Any]] = []
    selection_records: list[dict[str, Any]] = []
    for row in candidates:
        file_id = row.get("file_id", "")
        source = source_from_markdown(Path(row["md_path"]))
        if source is None or not source.exists():
            continue
        pages = existing_selection.get(file_id, [])
        if args.select_pages and not pages:
            pages = select_pages_with_minimax(client, model, source, file_id, work, args.force)
        for page_no in manual_pages.get(file_id, []):
            if all(int(item.get("page", 0)) != page_no for item in pages):
                pages.append({"page": page_no, "reason": "manual", "type": "manual"})
        pages = sorted(pages, key=lambda item: int(item.get("page", 0)))
        selection_records.append(
            {
                "file_id": file_id,
                "title": row.get("title", ""),
                "pages": pages,
                "source_path": str(source),
            }
        )
        for item in pages:
            targets.append({"candidate": row, "source": source, "page": int(item["page"])})

    if args.limit_pages:
        targets = targets[: args.limit_pages]
    selection_path.write_text(json.dumps(selection_records, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dry_run:
        print(json.dumps({"targets": len(targets), "pages": targets}, ensure_ascii=False, default=str, indent=2))
        return 0

    grouped: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    processed = 0
    for target in targets:
        row = target["candidate"]
        file_id = row["file_id"]
        page_no = target["page"]
        content, error = enhance_page(
            client, model, target["source"], file_id, page_no, work, args.dpi, args.force
        )
        if error:
            errors.append(
                {"file_id": file_id, "title": row.get("title", ""), "page": page_no, "error": error}
            )
            print(json.dumps({"file_id": file_id, "page": page_no, "error": error[:160]}, ensure_ascii=False), flush=True)
            continue
        if content:
            grouped.setdefault(
                file_id,
                {
                    "row": row,
                    "source": target["source"],
                    "pages": [],
                    "contents": [],
                },
            )
            grouped[file_id]["pages"].append(page_no)
            grouped[file_id]["contents"].append((page_no, content))
        processed += 1
        print(json.dumps({"file_id": file_id, "page": page_no, "kept": bool(content)}, ensure_ascii=False), flush=True)
        time.sleep(0.5)

    out_dir = kb / args.out_dir
    summary: list[dict[str, Any]] = []
    for file_id, item in grouped.items():
        row = item["row"]
        title = row.get("title") or file_id
        safe_title = re.sub(r'[<>:"/\\|?*]+', "_", title)[:90]
        output = out_dir / f"{safe_title}--{file_id}.tables.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        source = Path(item["source"])
        lines = [
            "---",
            f'doc_id: "{file_id}"',
            f'doc_title: "{escape_yaml_value(title)}"',
            f'source_path: "{escape_yaml_value(str(source))}"',
            'extraction_method: "minimax_m3_table_enhancement"',
            f'table_pages: "{",".join(str(page) for page in item["pages"])}"',
            'quality_status: "needs_human_spotcheck"',
            "---",
            "",
            f"# {title}",
            "",
            "> Table/form enhancement generated from selected source PDF pages. Verify against the original PDF before formal citation.",
            "",
        ]
        for page_no, content in item["contents"]:
            lines.extend([f"## Source Page {page_no}", "", content.strip(), ""])
        output.write_text("\n".join(lines), encoding="utf-8")
        summary.append(
            {
                "file_id": file_id,
                "title": title,
                "status": "ok",
                "pages": item["pages"],
                "output": str(output),
            }
        )

    work.joinpath("enhancement_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    work.joinpath("page_errors.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_outputs(kb, summary, errors, args.zip, out_dir, args.zip_name)
    print(
        json.dumps(
            {
                "processed_pages": processed,
                "outputs": len(summary),
                "page_errors": len(errors),
                "out_dir": str(out_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
