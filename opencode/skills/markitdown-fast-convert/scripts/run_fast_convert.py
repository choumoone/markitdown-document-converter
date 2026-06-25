#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


HOME = Path.home()
ROUTER = HOME / ".codex" / "skills" / "markitdown-document-router" / "scripts" / "classify_documents.py"
CONVERTER = HOME / ".codex" / "skills" / "markitdown-document-converter" / "scripts" / "convert_corpus.py"
COMPLEX_BUCKETS = ("needs_ocr", "pdf_table", "archive", "manual_review", "unsupported")


def run(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "command failed").strip())


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the cheap MarkItDown route in one command.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    plan_path = output / "route-plan.json"
    started = time.perf_counter()

    try:
        if not source.exists():
            raise RuntimeError(f"source does not exist: {source}")
        output.mkdir(parents=True, exist_ok=True)
        run([sys.executable, str(ROUTER), "--source", str(source), "--output", str(plan_path)])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        command = [
            sys.executable,
            str(CONVERTER),
            "--source",
            str(source),
            "--output",
            str(output),
            "--route-plan",
            str(plan_path),
            "--quiet",
        ]
        if args.skip_existing:
            command.append("--skip-existing")
        run(command)

        counts = plan.get("counts", {})
        complex_counts = {name: counts[name] for name in COMPLEX_BUCKETS if counts.get(name)}
        summary = {
            "status": "completed",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "source_files": plan.get("file_count", 0),
            "route_counts": counts,
            "documents": len(list((output / "documents").rglob("*.md"))),
            "manifest_lines": line_count(output / "manifest.jsonl"),
            "chunks": line_count(output / "chunks.jsonl"),
            "needs_authorization": bool(complex_counts),
            "complex_buckets": complex_counts,
            "output": str(output),
        }
        print(json.dumps(summary, ensure_ascii=False))
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
