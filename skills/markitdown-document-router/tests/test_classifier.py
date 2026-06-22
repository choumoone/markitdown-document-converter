from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from classify_documents import classify_pdf  # noqa: E402


class PdfClassifierTests(unittest.TestCase):
    def test_text_pdf_uses_direct_route(self) -> None:
        import fitz

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "text.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "This is embedded document text. " * 8)
            document.save(path)
            document.close()

            bucket, _reason, details = classify_pdf(path, sample_pages=8, low_text_chars=40)

            self.assertEqual(bucket, "pdf_text")
            self.assertGreater(details["average_text_chars"], 40)

    def test_image_only_pdf_uses_ocr_route(self) -> None:
        import fitz

        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        pixmap.clear_with(255)
        pixel_png = pixmap.tobytes("png")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scan.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_image(page.rect, stream=pixel_png)
            document.save(path)
            document.close()

            bucket, _reason, details = classify_pdf(path, sample_pages=8, low_text_chars=40)

            self.assertEqual(bucket, "needs_ocr")
            self.assertEqual(details["image_pages"], 1)

    def test_ruled_table_pdf_uses_table_route(self) -> None:
        import fitz

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "table.pdf"
            document = fitz.open()
            page = document.new_page()
            left, top, width, height = 72, 72, 320, 120
            for row in range(4):
                y = top + row * (height / 3)
                page.draw_line((left, y), (left + width, y))
            for column in range(3):
                x = left + column * (width / 2)
                page.draw_line((x, top), (x, top + height))
            page.insert_text((82, 96), "Name")
            page.insert_text((242, 96), "Value")
            page.insert_text((82, 136), "Alpha")
            page.insert_text((242, 136), "100")
            page.insert_text((82, 176), "Beta")
            page.insert_text((242, 176), "200")
            document.save(path)
            document.close()

            bucket, _reason, details = classify_pdf(path, sample_pages=8, low_text_chars=40)

            self.assertEqual(bucket, "pdf_table")
            self.assertEqual(details["table_pages"], [1])


if __name__ == "__main__":
    unittest.main()
