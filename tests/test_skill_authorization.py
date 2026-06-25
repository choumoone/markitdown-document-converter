from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPECIALISTS = (
    "markitdown-pdf-table-repair",
    "markitdown-corpus-audit",
    "markitdown-publisher",
)


class SkillAuthorizationTests(unittest.TestCase):
    def test_specialist_skills_define_authorization_gate(self) -> None:
        for name in SPECIALISTS:
            with self.subTest(skill=name):
                text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("## Authorization gate", text)
                self.assertIn("explicit", text.lower())

    def test_router_requires_separate_external_approval(self) -> None:
        text = (ROOT / "skills" / "markitdown-document-router" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("paid/external", text.lower())
        self.assertIn("local qwen/ollama calls do not require", text.lower())

    def test_ocr_defaults_to_local_qwen_worker(self) -> None:
        text = (ROOT / "skills" / "markitdown-ocr" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("qwen3.6:27b", text)
        self.assertIn("local_qwen_ocr.py", text)
        self.assertIn("do not route through an opencode agent", text.lower())


if __name__ == "__main__":
    unittest.main()
