#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


HOME = Path.home()
CORE_SCRIPTS = HOME / ".codex" / "skills" / "markitdown-document-converter" / "scripts"
sys.path.insert(0, str(CORE_SCRIPTS))


def standalone(args: argparse.Namespace) -> dict[str, object]:
    from openai import OpenAI
    from ocr_backfill import ocr_image, source_to_images

    source = Path(args.input).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not source.exists():
        raise RuntimeError(f"input does not exist: {source}")
    images = source_to_images(source, max_pages=args.max_pages, dpi=args.dpi)
    if not images:
        raise RuntimeError(f"unsupported OCR input: {source.suffix}")

    client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama", timeout=600)
    pages: list[str] = []
    for page_no, image in images:
        text = ocr_image(client, args.model, image, f"{source.name} page {page_no}", retries=1)
        pages.append(f"## Page {page_no}\n\n{text}".strip())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(pages) + "\n", encoding="utf-8")
    return {"mode": "standalone", "pages": len(images), "output": str(output)}


def corpus(args: argparse.Namespace) -> dict[str, object]:
    command = [
        sys.executable,
        str(CORE_SCRIPTS / "ocr_backfill.py"),
        "--kb",
        str(Path(args.kb).expanduser().resolve()),
        "--model",
        args.model,
        "--base-url",
        "http://localhost:11434/v1",
        "--api-key-env",
        "OPENAI_API_KEY",
        "--limit",
        str(args.limit),
        "--max-pages",
        str(args.max_pages),
        "--dpi",
        str(args.dpi),
        "--rebuild-chunks",
    ]
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = "ollama"
    result = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace", env=env)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "OCR failed").strip())
    kb = Path(args.kb).expanduser().resolve()
    return {
        "mode": "corpus",
        "kb": str(kb),
        "documents": len(list((kb / "documents").rglob("*.md"))),
        "chunks": sum(1 for _ in (kb / "chunks.jsonl").open("r", encoding="utf-8")) if (kb / "chunks.jsonl").exists() else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run authorized local Qwen vision OCR.")
    parser.add_argument("--authorized", action="store_true")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input")
    group.add_argument("--kb")
    parser.add_argument("--output")
    parser.add_argument("--model", default="qwen3.6:27b")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()

    started = time.perf_counter()
    try:
        if not args.authorized:
            raise RuntimeError("explicit authorization is required; pass --authorized only for the approved scope")
        if args.input and not args.output:
            raise RuntimeError("--output is required with --input")
        if args.limit < 1 or args.max_pages < 1:
            raise RuntimeError("--limit and --max-pages must be positive")
        result = standalone(args) if args.input else corpus(args)
        result.update(
            {
                "status": "completed",
                "model": args.model,
                "provider": "local Ollama",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "quality_status": "needs_human_spotcheck",
            }
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
