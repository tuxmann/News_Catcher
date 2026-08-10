"""Tests for short ALL-CAPS acronym handling."""

from __future__ import annotations

import unittest

from tts_acronym import (
    apply_short_caps_acronyms,
    is_word_covered_by_literal_rules,
    letter_hyphenate,
    sentence_triplet_at_offset,
    split_sentences,
)
from tts_normalize import LiteralReplacement, TtsReplacementRules, clear_rules_cache


class TestTtsAcronymHelpers(unittest.TestCase):
    def test_letter_hyphenate(self) -> None:
        self.assertEqual(letter_hyphenate("KTAR"), "K-T-A-R")

    def test_split_sentences(self) -> None:
        sents = split_sentences("First line. Second line! Third?")
        self.assertEqual(len(sents), 3)

    def test_sentence_triplet(self) -> None:
        para = "Alpha. We want to WIN today. Beta."
        offset = para.index("WIN")
        before, current, after = sentence_triplet_at_offset(para, offset)
        self.assertEqual(before, "Alpha.")
        self.assertIn("WIN", current)
        self.assertEqual(after, "Beta.")

    def test_covered_by_literal_rules(self) -> None:
        rules = TtsReplacementRules(
            literals=[LiteralReplacement("IRGC", "I-R-G-C")],
            regex=[],
        )
        self.assertTrue(is_word_covered_by_literal_rules("IRGC", rules))
        self.assertFalse(is_word_covered_by_literal_rules("KTAR", rules))


class TestApplyShortCapsAcronyms(unittest.TestCase):
    def setUp(self) -> None:
        clear_rules_cache()

    def tearDown(self) -> None:
        clear_rules_cache()

    def test_hyphenates_confirmed_acronym(self) -> None:
        rules = TtsReplacementRules(literals=[], regex=[])

        def classifier(word: str, before, current, after) -> bool:
            if word == "KTAR":
                return True
            return False

        out = apply_short_caps_acronyms(
            "Listen on KTAR this morning.",
            rules,
            enabled=True,
            classifier=classifier,
        )
        self.assertEqual(out, "Listen on K-T-A-R this morning.")

    def test_leaves_emphasis_word(self) -> None:
        rules = TtsReplacementRules(literals=[], regex=[])

        def classifier(word: str, before, current, after) -> bool:
            if word == "WIN":
                return False
            return None

        out = apply_short_caps_acronyms(
            "We want to WIN the game.",
            rules,
            enabled=True,
            classifier=classifier,
        )
        self.assertEqual(out, "We want to WIN the game.")

    def test_skips_words_with_replacement_rules(self) -> None:
        rules = TtsReplacementRules(
            literals=[LiteralReplacement("NES", "N-E-S")],
            regex=[],
        )

        def classifier(word: str, before, current, after) -> bool:
            self.fail("classifier should not run when rule exists")
            return True

        out = apply_short_caps_acronyms(
            "The NES classic sold well.",
            rules,
            enabled=True,
            classifier=classifier,
        )
        self.assertEqual(out, "The NES classic sold well.")

    def test_uses_paragraph_context(self) -> None:
        rules = TtsReplacementRules(literals=[], regex=[])
        seen: list[tuple[str | None, str, str | None]] = []

        def classifier(word: str, before, current, after) -> bool:
            seen.append((before, current, after))
            return word == "MOU"

        text = "They met Monday. The MOU was signed. Talks continue."
        out = apply_short_caps_acronyms(text, rules, enabled=True, classifier=classifier)
        self.assertEqual(out, "They met Monday. The M-O-U was signed. Talks continue.")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "They met Monday.")
        self.assertIn("MOU", seen[0][1])


if __name__ == "__main__":
    unittest.main()
