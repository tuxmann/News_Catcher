"""Tests for WordPress REST API article fetch."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fetch_wordpress import slug_from_article_url, wordpress_fetch_html


class TestSlugFromArticleUrl(unittest.TestCase):
    def test_marktechpost_style(self) -> None:
        url = (
            "https://www.marktechpost.com/2026/05/18/"
            "how-to-build-an-advanced-agentic-ai-system/"
        )
        self.assertEqual(
            slug_from_article_url(url),
            "how-to-build-an-advanced-agentic-ai-system",
        )

    def test_empty_path(self) -> None:
        self.assertIsNone(slug_from_article_url("https://example.com/"))


class TestWordpressFetchHtml(unittest.IsolatedAsyncioTestCase):
    async def test_returns_html_on_200(self) -> None:
        post = {
            "link": "https://www.marktechpost.com/2026/05/18/example/",
            "title": {"rendered": "Example Title"},
            "content": {"rendered": "<p>Body text here.</p>"},
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [post]
        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_resp)

        result = await wordpress_fetch_html(
            client,
            "https://www.marktechpost.com/2026/05/18/example/",
            {"marktechpost.com"},
            api_domains={"marktechpost.com"},
        )
        self.assertIsNotNone(result)
        raw, final_url = result
        self.assertEqual(final_url, post["link"])
        self.assertIn(b"Body text here", raw)
        self.assertIn(b"Example Title", raw)

    async def test_skips_non_api_domain(self) -> None:
        client = AsyncMock()
        result = await wordpress_fetch_html(
            client,
            "https://www.example.com/2026/05/18/foo/",
            {"example.com"},
            api_domains={"marktechpost.com"},
        )
        self.assertIsNone(result)
        client.get.assert_not_called()
