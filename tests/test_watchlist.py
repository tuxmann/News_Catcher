"""Tests for blog watchlist store and helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watchlist import domain_hint_from_user_input, first_paragraph, strip_html_to_text
from watchlist_store import (
    WatchedPost,
    WatchedSite,
    load_watchlist,
    merge_new_posts,
    remove_site,
    site_is_due,
    upsert_site,
)


class TestWatchlistStore(unittest.TestCase):
    def test_upsert_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlist.json"
            site = WatchedSite(domain="example.com", check_interval_minutes=30)
            upsert_site(path, site)
            loaded = load_watchlist(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].check_interval_minutes, 30)
            self.assertTrue(remove_site(path, "example.com"))
            self.assertEqual(load_watchlist(path), [])

    def test_merge_caps_at_20(self) -> None:
        site = WatchedSite(
            domain="blog.test",
            posts=[WatchedPost(url=f"https://blog.test/{i}", title=str(i), seen_at=i) for i in range(18)],
        )
        added = merge_new_posts(
            site,
            [
                WatchedPost(url="https://blog.test/new1", title="n1", seen_at=100),
                WatchedPost(url="https://blog.test/new2", title="n2", seen_at=101),
                WatchedPost(url="https://blog.test/new3", title="n3", seen_at=102),
            ],
        )
        self.assertEqual(len(added), 3)
        self.assertEqual(len(site.posts), 20)
        self.assertEqual(site.posts[0].url, "https://blog.test/new1")

    def test_site_is_due(self) -> None:
        site = WatchedSite(domain="x.com", check_interval_minutes=60, last_checked_at=1000)
        self.assertFalse(site_is_due(site, now=1000 + 30 * 60))
        self.assertTrue(site_is_due(site, now=1000 + 61 * 60))


class TestWatchlistHelpers(unittest.TestCase):
    def test_first_paragraph(self) -> None:
        text = "First paragraph here.\n\nSecond one."
        self.assertEqual(first_paragraph(text), "First paragraph here.")

    def test_strip_html(self) -> None:
        self.assertEqual(strip_html_to_text("<p>Hello <b>world</b></p>"), "Hello world")

    def test_domain_hint(self) -> None:
        self.assertEqual(domain_hint_from_user_input("Example.COM"), "example.com")
        self.assertEqual(
            domain_hint_from_user_input("https://www.example.com/posts"),
            "example.com",
        )


if __name__ == "__main__":
    unittest.main()
