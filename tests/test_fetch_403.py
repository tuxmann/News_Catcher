"""Tests for HTTP 403 fallback orchestration."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
from fetch_403 import (
    _browser_eligible,
    _curl_cffi_eligible,
    registrable_domain,
    try_403_fallbacks,
    try_antibot_fallbacks,
)
from fetch import FetchError


class TestRegistrableDomain(unittest.TestCase):
    def test_strips_www(self) -> None:
        self.assertEqual(registrable_domain("www.politico.com"), "politico.com")


class TestFallbackEligibility(unittest.TestCase):
    def test_auto_enables_curl_for_any_host(self) -> None:
        with patch.object(config, "AUTO_403_FALLBACKS", True):
            with patch.object(config, "CURL_CFFI_ON_403", True):
                self.assertTrue(_curl_cffi_eligible("www.politico.com"))

    def test_explicit_only_when_auto_off(self) -> None:
        with patch.object(config, "AUTO_403_FALLBACKS", False):
            with patch.object(config, "CURL_CFFI_ON_403", True):
                with patch.object(config, "PLAYWRIGHT_FALLBACK_DOMAINS", frozenset()):
                    with patch(
                        "fetch_403._recorded_domains",
                        return_value=set(),
                    ):
                        self.assertFalse(_curl_cffi_eligible("www.politico.com"))
                with patch.object(
                    config,
                    "PLAYWRIGHT_FALLBACK_DOMAINS",
                    frozenset({"politico.com"}),
                ):
                    self.assertTrue(_browser_eligible("www.politico.com"))


class TestTry403Fallbacks(unittest.IsolatedAsyncioTestCase):
    async def test_curl_cffi_success(self) -> None:
        html = b"<html><head><title>Story</title></head><body><p>Text</p></body></html>"
        with patch.object(config, "AUTO_403_FALLBACKS", True):
            with patch.object(config, "CURL_CFFI_ON_403", True):
                with patch.object(config, "BROWSER_ON_403", False):
                    with patch.object(config, "WORDPRESS_API_DOMAINS", frozenset()):
                        with patch(
                            "fetch_curl_cffi.curl_cffi_fetch_html",
                            new_callable=AsyncMock,
                            return_value=(html, "https://www.politico.com/a", "text/html"),
                        ):
                            client = AsyncMock()
                            result = await try_403_fallbacks(
                                client,
                                "https://www.politico.com/news/a",
                                1_000_000,
                                {"politico.com"},
                                allow_http=False,
                                user_agent="test",
                            )
        self.assertEqual(result.content, html)

    async def test_lemonde_402_status_passed_through(self) -> None:
        html = b"<html><head><title>Story</title></head><body><p>Text</p></body></html>"
        with patch.object(config, "AUTO_403_FALLBACKS", True):
            with patch.object(config, "CURL_CFFI_ON_403", True):
                with patch.object(config, "BROWSER_ON_403", False):
                    with patch.object(config, "WORDPRESS_API_DOMAINS", frozenset()):
                        with patch(
                            "fetch_curl_cffi.curl_cffi_fetch_html",
                            new_callable=AsyncMock,
                            return_value=(html, "https://www.lemonde.fr/a", "text/html"),
                        ):
                            client = AsyncMock()
                            result = await try_403_fallbacks(
                                client,
                                "https://www.lemonde.fr/en/article/a",
                                1_000_000,
                                {"lemonde.fr"},
                                allow_http=False,
                                user_agent="test",
                            )
        self.assertEqual(result.content, html)

    async def test_401_triggers_curl_cffi_attempt(self) -> None:
        with patch.object(config, "ANTIBOT_FALLBACK_STATUSES", frozenset({401})):
            with patch.object(config, "AUTO_403_FALLBACKS", True):
                with patch.object(config, "CURL_CFFI_ON_403", True):
                    with patch.object(config, "BROWSER_ON_403", False):
                        with patch.object(config, "WORDPRESS_API_TRY_ALL", False):
                            with patch.object(config, "WORDPRESS_API_DOMAINS", frozenset()):
                                with patch(
                                    "fetch_curl_cffi.curl_cffi_fetch_html",
                                    new_callable=AsyncMock,
                                    return_value=None,
                                ) as mock_curl:
                                    client = AsyncMock()
                                    with self.assertRaises(FetchError) as ctx:
                                        await try_antibot_fallbacks(
                                            client,
                                            "https://www.reuters.com/world/a",
                                            1_000_000,
                                            {"reuters.com"},
                                            allow_http=False,
                                            user_agent="test",
                                            http_status=401,
                                        )
                                    mock_curl.assert_called_once()
                                    self.assertEqual(
                                        ctx.exception.blocked_domain, "reuters.com"
                                    )

    async def test_raises_when_all_fail(self) -> None:
        with patch.object(config, "AUTO_403_FALLBACKS", True):
            with patch.object(config, "CURL_CFFI_ON_403", True):
                with patch.object(config, "BROWSER_ON_403", False):
                    with patch.object(config, "WORDPRESS_API_DOMAINS", frozenset()):
                        with patch(
                            "fetch_curl_cffi.curl_cffi_fetch_html",
                            new_callable=AsyncMock,
                            return_value=None,
                        ):
                            client = AsyncMock()
                            with self.assertRaises(FetchError) as ctx:
                                await try_403_fallbacks(
                                    client,
                                    "https://www.politico.com/news/a",
                                    1_000_000,
                                    {"politico.com"},
                                    allow_http=False,
                                    user_agent="test",
                                )
        self.assertEqual(ctx.exception.blocked_domain, "politico.com")
