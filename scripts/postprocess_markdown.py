from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path
from typing import Iterable


FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CHAPTER_RE = re.compile(
    r"^((Chapter|Section|Article)\s+\d+([.\-]\d+)*|第.{1,12}[章节条款]|[一二三四五六七八九十]+[、.])\s*(.{2,120})$",
    re.I,
)
PAGE_NOISE_RE = re.compile(
    r"^\s*(#{1,6}\s*)?((page\s+\d+(\s+of\s+\d+)?)|(\d+\s*/\s*\d+)|([-_]{3,}))\s*$",
    re.I,
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
HTML_BLOCK_RE = re.compile(r"```html\s*(.*?)\s*```", re.S | re.I)
HTML_HEADING_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.S | re.I)
HTML_PARAGRAPH_RE = re.compile(r"</?(p|div|body|html)[^>]*>", re.I)
HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.I)
HTML_TAG_RE = re.compile(r"<[^>]+>")


def sha(text: str, length: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def strip_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(0).strip("-\n")
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value.strip('"')
        else:
            value = value.strip('"')
        if key in {"source_path", "origin_archive", "archive_member_path", "output_markdown"}:
            value = re.sub(r"\\{2,}", r"\\", value)
        meta[key] = value
    return meta, text[match.end() :]


def yaml_quote(value: object) -> str:
    if value is None:
        return '""'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
    return f'"{text}"'


def make_frontmatter(meta: dict[str, object]) -> str:
    ordered = [
        "doc_id",
        "doc_title",
        "source_path",
        "origin_archive",
        "archive_member_path",
        "document_kind",
        "source_extension",
        "source_size",
        "extraction_method",
        "ocr_status",
        "quality_status",
    ]
    lines = ["---"]
    for key in ordered:
        if key not in meta:
            continue
        value = meta[key]
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        lines.append(f"{key}: {yaml_quote(value)}")
    for key in sorted(set(meta) - set(ordered)):
        value = meta[key]
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        lines.append(f"{key}: {yaml_quote(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def repeated_short_lines(lines: list[str]) -> set[str]:
    counts: dict[str, int] = {}
    for line in lines:
        s = line.strip()
        if not s or len(s) > 80:
            continue
        if PAGE_NOISE_RE.match(s):
            continue
        counts[s] = counts.get(s, 0) + 1
    return {line for line, count in counts.items() if count >= 4}


def promote_heading(line: str) -> str:
    s = line.strip()
    if not s or s.startswith("#") or "|" in s or len(s) > 140:
        return line
    if CHAPTER_RE.match(s):
        return "## " + s
    return line


def clean_html_fragments(text: str) -> str:
    text = HTML_BLOCK_RE.sub(lambda m: m.group(1), text)
    text = re.sub(r"^```html\s*$", "", text, flags=re.I | re.M)
    text = HTML_HEADING_RE.sub(
        lambda m: "\n" + "#" * min(int(m.group(1)) + 1, 6) + " " + HTML_TAG_RE.sub("", m.group(2)).strip() + "\n",
        text,
    )
    text = HTML_BREAK_RE.sub("\n", text)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = HTML_PARAGRAPH_RE.sub("", text)
    text = HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text


def clean_markdown_body(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = clean_html_fragments(text)
    text = CONTROL_RE.sub("", text)
    text = text.replace("\u00a0", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    repeats = repeated_short_lines(lines)
    cleaned: list[str] = []
    blank = 0
    for line in lines:
        s = line.strip()
        if PAGE_NOISE_RE.match(s):
            continue
        if s in repeats:
            continue
        line = promote_heading(line)
        if not line.strip():
            blank += 1
            if blank <= 2:
                cleaned.append("")
            continue
        blank = 0
        cleaned.append(line)
    text = "\n".join(cleaned).strip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", text)


def clean_markdown(text: str, meta: dict[str, object] | None = None) -> str:
    existing, body = strip_frontmatter(text)
    merged: dict[str, object] = {**existing}
    if meta:
        merged.update(meta)
    body = clean_markdown_body(body)
    if merged:
        return make_frontmatter(merged) + body
    return body


def iter_markdown_files(path: Path) -> Iterable[Path]:
    if path.is_file() and path.suffix.lower() == ".md":
        yield path
    elif path.is_dir():
        yield from sorted(path.rglob("*.md"))


def section_chunks(md_path: Path, max_chars: int = 2200) -> list[dict[str, str]]:
    meta, body = strip_frontmatter(md_path.read_text(encoding="utf-8", errors="replace"))
    title = meta.get("doc_title") or md_path.stem
    section = title
    buffer: list[str] = []
    chunks: list[dict[str, str]] = []

    def flush() -> None:
        nonlocal buffer
        text = "\n".join(buffer).strip()
        if not text:
            buffer = []
            return
        while len(text) > max_chars:
            split_at = text.rfind("\n", 0, max_chars)
            if split_at < 800:
                split_at = max_chars
            part = text[:split_at].strip()
            chunks.append({"section_title": section, "text": part})
            text = text[split_at:].strip()
        if text:
            chunks.append({"section_title": section, "text": text})
        buffer = []

    for line in body.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush()
            section = match.group(2).strip()
            buffer.append(line)
        else:
            buffer.append(line)
    flush()

    records: list[dict[str, str]] = []
    doc_id = meta.get("doc_id") or sha(str(md_path))
    for index, chunk in enumerate(chunks, 1):
        records.append(
            {
                "chunk_id": f"{doc_id}-{index:04d}",
                "doc_id": doc_id,
                "doc_title": title,
                "section_title": chunk["section_title"],
                "citation": f"{title} > {chunk['section_title']}",
                "markdown_path": str(md_path),
                "source_path": meta.get("source_path", ""),
                "origin_archive": meta.get("origin_archive", ""),
                "archive_member_path": meta.get("archive_member_path", ""),
                "document_kind": meta.get("document_kind", ""),
                "text": chunk["text"],
            }
        )
    return records


def write_chunks(md_root: Path, chunks_out: Path) -> int:
    count = 0
    chunks_out.parent.mkdir(parents=True, exist_ok=True)
    with chunks_out.open("w", encoding="utf-8", newline="\n") as handle:
        for md_path in iter_markdown_files(md_root):
            for chunk in section_chunks(md_path):
                handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean Markdown files and optionally rebuild chunks.jsonl.")
    parser.add_argument("--input", required=True, help="Markdown file or directory.")
    parser.add_argument("--manifest", help="Optional manifest path, kept for workflow compatibility.")
    parser.add_argument("--chunks-out", help="Optional chunks JSONL output path.")
    args = parser.parse_args()

    root = Path(args.input).resolve()
    for md_path in iter_markdown_files(root):
        text = md_path.read_text(encoding="utf-8", errors="replace")
        md_path.write_text(clean_markdown(text), encoding="utf-8", newline="\n")
    if args.chunks_out:
        count = write_chunks(root if root.is_dir() else root.parent, Path(args.chunks_out).resolve())
        print(f"Wrote chunks: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
