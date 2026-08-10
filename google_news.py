"""Resolve Google News redirect URLs to publisher article URLs."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_GOOGLE_NEWS_ARTICLE_RE = re.compile(
    r"^https?://news\.google\.com/(?:rss/)?(?:articles|read)/",
    re.IGNORECASE,
)


def is_google_news_article_url(url: str) -> bool:
    return bool(_GOOGLE_NEWS_ARTICLE_RE.match(url.strip()))


def resolve_google_news_url(
    url: str,
    *,
    interval: float | None = None,
) -> str | None:
    """
    Decode a Google News article/read URL to the original publisher URL.

    Returns None when the URL is not a Google News article link or decoding fails.
    """
    raw = url.strip()
    if not is_google_news_article_url(raw):
        return raw

    try:
        from googlenewsdecoder import gnewsdecoder
    except ImportError:
        logger.error(
            "googlenewsdecoder is not installed; cannot resolve Google News URLs"
        )
        return None

    kwargs: dict = {}
    if interval is not None:
        kwargs["interval"] = interval

    try:
        result = gnewsdecoder(raw, **kwargs)
    except Exception as exc:
        logger.warning("Google News decode failed for %s: %s", raw[:80], exc)
        return None

    if not result.get("status"):
        logger.debug(
            "Google News decode rejected %s: %s",
            raw[:80],
            result.get("message", "unknown"),
        )
        return None

    decoded = (result.get("decoded_url") or "").strip()
    if not decoded.startswith(("http://", "https://")):
        return None
    return decoded


def publisher_host_from_google_news_source(source_href: str) -> str:
    """Hostname from feedparser entry source href (e.g. https://www.nytimes.com)."""
    return (urlparse(source_href).hostname or "").lower()
