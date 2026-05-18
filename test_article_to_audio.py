#!/usr/bin/env python3
"""
Experiment: convert saved test articles (.txt) to MP3 with KittenTTS.

Scans TEST_ARTICLES_DIR (from .env / config), shows the 9 newest .txt files,
lets you pick an article and voice(s), then generates audio.

Run from the project root:

    python test_article_to_audio.py

Requires: KittenTTS 0.8.1 (see requirements.txt), ffmpeg on PATH, espeak-ng.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import config

# ---------------------------------------------------------------------------
# SETTINGS — TTS defaults (same meaning as .env.example / Telegram bot)
# ---------------------------------------------------------------------------

# Hugging Face model repo (must be KittenTTS 0.8.x). Examples:
#   KittenML/kitten-tts-mini-0.8        — default in .env; best quality of the small set
#   KittenML/kitten-tts-micro-0.8
#   KittenML/kitten-tts-nano-0.8
#   KittenML/kitten-tts-nano-0.8-int8   — smallest / fastest
TTS_MODEL = "KittenML/kitten-tts-mini-0.8"

# Speech rate. 1.0 = normal; below 1.0 slower, above 1.0 faster (e.g. 0.9, 1.15).
TTS_SPEED = 1.0

# If True, KittenTTS preprocesses text (numbers → words, etc.) before speaking.
TTS_CLEAN_TEXT = True

# Max characters per synthesis chunk before ffmpeg concat (bot default: 3500).
TTS_CHUNK_CHARS = 3500

# If True, prepend a spoken title before the body (derived from the filename stem).
PREPEND_TITLE = False

# How many recent articles to list for selection.
MAX_RECENT_ARTICLES = 9

# Set True to print voices for TTS_MODEL and exit (no prompts).
LIST_VOICES_ONLY = False

# ---------------------------------------------------------------------------
# End of settings
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _recent_articles(articles_dir: Path, limit: int) -> tuple[list[Path], int]:
    """Newest-first .txt files; returns (up to `limit` paths, total .txt count)."""
    if not articles_dir.is_dir():
        return [], 0
    all_txt = [p for p in articles_dir.glob("*.txt") if p.is_file()]
    all_txt.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return all_txt[:limit], len(all_txt)


def _parse_index_list(raw: str, max_index: int) -> list[int]:
    """Parse '1,3,4' or '1 3 4' into 1-based indices; raises ValueError on bad input."""
    parts = re.split(r"[\s,]+", raw.strip())
    if not parts or parts == [""]:
        raise ValueError("empty selection")
    indices: list[int] = []
    seen: set[int] = set()
    for part in parts:
        if not part.isdigit():
            raise ValueError(f"not a number: {part!r}")
        n = int(part)
        if n < 1 or n > max_index:
            raise ValueError(f"out of range 1–{max_index}: {n}")
        if n not in seen:
            seen.add(n)
            indices.append(n)
    return indices


def _prompt_line(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(130) from None


def _choose_article(articles: list[Path], total_count: int) -> Path:
    print(f"\nArticles in {config.TEST_ARTICLES_DIR.resolve()} (newest first):\n")
    for i, path in enumerate(articles, start=1):
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {i}. {path.name}  ({when})")

    if total_count > len(articles):
        extra = total_count - len(articles)
        print(
            f"\nNote: {total_count} article(s) in this folder; showing the "
            f"{len(articles)} most recent only. Delete older .txt files from "
            f"{config.TEST_ARTICLES_DIR} if you need to pick one that is not listed."
        )

    while True:
        raw = _prompt_line(f"\nWhich article? Enter 1–{len(articles)}: ")
        try:
            picked = _parse_index_list(raw, len(articles))
        except ValueError as e:
            print(f"  Invalid input ({e}). Example: 2")
            continue
        if len(picked) != 1:
            print("  Pick exactly one article number.")
            continue
        return articles[picked[0] - 1]


def _choose_voices(voices: list[str]) -> list[str]:
    print("\nVoices (same names as TTS_VOICE in .env.example):\n")
    for i, name in enumerate(voices, start=1):
        print(f"  {i}. {name}")

    while True:
        mode = _prompt_line(
            "\nGenerate audio for (a) all voices or (s) selected voices? [a/s]: "
        ).lower()
        if mode in ("a", "all"):
            return list(voices)
        if mode not in ("s", "select", "selected", ""):
            print("  Enter 'a' for all or 's' for selected.")
            continue

        raw = _prompt_line(
            f"Which voices? Enter numbers 1–{len(voices)}, e.g. 1,3,4,5,6: "
        )
        try:
            indices = _parse_index_list(raw, len(voices))
        except ValueError as e:
            print(f"  Invalid input ({e}). Example: 1,3,4")
            continue
        if not indices:
            print("  Select at least one voice.")
            continue
        return [voices[i - 1] for i in indices]


def _output_path_for_voice(article_path: Path, voice: str) -> Path:
    return article_path.with_name(f"{article_path.stem}_{voice}.mp3")


def _title_from_input_path(input_file: Path) -> str:
    return input_file.stem.replace("_", " ")


def main() -> int:
    from tts import list_available_voices, synthesize_to_mp3

    articles_dir = config.ensure_test_articles_dir()

    if LIST_VOICES_ONLY:
        voices = list_available_voices(TTS_MODEL)
        print(f"Voices for {TTS_MODEL}:")
        for name in voices:
            print(f"  - {name}")
        return 0

    articles, total = _recent_articles(articles_dir, MAX_RECENT_ARTICLES)
    if not articles:
        logger.error(
            "No .txt files in %s. Save an article from Telegram first.",
            articles_dir.resolve(),
        )
        return 1

    article_path = _choose_article(articles, total)
    text = article_path.read_text(encoding="utf-8").strip()
    if not text:
        logger.error("Article file is empty: %s", article_path)
        return 1

    voices = list_available_voices(TTS_MODEL)
    if not voices:
        logger.error("No voices reported for model %s", TTS_MODEL)
        return 1

    selected_voices = _choose_voices(voices)
    title = _title_from_input_path(article_path) if PREPEND_TITLE else None

    print(f"\nUsing model={TTS_MODEL} speed={TTS_SPEED} clean_text={TTS_CLEAN_TEXT}")
    print(f"Article: {article_path.name}\n")

    for voice in selected_voices:
        out_path = _output_path_for_voice(article_path, voice)
        logger.info("Synthesizing voice=%s → %s", voice, out_path)
        synthesize_to_mp3(
            text,
            out_path,
            title=title,
            model_name=TTS_MODEL,
            voice=voice,
            speed=TTS_SPEED,
            clean_text=TTS_CLEAN_TEXT,
            chunk_chars=TTS_CHUNK_CHARS,
            skip_enabled_check=True,
        )
        print(f"  Done: {out_path.resolve()}")

    print(f"\nFinished {len(selected_voices)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
