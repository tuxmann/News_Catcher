"""Tests for TTS helpers (no live KittenTTS in CI)."""

from __future__ import annotations

import unittest

from tts import build_narration_text, chunk_text_for_tts, is_speak_phrase


class TestSpeakPhrase(unittest.TestCase):
    def test_matches_variants(self) -> None:
        self.assertTrue(is_speak_phrase("newscatcher, speak to me"))
        self.assertTrue(is_speak_phrase("NEWSCATCHER SPEAK TO ME"))
        self.assertTrue(is_speak_phrase("newscatcher speak to me"))

    def test_rejects_other_text(self) -> None:
        self.assertFalse(is_speak_phrase("hello"))
        self.assertFalse(is_speak_phrase("newscatcher speak"))
        self.assertFalse(is_speak_phrase("https://example.com"))


class TestTtsTextPrep(unittest.TestCase):
    def test_narration_with_title(self) -> None:
        out = build_narration_text("Headline", "Body.")
        self.assertEqual(out, "Headline. Body.")

    def test_chunk_splits_long_paragraph(self) -> None:
        text = "a" * 5000
        chunks = chunk_text_for_tts(text, max_chars=1000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
