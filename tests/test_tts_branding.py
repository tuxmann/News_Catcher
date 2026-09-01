"""Tests for spoken source branding."""

from __future__ import annotations

import unittest

from tts import build_acrossfade_filter
from tts_branding import build_intro_text, build_outro_text, domain_to_spoken_name


class TestDomainSpokenName(unittest.TestCase):
    def test_hackaday(self) -> None:
        self.assertEqual(
            domain_to_spoken_name("hackaday.com", apply_pronunciation_rules=False),
            "hackaday dot com",
        )

    def test_strips_www(self) -> None:
        self.assertEqual(
            domain_to_spoken_name("www.bbc.com", apply_pronunciation_rules=False),
            "bbc dot com",
        )

    def test_intro_outro(self) -> None:
        self.assertEqual(
            build_intro_text("hackaday.com"),
            "From hackaday dot com.",
        )
        self.assertEqual(
            build_outro_text("hackaday.com"),
            "That's the end from hackaday dot com.",
        )

    def test_fox10_pronunciation_rules(self) -> None:
        from tts_normalize import TtsReplacementRules, normalize_for_tts

        name = domain_to_spoken_name("fox10phoenix.com", apply_pronunciation_rules=False)
        rules = TtsReplacementRules(
            literals=[("fox10phoenix", "fox ten fehnix")],
            regex=[],
        )
        intro = normalize_for_tts(f"From {name}.", rules=rules)
        self.assertEqual(intro, "From fox ten fehnix dot com.")

    def test_empty_domain(self) -> None:
        self.assertIsNone(build_intro_text(None))
        self.assertIsNone(build_outro_text("  "))

    def test_deep_research_outro(self) -> None:
        from tts_branding import build_deep_research_outro_text

        self.assertEqual(
            build_deep_research_outro_text(),
            "That's the end from News Catcher's Deep research.",
        )


class TestAcrossfadeFilter(unittest.TestCase):
    def test_two_inputs(self) -> None:
        fc = build_acrossfade_filter(2, 0.05)
        self.assertIn("[0:a][1:a]acrossfade", fc or "")
        self.assertIn("[out]", fc or "")

    def test_three_inputs(self) -> None:
        fc = build_acrossfade_filter(3, 0.05)
        self.assertIn("[a01]", fc or "")
        self.assertIn("[2:a]acrossfade", fc or "")

    def test_single_input_returns_none(self) -> None:
        self.assertIsNone(build_acrossfade_filter(1, 0.05))
