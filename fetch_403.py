"""Orchestrate anti-bot bypass strategies (WordPress API, curl_cffi, headless browser)."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

import config
from domains_store import host_allowed
from fallback_domains_store import record_fallback_domain
from fetch import FetchError, FetchOk, FetchOversizeKnown
from fetch_wordpress import wordpress_fetch_html

logger = logging.getLogger(__name__)


def registrable_domain(hostname: str | None) -> str | None:
    if not hostname:
        return None
    host = hostname.strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _auto_fallbacks_enabled() -> bool:
    return config.AUTO_403_FALLBACKS


def _recorded_domains() -> set[str]:
    if not config.USE_RECORDED_403_FALLBACKS:
        return set()
    from fallback_domains_store import load_recorded_fallback_domains

    return load_recorded_fallback_domains()


def _should_try_strategy(hostname: str | None, *, explicit_domains: set[str]) -> bool:
    if not hostname:
        return False
    if _auto_fallbacks_enabled():
        return True
    domain = registrable_domain(hostname)
    if domain and domain in _recorded_domains():
        return True
    return host_allowed(hostname, explicit_domains)


def _wordpress_eligible(hostname: str | None, allowed_domains: set[str]) -> bool:
    if not hostname:
        return False
    if config.WORDPRESS_API_DOMAINS and host_allowed(
        hostname, set(config.WORDPRESS_API_DOMAINS)
    ):
        return True
    if config.WORDPRESS_API_TRY_ALL and host_allowed(hostname, allowed_domains):
        return True
    return False


def _wordpress_api_domains(allowed_domains: set[str]) -> set[str]:
    """Hosts eligible for a wp-json lookup on this request."""
    if config.WORDPRESS_API_TRY_ALL:
        return allowed_domains
    return set(config.WORDPRESS_API_DOMAINS)


def _curl_cffi_eligible(hostname: str | None) -> bool:
    if not config.CURL_CFFI_ON_403:
        return False
    return _should_try_strategy(hostname, explicit_domains=set())


def _browser_eligible(hostname: str | None) -> bool:
    if not config.BROWSER_ON_403:
        return False
    return _should_try_strategy(
        hostname, explicit_domains=set(config.PLAYWRIGHT_FALLBACK_DOMAINS)
    )


def _ok_from_bytes(
    raw: bytes, final_url: str, content_type: str | None, byte_limit: int
) -> FetchOk | FetchOversizeKnown:
    n = len(raw)
    if n > byte_limit:
        return FetchOversizeKnown(
            final_url=final_url,
            content_length=n,
            soft_limit=byte_limit,
        )
    return FetchOk(content=raw, final_url=final_url, content_type=content_type)


async def try_antibot_fallbacks(
    client: httpx.AsyncClient,
    url: str,
    byte_limit: int,
    allowed_domains: set[str],
    *,
    allow_http: bool,
    user_agent: str,
    http_status: int = 403,
) -> FetchOk | FetchOversizeKnown:
    """
    Run bypass strategies in order. Raises FetchError if all fail.
    Records the domain when any strategy is attempted.
    """
    hostname = urlparse(url).hostname
    domain = registrable_domain(hostname)
    tried: list[str] = []

    if domain and (_auto_fallbacks_enabled() or domain in _recorded_domains()):
        record_fallback_domain(domain)

    if _wordpress_eligible(hostname, allowed_domains):
        tried.append("WordPress API")
        result = await wordpress_fetch_html(
            client,
            url,
            allowed_domains,
            api_domains=_wordpress_api_domains(allowed_domains),
        )
        if result is not None:
            raw, final_url = result
            logger.info("HTTP %s bypass via WordPress API: %s", http_status, url)
            return _ok_from_bytes(raw, final_url, "text/html; charset=utf-8", byte_limit)

    if _curl_cffi_eligible(hostname):
        tried.append("TLS impersonation (curl_cffi)")
        from fetch_curl_cffi import curl_cffi_fetch_html

        result = await curl_cffi_fetch_html(
            url,
            allowed_domains,
            allow_http=allow_http,
            impersonate=config.CURL_CFFI_IMPERSONATE,
            timeout_seconds=float(config.CURL_CFFI_TIMEOUT_SECONDS),
        )
        if result is not None:
            raw, final_url, ct = result
            logger.info("HTTP %s bypass via curl_cffi: %s", http_status, url)
            return _ok_from_bytes(raw, final_url, ct, byte_limit)

    if _browser_eligible(hostname):
        tried.append("headless browser (patchright)")
        try:
            from fetch_playwright import playwright_fetch_html
        except ImportError as e:
            raise FetchError(
                f"HTTP {http_status} — site blocked plain HTTP. Install bypass tools:\n"
                "pip install curl_cffi patchright && patchright install chromium",
                blocked_domain=domain,
                blocked_url=url,
                tried_strategies=tried,
            ) from e
        try:
            raw, final_url, ct = await playwright_fetch_html(
                url,
                allowed_domains,
                allow_http=allow_http,
                user_agent=user_agent,
                timeout_ms=config.PLAYWRIGHT_TIMEOUT_MS,
            )
            logger.info("HTTP %s bypass via browser: %s", http_status, url)
            return _ok_from_bytes(raw, final_url, ct, byte_limit)
        except RuntimeError as e:
            raise FetchError(
                str(e),
                blocked_domain=domain,
                blocked_url=url,
                tried_strategies=tried,
            ) from e
        except Exception as e:
            from fetch_playwright import close_playwright

            await close_playwright()
            tried.append(f"browser failed ({e})")
            raise FetchError(
                f"Browser fetch failed: {e}",
                blocked_domain=domain,
                blocked_url=url,
                tried_strategies=tried,
            ) from e

    raise FetchError(
        f"HTTP {http_status}",
        blocked_domain=domain,
        blocked_url=url,
        tried_strategies=tried or None,
    )


async def try_403_fallbacks(
    client: httpx.AsyncClient,
    url: str,
    byte_limit: int,
    allowed_domains: set[str],
    *,
    allow_http: bool,
    user_agent: str,
) -> FetchOk | FetchOversizeKnown:
    """Backward-compatible alias."""
    return await try_antibot_fallbacks(
        client,
        url,
        byte_limit,
        allowed_domains,
        allow_http=allow_http,
        user_agent=user_agent,
        http_status=403,
    )
