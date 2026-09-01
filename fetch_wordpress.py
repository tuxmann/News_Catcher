"""Fetch article HTML via the public WordPress REST API (bypasses Cloudflare on some sites)."""

from __future__ import annotations

import html as html_module
import json
import logging
import re
from urllib.parse import urlparse

import httpx

from domains_store import host_allowed

logger = logging.getLogger(__name__)

_POST_ID_PREFIX_RE = re.compile(r"^(\d+)-")


def slug_from_article_url(url: str) -> str | None:
    """Last path segment of a typical article URL."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[-1] if parts else None


def post_id_from_article_url(url: str) -> str | None:
    """Numeric WordPress post ID from paths like /media/6059334-article-slug/."""
    slug = slug_from_article_url(url)
    if not slug:
        return None
    match = _POST_ID_PREFIX_RE.match(slug)
    return match.group(1) if match else None


def slug_variants_from_article_url(url: str) -> list[str]:
    """Slug strings to try with ?slug= (most specific first)."""
    slug = slug_from_article_url(url)
    if not slug:
        return []
    variants: list[str] = []
    match = _POST_ID_PREFIX_RE.match(slug)
    if match:
        rest = slug[match.end() :]
        if rest:
            variants.append(rest)
    variants.append(slug)
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _wordpress_api_eligible(hostname: str | None, api_domains: set[str]) -> bool:
    if not hostname or not api_domains:
        return False
    return host_allowed(hostname, api_domains)


def _post_to_html(post: dict, fallback_url: str) -> tuple[bytes, str] | None:
    body = post.get("content", {}).get("rendered", "")
    if not body.strip():
        return None
    title = post.get("title", {}).get("rendered", "")
    canonical = post.get("link") or fallback_url
    safe_title = html_module.escape(title)
    page = (
        f"<!DOCTYPE html><html><head><title>{safe_title}</title></head>"
        f"<body><article>{body}</article></body></html>"
    )
    return page.encode("utf-8"), canonical


async def _get_json(
    client: httpx.AsyncClient, url: str, *, params: dict | None = None
) -> object | None:
    try:
        resp = await client.get(url, params=params, follow_redirects=True)
    except httpx.HTTPError as e:
        logger.warning("WordPress API request failed for %s: %s", url, e)
        return None
    if resp.status_code != 200:
        logger.debug("WordPress API HTTP %s for %s", resp.status_code, url)
        return None
    try:
        return resp.json()
    except json.JSONDecodeError:
        return None


async def wordpress_fetch_html(
    client: httpx.AsyncClient,
    url: str,
    allowed_domains: set[str],
    *,
    api_domains: set[str],
) -> tuple[bytes, str] | None:
    """
    Try wp-json for a post by numeric ID or slug. Returns (html_bytes, canonical_url) or None.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host or not _wordpress_api_eligible(host, api_domains):
        return None
    if not host_allowed(host, allowed_domains):
        return None

    api_base = f"{parsed.scheme}://{host}/wp-json/wp/v2/posts"

    post_id = post_id_from_article_url(url)
    if post_id:
        data = await _get_json(client, f"{api_base}/{post_id}")
        if isinstance(data, dict):
            result = _post_to_html(data, url)
            if result is not None:
                return result

    for slug in slug_variants_from_article_url(url):
        data = await _get_json(client, api_base, params={"slug": slug})
        if not isinstance(data, list) or not data:
            continue
        result = _post_to_html(data[0], url)
        if result is not None:
            return result

    return None
