"""Detect short ALL-CAPS tokens and hyphenate confirmed acronyms for TTS."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

import config
from ollama_util import resolve_ollama_model

if TYPE_CHECKING:
    from tts_normalize import TtsReplacementRules

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")

_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*?\}")


def is_word_covered_by_literal_rules(word: str, rules: TtsReplacementRules) -> bool:
    """True when tts_replacements.json already defines this token."""
    for rule in rules.literals:
        if rule.ignore_case:
            if rule.from_text.casefold() == word.casefold():
                return True
        elif rule.from_text == word:
            return True
    return False


def letter_hyphenate(word: str) -> str:
    """KTAR → K-T-A-R."""
    return "-".join(word)


def split_sentences(paragraph: str) -> list[str]:
    text = paragraph.strip()
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p for p in parts if p]


def sentence_triplet_at_offset(
    paragraph: str, offset: int
) -> tuple[str | None, str, str | None]:
    """Return (previous, current, next) sentence within one paragraph."""
    sentences = split_sentences(paragraph)
    if not sentences:
        return None, paragraph, None

    pos = 0
    idx = 0
    for i, sent in enumerate(sentences):
        start = paragraph.find(sent, pos)
        if start < 0:
            start = pos
        end = start + len(sent)
        if start <= offset < end:
            idx = i
            break
        pos = max(pos, end)

    before = sentences[idx - 1] if idx > 0 else None
    current = sentences[idx]
    after = sentences[idx + 1] if idx + 1 < len(sentences) else None
    return before, current, after


def _parse_is_acronym(raw: str) -> bool | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    match = _JSON_OBJECT_RE.search(cleaned)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict) and "is_acronym" in parsed:
                return bool(parsed["is_acronym"])
        except json.JSONDecodeError:
            pass

    lowered = cleaned.casefold()
    if '"is_acronym": true' in lowered or '"is_acronym":true' in lowered:
        return True
    if '"is_acronym": false' in lowered or '"is_acronym":false' in lowered:
        return False
    if re.search(r"\byes\b", lowered) and "acronym" in lowered:
        return True
    if re.search(r"\bno\b", lowered) and "acronym" in lowered:
        return False
    return None


def classify_caps_acronym_ollama(
    word: str,
    *,
    previous_sentence: str | None,
    current_sentence: str,
    next_sentence: str | None,
    ollama_host: str | None = None,
    ollama_model: str | None = None,
    timeout_seconds: float | None = None,
) -> bool | None:
    """
    Ask local Ollama whether an ALL-CAPS token should be read letter-by-letter.

    Returns True (acronym), False (normal word), or None when Ollama is unavailable.
    """
    host = (ollama_host or config.OLLAMA_HOST).rstrip("/")
    requested_model = (ollama_model or config.OLLAMA_MODEL).strip()
    timeout = timeout_seconds or config.TTS_ACRONYM_OLLAMA_TIMEOUT

    model, resolve_msg = resolve_ollama_model(host, requested_model)
    if model is None:
        if resolve_msg:
            logger.warning("Acronym classify skipped: %s", resolve_msg)
        return None
    if resolve_msg:
        logger.info("%s", resolve_msg)

    prev_block = previous_sentence.strip() if previous_sentence else "(none)"
    next_block = next_sentence.strip() if next_sentence else "(none)"

    prompt = f"""You classify ALL-CAPS tokens in news articles for text-to-speech.

An ACRONYM should be read letter-by-letter (e.g. FBI → F-B-I, KTAR → K-T-A-R).
A NORMAL WORD is emphasis, a verb, or a regular word in caps (e.g. WIN in "We want to WIN").

Previous sentence: {prev_block}
Sentence with token: {current_sentence.strip()}
Next sentence: {next_block}

Token to classify: {word}

Reply with ONLY JSON: {{"is_acronym": true}} or {{"is_acronym": false}}
"""

    url = f"{host}/api/generate"
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                url,
                json={"model": model, "prompt": prompt, "stream": False},
            )
            resp.raise_for_status()
            raw = (resp.json().get("response") or "").strip()
    except Exception as e:
        logger.warning("Ollama acronym classify failed for %r: %s", word, e)
        return None

    result = _parse_is_acronym(raw)
    if result is None:
        logger.warning("Ollama acronym classify unreadable for %r: %s", word, raw[:200])
    return result


def apply_short_caps_acronyms(
    text: str,
    rules: TtsReplacementRules,
    *,
    enabled: bool | None = None,
    ollama_enabled: bool | None = None,
    max_letters: int | None = None,
    classifier: Callable[[str, str | None, str, str | None], bool | None] | None = None,
) -> str:
    """
    Hyphenate short ALL-CAPS tokens (e.g. KTAR → K-T-A-R) when:
    - no literal rule exists in tts_replacements.json, and
    - Ollama (or injected classifier) confirms the token is an acronym.
    """
    if not text or not text.strip():
        return text
    if enabled is None:
        enabled = config.TTS_ACRONYM_ENABLED
    if not enabled:
        return text
    if ollama_enabled is None:
        ollama_enabled = config.TTS_ACRONYM_OLLAMA_ENABLED

    max_len = max_letters if max_letters is not None else config.TTS_ACRONYM_MAX_LETTERS
    caps_re = re.compile(rf"\b[A-Z]{{{config.TTS_ACRONYM_MIN_LETTERS},{max_len}}}\b")

    def default_classifier(
        word: str,
        before: str | None,
        current: str,
        after: str | None,
    ) -> bool | None:
        if not ollama_enabled:
            return None
        return classify_caps_acronym_ollama(
            word,
            previous_sentence=before,
            current_sentence=current,
            next_sentence=after,
        )

    decide = classifier or default_classifier
    context_cache: dict[tuple[str, str, str, str], bool | None] = {}
    span_replacements: list[tuple[int, int, str]] = []

    parts = re.split(r"(\n\n+)", text)
    offset_base = 0
    for part in parts:
        if part.startswith("\n"):
            offset_base += len(part)
            continue

        for match in caps_re.finditer(part):
            word = match.group(0)
            if is_word_covered_by_literal_rules(word, rules):
                continue

            before, current, after = sentence_triplet_at_offset(part, match.start())
            cache_key = (
                word,
                before or "",
                current,
                after or "",
            )
            if cache_key not in context_cache:
                context_cache[cache_key] = decide(word, before, current, after)
            if context_cache[cache_key]:
                span_replacements.append(
                    (offset_base + match.start(), offset_base + match.end(), letter_hyphenate(word))
                )

        offset_base += len(part)

    if not span_replacements:
        return text

    span_replacements.sort(key=lambda item: item[0])
    out: list[str] = []
    last = 0
    for start, end, replacement in span_replacements:
        if start < last:
            continue
        out.append(text[last:start])
        out.append(replacement)
        last = end
    out.append(text[last:])
    return "".join(out)
