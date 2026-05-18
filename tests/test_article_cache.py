"""Tests for last-article disk cache."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from article_cache import load_last_article, purge_expired, save_last_article


class TestArticleCache(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            save_last_article(cache_dir, 42, "https://example.com/a", "Title", "Body text")
            loaded = load_last_article(cache_dir, 42, ttl_seconds=3600)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.url, "https://example.com/a")
            self.assertEqual(loaded.title, "Title")
            self.assertEqual(loaded.text, "Body text")

    def test_ttl_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            article = save_last_article(cache_dir, 1, "https://x.com", None, "x")
            path = cache_dir / "1.json"
            data = path.read_text(encoding="utf-8")
            path.write_text(
                data.replace(str(article.saved_at), str(time.time() - 10000)),
                encoding="utf-8",
            )
            self.assertIsNone(load_last_article(cache_dir, 1, ttl_seconds=60))

    def test_purge_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            article = save_last_article(cache_dir, 9, "https://x.com", None, "old")
            path = cache_dir / "9.json"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    str(article.saved_at), str(time.time() - 100000)
                ),
                encoding="utf-8",
            )
            removed = purge_expired(cache_dir, ttl_seconds=3600)
            self.assertGreaterEqual(removed, 1)
            self.assertIsNone(load_last_article(cache_dir, 9, ttl_seconds=3600))
