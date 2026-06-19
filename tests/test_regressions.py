from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_llm_ready_corpus import build  # noqa: E402
from paddleocr_backfill import (  # noqa: E402
    extract_existing_paddleocr_pages,
    write_paddleocr_markdown,
)


def markdown(doc_id: str, body: str, *, ocr_status: str, quality_status: str = "ok") -> str:
    return (
        "---\n"
        f'doc_id: "{doc_id}"\n'
        f'doc_title: "Document {doc_id}"\n'
        'source_path: "source.pdf"\n'
        'document_kind: "pdf"\n'
        'source_extension: ".pdf"\n'
        f'ocr_status: "{ocr_status}"\n'
        f'quality_status: "{quality_status}"\n'
        "---\n\n"
        f"{body}\n"
    )


class LlmReadyRegressionTests(unittest.TestCase):
    def test_ocr_backfill_wins_over_empty_page_aware_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb = Path(temp)
            base = kb / "documents" / "pdf" / "scan--aaaaaaaaaaaaaa.md"
            page_aware = kb / "documents_page_aware" / "pdf" / base.name
            base.parent.mkdir(parents=True)
            page_aware.parent.mkdir(parents=True)
            base.write_text(
                markdown(
                    "aaaaaaaaaaaaaa",
                    "Recovered OCR text with enough meaningful content for import.",
                    ocr_status="paddleocr_completed",
                    quality_status="needs_human_spotcheck",
                ),
                encoding="utf-8",
            )
            page_aware.write_text(
                markdown(
                    "aaaaaaaaaaaaaa",
                    "<!-- source_page: 1 -->",
                    ocr_status="needs_ocr",
                ),
                encoding="utf-8",
            )

            summary = build(kb, "documents_llm_ready", "documents_page_aware")
            output = kb / "documents_llm_ready" / "documents" / "pdf" / base.name

            self.assertIn("Recovered OCR text", output.read_text(encoding="utf-8"))
            self.assertEqual(summary["ocr_pdf"], 1)
            self.assertEqual(summary["unresolved_ocr"], 0)
            self.assertTrue(summary["ready_for_import"])

    def test_needs_ocr_and_near_empty_output_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb = Path(temp)
            base = kb / "documents" / "pdf" / "scan--bbbbbbbbbbbbbb.md"
            page_aware = kb / "documents_page_aware" / "pdf" / base.name
            base.parent.mkdir(parents=True)
            page_aware.parent.mkdir(parents=True)
            base.write_text(
                markdown("bbbbbbbbbbbbbb", "", ocr_status="needs_ocr", quality_status="needs_review"),
                encoding="utf-8",
            )
            page_aware.write_text(
                markdown("bbbbbbbbbbbbbb", "<!-- source_page: 1 -->", ocr_status="needs_ocr"),
                encoding="utf-8",
            )

            summary = build(kb, "documents_llm_ready", "documents_page_aware")

            self.assertEqual(summary["unresolved_ocr"], 1)
            self.assertEqual(summary["near_empty"], 1)
            self.assertFalse(summary["ready_for_import"])
            unresolved = (kb / "qa" / "llm_ready_unresolved.md").read_text(encoding="utf-8")
            self.assertIn("bbbbbbbbbbbbbb", unresolved)

    def test_fragmented_single_character_output_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb = Path(temp)
            base = kb / "documents" / "pdf" / "scan--dddddddddddddd.md"
            base.parent.mkdir(parents=True)
            fragmented = "\n".join(chr(0x4E00 + index) for index in range(24))
            base.write_text(
                markdown("dddddddddddddd", fragmented, ocr_status="text_ok"),
                encoding="utf-8",
            )

            summary = build(kb, "documents_llm_ready", "documents_page_aware")

            self.assertEqual(summary["suspicious_content"], 1)
            self.assertFalse(summary["ready_for_import"])
            unresolved = (kb / "qa" / "llm_ready_unresolved.md").read_text(encoding="utf-8")
            self.assertIn("garbled_or_fragmented", unresolved)


class PaddleOcrRegressionTests(unittest.TestCase):
    def test_page_markers_and_blank_pages_survive_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb = Path(temp)
            source = kb / "source.png"
            source.write_bytes(b"not-used-by-this-test")
            output = kb / "documents" / "image" / "scan--cccccccccccccc.md"
            record = {
                "file_id": "cccccccccccccc",
                "doc_title": "Scanned form",
                "source_path": str(source),
                "working_path": str(source),
                "output_markdown": str(output),
                "document_kind": "image",
                "extension": ".png",
                "source_size": source.stat().st_size,
            }

            write_paddleocr_markdown(kb, record, {1: "Page one text", 2: ""}, "paddleocr_completed")
            text = output.read_text(encoding="utf-8")
            recovered = extract_existing_paddleocr_pages(output)

            self.assertIn("<!-- source_page: 1 -->", text)
            self.assertIn("<!-- source_page: 2 -->", text)
            self.assertIn('extraction_method: "paddleocr:PP-OCRv6"', text)
            self.assertIn('ocr_blank_pages: "2"', text)
            self.assertEqual(recovered, {1: "Page one text", 2: ""})


if __name__ == "__main__":
    unittest.main()
