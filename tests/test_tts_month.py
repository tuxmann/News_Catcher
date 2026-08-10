"""Tests for month abbreviation expansion."""

from __future__ import annotations

import unittest

from tts_month import (
    apply_month_abbreviations,
    looks_like_month_date_following,
    month_full_name,
)
from tts_normalize import LiteralReplacement, TtsReplacementRules, clear_rules_cache


class TestMonthHelpers(unittest.TestCase):
    def test_month_full_name(self) -> None:
        self.assertEqual(month_full_name("Jan"), "January")
        self.assertEqual(month_full_name("Sept"), "September")

    def test_date_following(self) -> None:
        self.assertTrue(looks_like_month_date_following("Jan 21 was cold.", 3))
        self.assertFalse(looks_like_month_date_following("Jan Smith arrived.", 3))


class TestApplyMonthAbbreviations(unittest.TestCase):
    def setUp(self) -> None:
        clear_rules_cache()

    def tearDown(self) -> None:
        clear_rules_cache()

    def test_expands_confirmed_month(self) -> None:
        rules = TtsReplacementRules(literals=[], regex=[])

        def classifier(token: str, before, current, after) -> bool:
            return token.casefold() == "jan"

        out = apply_month_abbreviations(
            "The rally is on Jan 21 downtown.",
            rules,
            enabled=True,
            classifier=classifier,
        )
        self.assertEqual(out, "The rally is on January 21 downtown.")

    def test_leaves_person_name(self) -> None:
        rules = TtsReplacementRules(literals=[], regex=[])

        def classifier(token: str, before, current, after) -> bool:
            return False

        out = apply_month_abbreviations(
            "Meet Jan Smith at noon.",
            rules,
            enabled=True,
            classifier=classifier,
        )
        self.assertEqual(out, "Meet Jan Smith at noon.")

    def test_date_fallback_when_ollama_silent(self) -> None:
        rules = TtsReplacementRules(literals=[], regex=[])

        def classifier(token: str, before, current, after) -> bool | None:
            return None

        out = apply_month_abbreviations(
            "Scheduled for Mar 3.",
            rules,
            enabled=True,
            classifier=classifier,
        )
        self.assertEqual(out, "Scheduled for March 3.")

    def test_skips_when_literal_rule_exists(self) -> None:
        rules = TtsReplacementRules(
            literals=[LiteralReplacement("Jan", "John")],
            regex=[],
        )

        def classifier(token: str, before, current, after) -> bool:
            self.fail("classifier should not run when rule exists")
            return True

        out = apply_month_abbreviations(
            "On Jan 21.",
            rules,
            enabled=True,
            classifier=classifier,
        )
        self.assertEqual(out, "On Jan 21.")


if __name__ == "__main__":
    unittest.main()
