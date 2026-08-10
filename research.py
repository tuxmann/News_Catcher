"""Deep research: collect news on a topic via Google News, synthesize with Ollama."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Literal
from urllib.parse import quote_plus, urlparse

import feedparser
import httpx
from lxml import html as lxml_html

import config
from briefing.fetch_articles import fetch_snippets
from briefing.ingest import FeedItem
from briefing.synthesize import ArticleSnippet
from domains_store import host_allowed, load_domains
from google_news import is_google_news_article_url, resolve_google_news_url
from ollama_util import resolve_ollama_model

logger = logging.getLogger(__name__)

_GOOGLE_NEWS_COVERAGE_RE = re.compile(
    r"https?://news\.google\.com/(?:stories|read)/",
    re.IGNORECASE,
)

ProgressCallback = Callable[[str], None]


@dataclass
class ResearchResult:
    topic: str
    title: str
    text: str
    sources: list[ArticleSnippet]
    ollama_warning: str | None = None


def is_google_news_coverage_url(url: str) -> bool:
    return bool(_GOOGLE_NEWS_COVERAGE_RE.search(url.strip()))


def parse_research_input(text: str) -> tuple[Literal["coverage", "topic"], str]:
    """Return research mode and the topic phrase or Full Coverage URL."""
    raw = text.strip()
    if not raw:
        raise ValueError("Enter a topic to research (e.g. US war with Iran).")
    lowered = raw.lower()
    if lowered.startswith(("http://", "https://")):
        if is_google_news_coverage_url(raw):
            return "coverage", raw
        raise ValueError(
            "For a single article URL, use Go. "
            "Research accepts a topic phrase or a Google News Full Coverage link."
        )
    return "topic", raw


def google_news_search_rss_url(query: str) -> str:
    q = quote_plus(query.strip())
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _entry_source_href(entry: dict) -> str:
    source = entry.get("source")
    if isinstance(source, dict):
        href = source.get("href") or ""
        if isinstance(href, str):
            return href.strip()
    return ""


def _feed_items_from_rss(
    feed_url: str,
    allowed_domains: set[str],
    *,
    max_items: int,
) -> list[FeedItem]:
    parsed = feedparser.parse(feed_url)
    items: list[FeedItem] = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        if not link.startswith(("http://", "https://")):
            continue
        source_href = _entry_source_href(entry)
        host = urlparse(source_href).hostname or urlparse(link).hostname or ""
        if not host_allowed(host, allowed_domains):
            logger.debug("Skipping non-allowed host %s for %s", host, link)
            continue
        title = (entry.get("title") or "").strip() or link
        summary = (entry.get("summary") or entry.get("description") or "").strip()
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = " ".join(summary.split())
        label_host = urlparse(source_href).hostname or host

        article_url = link
        if is_google_news_article_url(link):
            decoded = resolve_google_news_url(link)
            if not decoded:
                logger.debug("Could not decode Google News URL: %s", link[:80])
                continue
            article_url = decoded

        items.append(
            FeedItem(
                title=title,
                url=article_url,
                summary=summary,
                source_label=label_host or "news",
            )
        )
        if len(items) >= max_items:
            break
    return items


def collect_topic_items(
    topic: str,
    allowed_domains: set[str],
    *,
    max_items: int,
) -> list[FeedItem]:
    feed_url = google_news_search_rss_url(topic)
    logger.info("Google News RSS search: %s", feed_url)
    return _feed_items_from_rss(feed_url, allowed_domains, max_items=max_items)


def _extract_publisher_links(
    html_bytes: bytes,
    allowed_domains: set[str],
    *,
    max_items: int,
) -> list[FeedItem]:
    tree = lxml_html.fromstring(html_bytes)
    seen: set[str] = set()
    items: list[FeedItem] = []
    for anchor in tree.xpath("//a[@href]"):
        href = (anchor.get("href") or "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        host = (urlparse(href).hostname or "").lower()
        if not host or not host_allowed(host, allowed_domains):
            continue
        if "news.google.com" in host or "google.com" in host:
            continue
        if href in seen:
            continue
        seen.add(href)
        title = " ".join((anchor.text_content() or "").split())
        if not title:
            title = href
        items.append(
            FeedItem(
                title=title,
                url=href,
                summary="",
                source_label=host,
            )
        )
        if len(items) >= max_items:
            break
    return items


async def _extract_coverage_items_playwright(
    coverage_url: str,
    allowed_domains: set[str],
    *,
    max_items: int,
) -> list[FeedItem]:
    """
    Load a Google News Full Coverage page in Chromium and collect article links.

    The page is JS-rendered; static HTML has no publisher URLs. Article cards link
    to news.google.com/read/… URLs that must be decoded to publisher URLs.
    """
    from fetch_playwright import _get_browser

    timeout_ms = int(config.FETCH_TIMEOUT_SECONDS * 1000)
    browser = await _get_browser()
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        locale="en-US",
    )
    page = await context.new_page()
    try:
        await page.goto(coverage_url, wait_until="networkidle", timeout=timeout_ms)
        raw_links = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .filter(a => {
                    const href = a.href || '';
                    const text = (a.innerText || '').trim();
                    return (href.includes('/read/') || href.includes('/articles/'))
                        && text.length > 20;
                })
                .map(a => ({ href: a.href, text: a.innerText.trim() }))"""
        )
    finally:
        await context.close()

    items: list[FeedItem] = []
    seen_urls: set[str] = set()
    for link in raw_links:
        google_url = (link.get("href") or "").strip()
        title = " ".join((link.get("text") or "").split())
        if not google_url or not title:
            continue
        publisher_url = resolve_google_news_url(google_url)
        if not publisher_url:
            logger.debug("Skipping undecodable coverage link: %s", google_url[:80])
            continue
        host = (urlparse(publisher_url).hostname or "").lower()
        if not host_allowed(host, allowed_domains):
            continue
        if publisher_url in seen_urls:
            continue
        seen_urls.add(publisher_url)
        items.append(
            FeedItem(
                title=title,
                url=publisher_url,
                summary="",
                source_label=host,
            )
        )
        if len(items) >= max_items:
            break
    return items


async def _fetch_coverage_html(url: str, allowed_domains: set[str]) -> bytes:
    timeout_ms = int(config.FETCH_TIMEOUT_SECONDS * 1000)
    try:
        from fetch_playwright import playwright_fetch_html

        html_bytes, _, _ = await playwright_fetch_html(
            url,
            allowed_domains,
            allow_http=config.ALLOW_HTTP,
            user_agent=config.USER_AGENT,
            timeout_ms=timeout_ms,
        )
        return html_bytes
    except Exception as exc:
        logger.warning("Playwright coverage fetch failed (%s); trying HTTP", exc)
    timeout = httpx.Timeout(config.FETCH_TIMEOUT_SECONDS)
    headers = {"User-Agent": config.USER_AGENT}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content
    # unreachable — kept for type checkers
    raise RuntimeError("Could not fetch Google News Full Coverage page.")


async def collect_coverage_items(
    coverage_url: str,
    allowed_domains: set[str],
    *,
    max_items: int,
) -> tuple[list[FeedItem], str]:
    """
    Collect publisher articles from a Google News Full Coverage page.

    Returns (feed_items, topic_label).
    """
    try:
        items = await _extract_coverage_items_playwright(
            coverage_url, allowed_domains, max_items=max_items
        )
        if items:
            topic = items[0].title
            return items, topic
    except Exception as exc:
        logger.warning("Playwright coverage extraction failed (%s); trying HTML fallback", exc)

    html_bytes = await _fetch_coverage_html(coverage_url, allowed_domains)
    items = _extract_publisher_links(html_bytes, allowed_domains, max_items=max_items)
    if items:
        return items, items[0].title

    tree = lxml_html.fromstring(html_bytes)
    titles = tree.xpath("//title/text()")
    page_title = titles[0].strip() if titles else ""
    page_title = re.sub(r"\s*-\s*Google News\s*$", "", page_title, flags=re.IGNORECASE)
    if page_title and page_title.lower() not in ("google news", "google news - overview", "overview"):
        logger.info("Full Coverage had no direct links; searching RSS for %r", page_title)
        return (
            collect_topic_items(page_title, allowed_domains, max_items=max_items),
            page_title,
        )
    raise RuntimeError(
        "Could not find article links on that Google News Full Coverage page. "
        "Try a topic phrase instead, or ensure patchright is installed."
    )


def synthesize_research_article(
    topic: str,
    snippets: list[ArticleSnippet],
    *,
    ollama_host: str | None = None,
    ollama_model: str | None = None,
    target_words: int | None = None,
    timeout_seconds: float | None = None,
) -> tuple[str, str, str | None]:
    """
    Write a news article from source snippets.

    Returns (headline, body, ollama_warning).
    """
    if not snippets:
        raise ValueError("No article snippets to synthesize.")

    host = (ollama_host or config.OLLAMA_HOST).rstrip("/")
    requested_model = (ollama_model or config.OLLAMA_MODEL).strip()
    timeout = timeout_seconds or float(config.RESEARCH_OLLAMA_TIMEOUT)
    words = target_words or config.RESEARCH_TARGET_WORDS

    model, warning = resolve_ollama_model(host, requested_model)
    if model is None:
        raise RuntimeError(warning or "Ollama model is not available.")

    parts: list[str] = []
    for i, snippet in enumerate(snippets, start=1):
        excerpt = snippet.text.strip()
        if len(excerpt) > 4000:
            excerpt = excerpt[:4000] + "…"
        parts.append(
            f"--- Source {i}: {snippet.title} ({snippet.source})\n"
            f"URL: {snippet.url}\n{excerpt}\n"
        )
    sources_block = "\n".join(parts)

    prompt = f"""You are a news editor synthesizing recent reporting into one original news article.

Topic: {topic}

Target length: about {words} words.

Instructions:
- Write in clear third-person journalistic prose (for reading, not radio).
- Open with a compelling headline on its own first line, formatted exactly as: HEADLINE: Your headline here
- Follow with the article body (3–6 paragraphs, no bullet lists).
- Weave facts and contrasting viewpoints from the sources; attribute outlets when views differ.
- Do not invent facts, quotes, or events beyond the sources below.
- No markdown, stage directions, or meta commentary.

Sources:
{sources_block}
"""

    url = f"{host}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False}
    logger.info("Calling Ollama model %s for research article", model)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        raw = (resp.json().get("response") or "").strip()
    if not raw:
        raise RuntimeError("Ollama returned an empty article.")

    headline = topic
    body = raw
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("HEADLINE:"):
            headline = stripped.split(":", 1)[1].strip() or topic
            body = "\n".join(
                ln for ln in raw.splitlines() if not ln.strip().upper().startswith("HEADLINE:")
            ).strip()
            break
    return headline, body, warning


def format_research_display(
    *,
    topic: str,
    headline: str,
    body: str,
    sources: list[ArticleSnippet],
) -> str:
    lines = [f"Research topic: {topic}", f"Headline: {headline}", ""]
    lines.append(f"Sources ({len(sources)} articles):")
    for snippet in sources:
        lines.append(f"• {snippet.title} ({snippet.source})")
        lines.append(f"  {snippet.url}")
    lines.extend(["", body])
    return "\n".join(lines)


async def run_deep_research_async(
    text: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> ResearchResult:
    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    mode, value = parse_research_input(text)
    allowed = load_domains(config.DOMAINS_FILE)
    max_items = config.RESEARCH_MAX_ARTICLES

    if mode == "coverage":
        progress("Loading Google News Full Coverage…")
        items, topic_label = await collect_coverage_items(value, allowed, max_items=max_items)
    else:
        progress(f"Searching Google News for {value!r}…")
        items = collect_topic_items(value, allowed, max_items=max_items)
        topic_label = value

    if not items:
        raise RuntimeError(
            "No articles from your approved domains matched this topic. "
            "Try a different phrase or add outlets to domains.json."
        )

    progress(f"Found {len(items)} headline(s). Downloading articles…")
    timeout = httpx.Timeout(config.FETCH_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        snippets: list[ArticleSnippet] = []
        for i, item in enumerate(items, start=1):
            progress(f"Reading article {i}/{len(items)}…")
            batch = await fetch_snippets([item], client)
            snippets.extend(batch)

    if not snippets:
        raise RuntimeError(
            "Headlines were found but article text could not be extracted "
            "(paywalls, blocks, or unsupported layouts)."
        )

    progress("Writing article with Ollama…")
    headline, body, warning = synthesize_research_article(topic_label, snippets)
    return ResearchResult(
        topic=topic_label,
        title=headline,
        text=body,
        sources=snippets,
        ollama_warning=warning,
    )


def run_deep_research(
    text: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> ResearchResult:
    import asyncio

    return asyncio.run(run_deep_research_async(text, on_progress=on_progress))
