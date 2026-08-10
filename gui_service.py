"""Core fetch / TTS / pronunciation logic for the desktop GUI (no Telegram)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

import config
from article_cache import load_last_article, purge_expired as purge_article_cache, save_last_article
from domains_store import (
    add_bad_domain,
    bad_domain_refusal_message,
    host_is_bad,
    is_valid_registrable_domain,
    load_bad_domains,
    load_domains,
    normalize_registrable_hint,
    registrable_domain_from_url,
    save_domains,
)
from extract import extract_article
from fetch import (
    FetchError,
    FetchOk,
    FetchOversizeKnown,
    FetchOversizeUnknown,
    fetch_url,
)
from pronunciation_suggest import suggest_pronunciations
from research import format_research_display, run_deep_research
from tts import synthesize_pronunciation_sample, synthesize_to_mp3
from tts_normalize import add_literal_replacement

logger = logging.getLogger(__name__)

GUI_USER_ID = 0
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_first_url(text: str) -> str | None:
    match = URL_RE.search(text)
    return match.group(0).rstrip(").,;") if match else None


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def fetch_error_message(exc: FetchError) -> str:
    message = str(exc)
    if message == "HTTP 401":
        return (
            "HTTP 401 — the server refused the request (common with anti-bot, e.g. Reuters). "
            "Use the default browser-like USER_AGENT from .env.example."
        )
    if exc.blocked_domain or message.startswith("HTTP 402") or message.startswith("HTTP 403"):
        domain = exc.blocked_domain or "this site"
        tried = exc.tried_strategies
        code = "403"
        if message.startswith("HTTP "):
            parts = message.split()
            if len(parts) >= 2 and parts[1].isdigit():
                code = parts[1]
        lines = [f"HTTP {code} — {domain} blocked plain HTTP (anti-bot / paywall probe)."]
        if tried:
            lines.append("Tried: " + ", ".join(tried) + ".")
        lines.extend(
            [
                "",
                "If it keeps failing:",
                "pip install curl_cffi patchright && patchright install chromium",
            ]
        )
        return "\n".join(lines)
    return message


def article_context_snippet(title: str | None, text: str, word: str) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"Title: {title}")
    needle = word.casefold()
    for para in re.split(r"\n\s*\n+", text):
        if needle in para.casefold():
            parts.append(para.strip()[:500])
            break
    else:
        parts.append(text.strip()[:400])
    return "\n".join(parts)


def format_article_display(
    *,
    url: str,
    title: str | None,
    author: str | None,
    date: str | None,
    text: str,
) -> str:
    header: list[str] = [f"URL: {url}"]
    if title:
        header.append(f"Title: {title}")
    if author:
        header.append(f"Author: {author}")
    if date:
        header.append(f"Date: {date}")
    return "\n".join(header) + "\n\n" + text


@dataclass
class ArticleResult:
    url: str
    title: str | None
    author: str | None
    date: str | None
    text: str
    display_text: str


@dataclass
class DomainPrompt:
    domain: str
    url: str
    message: str


@dataclass
class BadDomainPrompt:
    domain: str
    url: str
    message: str


@dataclass
class OversizePrompt:
    url: str
    message: str


@dataclass
class FetchArticleOutcome:
    kind: str
    article: ArticleResult | None = None
    error: str | None = None
    domain_prompt: DomainPrompt | None = None
    bad_domain_prompt: BadDomainPrompt | None = None
    oversize_prompt: OversizePrompt | None = None


@dataclass
class DeepResearchOutcome:
    kind: str
    article: ArticleResult | None = None
    error: str | None = None
    warning: str | None = None


def _run_async(coro):
    return asyncio.run(coro)


async def _fetch_with_client(
    url: str,
    byte_limit: int,
    *,
    extra_allowed_domains: set[str] | None = None,
) -> FetchOk | FetchOversizeKnown | FetchOversizeUnknown:
    domains = set(load_domains(config.DOMAINS_FILE))
    if extra_allowed_domains:
        domains |= extra_allowed_domains
    timeout = httpx.Timeout(config.FETCH_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await fetch_url(
            client,
            url,
            byte_limit,
            domains,
            allow_http=config.ALLOW_HTTP,
            max_redirects=config.MAX_REDIRECTS,
            user_agent=config.USER_AGENT,
        )


def _process_html(
    html_bytes: bytes,
    final_url: str,
    content_type: str | None,
) -> FetchArticleOutcome:
    warning = ""
    if content_type and "html" not in content_type.lower() and "xml" not in content_type.lower():
        warning = "Warning: response may not be HTML; extraction might be poor.\n\n"

    try:
        article = extract_article(html_bytes, final_url)
    except Exception as exc:
        logger.exception("extract failed")
        return FetchArticleOutcome(kind="error", error=f"Could not extract article text: {exc}")

    if not article.text.strip():
        return FetchArticleOutcome(
            kind="error",
            error="No article body found in the HTML (paywall, JS-only page, or unsupported layout).",
        )

    display = warning + format_article_display(
        url=final_url,
        title=article.title,
        author=article.author,
        date=article.date,
        text=article.text,
    )
    save_last_article(
        config.ARTICLE_CACHE_DIR,
        GUI_USER_ID,
        final_url,
        article.title,
        article.text,
    )
    return FetchArticleOutcome(
        kind="ok",
        article=ArticleResult(
            url=final_url,
            title=article.title,
            author=article.author,
            date=article.date,
            text=article.text,
            display_text=display,
        ),
    )


def fetch_article(
    url: str,
    *,
    byte_limit: int | None = None,
    ignore_bad_domain: bool = False,
) -> FetchArticleOutcome:
    """Fetch and extract a news article URL for the GUI."""
    purge_article_cache(config.ARTICLE_CACHE_DIR, config.LAST_ARTICLE_TTL_SECONDS)
    limit = byte_limit if byte_limit is not None else config.FETCH_SOFT_MAX_BYTES
    bad_domains = load_bad_domains(config.DOMAINS_BAD_FILE)
    host = (urlparse(url).hostname or "").lower()
    trial_domain = registrable_domain_from_url(url)
    if host and host_is_bad(host, bad_domains) and not ignore_bad_domain:
        label = trial_domain or host
        return FetchArticleOutcome(
            kind="bad_domain_prompt",
            bad_domain_prompt=BadDomainPrompt(
                domain=label if is_valid_registrable_domain(label) else host,
                url=url,
                message=bad_domain_refusal_message(label, for_bot=False),
            ),
        )

    extra: set[str] | None = None
    if ignore_bad_domain and trial_domain and is_valid_registrable_domain(trial_domain):
        extra = {trial_domain}

    try:
        result = _run_async(_fetch_with_client(url, limit, extra_allowed_domains=extra))
    except FetchError as exc:
        if str(exc) == "Domain is not on the approved list.":
            reject = exc.rejected_url or url
            domain = registrable_domain_from_url(reject)
            if domain and is_valid_registrable_domain(domain):
                current = load_domains(config.DOMAINS_FILE)
                bad = load_bad_domains(config.DOMAINS_BAD_FILE)
                if domain in bad and not ignore_bad_domain:
                    return FetchArticleOutcome(
                        kind="bad_domain_prompt",
                        bad_domain_prompt=BadDomainPrompt(
                            domain=domain,
                            url=url,
                            message=bad_domain_refusal_message(domain, for_bot=False),
                        ),
                    )
                if domain not in current:
                    return FetchArticleOutcome(
                        kind="domain_prompt",
                        domain_prompt=DomainPrompt(
                            domain=domain,
                            url=url,
                            message=(
                                f"The domain {domain} is not on your approved list.\n\n"
                                "Add it so links to this site work?"
                            ),
                        ),
                    )
        return FetchArticleOutcome(kind="error", error=fetch_error_message(exc))
    except (httpx.HTTPError, OSError) as exc:
        logger.exception("fetch failed")
        return FetchArticleOutcome(kind="error", error=f"Network error: {exc}")

    if isinstance(result, FetchOversizeKnown):
        message = (
            f"Response size is about {format_size(result.content_length)} "
            f"(soft limit {format_size(result.soft_limit)}). "
            f"Proceed up to {format_size(config.FETCH_HARD_MAX_BYTES)}?"
        )
        return FetchArticleOutcome(
            kind="oversize",
            oversize_prompt=OversizePrompt(url=result.final_url, message=message),
        )

    if isinstance(result, FetchOversizeUnknown):
        message = (
            f"Download exceeded soft limit ({format_size(result.soft_limit)}) "
            f"after {format_size(result.bytes_read)} (total size unknown). "
            f"Proceed and refetch up to {format_size(config.FETCH_HARD_MAX_BYTES)}?"
        )
        return FetchArticleOutcome(
            kind="oversize",
            oversize_prompt=OversizePrompt(url=result.final_url, message=message),
        )

    if isinstance(result, FetchOk):
        return _process_html(result.content, result.final_url, result.content_type)

    return FetchArticleOutcome(kind="error", error="Unexpected fetch result.")


def deep_research(
    query: str,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> DeepResearchOutcome:
    """Search Google News on a topic, synthesize an article with Ollama."""
    try:
        result = run_deep_research(query, on_progress=on_progress)
    except ValueError as exc:
        return DeepResearchOutcome(kind="error", error=str(exc))
    except Exception as exc:
        logger.exception("deep research failed")
        return DeepResearchOutcome(kind="error", error=str(exc))

    display = format_research_display(
        topic=result.topic,
        headline=result.title,
        body=result.text,
        sources=result.sources,
    )
    save_last_article(
        config.ARTICLE_CACHE_DIR,
        GUI_USER_ID,
        f"research:{result.topic}",
        result.title,
        result.text,
    )
    return DeepResearchOutcome(
        kind="ok",
        article=ArticleResult(
            url=f"research:{result.topic}",
            title=result.title,
            author=None,
            date=None,
            text=result.text,
            display_text=display,
        ),
        warning=result.ollama_warning,
    )


def add_approved_domain(domain: str) -> str | None:
    """Add a domain to the allowlist. Returns an error message or None on success."""
    normalized = normalize_registrable_hint(domain)
    if not is_valid_registrable_domain(normalized):
        return "That domain label is invalid."
    current = load_domains(config.DOMAINS_FILE)
    current.add(normalized)
    save_domains(config.DOMAINS_FILE, current)
    return None


def mark_domain_bad(domain: str) -> str | None:
    normalized = normalize_registrable_hint(domain)
    if not is_valid_registrable_domain(normalized):
        return "Invalid domain."
    add_bad_domain(config.DOMAINS_BAD_FILE, config.DOMAINS_FILE, normalized)
    return None


def get_cached_article():
    purge_article_cache(config.ARTICLE_CACHE_DIR, config.LAST_ARTICLE_TTL_SECONDS)
    return load_last_article(
        config.ARTICLE_CACHE_DIR,
        GUI_USER_ID,
        ttl_seconds=config.LAST_ARTICLE_TTL_SECONDS,
    )


def speak_last_article() -> Path:
    """Synthesize the cached article to MP3 and return the output path."""
    if not config.TTS_ENABLED:
        raise RuntimeError("Text-to-speech is disabled (TTS_ENABLED=0).")
    cached = get_cached_article()
    if cached is None:
        raise RuntimeError("No recent article. Fetch a news URL first.")

    config.AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.AUDIO_OUTPUT_DIR / f"gui_{GUI_USER_ID}_{int(time.time())}.mp3"
    synthesize_to_mp3(
        cached.text,
        out_path,
        title=cached.title,
        source_domain=registrable_domain_from_url(cached.url),
    )
    return out_path


@dataclass
class PronunciationSample:
    from_text: str
    spelling: str
    path: Path


def suggest_word_fix_samples(from_text: str) -> tuple[list[PronunciationSample], str | None]:
    """Return pronunciation sample clips for a mispronounced word."""
    if not config.TTS_ENABLED:
        raise RuntimeError("Text-to-speech is disabled (TTS_ENABLED=0).")
    cached = get_cached_article()
    if cached is None:
        raise RuntimeError("No recent article. Fetch a news URL first.")

    context = article_context_snippet(cached.title, cached.text, from_text)
    if not config.PRONUNCIATION_SUGGEST_ENABLED:
        raise RuntimeError("Pronunciation suggestions are disabled (PRONUNCIATION_SUGGEST_ENABLED=0).")

    result = suggest_pronunciations(from_text, article_context=context)
    if result.error:
        raise RuntimeError(result.error)
    if not result.suggestions:
        raise RuntimeError(
            f"No pronunciation suggestions for {from_text!r}. "
            "Check that Ollama is running or add a rule in tts_replacements.json."
        )

    config.AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    samples: list[PronunciationSample] = []
    warning = result.warning
    for alt in result.suggestions:
        out_path = config.AUDIO_OUTPUT_DIR / f"gui_pronounce_{int(time.time())}_{len(samples)}.mp3"
        synthesize_pronunciation_sample(alt, out_path)
        samples.append(PronunciationSample(from_text=from_text, spelling=alt, path=out_path))
    return samples, warning


def save_pronunciation(from_text: str, to_text: str, *, ignore_case: bool = False) -> bool:
    return add_literal_replacement(from_text, to_text, ignore_case=ignore_case)


# Test & Fix: full test paragraphs get 1s lead silence; AI sample clips get 0.5s.
TEST_FIX_LEAD_SILENCE_MS = 1000
SUGGEST_SAMPLE_LEAD_SILENCE_MS = 500


def synthesize_test_phrase(text: str) -> Path:
    """Synthesize pasted test paragraphs to MP3 (no intro/outro branding)."""
    if not config.TTS_ENABLED:
        raise RuntimeError("Text-to-speech is disabled (TTS_ENABLED=0).")
    phrase = text.strip()
    if not phrase:
        raise RuntimeError("Enter some text to test.")
    config.AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.AUDIO_OUTPUT_DIR / f"gui_test_{int(time.time())}.mp3"
    synthesize_to_mp3(
        phrase,
        out_path,
        title=None,
        source_domain=None,
        chunk_chars=1000,
        lead_silence_ms=TEST_FIX_LEAD_SILENCE_MS,
    )
    return out_path


def suggest_test_fix_samples(
    from_text: str,
    *,
    context: str | None = None,
) -> tuple[list[PronunciationSample], str | None, str | None]:
    """Return up to four pronunciation sample clips for the Test & Fix dialog."""
    word = from_text.strip()
    if not word:
        return [], None, "Enter the word or phrase to fix."
    if not config.TTS_ENABLED:
        return [], None, "Text-to-speech is disabled (TTS_ENABLED=0)."
    if not config.PRONUNCIATION_SUGGEST_ENABLED:
        return [], None, "Pronunciation suggestions are disabled (PRONUNCIATION_SUGGEST_ENABLED=0)."

    result = suggest_pronunciations(word, article_context=context)
    if result.error:
        return [], result.warning, result.error
    if not result.suggestions:
        return [], result.warning, f"No pronunciation suggestions for {word!r}."

    config.AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    samples: list[PronunciationSample] = []
    for alt in result.suggestions:
        out_path = config.AUDIO_OUTPUT_DIR / f"gui_suggest_{int(time.time())}_{len(samples)}.mp3"
        synthesize_pronunciation_sample(
            alt,
            out_path,
            lead_silence_ms=SUGGEST_SAMPLE_LEAD_SILENCE_MS,
        )
        samples.append(PronunciationSample(from_text=word, spelling=alt, path=out_path))
    return samples, result.warning, None


def test_fix_context_snippet(text: str, from_text: str, *, max_chars: int = 800) -> str:
    """Build article-style context from pasted test paragraphs."""
    needle = from_text.strip().casefold()
    if needle:
        for para in re.split(r"\n\s*\n+", text):
            if needle in para.casefold():
                return para.strip()[:max_chars]
    return text.strip()[:max_chars]
