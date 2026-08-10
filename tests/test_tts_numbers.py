"""Tests for long-number → words TTS expansion."""

from __future__ import annotations

import unittest

from tts_normalize import TtsReplacementRules, clear_rules_cache, normalize_for_tts
from tts_numbers import apply_long_numbers, number_token_to_words


class TestNumberTokenToWords(unittest.TestCase):
    def test_year_1977(self) -> None:
        self.assertEqual(number_token_to_words("1977"), "nineteen seventy seven")

    def test_year_1900(self) -> None:
        self.assertEqual(number_token_to_words("1900"), "nineteen hundred")

    def test_year_1905(self) -> None:
        self.assertEqual(number_token_to_words("1905"), "nineteen oh five")

    def test_year_2000(self) -> None:
        self.assertEqual(number_token_to_words("2000"), "two thousand")

    def test_year_2005(self) -> None:
        self.assertEqual(number_token_to_words("2005"), "two thousand five")

    def test_year_2015(self) -> None:
        self.assertEqual(number_token_to_words("2015"), "twenty fifteen")

    def test_three_digit_cardinal(self) -> None:
        self.assertEqual(number_token_to_words("500"), "five hundred")
        self.assertEqual(number_token_to_words("123"), "one hundred twenty three")

    def test_comma_quantity_not_year(self) -> None:
        self.assertEqual(number_token_to_words("1,977"), "one thousand nine hundred seventy seven")

    def test_thousands_with_commas(self) -> None:
        self.assertEqual(number_token_to_words("3,000"), "three thousand")
        self.assertEqual(
            number_token_to_words("12,345"),
            "twelve thousand three hundred forty five",
        )


class TestApplyLongNumbers(unittest.TestCase):
    def test_sentence_year(self) -> None:
        self.assertEqual(
            apply_long_numbers("The U.S. in 1977 under former President."),
            "The U.S. in nineteen seventy seven under former President.",
        )

    def test_leaves_one_and_two_digit(self) -> None:
        self.assertEqual(apply_long_numbers("On day 7 of week 12."), "On day 7 of week 12.")

    def test_skips_letter_hyphen_codes(self) -> None:
        self.assertEqual(apply_long_numbers("The A-380 landed."), "The A-380 landed.")

    def test_skips_decimals(self) -> None:
        self.assertEqual(apply_long_numbers("Pi is 3.141."), "Pi is 3.141.")


class TestNormalizeIntegratesNumbers(unittest.TestCase):
    def setUp(self) -> None:
        clear_rules_cache()

    def tearDown(self) -> None:
        clear_rules_cache()

    def test_year_via_normalize(self) -> None:
        rules = TtsReplacementRules(literals=[], regex=[])
        out = normalize_for_tts(
            "The U.S. in 1977 under former President.",
            rules=rules,
            enabled=True,
        )
        self.assertEqual(
            out,
            "The U.S. in nineteen seventy seven under former President.",
        )

    def test_distance_then_words(self) -> None:
        rules = TtsReplacementRules(literals=[], regex=[])
        out = normalize_for_tts(
            "It flew 3,000km across the desert.", rules=rules, enabled=True
        )
        self.assertEqual(out, "It flew three thousand across the desert.")


if __name__ == "__main__":
    unittest.main()
