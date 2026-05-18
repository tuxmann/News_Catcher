"""Tests for RSS ingest filtering."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from briefing.config_loader import BriefingConfig, FeedConfig
from briefing.ingest import collect_feed_items


class TestBriefingIngest(unittest.TestCase):
    @patch("briefing.ingest.feedparser.parse")
    def test_filters_by_allowed_domain(self, mock_parse) -> None:
        mock_parse.return_value = type(
            "Feed",
            (),
            {
                "entries": [
                    {
                        "title": "Good story",
                        "link": "https://www.reuters.com/world/story",
                        "summary": "Markets rise",
                    },
                    {
                        "title": "Bad domain",
                        "link": "https://random-blog.example.org/post",
                        "summary": "ignored",
                    },
                ]
            },
        )()
        cfg = BriefingConfig(
            feeds=[FeedConfig(url="https://feeds.example.com/rss")],
            max_articles_per_feed=10,
            max_articles_total=10,
        )
        items = collect_feed_items(
            cfg,
            domains_file=None,
            allowed_domains={"reuters.com"},
        )
        self.assertEqual(len(items), 1)
        self.assertIn("reuters.com", items[0].url)
