"""Tests for deep research topic parsing and synthesis."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from briefing.synthesize import ArticleSnippet
from research import (
    format_research_display,
    google_news_search_rss_url,
    is_google_news_coverage_url,
    parse_research_input,
    synthesize_research_article,
)


class TestParseResearchInput(unittest.TestCase):
    def test_topic_phrase(self) -> None:
        mode, value = parse_research_input("US war with Iran")
        self.assertEqual(mode, "topic")
        self.assertEqual(value, "US war with Iran")

    def test_coverage_url(self) -> None:
        url = "https://news.google.com/stories/CBMi123?hl=en-US"
        self.assertTrue(is_google_news_coverage_url(url))
        mode, value = parse_research_input(url)
        self.assertEqual(mode, "coverage")
        self.assertEqual(value, url)

    def test_regular_url_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_research_input("https://www.reuters.com/world/article")

    def test_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_research_input("   ")


class TestGoogleNewsRss(unittest.TestCase):
    def test_encodes_query(self) -> None:
        url = google_news_search_rss_url("Apple smart glasses")
        self.assertIn("Apple+smart+glasses", url)
        self.assertIn("news.google.com/rss/search", url)


class TestSynthesizeResearchArticle(unittest.TestCase):
    @patch("research.httpx.Client")
    @patch("research.resolve_ollama_model")
    def test_parses_headline(self, mock_resolve, mock_client_cls) -> None:
        mock_resolve.return_value = ("llama3.1:8b", None)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "response": "HEADLINE: Iran tensions rise\n\nBody paragraph one.\n\nParagraph two."
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp

        snippets = [
            ArticleSnippet(
                title="Story",
                url="https://reuters.com/a",
                source="reuters.com",
                text="Facts here.",
            )
        ]
        headline, body, warning = synthesize_research_article("Iran", snippets)
        self.assertEqual(headline, "Iran tensions rise")
        self.assertIn("Body paragraph", body)
        self.assertNotIn("HEADLINE:", body)
        self.assertIsNone(warning)


class TestFormatResearchDisplay(unittest.TestCase):
    def test_includes_sources(self) -> None:
        text = format_research_display(
            topic="Iran",
            headline="Tensions rise",
            body="Article body.",
            sources=[
                ArticleSnippet(
                    title="A",
                    url="https://reuters.com/a",
                    source="reuters.com",
                    text="x",
                )
            ],
        )
        self.assertIn("Research topic: Iran", text)
        self.assertIn("reuters.com", text)
        self.assertIn("Article body.", text)


if __name__ == "__main__":
    unittest.main()
