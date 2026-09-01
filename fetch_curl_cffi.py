"""TLS/browser impersonation fetch for Cloudflare-protected sites (via curl_cffi)."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from domains_store import host_allowed

logger = logging.getLogger(__name__)

from fetch_challenge import looks_like_challenge_page


def _final_url_allowed(final_url: str, allowed_domains: set[str], allow_http: bool) -> bool:
    p = urlparse(final_url)
    if p.scheme == "https":
        pass
    elif p.scheme == "http" and allow_http:
        pass
    else:
        return False
    h = p.hostname
    return bool(h and host_allowed(h, allowed_domains))


async def curl_cffi_fetch_html(
    url: str,
    allowed_domains: set[str],
    *,
    allow_http: bool,
    impersonate: str,
    timeout_seconds: float,
) -> tuple[bytes, str, str | None] | None:
    """
    GET with Chrome TLS fingerprint. Returns (html_bytes, final_url, content_type) or None.
    """
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        logger.debug("curl_cffi not installed")
        return None

    def _sync_fetch() -> tuple[bytes, str, str | None] | None:
        try:
            resp = curl_requests.get(
                url,
                impersonate=impersonate,
                timeout=timeout_seconds,
                allow_redirects=True,
            )
        except Exception as e:
            logger.warning("curl_cffi request failed for %s: %s", url, e)
            return None

        if resp.status_code < 200 or resp.status_code >= 400:
            logger.warning("curl_cffi HTTP %s for %s", resp.status_code, url)
            return None

        final_url = str(resp.url)
        if not _final_url_allowed(final_url, allowed_domains, allow_http):
            logger.warning("curl_cffi redirected off allowlist: %s", final_url)
            return None

        text = resp.text
        title = None
        if "<title" in text.lower():
            start = text.lower().find("<title")
            end = text.lower().find("</title>", start)
            if end > start:
                title = text[start:end]

        if looks_like_challenge_page(text, title):
            logger.warning("curl_cffi still got challenge page for %s", url)
            return None

        ct = resp.headers.get("content-type") or "text/html; charset=utf-8"
        return resp.content, final_url, ct

    return await asyncio.to_thread(_sync_fetch)
