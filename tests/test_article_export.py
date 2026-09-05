"""Tests for save-to-disk export helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from article_cache import CachedArticle
from article_export import (
    domain_to_display_name,
    is_save_to_disk_phrase,
    save_article_text_file,
    site_to_filename_stem,
    telegram_audio_filename,
    title_to_filename_stem,
)


class TestSaveToDiskPhrase(unittest.TestCase):
    def test_matches(self) -> None:
        self.assertTrue(is_save_to_disk_phrase("newscatcher, save to disk"))
        self.assertTrue(is_save_to_disk_phrase("NEWSCATCHER SAVE TO DISK"))

    def test_rejects_other(self) -> None:
        self.assertFalse(is_save_to_disk_phrase("newscatcher, speak to me"))


class TestTitleFilename(unittest.TestCase):
    def test_six_words_underscores(self) -> None:
        title = "The Quick Brown Fox Jumps Over the Lazy Dog"
        self.assertEqual(
            title_to_filename_stem(title),
            "The_Quick_Brown_Fox_Jumps_Over",
        )

    def test_strips_punctuation(self) -> None:
        self.assertEqual(
            title_to_filename_stem("Hello, World! — A Test"),
            "Hello_World_A_Test",
        )

    def test_untitled_fallback(self) -> None:
        self.assertEqual(title_to_filename_stem(None), "untitled")
        self.assertEqual(title_to_filename_stem("!!!"), "untitled")


class TestTelegramAudioFilename(unittest.TestCase):
    def test_site_stem(self) -> None:
        self.assertEqual(site_to_filename_stem("reuters.com"), "reuters_com")
        self.assertEqual(site_to_filename_stem("www.bbc.com"), "bbc_com")

    def test_display_name(self) -> None:
        self.assertEqual(domain_to_display_name("hackaday.com"), "Hackaday")
        self.assertEqual(domain_to_display_name("www.FoxNews.com"), "Foxnews")
        self.assertEqual(domain_to_display_name("the-register.com"), "The register")
        self.assertEqual(domain_to_display_name("independent.co.uk"), "Independent")
        self.assertEqual(domain_to_display_name(None), "News Catcher")

    def test_prepends_site(self) -> None:
        self.assertEqual(
            telegram_audio_filename("reuters.com", "Markets Rally Today Fast"),
            "reuters_com_Markets_Rally_Today_Fast.mp3",
        )

    def test_multipart(self) -> None:
        self.assertEqual(
            telegram_audio_filename("a.com", "Hi", part=2, total=3),
            "a_com_Hi_part2.mp3",
        )


class TestSaveArticleTextFile(unittest.TestCase):
    def test_writes_body_and_collision_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            article = CachedArticle(
                user_id=1,
                url="https://example.com",
                title="My Sample Article Title Here",
                text="Body text for experiments.",
                saved_at=0.0,
            )
            p1 = save_article_text_file(article, out_dir)
            self.assertEqual(p1.name, "My_Sample_Article_Title_Here.txt")
            self.assertEqual(p1.read_text(encoding="utf-8"), "Body text for experiments.")
            p2 = save_article_text_file(article, out_dir)
            self.assertEqual(p2.name, "My_Sample_Article_Title_Here_2.txt")
