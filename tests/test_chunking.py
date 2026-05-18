"""Tests for Telegram article chunk formatting."""

from __future__ import annotations

import re
import unittest

import News_bot as bot


class TestFormatArticleChunks(unittest.TestCase):
    def test_prefix_on_every_chunk(self) -> None:
        text = "A.\n\nB.\n\nC."
        msgs = bot._format_article_chunks(text, 500)
        self.assertEqual(len(msgs), 1)
        self.assertTrue(re.match(r"^1 of 1\n\n", msgs[0]), msgs[0][:20])

    def test_multi_chunk_numbering(self) -> None:
        p = "Paragraph one. " * 30
        text = f"{p}\n\n{p}\n\n{p}"
        msgs = bot._format_article_chunks(text, 400)
        self.assertGreaterEqual(len(msgs), 2)
        for i, m in enumerate(msgs):
            self.assertTrue(
                m.startswith(f"{i + 1} of {len(msgs)}\n\n"),
                m[:25],
            )

    def test_respects_telegram_max_length(self) -> None:
        text = ("word " * 8000).strip()
        msgs = bot._format_article_chunks(text, 4000)
        for m in msgs:
            self.assertLessEqual(len(m), 4096, f"length {len(m)}")

    def test_no_word_kuwait_split_across_messages(self) -> None:
        text = "Intro.\n\n" + ("x " * 100) + "Kuwait matters here. " + ("y " * 100)
        msgs = bot._format_article_chunks(text, 280)
        bodies = [m.split("\n\n", 1)[1] for m in msgs]
        for a, b in zip(bodies, bodies[1:]):
            tail = a.rstrip()[-1:]
            head = b.lstrip()[:5]
            self.assertFalse(tail == "K" and head.startswith("uwait"), (a[-30:], b[:30]))


if __name__ == "__main__":
    unittest.main()
