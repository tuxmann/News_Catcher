"""Tests for find/remove pronunciation rules."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tts_normalize import (
    add_literal_replacement,
    clear_rules_cache,
    find_literal_replacements,
    remove_literal_replacement,
)


class TestFindRemoveReplacements(unittest.TestCase):
    def setUp(self) -> None:
        clear_rules_cache()

    def tearDown(self) -> None:
        clear_rules_cache()

    def _path(self, tmp: str) -> Path:
        path = Path(tmp) / "tts_replacements.json"
        path.write_text(
            json.dumps(
                {
                    "replacements": [
                        {"from": "U.S.", "to": "United States"},
                        {"from": "a.m.", "to": "A eM"},
                        {"from": "Polish", "to": "Poleish"},
                    ],
                    "regex": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_find_ranks_similar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            matches = find_literal_replacements("US", path=path, limit=5)
            self.assertTrue(matches)
            self.assertEqual(matches[0].from_text, "U.S.")

    def test_find_by_to_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            matches = find_literal_replacements("Poleish", path=path)
            self.assertEqual(matches[0].from_text, "Polish")

    def test_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            self.assertTrue(remove_literal_replacement("a.m.", path=path))
            self.assertFalse(remove_literal_replacement("a.m.", path=path))
            matches = find_literal_replacements("a.m.", path=path)
            self.assertEqual(matches, [])

    def test_add_then_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tts_replacements.json"
            path.write_text(
                json.dumps({"replacements": [], "regex": []}) + "\n", encoding="utf-8"
            )
            add_literal_replacement("foo", "bar", path=path)
            self.assertTrue(remove_literal_replacement("foo", path=path))


if __name__ == "__main__":
    unittest.main()
