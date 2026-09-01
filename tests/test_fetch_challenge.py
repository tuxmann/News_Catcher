"""Tests for anti-bot challenge page detection."""

from __future__ import annotations

import unittest

from fetch_challenge import looks_like_challenge_page


class TestLooksLikeChallengePage(unittest.TestCase):
    def test_reuters_datadome_stub(self) -> None:
        html = (
            '<html><head><title>reuters.com</title></head><body>'
            '<p id="cmsg">Please enable JavaScript</p></body></html>'
        )
        self.assertTrue(looks_like_challenge_page(html))

    def test_real_article_not_challenge(self) -> None:
        html = (
            "<html><head><title>Markets rally on jobs data</title></head>"
            "<body><article>" + ("word " * 200) + "</article></body></html>"
        )
        self.assertFalse(looks_like_challenge_page(html))
