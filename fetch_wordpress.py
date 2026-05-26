"""Fetch article HTML via the public WordPress REST API (bypasses Cloudflare on some sites)."""

from __future__ import annotations

import html as html_module
import json
import logging
from urllib.parse import urlparse

import httpx

from domains_store import host_allowed

logger = logging.getLogger(__name__)


def slug_from_article_url(url: str) -> str | None:
    """Last path segment of a typical /YYYY/MM/DD/slug/ article URL."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[-1] if parts else None


def _wordpress_api_eligible(hostname: str | None, api_domains: set[str]) -> bool:
    if not hostname or not api_domains:
        return False
    return host_allowed(hostname, api_domains)


async def wordpress_fetch_html(
    client: httpx.AsyncClient,
    url: str,
    allowed_domains: set[str],
    *,
    api_domains: set[str],
) -> tuple[bytes, str] | None:
    """
    Try wp-json for a post by slug. Returns (html_bytes, canonical_url) or None.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host or not _wordpress_api_eligible(host, api_domains):
        return None
    if not host_allowed(host, allowed_domains):
        return None

    slug = slug_from_article_url(url)
    if not slug:
        return None

    api_url = f"{parsed.scheme}://{host}/wp-json/wp/v2/posts"
    try:
        resp = await client.get(api_url, params={"slug": slug}, follow_redirects=True)
    except httpx.HTTPError as e:
        logger.warning("WordPress API request failed for %s: %s", url, e)
        return None

    if resp.status_code != 200:
        logger.warning("WordPress API HTTP %s for %s", resp.status_code, url)
        return None

    try:
        posts = resp.json()
    except json.JSONDecodeError:
        return None
    if not posts:
        return None

    post = posts[0]
    title = post.get("title", {}).get("rendered", "")
    body = post.get("content", {}).get("rendered", "")
    if not body.strip():
        return None

    canonical = post.get("link") or url
    safe_title = html_module.escape(title)
    page = (
        f"<!DOCTYPE html><html><head><title>{safe_title}</title></head>"
        f"<body><article>{body}</article></body></html>"
    )
    return page.encode("utf-8"), canonical
