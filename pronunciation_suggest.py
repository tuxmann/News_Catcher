"""Suggest TTS-friendly spellings via local Ollama."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import config
from ollama_util import resolve_ollama_model

logger = logging.getLogger(__name__)

_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*?\]")


@dataclass
class PronunciationSuggestResult:
    suggestions: list[str]
    warning: str | None = None
    error: str | None = None


def suggest_pronunciations(
    word: str,
    *,
    article_context: str | None = None,
    user_feedback: str | None = None,
    avoid_spellings: list[str] | None = None,
    ollama_host: str | None = None,
    ollama_model: str | None = None,
    max_suggestions: int | None = None,
    timeout_seconds: float | None = None,
) -> PronunciationSuggestResult:
    """
    Ask Ollama for alternative spellings that sound correct when read aloud.
    """
    word = word.strip()
    if not word:
        return PronunciationSuggestResult([], error="Word is empty.")

    host = (ollama_host or config.OLLAMA_HOST).rstrip("/")
    requested_model = (ollama_model or config.OLLAMA_MODEL).strip()
    limit = max_suggestions or config.PRONUNCIATION_SUGGEST_COUNT
    timeout = timeout_seconds or config.PRONUNCIATION_OLLAMA_TIMEOUT

    model, resolve_msg = resolve_ollama_model(host, requested_model)
    if model is None:
        return PronunciationSuggestResult([], error=resolve_msg)

    context_block = ""
    if article_context and article_context.strip():
        snippet = article_context.strip()
        if len(snippet) > 800:
            snippet = snippet[:800] + "…"
        context_block = f"\nArticle context (for disambiguation):\n{snippet}\n"

    feedback_block = ""
    if user_feedback and user_feedback.strip():
        feedback_block = (
            f"\nUser feedback on previous samples (follow this closely):\n"
            f"{user_feedback.strip()}\n"
        )

    avoid_block = ""
    if avoid_spellings:
        avoid = [s.strip() for s in avoid_spellings if s.strip()]
        if avoid:
            avoid_block = (
                "\nDo NOT suggest these spellings (already tried):\n"
                + ", ".join(repr(s) for s in avoid)
                + "\n"
            )

    prompt = f"""You help fix text-to-speech mispronunciations.

The word or phrase "{word}" is mispronounced by a TTS engine when reading news articles.
{context_block}{feedback_block}{avoid_block}
Suggest {limit} alternative spellings that would sound correct when read aloud (same meaning).
Use phonetic spellings, hyphens, or spacing — not IPA symbols.
Do not repeat the original unless it is already the best option.

Return ONLY a JSON array of strings, e.g. ["spelling one", "spelling two"].
"""

    url = f"{host}/api/generate"
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                url,
                json={"model": model, "prompt": prompt, "stream": False},
            )
            if resp.status_code == 404:
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err = body.get("error") if isinstance(body, dict) else resp.text
                return PronunciationSuggestResult(
                    [],
                    error=f"Ollama model error: {err or resp.text}",
                )
            resp.raise_for_status()
            raw = (resp.json().get("response") or "").strip()
    except Exception as e:
        logger.warning("Ollama pronunciation suggest failed: %s", e)
        return PronunciationSuggestResult(
            [],
            error=f"Ollama request failed: {e}",
        )

    suggestions = _parse_suggestion_list(raw, word, limit)
    if avoid_spellings:
        avoid_keys = {s.casefold() for s in avoid_spellings}
        suggestions = [s for s in suggestions if s.casefold() not in avoid_keys]
    if not suggestions:
        return PronunciationSuggestResult(
            [],
            warning=resolve_msg,
            error=(
                "Ollama returned no usable spellings. "
                f"Try /pronounce {word} your-spelling"
            ),
        )
    return PronunciationSuggestResult(suggestions, warning=resolve_msg)


def _parse_suggestion_list(raw: str, original: str, limit: int) -> list[str]:
    candidates: list[str] = []
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    match = _JSON_ARRAY_RE.search(cleaned)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                candidates = [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass

    if not candidates:
        for line in cleaned.splitlines():
            line = line.strip().lstrip("-•*0123456789.) ").strip('"\'`,')
            if line and line.lower() != original.lower() and not line.startswith("["):
                candidates.append(line)

    seen: set[str] = set()
    out: list[str] = []
    for item in candidates:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out
