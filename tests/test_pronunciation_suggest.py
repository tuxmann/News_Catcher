"""Tests for Ollama pronunciation suggestion parsing."""

from __future__ import annotations

import unittest

from pronunciation_suggest import _parse_suggestion_list


class TestParseSuggestionList(unittest.TestCase):
    def test_json_array(self) -> None:
        raw = 'Here you go:\n["Poleish", "Pole-ish", "Po-lish"]'
        out = _parse_suggestion_list(raw, "Polish", 5)
        self.assertEqual(out, ["Poleish", "Pole-ish", "Po-lish"])

    def test_dedupes_and_limits(self) -> None:
        raw = '["A", "A", "B", "C", "D", "E"]'
        out = _parse_suggestion_list(raw, "X", 3)
        self.assertEqual(out, ["A", "B", "C"])
