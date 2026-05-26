"""Tests for TTS normalization (no live KittenTTS)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tts_normalize import (
    TtsReplacementRules,
    clear_rules_cache,
    load_tts_replacement_rules,
    normalize_for_tts,
)


class TestNormalizeForTts(unittest.TestCase):
    def setUp(self) -> None:
        clear_rules_cache()

    def tearDown(self) -> None:
        clear_rules_cache()

    def test_literal_us_abbreviation(self) -> None:
        rules = TtsReplacementRules(
            literals=[("U.S.", "United States")],
            regex=[],
        )
        out = normalize_for_tts("The U.S. envoy arrived.", rules=rules, enabled=True)
        self.assertEqual(out, "The United States envoy arrived.")

    def test_longest_literal_first(self) -> None:
        rules = TtsReplacementRules(
            literals=[("U.S.", "United States"), ("U.S.A.", "United States of America")],
            regex=[],
        )
        out = normalize_for_tts("U.S.A. and U.S. trade.", rules=rules, enabled=True)
        self.assertEqual(
            out,
            "United States of America and United States trade.",
        )

    def test_regex_polish_government(self) -> None:
        import re

        from tts_normalize import RegexReplacement

        rules = TtsReplacementRules(
            literals=[],
            regex=[
                RegexReplacement(
                    re.compile(r"\bPolish\b(?=\s+government)", re.IGNORECASE),
                    "Poleish",
                )
            ],
        )
        out = normalize_for_tts("The Polish government met.", rules=rules, enabled=True)
        self.assertEqual(out, "The Poleish government met.")
        out2 = normalize_for_tts("Use furniture polish.", rules=rules, enabled=True)
        self.assertEqual(out2, "Use furniture polish.")

    def test_disabled_passthrough(self) -> None:
        rules = TtsReplacementRules(literals=[("U.S.", "United States")], regex=[])
        out = normalize_for_tts("The U.S. left.", rules=rules, enabled=False)
        self.assertEqual(out, "The U.S. left.")

    def test_load_from_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            path.write_text(
                json.dumps(
                    {
                        "replacements": [{"from": "U.K.", "to": "United Kingdom"}],
                        "regex": [],
                    }
                ),
                encoding="utf-8",
            )
            rules = load_tts_replacement_rules(path, reload=True)
            self.assertEqual(rules.literals, [("U.K.", "United Kingdom")])

    def test_missing_file_yields_empty_rules(self) -> None:
        rules = load_tts_replacement_rules(
            Path("/nonexistent/tts_replacements.json"), reload=True
        )
        self.assertEqual(rules.literals, [])
        self.assertEqual(rules.regex, [])


if __name__ == "__main__":
    unittest.main()
