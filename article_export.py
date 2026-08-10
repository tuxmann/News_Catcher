"""Export last cached article to disk for TTS experiments."""

from __future__ import annotations

import re
from pathlib import Path

from article_cache import CachedArticle

SAVE_TO_DISK_PHRASE_RE = re.compile(
    r"^newscatcher\s*,?\s*save\s+to\s+disk\s*$",
    re.IGNORECASE,
)


def is_save_to_disk_phrase(text: str) -> bool:
    return bool(SAVE_TO_DISK_PHRASE_RE.match(text.strip()))


def title_to_filename_stem(title: str | None, *, max_words: int = 6) -> str:
    """First `max_words` title words joined with underscores (filesystem-safe)."""
    if not title or not title.strip():
        return "untitled"
    words = re.findall(r"\w+", title.strip(), flags=re.UNICODE)[:max_words]
    if not words:
        return "untitled"
    stem = "_".join(words)
    return stem[:200]


def site_to_filename_stem(source_domain: str | None) -> str:
    """Filesystem-safe site label for audio filenames (e.g. reuters.com → reuters_com)."""
    if not source_domain or not source_domain.strip():
        return "article"
    raw = source_domain.strip().lower().rstrip(".")
    if raw.startswith("www."):
        raw = raw[4:]
    stem = re.sub(r"[^\w]+", "_", raw, flags=re.UNICODE).strip("_")
    return (stem or "article")[:60]


def telegram_audio_filename(
    source_domain: str | None,
    title: str | None,
    *,
    part: int | None = None,
    total: int = 1,
) -> str:
    """Download name for Telegram send_audio: site_title.mp3 (optional part suffix)."""
    base = f"{site_to_filename_stem(source_domain)}_{title_to_filename_stem(title)}"
    if total > 1 and part is not None:
        return f"{base}_part{part}.mp3"
    return f"{base}.mp3"


def save_article_text_file(article: CachedArticle, output_dir: Path) -> Path:
    """Write article body to output_dir; return path used."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = title_to_filename_stem(article.title)
    path = output_dir / f"{stem}.txt"
    if path.exists():
        n = 2
        while True:
            candidate = output_dir / f"{stem}_{n}.txt"
            if not candidate.exists():
                path = candidate
                break
            n += 1
    path.write_text(article.text, encoding="utf-8")
    return path
