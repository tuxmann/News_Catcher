"""Tests for watchlist-gated article strip rules."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from article_strip import (
    SiteStripRules,
    apply_html_selectors,
    apply_text_strip_rules,
    paragraph_is_whole_emphasis,
    prepare_html_for_extract,
    rules_for_url,
    strip_trailing_emphasis_paragraphs,
    upsert_site_rules,
)
from extract import extract_article


class TestTrailingEmphasis(unittest.TestCase):
    def test_whole_emphasis(self) -> None:
        self.assertTrue(
            paragraph_is_whole_emphasis(
                "*If you’re driving a Rivian, powering it with home solar…*"
            )
        )
        self.assertTrue(
            paragraph_is_whole_emphasis(
                "*FTC: We use income earning auto affiliate links.* More."
            )
        )
        self.assertFalse(paragraph_is_whole_emphasis("Normal closing sentence."))

    def test_strip_trailing(self) -> None:
        text = (
            "Real takeaway paragraph.\n\n"
            "*If you’re driving a Rivian, get solar via EnergySage.*\n\n"
            "*FTC: We use income earning auto affiliate links.* More."
        )
        out = strip_trailing_emphasis_paragraphs(text)
        self.assertEqual(out, "Real takeaway paragraph.")
        self.assertNotIn("EnergySage", out)
        self.assertNotIn("FTC", out)


class TestHtmlSelectors(unittest.TestCase):
    def test_removes_disclaimer_nodes(self) -> None:
        html = """
        <html><body>
        <p>Body.</p>
        <div class="ad-disclaimer-container">
          <p class="disclaimer-affiliate"><em>FTC: affiliate</em> More.</p>
        </div>
        </body></html>
        """
        out = apply_html_selectors(
            html, [".ad-disclaimer-container", ".disclaimer-affiliate"]
        )
        self.assertIn("Body.", out)
        self.assertNotIn("FTC", out)
        self.assertNotIn("ad-disclaimer-container", out)


class TestWatchlistGate(unittest.TestCase):
    def test_rules_require_watchlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = Path(tmp) / "strip.json"
            watch_path = Path(tmp) / "watch.json"
            watch_path.write_text(
                json.dumps(
                    {
                        "sites": [
                            {
                                "domain": "electrek.co",
                                "check_interval_minutes": 60,
                                "posts": [],
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            upsert_site_rules(
                SiteStripRules(
                    domain="electrek.co",
                    html_selectors=[".disclaimer-affiliate"],
                    drop_trailing_emphasis_paragraphs=True,
                ),
                path=rules_path,
            )
            url = "https://electrek.co/2026/09/04/example/"
            with patch("article_strip.config.WATCHLIST_FILE", watch_path):
                self.assertIsNotNone(
                    rules_for_url(url, path=rules_path, watchlist_path=watch_path)
                )
                # Not watchlisted → no rules applied
                other_watch = Path(tmp) / "watch2.json"
                other_watch.write_text('{"sites": []}\n', encoding="utf-8")
                self.assertIsNone(
                    rules_for_url(url, path=rules_path, watchlist_path=other_watch)
                )

    def test_prepare_and_text_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = Path(tmp) / "strip.json"
            watch_path = Path(tmp) / "watch.json"
            watch_path.write_text(
                json.dumps(
                    {
                        "sites": [
                            {
                                "domain": "electrek.co",
                                "check_interval_minutes": 60,
                                "posts": [],
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            upsert_site_rules(
                SiteStripRules(
                    domain="electrek.co",
                    html_selectors=[".disclaimer-affiliate"],
                    drop_trailing_emphasis_paragraphs=True,
                ),
                path=rules_path,
            )
            html = (
                "<html><body><article><p>News.</p>"
                '<p class="disclaimer-affiliate"><em>FTC: links</em></p>'
                "</article></body></html>"
            )
            url = "https://electrek.co/x/"
            with (
                patch("article_strip.config.WATCHLIST_FILE", watch_path),
                patch("article_strip.config.ARTICLE_STRIP_RULES_FILE", rules_path),
            ):
                cleaned = prepare_html_for_extract(html, url)
                self.assertNotIn("FTC", cleaned)
                text = (
                    "News body.\n\n"
                    "*Solar pitch via EnergySage for Rivian owners.*"
                )
                stripped = apply_text_strip_rules(text, url)
                self.assertEqual(stripped, "News body.")


class TestExtractElectrekStyle(unittest.TestCase):
    def test_extract_drops_italic_ad_when_watchlisted(self) -> None:
        html = b"""<!DOCTYPE html><html><body><article>
        <h1>Rivian update</h1>
        <p>Real article paragraph about software.</p>
        <p>Another real paragraph.</p>
        <p><em>If you are driving a Rivian, powering it with home solar can cut costs.
        Check out EnergySage. Get your free quotes here.</em></p>
        <div class="ad-disclaimer-container">
          <p class="disclaimer-affiliate"><em>FTC: We use income earning auto affiliate links.</em> More.</p>
        </div>
        </article></body></html>"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = Path(tmp) / "strip.json"
            watch_path = Path(tmp) / "watch.json"
            watch_path.write_text(
                json.dumps(
                    {
                        "sites": [
                            {
                                "domain": "electrek.co",
                                "check_interval_minutes": 60,
                                "posts": [],
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            upsert_site_rules(
                SiteStripRules(
                    domain="electrek.co",
                    html_selectors=[
                        ".ad-disclaimer-container",
                        ".disclaimer-affiliate",
                    ],
                    drop_trailing_emphasis_paragraphs=True,
                ),
                path=rules_path,
            )
            with (
                patch("article_strip.config.WATCHLIST_FILE", watch_path),
                patch("article_strip.config.ARTICLE_STRIP_RULES_FILE", rules_path),
            ):
                article = extract_article(
                    html, "https://electrek.co/2026/09/04/rivian-example/"
                )
        self.assertIn("Real article paragraph", article.text)
        self.assertNotIn("EnergySage", article.text)
        self.assertNotIn("FTC", article.text)


if __name__ == "__main__":
    unittest.main()
