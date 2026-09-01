"""Tests for blog watchlist store and helpers."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from watchlist import (
    CandidatePost,
    domain_hint_from_user_input,
    first_paragraph,
    format_site_digest,
    strip_html_to_text,
)
from watchlist_store import (
    WatchedPost,
    WatchedSite,
    current_slot_start,
    is_known_post,
    load_watchlist,
    merge_new_posts,
    normalize_check_interval,
    normalize_post_title,
    normalize_post_url,
    refresh_recent_posts,
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

    def test_dedupe_by_title_and_normalized_url(self) -> None:
        site = WatchedSite(
            domain="blog.test",
            posts=[
                WatchedPost(
                    url="https://blog.test/post-a/",
                    title="Hello World",
                    seen_at=1,
                )
            ],
        )
        self.assertTrue(is_known_post(site, "https://www.blog.test/post-a", "Other title"))
        self.assertTrue(is_known_post(site, "https://blog.test/different", "Hello  World"))
        self.assertFalse(is_known_post(site, "https://blog.test/new", "Brand New"))
        added = merge_new_posts(
            site,
            [
                WatchedPost(url="https://blog.test/post-a", title="Hello World", seen_at=2),
                WatchedPost(url="https://blog.test/x", title="hello world", seen_at=3),
                WatchedPost(url="https://blog.test/new", title="Brand New", seen_at=4),
            ],
        )
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].title, "Brand New")

    def test_refresh_keeps_twenty_recent_titles(self) -> None:
        site = WatchedSite(
            domain="blog.test",
            posts=[
                WatchedPost(url=f"https://blog.test/old-{i}", title=f"Old {i}", seen_at=i)
                for i in range(20)
            ],
        )
        recent = [
            WatchedPost(url=f"https://blog.test/new-{i}", title=f"New {i}", seen_at=100 + i)
            for i in range(15)
        ]
        refresh_recent_posts(site, recent)
        self.assertEqual(len(site.posts), 20)
        self.assertEqual(site.posts[0].title, "New 0")
        # Remaining slots filled from previously remembered titles.
        self.assertTrue(any(p.title.startswith("Old ") for p in site.posts))

    def test_normalize_helpers(self) -> None:
        self.assertEqual(
            normalize_post_url("https://WWW.Example.com/path/?utm_source=x"),
            "https://example.com/path",
        )
        self.assertEqual(normalize_post_title("  Hello   World "), "hello world")

    def test_normalize_interval(self) -> None:
        self.assertEqual(normalize_check_interval(15), 15)
        self.assertEqual(normalize_check_interval(30), 30)
        self.assertEqual(normalize_check_interval(10), 15)
        self.assertEqual(normalize_check_interval(45), 60)
        self.assertEqual(normalize_check_interval(75), 60)
        self.assertEqual(normalize_check_interval(120), 120)

    def test_slot_start_hourly(self) -> None:
        now = datetime(2026, 8, 12, 10, 37, 12)
        self.assertEqual(
            current_slot_start(60, now=now),
            datetime(2026, 8, 12, 10, 0, 0),
        )
        self.assertEqual(
            current_slot_start(120, now=datetime(2026, 8, 12, 11, 5)),
            datetime(2026, 8, 12, 10, 0, 0),
        )

    def test_slot_start_subhour(self) -> None:
        now = datetime(2026, 8, 12, 10, 37, 12)
        self.assertEqual(
            current_slot_start(15, now=now),
            datetime(2026, 8, 12, 10, 30, 0),
        )
        self.assertEqual(
            current_slot_start(30, now=now),
            datetime(2026, 8, 12, 10, 30, 0),
        )

    def test_site_is_due_clock_aligned(self) -> None:
        slot = datetime(2026, 8, 12, 10, 0, 0)
        site = WatchedSite(
            domain="x.com",
            check_interval_minutes=60,
            last_checked_at=slot.timestamp() - 1,
        )
        self.assertTrue(site_is_due(site, now=datetime(2026, 8, 12, 10, 0, 30)))
        site.last_checked_at = datetime(2026, 8, 12, 10, 0, 5).timestamp()
        self.assertFalse(site_is_due(site, now=datetime(2026, 8, 12, 10, 30, 0)))
        self.assertTrue(site_is_due(site, now=datetime(2026, 8, 12, 11, 0, 10)))


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

    def test_format_site_digest(self) -> None:
        posts = [
            CandidatePost(url="https://b.test/1", title="Alpha", summary="First para."),
            CandidatePost(url="https://b.test/2", title="Beta", summary="Second para."),
        ]
        text = format_site_digest("b.test", posts)
        self.assertIn("<b>b.test</b>", text)
        self.assertIn("2 new posts", text)
        self.assertIn("<b>1. Alpha</b>", text)
        self.assertIn("First para.", text)
        self.assertIn("<b>2. Beta</b>", text)


if __name__ == "__main__":
    unittest.main()
