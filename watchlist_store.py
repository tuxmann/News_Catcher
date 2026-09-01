"""Persisted blog watchlist (domains to poll for new posts)."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse, urlunparse

_lock = threading.Lock()

MAX_POSTS_PER_SITE = 20
DEFAULT_CHECK_INTERVAL_MINUTES = 60
MIN_CHECK_INTERVAL_MINUTES = 15
ALLOWED_SUBHOUR_INTERVALS = (15, 30)

_TRACKING_QUERY_RE = re.compile(
    r"(?:^|&)(?:utm_[^=&]*|fbclid|gclid|mc_cid|mc_eid)=[^&]*",
    re.IGNORECASE,
)


@dataclass
class WatchedPost:
    url: str
    title: str
    seen_at: float


@dataclass
class WatchedSite:
    domain: str
    check_interval_minutes: int = DEFAULT_CHECK_INTERVAL_MINUTES
    feed_url: str | None = None
    last_checked_at: float = 0.0
    posts: list[WatchedPost] = field(default_factory=list)

    def known_urls(self) -> set[str]:
        return {normalize_post_url(p.url) for p in self.posts if p.url}

    def known_titles(self) -> set[str]:
        return {normalize_post_title(p.title) for p in self.posts if p.title.strip()}


def normalize_post_title(title: str) -> str:
    """Case/whitespace-normalized title for duplicate detection."""
    return " ".join((title or "").casefold().split())


def normalize_post_url(url: str) -> str:
    """
    Normalize a post URL so minor differences (trailing slash, www, tracking
    query params) still count as the same post.
    """
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except Exception:
        return text.rstrip("/").casefold()
    scheme = (parsed.scheme or "https").casefold()
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = parsed.query or ""
    if query:
        query = _TRACKING_QUERY_RE.sub("", query)
        query = query.lstrip("&")
        query = re.sub(r"&&+", "&", query).strip("&")
    netloc = host
    if parsed.port and not (
        (scheme == "http" and parsed.port == 80)
        or (scheme == "https" and parsed.port == 443)
    ):
        netloc = f"{host}:{parsed.port}"
    return urlunparse((scheme, netloc, path, "", query, ""))


def post_identity_keys(url: str, title: str) -> tuple[str, str]:
    return normalize_post_url(url), normalize_post_title(title)


def is_known_post(site: WatchedSite, url: str, title: str) -> bool:
    """True if this post matches a stored URL or title in the site history."""
    norm_url, norm_title = post_identity_keys(url, title)
    if norm_url and norm_url in site.known_urls():
        return True
    if norm_title and norm_title in site.known_titles():
        return True
    return False


def normalize_check_interval(minutes: int) -> int:
    """
    Snap to 15, 30, or a whole-hour multiple (60, 120, …).
    Hourly (and longer) checks run at the top of the hour.
    """
    m = int(minutes)
    if m <= 22:
        return 15
    if m <= 44:
        return 30
    hours = max(1, (m + 30) // 60)
    return hours * 60


def _clamp_interval(minutes: int) -> int:
    return normalize_check_interval(minutes)


def current_slot_start(
    interval_minutes: int, *, now: datetime | None = None
) -> datetime:
    """Most recent clock-aligned slot start at or before `now` (local time)."""
    now = datetime.now() if now is None else now
    interval = normalize_check_interval(interval_minutes)
    if interval % 60 == 0:
        hours = interval // 60
        aligned_hour = (now.hour // hours) * hours
        return now.replace(hour=aligned_hour, minute=0, second=0, microsecond=0)
    aligned_min = (now.minute // interval) * interval
    return now.replace(minute=aligned_min, second=0, microsecond=0)


def _post_from_raw(raw: object) -> WatchedPost | None:
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    if not url:
        return None
    title = str(raw.get("title") or url).strip() or url
    try:
        seen_at = float(raw.get("seen_at") or 0)
    except (TypeError, ValueError):
        seen_at = 0.0
    return WatchedPost(url=url, title=title, seen_at=seen_at)


def _site_from_raw(raw: object) -> WatchedSite | None:
    if not isinstance(raw, dict):
        return None
    domain = str(raw.get("domain") or "").strip().lower().rstrip(".")
    if not domain:
        return None
    try:
        interval = _clamp_interval(
            int(raw.get("check_interval_minutes") or DEFAULT_CHECK_INTERVAL_MINUTES)
        )
    except (TypeError, ValueError):
        interval = DEFAULT_CHECK_INTERVAL_MINUTES
    feed_url = raw.get("feed_url")
    feed = str(feed_url).strip() if isinstance(feed_url, str) and feed_url.strip() else None
    try:
        last_checked = float(raw.get("last_checked_at") or 0)
    except (TypeError, ValueError):
        last_checked = 0.0
    posts_raw = raw.get("posts") or []
    posts: list[WatchedPost] = []
    if isinstance(posts_raw, list):
        for item in posts_raw:
            post = _post_from_raw(item)
            if post is not None:
                posts.append(post)
    return WatchedSite(
        domain=domain,
        check_interval_minutes=interval,
        feed_url=feed,
        last_checked_at=last_checked,
        posts=posts[:MAX_POSTS_PER_SITE],
    )


def load_watchlist(path: Path) -> list[WatchedSite]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return []
    sites_raw = data.get("sites") or []
    if not isinstance(sites_raw, list):
        return []
    sites: list[WatchedSite] = []
    for raw in sites_raw:
        site = _site_from_raw(raw)
        if site is not None:
            sites.append(site)
    return sites


def save_watchlist(path: Path, sites: Iterable[WatchedSite]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sites": [
            {
                "domain": s.domain,
                "check_interval_minutes": s.check_interval_minutes,
                "feed_url": s.feed_url,
                "last_checked_at": s.last_checked_at,
                "posts": [asdict(p) for p in s.posts[:MAX_POSTS_PER_SITE]],
            }
            for s in sites
        ]
    }
    with _lock:
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")


def get_site(path: Path, domain: str) -> WatchedSite | None:
    d = domain.strip().lower().rstrip(".")
    for site in load_watchlist(path):
        if site.domain == d:
            return site
    return None


def upsert_site(path: Path, site: WatchedSite) -> WatchedSite:
    sites = load_watchlist(path)
    site.check_interval_minutes = _clamp_interval(site.check_interval_minutes)
    site.posts = site.posts[:MAX_POSTS_PER_SITE]
    for i, existing in enumerate(sites):
        if existing.domain == site.domain:
            sites[i] = site
            save_watchlist(path, sites)
            return site
    sites.append(site)
    save_watchlist(path, sites)
    return site


def remove_site(path: Path, domain: str) -> bool:
    d = domain.strip().lower().rstrip(".")
    sites = load_watchlist(path)
    kept = [s for s in sites if s.domain != d]
    if len(kept) == len(sites):
        return False
    save_watchlist(path, kept)
    return True


def set_interval(path: Path, domain: str, minutes: int) -> WatchedSite | None:
    site = get_site(path, domain)
    if site is None:
        return None
    site.check_interval_minutes = _clamp_interval(minutes)
    return upsert_site(path, site)


def merge_new_posts(site: WatchedSite, new_posts: list[WatchedPost]) -> list[WatchedPost]:
    """
    Prepend truly new posts (by normalized URL or title). Cap at MAX_POSTS_PER_SITE.
    Returns the posts that were newly added (caller order preserved among added).
    """
    if not new_posts:
        return []
    known_urls = site.known_urls()
    known_titles = site.known_titles()
    added: list[WatchedPost] = []
    now = time.time()
    for post in new_posts:
        norm_url, norm_title = post_identity_keys(post.url, post.title)
        if norm_url and norm_url in known_urls:
            continue
        if norm_title and norm_title in known_titles:
            continue
        if norm_url:
            known_urls.add(norm_url)
        if norm_title:
            known_titles.add(norm_title)
        added.append(
            WatchedPost(
                url=post.url,
                title=post.title or post.url,
                seen_at=post.seen_at or now,
            )
        )
    if not added:
        return []
    # Keep feed order (caller should pass newest-first); prepend batch as a block.
    site.posts = (added + site.posts)[:MAX_POSTS_PER_SITE]
    return added


def refresh_recent_posts(
    site: WatchedSite,
    recent: list[WatchedPost],
) -> None:
    """
    Replace the site's stored history with the feed's current top posts (≤20),
    preserving seen_at when a post matches a prior URL or title.
    """
    previous = list(site.posts)
    by_url = {normalize_post_url(p.url): p for p in previous if p.url}
    by_title = {
        normalize_post_title(p.title): p for p in previous if p.title.strip()
    }
    now = time.time()
    refreshed: list[WatchedPost] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for post in recent:
        title = (post.title or post.url).strip() or post.url
        norm_url, norm_title = post_identity_keys(post.url, title)
        if norm_url and norm_url in seen_urls:
            continue
        if norm_title and norm_title in seen_titles:
            continue
        prior = None
        if norm_url and norm_url in by_url:
            prior = by_url[norm_url]
        elif norm_title and norm_title in by_title:
            prior = by_title[norm_title]
        refreshed.append(
            WatchedPost(
                url=post.url,
                title=title,
                seen_at=prior.seen_at if prior is not None else (post.seen_at or now),
            )
        )
        if norm_url:
            seen_urls.add(norm_url)
        if norm_title:
            seen_titles.add(norm_title)
        if len(refreshed) >= MAX_POSTS_PER_SITE:
            break

    # Keep older remembered posts that fell off the current feed window so a
    # brief disappearance does not cause a duplicate Telegram notify later.
    for prior in previous:
        if len(refreshed) >= MAX_POSTS_PER_SITE:
            break
        norm_url, norm_title = post_identity_keys(prior.url, prior.title)
        if norm_url and norm_url in seen_urls:
            continue
        if norm_title and norm_title in seen_titles:
            continue
        refreshed.append(prior)
        if norm_url:
            seen_urls.add(norm_url)
        if norm_title:
            seen_titles.add(norm_title)

    site.posts = refreshed[:MAX_POSTS_PER_SITE]


def site_is_due(
    site: WatchedSite,
    *,
    now: float | datetime | None = None,
) -> bool:
    """
    True if this site has not yet been checked in the current clock slot.

    15 min → :00, :15, :30, :45
    30 min → :00, :30
    60+ min (multiples of 60) → top of the hour (every N hours from midnight)
    """
    if site.last_checked_at <= 0:
        return True
    if isinstance(now, datetime):
        dt = now
    elif now is None:
        dt = datetime.now()
    else:
        dt = datetime.fromtimestamp(now)
    slot = current_slot_start(site.check_interval_minutes, now=dt)
    return site.last_checked_at < slot.timestamp()
