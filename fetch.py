"""HTTP fetch with domain allowlist, redirect checks, and byte limits."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin

import httpx

import config
from domains_store import host_allowed


class FetchError(Exception):
    """User-facing fetch failure."""

    def __init__(self, message: str, *, rejected_url: str | None = None) -> None:
        super().__init__(message)
        self.rejected_url = rejected_url


@dataclass
class FetchOk:
    content: bytes
    final_url: str
    content_type: str | None


@dataclass
class FetchOversizeKnown:
    """Content-Length (or total declared) exceeds the current byte limit."""

    final_url: str
    content_length: int
    soft_limit: int


@dataclass
class FetchOversizeUnknown:
    """Body grew past the byte limit before EOF (chunked or wrong CL)."""

    final_url: str
    bytes_read: int
    soft_limit: int


FetchResult = FetchOk | FetchOversizeKnown | FetchOversizeUnknown


def _browser_headers(user_agent: str, referer: str | None, target_url: str) -> dict[str, str]:
    """
    Headers typical of a browser navigation. Some publishers (e.g. Reuters via DataDome)
    return 401 without Accept / Accept-Language / Sec-Fetch-*.
    """
    if referer is None:
        sec_fetch_site = "none"
    else:
        r_host = urlparse(referer).hostname
        t_host = urlparse(target_url).hostname
        if r_host and t_host and r_host.lower() == t_host.lower():
            sec_fetch_site = "same-origin"
        else:
            sec_fetch_site = "cross-site"
    headers: dict[str, str] = {
        "User-Agent": user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": sec_fetch_site,
        "Sec-Fetch-User": "?1",
    }
    if referer is not None:
        headers["Referer"] = referer
    return headers


def _scheme_ok(scheme: str, allow_http: bool) -> bool:
    s = scheme.lower()
    if s == "https":
        return True
    if allow_http and s == "http":
        return True
    return False


def _hostname_blocked(hostname: str) -> bool:
    """Block obvious local/metadata hosts by name (defense in depth)."""
    h = hostname.lower().rstrip(".")
    if h in ("localhost", "metadata.google.internal"):
        return True
    if h.endswith(".local") or h.endswith(".localhost"):
        return True
    return False


def _resolve_all_ips(hostname: str) -> list[str]:
    infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    return ips


def _ip_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved:
        return True
    if ip.version == 4:
        if ip in ipaddress.ip_network("169.254.0.0/16"):
            return True
        if ip in ipaddress.ip_network("0.0.0.0/8"):
            return True
    if ip.version == 6:
        if ip in ipaddress.ip_network("fe80::/10"):
            return True
        if ip in ipaddress.ip_network("fc00::/7"):
            return True
        if ip in ipaddress.ip_network("::ffff:0:0/96"):
            mapped = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
            return _ip_blocked(str(mapped))
    return False


def _playwright_eligible(hostname: str | None) -> bool:
    if not hostname or not config.PLAYWRIGHT_FALLBACK_DOMAINS:
        return False
    return host_allowed(hostname, set(config.PLAYWRIGHT_FALLBACK_DOMAINS))


def _wordpress_api_eligible(hostname: str | None) -> bool:
    if not hostname or not config.WORDPRESS_API_DOMAINS:
        return False
    return host_allowed(hostname, set(config.WORDPRESS_API_DOMAINS))


async def _try_wordpress_api_fetch(
    client: httpx.AsyncClient,
    url: str,
    byte_limit: int,
    allowed_domains: set[str],
) -> FetchOk | FetchOversizeKnown | None:
    from fetch_wordpress import wordpress_fetch_html

    result = await wordpress_fetch_html(
        client,
        url,
        allowed_domains,
        api_domains=set(config.WORDPRESS_API_DOMAINS),
    )
    if result is None:
        return None
    raw, final_url = result
    n = len(raw)
    if n > byte_limit:
        return FetchOversizeKnown(
            final_url=final_url,
            content_length=n,
            soft_limit=byte_limit,
        )
    return FetchOk(
        content=raw,
        final_url=final_url,
        content_type="text/html; charset=utf-8",
    )


def validate_target_url(url: str, allowed_domains: set[str], allow_http: bool) -> None:
    parsed = urlparse(url)
    if not parsed.hostname:
        raise FetchError("URL has no host.")
    if not _scheme_ok(parsed.scheme, allow_http):
        raise FetchError("Only HTTPS URLs are allowed (or enable ALLOW_HTTP=1).")
    host = parsed.hostname
    if _hostname_blocked(host):
        raise FetchError("This host is not allowed.")
    if not host_allowed(host, allowed_domains):
        raise FetchError("Domain is not on the approved list.", rejected_url=url)
    try:
        ips = _resolve_all_ips(host)
    except OSError as e:
        raise FetchError(f"Could not resolve host: {e}") from e
    if not ips:
        raise FetchError("Host resolved to no addresses.")
    for ip in ips:
        if _ip_blocked(ip):
            raise FetchError("Resolved address is not allowed.")


async def fetch_url(
    client: httpx.AsyncClient,
    start_url: str,
    byte_limit: int,
    allowed_domains: set[str],
    *,
    allow_http: bool,
    max_redirects: int,
    user_agent: str,
) -> FetchResult:
    """
    Follow redirects manually; re-validate host and DNS after each hop.
    Enforce byte_limit on the final response body.
    """
    url = start_url.strip()
    validate_target_url(url, allowed_domains, allow_http)

    redirects = 0
    referer: str | None = None
    while True:
        validate_target_url(url, allowed_domains, allow_http)

        headers = _browser_headers(user_agent, referer, url)
        req = client.build_request("GET", url, headers=headers)
        resp = await client.send(req, stream=True)
        response_closed = False
        try:
            status = resp.status_code
            if status in (301, 302, 303, 307, 308):
                redirects += 1
                if redirects > max_redirects:
                    raise FetchError("Too many redirects.")
                loc = resp.headers.get("location")
                if not loc:
                    raise FetchError("Redirect without Location header.")
                referer = str(resp.url)
                url = urljoin(url, loc)
                continue

            if status < 200 or status >= 400:
                await resp.aclose()
                response_closed = True
                if status == 403:
                    hostname = urlparse(url).hostname
                    if _wordpress_api_eligible(hostname):
                        wp = await _try_wordpress_api_fetch(
                            client, url, byte_limit, allowed_domains
                        )
                        if wp is not None:
                            return wp
                    if _playwright_eligible(hostname):
                        try:
                            from fetch_playwright import playwright_fetch_html
                        except ImportError as e:
                            raise FetchError(
                                "HTTP 403 — site blocked the request (often Cloudflare). "
                                "Install patchright: pip install patchright && patchright install chromium"
                            ) from e
                        try:
                            raw, final_url, ct = await playwright_fetch_html(
                                url,
                                allowed_domains,
                                allow_http=allow_http,
                                user_agent=user_agent,
                                timeout_ms=config.PLAYWRIGHT_TIMEOUT_MS,
                            )
                        except RuntimeError as e:
                            raise FetchError(str(e)) from e
                        except Exception as e:
                            from fetch_playwright import close_playwright

                            await close_playwright()
                            raise FetchError(f"Browser fetch failed: {e}") from e
                        n = len(raw)
                        if n > byte_limit:
                            return FetchOversizeKnown(
                                final_url=final_url,
                                content_length=n,
                                soft_limit=byte_limit,
                            )
                        return FetchOk(
                            content=raw, final_url=final_url, content_type=ct
                        )

                raise FetchError(f"HTTP {status}")

            cl_header = resp.headers.get("content-length")
            content_length: int | None = None
            if cl_header is not None:
                try:
                    content_length = int(cl_header)
                except ValueError:
                    content_length = None

            if content_length is not None and content_length > byte_limit:
                return FetchOversizeKnown(
                    final_url=str(resp.url),
                    content_length=content_length,
                    soft_limit=byte_limit,
                )

            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > byte_limit:
                    return FetchOversizeUnknown(
                        final_url=str(resp.url),
                        bytes_read=len(buf),
                        soft_limit=byte_limit,
                    )

            ct = resp.headers.get("content-type")
            return FetchOk(
                content=bytes(buf),
                final_url=str(resp.url),
                content_type=ct,
            )
        finally:
            if not response_closed:
                await resp.aclose()
