"""Tests for article text cleanup and Telegram HTML formatting."""

from __future__ import annotations

import unittest

from article_format import (
    body_starts_with_title,
    deduplicate_article_text,
    emphasis_to_telegram_html,
    format_paragraphs_for_telegram,
    strip_emphasis_markers,
    strip_formatting_prefixes,
    strip_link_only_paragraphs,
)


class TestLinkOnlyParagraphs(unittest.TestCase):
    def test_drops_plain_url_paragraph(self) -> None:
        text = "Real story.\n\nhttps://example.com/other-story\n\nMore story."
        out = strip_link_only_paragraphs(text)
        self.assertIn("Real story", out)
        self.assertIn("More story", out)
        self.assertNotIn("example.com", out)

    def test_drops_markdown_link_paragraph(self) -> None:
        text = "Lead.\n\n[Read more](https://example.com/foo)\n\nTail."
        out = strip_link_only_paragraphs(text)
        self.assertNotIn("Read more", out)
        self.assertIn("Lead", out)
        self.assertIn("Tail", out)

    def test_keeps_url_inside_sentence(self) -> None:
        text = "See https://example.com for details."
        out = strip_link_only_paragraphs(text)
        self.assertIn("example.com", out)


class TestEmphasisFormatting(unittest.TestCase):
    def test_strip_markers_for_tts(self) -> None:
        self.assertEqual(
            strip_emphasis_markers("The **Polish** government"),
            "The Polish government",
        )

    def test_bold_to_html(self) -> None:
        self.assertEqual(
            emphasis_to_telegram_html("The **Polish** government"),
            "The <b>Polish</b> government",
        )

    def test_underscore_and_hash(self) -> None:
        self.assertEqual(
            emphasis_to_telegram_html("__Mesa, Ariz__ and ##breaking##"),
            "<b>Mesa, Ariz</b> and <b>breaking</b>",
        )

    def test_escapes_html_outside_emphasis(self) -> None:
        self.assertEqual(
            emphasis_to_telegram_html("A & B **bold**"),
            "A &amp; B <b>bold</b>",
        )


class TestFormatParagraphs(unittest.TestCase):
    def test_paragraphs_joined(self) -> None:
        out = format_paragraphs_for_telegram("**One.**\n\nTwo.")
        self.assertEqual(out, "<b>One.</b>\n\nTwo.")

    def test_strips_hash_prefix_for_telegram(self) -> None:
        out = format_paragraphs_for_telegram("# The Brief\n\nNormal text.")
        self.assertEqual(out, "The Brief\n\nNormal text.")


class TestDeduplication(unittest.TestCase):
    def test_drops_duplicate_paragraph(self) -> None:
        title = "Suspect electrocuted at Mesa substation"
        body = f"{title}\n\n{title}\n\nMesa police released new details."
        out = deduplicate_article_text(body, title=title)
        self.assertIn("Mesa police", out)
        self.assertLessEqual(out.count("Suspect electrocuted"), 1)

    def test_body_starts_with_title(self) -> None:
        title = "Headline here"
        body = "Headline here. More story."
        self.assertTrue(body_starts_with_title(body, title))


class TestFormattingPrefixes(unittest.TestCase):
    def test_strip_line_prefixes(self) -> None:
        self.assertEqual(
            strip_formatting_prefixes("# The Brief\n- bullet item"),
            "The Brief\nbullet item",
        )

    def test_tts_strips_prefixes(self) -> None:
        self.assertEqual(
            strip_emphasis_markers("# **Mesa**"),
            "Mesa",
        )
