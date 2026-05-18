"""Fetch and extract articles for the briefing pipeline."""

from __future__ import annotations

import logging

import httpx

import config
from briefing.ingest import FeedItem
from briefing.synthesize import ArticleSnippet
from domains_store import load_domains
from extract import extract_article
from fetch import FetchOk, fetch_url

logger = logging.getLogger(__name__)


async def fetch_snippets(
    items: list[FeedItem],
    client: httpx.AsyncClient,
) -> list[ArticleSnippet]:
    domains = load_domains(config.DOMAINS_FILE)
    snippets: list[ArticleSnippet] = []
    for item in items:
        try:
            result = await fetch_url(
                client,
                item.url,
                config.FETCH_SOFT_MAX_BYTES,
                domains,
                allow_http=config.ALLOW_HTTP,
                max_redirects=config.MAX_REDIRECTS,
                user_agent=config.USER_AGENT,
            )
        except Exception as e:
            logger.warning("Fetch failed %s: %s", item.url, e)
            continue
        if not isinstance(result, FetchOk):
            logger.warning("Skipping non-OK fetch for %s", item.url)
            continue
        try:
            article = extract_article(result.content, result.final_url)
        except Exception as e:
            logger.warning("Extract failed %s: %s", item.url, e)
            continue
        if not article.text.strip():
            continue
        snippets.append(
            ArticleSnippet(
                title=article.title or item.title,
                url=result.final_url,
                source=item.source_label,
                text=article.text,
            )
        )
    return snippets
