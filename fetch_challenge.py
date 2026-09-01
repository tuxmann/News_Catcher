"""Detect anti-bot / DataDome / Cloudflare challenge pages in HTML."""

from __future__ import annotations

_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "enable javascript and cookies",
    "javascript disabled",
    "javascript appears to have",
    "please enable javascript",
    "access denied",
    "access to this page has been denied",
    "403 - forbidden",
    "accès restreint",
    "acces restreint",
    "client challenge",
    "couldn't load",
    "challengehelp@humansecurity.com",
    "window._pxvid",
    'id="cmsg"',
    "datadome",
)


def extract_title(html: str) -> str | None:
    lower = html.lower()
    if "<title" not in lower:
        return None
    start = lower.find("<title")
    end = lower.find("</title>", start)
    if end <= start:
        return None
    return html[start:end]


def looks_like_challenge_page(html: str, title: str | None = None) -> bool:
    """True when HTML looks like a bot block rather than an article."""
    if not html or len(html.strip()) < 50:
        return True
    title_blob = (title or extract_title(html) or "").lower()
    blob = title_blob + " " + html[:12000].lower()
    if any(m in blob for m in _CHALLENGE_MARKERS):
        return True
    # DataDome often returns a tiny page titled only "reuters.com".
    if title_blob.strip() in ("reuters.com", "www.reuters.com") and len(html) < 8000:
        return True
    return False
