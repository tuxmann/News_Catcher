"""Synthesize a briefing script via local Ollama."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from briefing.ingest import FeedItem
from briefing.planner import ScriptBudget

logger = logging.getLogger(__name__)


@dataclass
class ArticleSnippet:
    title: str
    url: str
    source: str
    text: str


def _build_sources_block(snippets: list[ArticleSnippet], deep_dives: list[str]) -> str:
    parts: list[str] = []
    if deep_dives:
        parts.append("Deep-dive topics requested: " + "; ".join(deep_dives))
    for i, s in enumerate(snippets, start=1):
        excerpt = s.text.strip()
        if len(excerpt) > 4000:
            excerpt = excerpt[:4000] + "…"
        parts.append(
            f"--- Source {i}: {s.title} ({s.source})\nURL: {s.url}\n{excerpt}\n"
        )
    return "\n".join(parts)


def synthesize_script(
    snippets: list[ArticleSnippet],
    budget: ScriptBudget,
    *,
    ollama_host: str,
    ollama_model: str,
    deep_dives: list[str] | None = None,
    timeout_seconds: float = 600.0,
) -> str:
    if not snippets:
        raise ValueError("No article snippets to synthesize.")

    deep_dives = deep_dives or []
    sources = _build_sources_block(snippets, deep_dives)
    prompt = f"""You are writing a spoken news briefing script for audio narration.

Target length: about {budget.target_words} words (~{budget.target_minutes} minutes at {budget.words_per_minute} words per minute).

Instructions:
- Weave the most important facts and contrasting viewpoints across outlets into one coherent narrative.
- Use clear section transitions suitable for listening (no bullet lists).
- Attribute perspectives by outlet name when views differ.
- Do not invent facts beyond the sources below.
- Write only the script text to be read aloud (no stage directions or markdown).

Sources:
{sources}
"""

    url = f"{ollama_host.rstrip('/')}/api/generate"
    payload = {
        "model": ollama_model,
        "prompt": prompt,
        "stream": False,
    }
    logger.info("Calling Ollama model %s at %s", ollama_model, ollama_host)
    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    script = (data.get("response") or "").strip()
    if not script:
        raise RuntimeError("Ollama returned an empty script.")
    return script


def synthesize_deep_dive_section(
    topic: str,
    snippets: list[ArticleSnippet],
    *,
    ollama_host: str,
    ollama_model: str,
    max_words: int = 1500,
    timeout_seconds: float = 300.0,
) -> str:
    related = [
        s
        for s in snippets
        if topic.casefold() in f"{s.title} {s.text}".casefold()
    ]
    if not related:
        related = snippets[:5]
    sources = _build_sources_block(related, [topic])
    prompt = f"""Write a deep-dive segment (~{max_words} words) for audio about: {topic}

Use only facts from these sources. Narration style, no markdown.

{sources}
"""
    url = f"{ollama_host.rstrip('/')}/api/generate"
    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.post(
            url,
            json={"model": ollama_model, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        script = (resp.json().get("response") or "").strip()
    if not script:
        raise RuntimeError(f"Empty deep-dive script for topic: {topic}")
    return script
