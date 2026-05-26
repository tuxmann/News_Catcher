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

_PAGE_READY_JS = """
() => {
  const t = (document.title || "").toLowerCase();
  if (!document.title) return false;
  if (t.includes("just a moment")) return false;
  if (t.includes("403") || t.includes("forbidden")) return false;
  if (t.includes("access denied")) return false;
  return true;
}
"""


async def _async_playwright_module():
    """Prefer patchright (stealth-patched Playwright); fall back to stock playwright."""
    try:
        from patchright.async_api import async_playwright

        return async_playwright
    except ImportError:
        from playwright.async_api import async_playwright

        return async_playwright


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
            async_playwright = await _async_playwright_module()
            try:
                _playwright = await async_playwright().start()
            except Exception as e:
                raise RuntimeError(
                    "Browser automation is not installed. Run: pip install patchright && "
                    "patchright install chromium"
                ) from e
            launch_kwargs: dict = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            import config

            channel = config.PLAYWRIGHT_CHANNEL
            if channel:
                launch_kwargs["channel"] = channel
            try:
                _browser = await _playwright.chromium.launch(**launch_kwargs)
            except Exception as e:
                if channel:
                    logger.warning(
                        "Chromium channel %r unavailable (%s); using bundled browser",
                        channel,
                        e,
                    )
                    _browser = await _playwright.chromium.launch(
                        headless=True,
                        args=launch_kwargs["args"],
                    )
                else:
                    raise
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

    user_agent is accepted for API compatibility but intentionally not applied:
    overriding the browser UA often mismatches TLS/CDP fingerprints and triggers
    blocks (e.g. marktechpost.com with a desktop Chrome 131 string).
    """
    del user_agent
    browser = await _get_browser()
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        locale="en-US",
    )
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="load", timeout=timeout_ms)
        await page.wait_for_function(
            _PAGE_READY_JS,
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
