"""Tests for editing tts_replacements.json."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tts_normalize import add_literal_replacement, clear_rules_cache, load_tts_replacement_rules


class TestAddLiteralReplacement(unittest.TestCase):
    def setUp(self) -> None:
        clear_rules_cache()

    def tearDown(self) -> None:
        clear_rules_cache()

    def test_add_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tts_replacements.json"
            path.write_text(
                json.dumps({"replacements": [], "regex": []}) + "\n",
                encoding="utf-8",
            )
            added = add_literal_replacement("Polish", "Poleish", path=path)
            self.assertTrue(added)
            rules = load_tts_replacement_rules(path, reload=True)
            self.assertIn(("Polish", "Poleish"), rules.literals)
            updated = add_literal_replacement("Polish", "Pole-ish", path=path)
            self.assertFalse(updated)
            rules2 = load_tts_replacement_rules(path, reload=True)
            self.assertIn(("Polish", "Pole-ish"), rules2.literals)
