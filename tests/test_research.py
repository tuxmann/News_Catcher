"""Tests for deep research topic parsing and synthesis."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from briefing.synthesize import ArticleSnippet
from research import (
    RESEARCH_LENGTH_PRESETS,
    ResearchOptions,
    answer_research_followup,
    format_research_display,
    google_news_search_rss_url,
    is_google_news_coverage_url,
    parse_research_input,
    split_research_display,
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

    @patch("research.httpx.Client")
    @patch("research.resolve_ollama_model")
    def test_prompt_requests_prose_not_lists(self, mock_resolve, mock_client_cls) -> None:
        mock_resolve.return_value = ("llama3.1:8b", None)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "HEADLINE: Test\n\nBody."}
        mock_resp.raise_for_status = MagicMock()
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.return_value = mock_resp

        snippets = [
            ArticleSnippet(
                title="Story",
                url="https://reuters.com/a",
                source="reuters.com",
                text="Facts here.",
            )
        ]
        synthesize_research_article(
            "Iran",
            snippets,
            target_words=900,
            length_label="500–1200 words",
        )
        prompt = mock_client.post.call_args.kwargs["json"]["prompt"]
        self.assertIn("NEVER use bullet points", prompt)
        self.assertIn("just the facts", prompt)
        self.assertIn("500–1200 words", prompt)


class TestResearchOptions(unittest.TestCase):
    def test_normalized_presets(self) -> None:
        opts = ResearchOptions(
            max_articles=24,
            target_words=5000,
            length_label="5000 word essay",
        ).normalized()
        self.assertEqual(opts.max_articles, 25)
        self.assertEqual(opts.length_label, "5000 word essay")
        self.assertEqual(opts.target_words, 5000)


class TestResearchFollowup(unittest.TestCase):
    @patch("research.httpx.Client")
    @patch("research.resolve_ollama_model")
    def test_followup_uses_article_context(self, mock_resolve, mock_client_cls) -> None:
        mock_resolve.return_value = ("llama3.1:8b", None)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "Based on the reporting, tensions remain high."}
        mock_resp.raise_for_status = MagicMock()
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.return_value = mock_resp

        snippets = [
            ArticleSnippet(
                title="Story",
                url="https://reuters.com/a",
                source="reuters.com",
                text="Facts here.",
            )
        ]
        answer, warning = answer_research_followup(
            topic="Iran",
            headline="Tensions rise",
            article_body="Diplomats met in Geneva.",
            sources=snippets,
            question="What happens next?",
        )
        self.assertIn("tensions", answer.lower())
        self.assertIsNone(warning)
        prompt = mock_client.post.call_args.kwargs["json"]["prompt"]
        self.assertIn("Do not invent facts", prompt)
        self.assertIn("What happens next?", prompt)


class TestFormatResearchDisplay(unittest.TestCase):
    def test_sources_after_body_with_divider(self) -> None:
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
        body_pos = text.find("Article body.")
        sources_pos = text.find("SOURCES (1 articles)")
        divider_pos = text.find("━━━━━━━━")
        self.assertGreater(sources_pos, body_pos)
        self.assertGreater(divider_pos, body_pos)
        self.assertIn("Research topic: Iran", text)
        self.assertIn("reuters.com", text)


class TestSplitResearchDisplay(unittest.TestCase):
    def test_splits_at_sources_divider(self) -> None:
        from research import SOURCES_DIVIDER

        display = format_research_display(
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
        article_part, sources_part = split_research_display(display)
        self.assertIn("Article body.", article_part)
        self.assertNotIn(SOURCES_DIVIDER, article_part)
        self.assertIn("SOURCES (1 articles)", sources_part)
        self.assertIn("reuters.com", sources_part)


if __name__ == "__main__":
    unittest.main()
