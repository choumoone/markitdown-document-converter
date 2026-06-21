from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_llm_ready_corpus import build  # noqa: E402
from convert_corpus import extract_zip  # noqa: E402
from final_corpus_audit import audit as audit_final_corpus  # noqa: E402
from pdf_page_table_repair import resolve_pdf_source  # noqa: E402
from postprocess_markdown import normalize_markdown_tables  # noqa: E402
from paddleocr_backfill import (  # noqa: E402
    extract_existing_paddleocr_pages,
    write_paddleocr_markdown,
)
from source_table_content_audit import apply_attestations, char_recall, manifest_sources, write_reports  # noqa: E402


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
                    "<!-- source_page: 1 -->\n\nRecovered OCR text with enough meaningful content for import.",
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

    def test_completed_ocr_with_sparse_pages_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb = Path(temp)
            base = kb / "documents" / "pdf" / "scan--eeeeeeeeeeeeee.md"
            base.parent.mkdir(parents=True)
            body = "\n\n".join(
                f"<!-- source_page: {page} -->\n\n" + ("Only a title" if page == 1 else "")
                for page in range(1, 8)
            )
            base.write_text(
                markdown("eeeeeeeeeeeeee", body, ocr_status="paddleocr_completed"),
                encoding="utf-8",
            )

            summary = build(kb, "documents_llm_ready", "documents_page_aware")

            self.assertFalse(summary["ready_for_import"])
            unresolved = (kb / "qa" / "llm_ready_unresolved.md").read_text(encoding="utf-8")
            self.assertIn("completed_ocr_suspiciously_sparse", unresolved)
            self.assertIn("completed_ocr_mostly_blank_pages", unresolved)

    def test_malformed_markdown_table_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb = Path(temp)
            base = kb / "documents" / "pdf" / "table--ffffffffffffff.md"
            base.parent.mkdir(parents=True)
            body = "| A | B |\n| --- | --- |\n| one | two | three |"
            base.write_text(
                markdown("ffffffffffffff", body, ocr_status="text_ok"),
                encoding="utf-8",
            )

            summary = build(kb, "documents_llm_ready", "documents_page_aware")

            self.assertFalse(summary["ready_for_import"])
            unresolved = (kb / "qa" / "llm_ready_unresolved.md").read_text(encoding="utf-8")
            self.assertIn("markdown_table_column_mismatch", unresolved)


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


class AuditRegressionTests(unittest.TestCase):
    def test_source_audit_report_accepts_attestation_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            rows = [
                {"document": "a.md", "status": "checked", "char_recall": 1.0},
                {
                    "document": "b.md",
                    "status": "attested:source_page_verified",
                    "char_recall": 0.5,
                    "original_status": "checked",
                    "attestation_note": "render checked",
                },
            ]

            write_reports(Path(temp), rows, 0.9)

            header = (Path(temp) / "qa" / "source_table_content_audit.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("attestation_note", header)

    def test_source_audit_uses_archive_member_working_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            member = root / "work" / "member.pdf"
            member.parent.mkdir()
            member.write_bytes(b"pdf")
            record = {
                "file_id": "doc-1",
                "conversion_status": "converted",
                "source_path": str(root / "source.zip"),
                "working_path": str(member),
            }
            (root / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

            self.assertEqual(manifest_sources(root)["doc-1"], member)

            legacy = root / "legacy.doc"
            legacy.write_bytes(b"doc")
            digest = hashlib.sha1(str(legacy).encode("utf-8")).hexdigest()[:10]
            converted = root / "work" / "office_converted" / f"libreoffice--{digest}" / "legacy.docx"
            converted.parent.mkdir(parents=True)
            converted.write_bytes(b"docx")
            second = {
                "file_id": "doc-2",
                "conversion_status": "converted",
                "source_path": str(legacy),
                "working_path": str(legacy),
            }
            with (root / "manifest.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(second) + "\n")

            self.assertEqual(manifest_sources(root)["doc-2"], converted)

    def test_table_normalizer_preserves_cells_and_repairs_width_and_separator(self) -> None:
        source = "| A | B |\n| value |\n"
        repaired = normalize_markdown_tables(source)

        self.assertIn("| A | B |", repaired)
        self.assertIn("| --- | --- |", repaired)
        self.assertIn("| value ||", repaired)

    def test_page_repair_uses_extracted_pdf_for_archive_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "source.zip"
            archive.write_bytes(b"archive")
            member = root / "extracted" / "member.pdf"
            member.parent.mkdir()
            member.write_bytes(b"pdf")

            resolved = resolve_pdf_source(
                {"source_path": str(archive), "working_path": str(member)}
            )

            self.assertEqual(resolved, member)

    def test_character_recall_handles_reordered_table_text(self) -> None:
        self.assertEqual(char_recall("甲甲乙", "乙甲甲"), 1.0)
        self.assertAlmostEqual(char_recall("甲甲乙", "甲乙"), 2 / 3)

    def test_manual_review_attestation_is_applied_by_exact_key(self) -> None:
        rows = [
            {
                "document": "form.md",
                "page": 3,
                "table": 1,
                "status": "source_table_not_redetected",
            },
            {"document": "low.md", "page": 4, "table": 2, "status": "checked"},
        ]
        apply_attestations(
            rows,
            {
                ("form.md", "3", "1"): {"status": "source_page_verified", "note": "checked render"},
                ("low.md", "4", "2"): {"status": "source_page_verified", "note": "checked low recall"},
            },
        )
        self.assertEqual(rows[0]["status"], "attested:source_page_verified")
        self.assertEqual(rows[0]["original_status"], "source_table_not_redetected")
        self.assertEqual(rows[1]["status"], "attested:source_page_verified")
        self.assertEqual(rows[1]["original_status"], "checked")

    def test_final_corpus_audit_checks_chunk_paths_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            documents = root / "documents"
            documents.mkdir()
            source = root / "source.txt"
            source.write_text("source", encoding="utf-8")
            md_path = documents / "document.md"
            md_path.write_text(
                "---\n"
                'doc_id: "doc1"\n'
                f'source_path: "{str(source).replace(chr(92), chr(92) * 2)}"\n'
                'document_kind: "text"\n'
                'ocr_status: "not_required"\n'
                "---\n\n"
                "Enough final document content to pass the near-empty gate.\n",
                encoding="utf-8",
            )
            chunks = root / "chunks.jsonl"
            chunks.write_text(
                json.dumps(
                    {
                        "chunk_id": "doc1-0001",
                        "doc_id": "doc1",
                        "markdown_path": str(md_path),
                        "source_path": str(source),
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = audit_final_corpus(documents, chunks, expected_documents=1)

            self.assertTrue(result["summary"]["clean"])
            self.assertEqual(result["summary"]["chunks"], 1)

    def test_final_corpus_audit_distinguishes_archive_members(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            documents = root / "documents"
            documents.mkdir()
            archive = root / "source.zip"
            archive.write_bytes(b"archive")
            for index, member in enumerate(("folder/a.pdf", "folder/b.pdf"), 1):
                (documents / f"document-{index}.md").write_text(
                    "---\n"
                    f'doc_id: "doc{index}"\n'
                    f'source_path: "{str(archive).replace(chr(92), chr(92) * 2)}"\n'
                    f'archive_member_path: "{member}"\n'
                    'document_kind: "pdf"\n'
                    'ocr_status: "not_required"\n'
                    "---\n\n"
                    "Enough final document content to pass the near-empty gate.\n",
                    encoding="utf-8",
                )

            result = audit_final_corpus(documents, None, expected_documents=2)

            self.assertTrue(result["summary"]["clean"])
            self.assertEqual(result["summary"]["unique_source_paths"], 2)
            self.assertEqual(result["duplicate_source_paths"], [])

    def test_zip_extraction_strips_redundant_common_root_but_preserves_member_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "archive.zip"
            member = "a-very-long-redundant-folder-name/document.pdf"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr(member, b"pdf-bytes")
            extracted = extract_zip(archive, root / "out")

            self.assertEqual(len(extracted), 1)
            path, original_member = extracted[0]
            self.assertEqual(path.relative_to(root / "out").as_posix(), "document.pdf")
            self.assertEqual(original_member, member)


if __name__ == "__main__":
    unittest.main()
