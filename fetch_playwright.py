"""Headless Chromium fetch for sites that return HTTP 403 to plain HTTP (e.g. Cloudflare)."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from domains_store import host_allowed

logger = logging.getLogger(__name__)

_playwright = None
_browser = None
_lock = asyncio.Lock()


async def close_playwright() -> None:
    global _playwright, _browser
    async with _lock:
        if _browser is not None:
            try:
                await _browser.close()
            except Exception as e:
                logger.debug("browser close: %s", e)
            _browser = None
        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception as e:
                logger.debug("playwright stop: %s", e)
            _playwright = None


async def _get_browser():
    global _playwright, _browser
    async with _lock:
        if _browser is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as e:
                raise RuntimeError(
                    "playwright is not installed. Run: pip install playwright && playwright install chromium"
                ) from e
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
        return _browser


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


async def playwright_fetch_html(
    url: str,
    allowed_domains: set[str],
    *,
    allow_http: bool,
    user_agent: str,
    timeout_ms: int,
) -> tuple[bytes, str, str | None]:
    """
    Load URL in Chromium; wait for Cloudflare interstitial to clear.
    Returns (html_bytes, final_url, content_type). Caller enforces byte limits.
    """
    browser = await _get_browser()
    context = await browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1280, "height": 720},
        locale="en-US",
    )
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="load", timeout=timeout_ms)
        await page.wait_for_function(
            "() => document.title && !document.title.toLowerCase().includes('just a moment')",
            timeout=min(timeout_ms, 45_000),
        )
        final_url = page.url
        if not _final_url_allowed(final_url, allowed_domains, allow_http):
            raise RuntimeError("Page navigated to a host that is not on the approved list.")
        html = await page.content()
        raw = html.encode("utf-8")
        return raw, final_url, "text/html; charset=utf-8"
    finally:
        await context.close()
