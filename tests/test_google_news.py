"""Tests for Google News URL helpers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from google_news import is_google_news_article_url, resolve_google_news_url


class TestGoogleNewsUrl(unittest.TestCase):
    def test_is_google_news_article_url(self) -> None:
        self.assertTrue(
            is_google_news_article_url(
                "https://news.google.com/read/CBMixwFBVV95cUxP"
            )
        )
        self.assertTrue(
            is_google_news_article_url(
                "https://news.google.com/rss/articles/CBMiabc"
            )
        )
        self.assertFalse(is_google_news_article_url("https://www.reuters.com/world"))

    def test_resolve_passthrough(self) -> None:
        url = "https://www.reuters.com/world/article"
        self.assertEqual(resolve_google_news_url(url), url)

    @patch("googlenewsdecoder.gnewsdecoder")
    def test_resolve_google_news(self, mock_decoder) -> None:
        mock_decoder.return_value = {
            "status": True,
            "decoded_url": "https://www.washingtonpost.com/nation/2026/07/05/story",
        }
        out = resolve_google_news_url("https://news.google.com/read/CBMiabc")
        self.assertEqual(out, "https://www.washingtonpost.com/nation/2026/07/05/story")

    @patch("googlenewsdecoder.gnewsdecoder")
    def test_resolve_failure(self, mock_decoder) -> None:
        mock_decoder.return_value = {"status": False, "message": "bad"}
        self.assertIsNone(resolve_google_news_url("https://news.google.com/read/CBMiabc"))


if __name__ == "__main__":
    unittest.main()
