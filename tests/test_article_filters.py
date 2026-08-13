"""Tests for per-site eliminate-phrase filters."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from article_filters import (
    add_eliminate_phrase,
    remove_eliminate_phrase,
    strip_eliminated_phrases,
)


class TestEliminatePhrases(unittest.TestCase):
    def test_drops_standalone_paragraph(self) -> None:
        text = (
            "Story lead.\n\n"
            "CLICK HERE TO DOWNLOAD THE FOX NEWS APP\n\n"
            "More story."
        )
        out = strip_eliminated_phrases(
            text,
            "https://www.foxnews.com/politics/example",
            phrases=["CLICK HERE TO DOWNLOAD THE FOX NEWS APP"],
        )
        self.assertNotIn("FOX NEWS APP", out)
        self.assertIn("Story lead.", out)
        self.assertIn("More story.", out)

    def test_case_and_whitespace_insensitive(self) -> None:
        text = "Intro.\n\nClick  here   to download the fox news app.\n\nEnd."
        out = strip_eliminated_phrases(
            text,
            "https://foxnews.com/x",
            phrases=["CLICK HERE TO DOWNLOAD THE FOX NEWS APP"],
        )
        self.assertEqual(out, "Intro.\n\nEnd.")

    def test_strips_inline_phrase(self) -> None:
        text = "Read this. CLICK HERE TO DOWNLOAD THE FOX NEWS APP Then continue."
        out = strip_eliminated_phrases(
            text,
            "https://www.foxnews.com/x",
            phrases=["CLICK HERE TO DOWNLOAD THE FOX NEWS APP"],
        )
        self.assertNotIn("FOX NEWS APP", out)
        self.assertIn("Read this.", out)
        self.assertIn("Then continue.", out)

    def test_other_site_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phrases.json"
            add_eliminate_phrase(
                "foxnews.com",
                "CLICK HERE TO DOWNLOAD THE FOX NEWS APP",
                path=path,
            )
            text = "CLICK HERE TO DOWNLOAD THE FOX NEWS APP\n\nBody."
            out = strip_eliminated_phrases(
                text, "https://www.reuters.com/world/x", path=path
            )
            self.assertIn("FOX NEWS APP", out)

    def test_add_and_remove_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phrases.json"
            self.assertTrue(
                add_eliminate_phrase("foxnews.com", "CLICK HERE", path=path)
            )
            self.assertFalse(
                add_eliminate_phrase("FoxNews.com", "click here", path=path)
            )
            self.assertTrue(remove_eliminate_phrase("foxnews.com", "CLICK HERE", path=path))
            self.assertFalse(remove_eliminate_phrase("foxnews.com", "CLICK HERE", path=path))


if __name__ == "__main__":
    unittest.main()
