from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPECIALISTS = (
    "markitdown-document-router",
    "markitdown-ocr",
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
        self.assertIn("estimated file/page/api-call scope", text.lower())


if __name__ == "__main__":
    unittest.main()
