"""Collect article URLs from RSS feeds and optional topic filters."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import feedparser

from briefing.config_loader import BriefingConfig
from domains_store import host_allowed, load_domains

logger = logging.getLogger(__name__)


@dataclass
class FeedItem:
    title: str
    url: str
    summary: str
    source_label: str
    matched_subject: str | None = None
    matched_deep_dive: str | None = None


def _text_matches_any(text: str, needles: list[str]) -> str | None:
    hay = text.casefold()
    for needle in needles:
        if needle.casefold() in hay:
            return needle
    return None


def _subject_keywords(config: BriefingConfig) -> list[tuple[str, list[str]]]:
    return [(s.name, s.keywords) for s in config.subjects if s.keywords]


def collect_feed_items(
    config: BriefingConfig,
    domains_file,
    *,
    allowed_domains: set[str] | None = None,
) -> list[FeedItem]:
    if allowed_domains is None:
        allowed_domains = load_domains(domains_file)
    items: list[FeedItem] = []
    subject_map = _subject_keywords(config)

    for feed in config.feeds:
        logger.info("Parsing feed %s", feed.url)
        parsed = feedparser.parse(feed.url)
        count = 0
        for entry in parsed.entries:
            link = entry.get("link") or ""
            if not link.startswith(("http://", "https://")):
                continue
            host = urlparse(link).hostname or ""
            if not host_allowed(host, allowed_domains):
                logger.debug("Skipping non-allowed host %s: %s", host, link)
                continue
            title = (entry.get("title") or "").strip() or link
            summary = (entry.get("summary") or entry.get("description") or "").strip()
            summary = re.sub(r"<[^>]+>", " ", summary)
            summary = " ".join(summary.split())

            blob = f"{title} {summary}"
            matched_subject = None
            for subj_name, keywords in subject_map:
                if _text_matches_any(blob, keywords):
                    matched_subject = subj_name
                    break

            matched_dive = _text_matches_any(blob, config.deep_dives)

            # If subjects configured, prefer matching items; still allow general news.
            if subject_map and not matched_subject and not matched_dive:
                pass

            items.append(
                FeedItem(
                    title=title,
                    url=link,
                    summary=summary,
                    source_label=feed.label or urlparse(feed.url).netloc,
                    matched_subject=matched_subject,
                    matched_deep_dive=matched_dive,
                )
            )
            count += 1
            if count >= config.max_articles_per_feed:
                break

    # Prioritize deep dives and subject matches, then dedupe by URL.
    seen: set[str] = set()
    prioritized: list[FeedItem] = []
    for item in sorted(
        items,
        key=lambda x: (
            0 if x.matched_deep_dive else 1,
            0 if x.matched_subject else 1,
        ),
    ):
        if item.url in seen:
            continue
        seen.add(item.url)
        prioritized.append(item)
        if len(prioritized) >= config.max_articles_total:
            break
    return prioritized
