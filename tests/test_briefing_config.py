"""Tests for briefing configuration loading."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from briefing.config_loader import load_briefing_config


class TestBriefingConfig(unittest.TestCase):
    def test_load_yaml(self) -> None:
        yaml_text = """
feeds:
  - url: https://example.com/feed.xml
    label: Example
deep_dives:
  - test topic
subjects:
  - name: Economy
    keywords: [gdp, markets]
max_articles_per_feed: 3
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(yaml_text)
            path = Path(f.name)
        try:
            cfg = load_briefing_config(path)
            self.assertEqual(len(cfg.feeds), 1)
            self.assertEqual(cfg.feeds[0].label, "Example")
            self.assertEqual(cfg.deep_dives, ["test topic"])
            self.assertEqual(cfg.subjects[0].name, "Economy")
            self.assertEqual(cfg.max_articles_per_feed, 3)
        finally:
            path.unlink(missing_ok=True)
