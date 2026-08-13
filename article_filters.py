"""Per-site phrases to strip from extracted article text."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import config
from domains_store import (
    host_allowed,
    is_valid_registrable_domain,
    normalize_registrable_hint,
    registrable_domain_from_url,
)

_lock = threading.Lock()
_PUNCT_ONLY_RE = re.compile(r"^[\s.,;:!?…\-'\"“”‘’—–-]*$")


def _default_path() -> Path:
    return config.ELIMINATE_PHRASES_FILE


def normalize_site_key(raw: str) -> str | None:
    """Turn user input (domain or URL) into a registrable domain."""
    text = raw.strip()
    if not text:
        return None
    if "://" not in text and "/" not in text:
        hint = normalize_registrable_hint(text)
        as_url = registrable_domain_from_url(f"https://{hint}")
        if as_url:
            return as_url
        return hint if is_valid_registrable_domain(hint) else None
    if "://" not in text:
        text = "https://" + text
    return registrable_domain_from_url(text)


def _norm_phrase(phrase: str) -> str:
    return " ".join(phrase.split()).casefold()


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    parts = [p for p in re.split(r"\s+", phrase.strip()) if p]
    if not parts:
        return re.compile(r"$^")
    body = r"\s+".join(re.escape(p) for p in parts)
    return re.compile(body, re.IGNORECASE)


def load_eliminate_phrases(path: Path | None = None) -> dict[str, list[str]]:
    path = (path or _default_path()).resolve()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    sites = raw.get("sites")
    if not isinstance(sites, dict):
        return {}
    out: dict[str, list[str]] = {}
    for domain, phrases in sites.items():
        key = normalize_site_key(str(domain))
        if not key or not isinstance(phrases, list):
            continue
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in phrases:
            phrase = " ".join(str(item).split())
            if not phrase:
                continue
            marker = _norm_phrase(phrase)
            if marker in seen:
                continue
            seen.add(marker)
            cleaned.append(phrase)
        if cleaned:
            out[key] = cleaned
    return out


def save_eliminate_phrases(sites: dict[str, list[str]], path: Path | None = None) -> Path:
    path = (path or _default_path()).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sites": {k: v for k, v in sorted(sites.items()) if v}}
    with _lock:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def add_eliminate_phrase(
    domain: str, phrase: str, *, path: Path | None = None
) -> bool:
    """Add a phrase for a site. Returns True if newly added."""
    key = normalize_site_key(domain)
    cleaned = " ".join(phrase.split())
    if not key or not cleaned:
        raise ValueError("website and phrase are required")
    sites = load_eliminate_phrases(path)
    existing = sites.get(key, [])
    marker = _norm_phrase(cleaned)
    for item in existing:
        if _norm_phrase(item) == marker:
            return False
    existing.append(cleaned)
    sites[key] = existing
    save_eliminate_phrases(sites, path)
    return True


def remove_eliminate_phrase(
    domain: str, phrase: str, *, path: Path | None = None
) -> bool:
    """Remove a phrase for a site (case-insensitive). Returns True if removed."""
    key = normalize_site_key(domain)
    cleaned = " ".join(phrase.split())
    if not key or not cleaned:
        raise ValueError("website and phrase are required")
    sites = load_eliminate_phrases(path)
    existing = sites.get(key, [])
    marker = _norm_phrase(cleaned)
    kept = [item for item in existing if _norm_phrase(item) != marker]
    if len(kept) == len(existing):
        return False
    if kept:
        sites[key] = kept
    else:
        sites.pop(key, None)
    save_eliminate_phrases(sites, path)
    return True


def phrases_for_url(source_url: str, *, path: Path | None = None) -> list[str]:
    host = ""
    try:
        from urllib.parse import urlparse

        host = (urlparse(source_url).hostname or "").lower().rstrip(".")
    except Exception:
        host = ""
    domain = registrable_domain_from_url(source_url) or host
    if not host and not domain:
        return []
    sites = load_eliminate_phrases(path)
    if not sites:
        return []
    matched: list[str] = []
    seen: set[str] = set()
    for site, phrases in sites.items():
        if host and host_allowed(host, {site}):
            pass
        elif domain == site:
            pass
        else:
            continue
        for phrase in phrases:
            marker = _norm_phrase(phrase)
            if marker in seen:
                continue
            seen.add(marker)
            matched.append(phrase)
    return matched


def strip_eliminated_phrases(
    text: str,
    source_url: str,
    *,
    path: Path | None = None,
    phrases: list[str] | None = None,
) -> str:
    """Remove configured site phrases from article text."""
    if not text or not text.strip():
        return text
    to_strip = phrases if phrases is not None else phrases_for_url(source_url, path=path)
    if not to_strip:
        return text
    compiled = sorted(
        ((_phrase_regex(p), p) for p in to_strip if p.strip()),
        key=lambda t: len(t[1]),
        reverse=True,
    )
    parts = [p.strip() for p in re.split(r"\n\s*\n+", text.strip()) if p.strip()]
    kept: list[str] = []
    for para in parts:
        out = para
        for pattern, _raw in compiled:
            if _norm_phrase(out) == _norm_phrase(_raw):
                out = ""
                break
            out = pattern.sub(" ", out)
        out = re.sub(r"[ \t]+", " ", out)
        out = re.sub(r"\s+([.,;:!?])", r"\1", out)
        out = out.strip(" \t-—–")
        if not out or _PUNCT_ONLY_RE.match(out):
            continue
        kept.append(out)
    return "\n\n".join(kept)
