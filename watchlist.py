"""Discover blog feeds and detect new posts for the watchlist."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

from domains_store import registrable_domain_from_url
from watchlist_store import (
    MAX_POSTS_PER_SITE,
    WatchedPost,
    WatchedSite,
    merge_new_posts,
)

logger = logging.getLogger(__name__)

_FEED_PATHS = (
    "/feed",
    "/feed/",
    "/rss",
    "/rss.xml",
    "/atom.xml",
    "/index.xml",
    "/feeds/posts/default",
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CandidatePost:
    url: str
    title: str
    summary: str


def strip_html_to_text(raw: str) -> str:
    text = unescape(_TAG_RE.sub(" ", raw or ""))
    return _WS_RE.sub(" ", text).strip()


def first_paragraph(text: str, *, max_chars: int = 600) -> str:
    """First non-empty paragraph, trimmed for Telegram notifications."""
    if not text or not text.strip():
        return ""
    parts = [p.strip() for p in re.split(r"\n\s*\n+", text.strip()) if p.strip()]
    if not parts:
        parts = [text.strip()]
    para = _WS_RE.sub(" ", parts[0])
    if len(para) > max_chars:
        para = para[: max_chars - 1].rstrip() + "…"
    return para


def _base_url(domain: str) -> str:
    d = domain.strip().lower().rstrip(".")
    if d.startswith("http://") or d.startswith("https://"):
        parsed = urlparse(d)
        host = parsed.netloc or parsed.path
        return f"https://{host.rstrip('/')}"
    return f"https://{d}"


def _feed_has_entries(parsed: feedparser.FeedParserDict) -> bool:
    return bool(getattr(parsed, "entries", None))


async def discover_feed_url(client: httpx.AsyncClient, domain: str) -> str | None:
    """Try common RSS/Atom paths; return first URL that parses with entries."""
    base = _base_url(domain)
    for path in _FEED_PATHS:
        url = urljoin(base + "/", path.lstrip("/"))
        try:
            resp = await client.get(url, follow_redirects=True, timeout=20.0)
        except httpx.HTTPError as e:
            logger.debug("feed probe failed %s: %s", url, e)
            continue
        if resp.status_code != 200 or not resp.content:
            continue
        ctype = (resp.headers.get("content-type") or "").lower()
        body = resp.text
        if "html" in ctype and "<rss" not in body[:2000].lower() and "<feed" not in body[:2000].lower():
            continue
        parsed = feedparser.parse(resp.content)
        if _feed_has_entries(parsed):
            logger.info("Discovered feed for %s: %s", domain, str(resp.url))
            return str(resp.url)
    return None


async def _fetch_wordpress_posts(
    client: httpx.AsyncClient, domain: str
) -> list[CandidatePost] | None:
    base = _base_url(domain)
    api = f"{base}/wp-json/wp/v2/posts"
    try:
        resp = await client.get(
            api,
            params={"per_page": MAX_POSTS_PER_SITE, "_fields": "link,title,excerpt,content"},
            follow_redirects=True,
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        logger.debug("WordPress API failed for %s: %s", domain, e)
        return None
    if resp.status_code != 200:
        return None
    try:
        posts = resp.json()
    except ValueError:
        return None
    if not isinstance(posts, list) or not posts:
        return None
    out: list[CandidatePost] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        link = str(post.get("link") or "").strip()
        if not link.startswith(("http://", "https://")):
            continue
        title_obj = post.get("title") or {}
        title = strip_html_to_text(
            title_obj.get("rendered", "") if isinstance(title_obj, dict) else str(title_obj)
        )
        excerpt_obj = post.get("excerpt") or {}
        content_obj = post.get("content") or {}
        excerpt = ""
        if isinstance(excerpt_obj, dict):
            excerpt = strip_html_to_text(str(excerpt_obj.get("rendered") or ""))
        if not excerpt and isinstance(content_obj, dict):
            excerpt = first_paragraph(
                strip_html_to_text(str(content_obj.get("rendered") or ""))
            )
        out.append(CandidatePost(url=link, title=title or link, summary=excerpt))
    return out or None


def _candidates_from_feed(parsed: feedparser.FeedParserDict) -> list[CandidatePost]:
    out: list[CandidatePost] = []
    for entry in parsed.entries[:MAX_POSTS_PER_SITE]:
        link = (entry.get("link") or "").strip()
        if not link.startswith(("http://", "https://")):
            continue
        title = strip_html_to_text((entry.get("title") or "").strip()) or link
        summary = strip_html_to_text(
            (entry.get("summary") or entry.get("description") or "").strip()
        )
        summary = first_paragraph(summary)
        out.append(CandidatePost(url=link, title=title, summary=summary))
    return out


async def fetch_recent_posts(
    client: httpx.AsyncClient, site: WatchedSite
) -> tuple[list[CandidatePost], str | None]:
    """
    Fetch newest posts for a site. Returns (candidates newest-first, feed_url used).
    """
    feed_url = site.feed_url
    if feed_url:
        try:
            resp = await client.get(feed_url, follow_redirects=True, timeout=20.0)
            if resp.status_code == 200 and resp.content:
                parsed = feedparser.parse(resp.content)
                if _feed_has_entries(parsed):
                    return _candidates_from_feed(parsed), str(resp.url)
        except httpx.HTTPError as e:
            logger.warning("Cached feed failed for %s (%s): %s", site.domain, feed_url, e)

    discovered = await discover_feed_url(client, site.domain)
    if discovered:
        try:
            resp = await client.get(discovered, follow_redirects=True, timeout=20.0)
            if resp.status_code == 200 and resp.content:
                parsed = feedparser.parse(resp.content)
                if _feed_has_entries(parsed):
                    return _candidates_from_feed(parsed), str(resp.url)
        except httpx.HTTPError as e:
            logger.warning("Discovered feed fetch failed for %s: %s", site.domain, e)

    wp_posts = await _fetch_wordpress_posts(client, site.domain)
    if wp_posts:
        return wp_posts, None

    return [], feed_url


@dataclass
class WatchCheckResult:
    site: WatchedSite
    new_posts: list[CandidatePost]
    error: str | None = None


async def check_site(client: httpx.AsyncClient, site: WatchedSite) -> WatchCheckResult:
    """Poll one site, update seen posts, return newly discovered posts (oldest first for notify)."""
    updated = WatchedSite(
        domain=site.domain,
        check_interval_minutes=site.check_interval_minutes,
        feed_url=site.feed_url,
        last_checked_at=site.last_checked_at,
        posts=list(site.posts),
    )
    try:
        candidates, feed_url = await fetch_recent_posts(client, updated)
    except Exception as e:
        logger.exception("watchlist check failed for %s", site.domain)
        updated.last_checked_at = time.time()
        return WatchCheckResult(site=updated, new_posts=[], error=str(e))

    if feed_url:
        updated.feed_url = feed_url
    updated.last_checked_at = time.time()

    if not candidates:
        return WatchCheckResult(
            site=updated,
            new_posts=[],
            error="No RSS/Atom feed or WordPress API posts found",
        )

    # First successful check: seed history without notifying.
    if not updated.posts:
        now = time.time()
        updated.posts = [
            WatchedPost(url=c.url, title=c.title, seen_at=now) for c in candidates
        ][:MAX_POSTS_PER_SITE]
        return WatchCheckResult(site=updated, new_posts=[])

    known = updated.known_urls()
    brand_new = [c for c in candidates if c.url not in known]
    if not brand_new:
        # Refresh titles for known URLs still in the top window (optional no-op).
        return WatchCheckResult(site=updated, new_posts=[])

    # merge_new_posts expects WatchedPost; keep candidate order (feed newest-first).
    added = merge_new_posts(
        updated,
        [
            WatchedPost(url=c.url, title=c.title, seen_at=time.time())
            for c in brand_new
        ],
    )
    added_urls = {p.url for p in added}
    # Notify oldest-first so the conversation reads chronologically.
    notify = [c for c in reversed(brand_new) if c.url in added_urls]
    return WatchCheckResult(site=updated, new_posts=notify)


def domain_hint_from_user_input(raw: str) -> str | None:
    """Normalize user input (domain or URL) to a registrable domain when possible."""
    text = raw.strip()
    if not text:
        return None
    if "://" not in text and "/" not in text:
        return text.lower().rstrip(".")
    if "://" not in text:
        text = "https://" + text
    return registrable_domain_from_url(text) or urlparse(text).hostname
