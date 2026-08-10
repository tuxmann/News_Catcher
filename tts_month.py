"""Expand month abbreviations (Jan → January) with Ollama context checks."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

import config
from ollama_util import resolve_ollama_model
from tts_acronym import is_word_covered_by_literal_rules, sentence_triplet_at_offset

if TYPE_CHECKING:
    from tts_normalize import TtsReplacementRules

logger = logging.getLogger(__name__)

_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*?\}")

_MONTH_ABBREVS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Sept",
    "Oct",
    "Nov",
    "Dec",
)

_MONTH_ABBREV_RE = re.compile(
    r"\b(" + "|".join(_MONTH_ABBREVS) + r")(\.?)(?=\s|$|[,;:]|\d)",
    re.IGNORECASE,
)

_FULL_MONTH_NAMES = frozenset(
    {
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    }
)

_MONTH_EXPAND: dict[str, str] = {
    "jan": "January",
    "feb": "February",
    "mar": "March",
    "apr": "April",
    "may": "May",
    "jun": "June",
    "jul": "July",
    "aug": "August",
    "sep": "September",
    "sept": "September",
    "oct": "October",
    "nov": "November",
    "dec": "December",
}

_DATE_AFTER_RE = re.compile(
    r"\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{2,4})?",
    re.IGNORECASE,
)


def month_full_name(abbrev: str) -> str:
    key = abbrev.rstrip(".").casefold()
    return _MONTH_EXPAND.get(key, abbrev)


def is_full_month_name(word: str) -> bool:
    return word.casefold() in _FULL_MONTH_NAMES


def looks_like_month_date_following(text: str, match_end: int) -> bool:
    """True when abbrev is immediately followed by a day (e.g. Jan 21)."""
    return bool(_DATE_AFTER_RE.match(text[match_end:]))


def _parse_is_month(raw: str) -> bool | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    match = _JSON_OBJECT_RE.search(cleaned)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict) and "is_month" in parsed:
                return bool(parsed["is_month"])
        except json.JSONDecodeError:
            pass

    lowered = cleaned.casefold()
    if '"is_month": true' in lowered or '"is_month":true' in lowered:
        return True
    if '"is_month": false' in lowered or '"is_month":false' in lowered:
        return False
    return None


def classify_month_abbrev_ollama(
    token: str,
    *,
    previous_sentence: str | None,
    current_sentence: str,
    next_sentence: str | None,
    ollama_host: str | None = None,
    ollama_model: str | None = None,
    timeout_seconds: float | None = None,
) -> bool | None:
    """
    Ask local Ollama whether a token is a calendar month (e.g. Jan in Jan 21).

    Returns True (month), False (name/other), or None when Ollama is unavailable.
    """
    host = (ollama_host or config.OLLAMA_HOST).rstrip("/")
    requested_model = (ollama_model or config.OLLAMA_MODEL).strip()
    timeout = timeout_seconds or config.TTS_MONTH_OLLAMA_TIMEOUT

    model, resolve_msg = resolve_ollama_model(host, requested_model)
    if model is None:
        if resolve_msg:
            logger.warning("Month classify skipped: %s", resolve_msg)
        return None
    if resolve_msg:
        logger.info("%s", resolve_msg)

    prev_block = previous_sentence.strip() if previous_sentence else "(none)"
    next_block = next_sentence.strip() if next_sentence else "(none)"

    prompt = f"""You classify month-like tokens in news articles for text-to-speech.

A MONTH should be read as the full month name (e.g. Jan 21 → January 21, Mar 3 → March 3).
NOT A MONTH means a person's name, place, verb, or other word (e.g. Jan in "Meet Jan Smith", May in "you may leave").

Previous sentence: {prev_block}
Sentence with token: {current_sentence.strip()}
Next sentence: {next_block}

Token to classify: {token}

Reply with ONLY JSON: {{"is_month": true}} or {{"is_month": false}}
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
        logger.warning("Ollama month classify failed for %r: %s", token, e)
        return None

    result = _parse_is_month(raw)
    if result is None:
        logger.warning("Ollama month classify unreadable for %r: %s", token, raw[:200])
    return result


def apply_month_abbreviations(
    text: str,
    rules: TtsReplacementRules,
    *,
    enabled: bool | None = None,
    ollama_enabled: bool | None = None,
    classifier: Callable[[str, str | None, str, str | None], bool | None] | None = None,
) -> str:
    """
    Expand month abbreviations (Jan → January) when:
    - no literal rule exists in tts_replacements.json, and
    - Ollama (or injected classifier) confirms a calendar month, or
    - the token is followed by a day number and Ollama is unavailable.
    """
    if not text or not text.strip():
        return text
    if enabled is None:
        enabled = config.TTS_MONTH_ENABLED
    if not enabled:
        return text
    if ollama_enabled is None:
        ollama_enabled = config.TTS_MONTH_OLLAMA_ENABLED

    def default_classifier(
        token: str,
        before: str | None,
        current: str,
        after: str | None,
    ) -> bool | None:
        if not ollama_enabled:
            return None
        return classify_month_abbrev_ollama(
            token,
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

        for match in _MONTH_ABBREV_RE.finditer(part):
            abbrev = match.group(1)
            if is_full_month_name(abbrev):
                continue
            if is_word_covered_by_literal_rules(abbrev, rules):
                continue

            before, current, after = sentence_triplet_at_offset(part, match.start())
            cache_key = (abbrev, before or "", current, after or "")
            if cache_key not in context_cache:
                context_cache[cache_key] = decide(abbrev, before, current, after)

            is_month = context_cache[cache_key]
            if is_month is None and config.TTS_MONTH_DATE_FALLBACK:
                is_month = looks_like_month_date_following(part, match.end())

            if is_month:
                span_replacements.append(
                    (
                        offset_base + match.start(),
                        offset_base + match.end(),
                        month_full_name(abbrev),
                    )
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
