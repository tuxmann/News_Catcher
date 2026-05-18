"""Overnight briefing pipeline entry logic."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

import httpx

import config
from briefing.config_loader import load_briefing_config
from briefing.drive import delete_files_older_than, upload_file
from briefing.fetch_articles import fetch_snippets
from briefing.ingest import collect_feed_items
from briefing.planner import ScriptBudget
from briefing.synthesize import synthesize_deep_dive_section, synthesize_script
from tts import synthesize_to_mp3

logger = logging.getLogger(__name__)


def _validate_briefing_env() -> None:
    if not config.GOOGLE_DRIVE_FOLDER_ID:
        raise SystemExit("Set GOOGLE_DRIVE_FOLDER_ID for briefing uploads.")
    if not config.GOOGLE_APPLICATION_CREDENTIALS:
        raise SystemExit("Set GOOGLE_APPLICATION_CREDENTIALS to a service account JSON file.")
    creds = Path(config.GOOGLE_APPLICATION_CREDENTIALS)
    if not creds.is_file():
        raise SystemExit(f"Credentials file not found: {creds}")


async def run_briefing_async() -> Path:
    _validate_briefing_env()
    briefing_cfg = load_briefing_config(config.BRIEFING_CONFIG_FILE)
    if not briefing_cfg.feeds:
        raise SystemExit(f"No feeds in {config.BRIEFING_CONFIG_FILE}")

    feed_items = collect_feed_items(briefing_cfg, config.DOMAINS_FILE)
    if not feed_items:
        raise RuntimeError("No feed items matched allowed domains.")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(config.FETCH_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as client:
        snippets = await fetch_snippets(feed_items, client)

    if not snippets:
        raise RuntimeError("No articles could be fetched and extracted.")

    budget = ScriptBudget(
        target_minutes=config.BRIEFING_TARGET_MINUTES,
        words_per_minute=config.BRIEFING_WORDS_PER_MINUTE,
    )
    script = synthesize_script(
        snippets,
        budget,
        ollama_host=config.OLLAMA_HOST,
        ollama_model=config.OLLAMA_MODEL,
        deep_dives=briefing_cfg.deep_dives,
    )

    for topic in briefing_cfg.deep_dives:
        try:
            section = synthesize_deep_dive_section(
                topic,
                snippets,
                ollama_host=config.OLLAMA_HOST,
                ollama_model=config.OLLAMA_MODEL,
            )
            script = f"{script}\n\nNow, a closer look at {topic}.\n\n{section}"
        except Exception as e:
            logger.warning("Deep dive failed for %s: %s", topic, e)

    config.BRIEFING_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    day = date.today().isoformat()
    base = config.BRIEFING_OUTPUT_DIR / f"briefing-{day}"
    mp3_path = base.with_suffix(".mp3")
    meta_path = base.with_suffix(".json")

    await asyncio.to_thread(
        synthesize_to_mp3,
        script,
        mp3_path,
        title=f"News briefing {day}",
    )

    meta = {
        "date": day,
        "target_minutes": budget.target_minutes,
        "word_count": len(script.split()),
        "sources": [{"title": s.title, "url": s.url, "source": s.source} for s in snippets],
        "deep_dives": briefing_cfg.deep_dives,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    upload_file(
        mp3_path,
        config.GOOGLE_DRIVE_FOLDER_ID,
        credentials_path=config.GOOGLE_APPLICATION_CREDENTIALS,
        remote_name=mp3_path.name,
    )
    upload_file(
        meta_path,
        config.GOOGLE_DRIVE_FOLDER_ID,
        credentials_path=config.GOOGLE_APPLICATION_CREDENTIALS,
        remote_name=meta_path.name,
    )

    removed = delete_files_older_than(
        config.GOOGLE_DRIVE_FOLDER_ID,
        credentials_path=config.GOOGLE_APPLICATION_CREDENTIALS,
        retention_days=config.BRIEFING_RETENTION_DAYS,
    )
    logger.info("Drive cleanup removed %s file(s) older than %s days", removed, config.BRIEFING_RETENTION_DAYS)
    return mp3_path


def run_briefing() -> Path:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    return asyncio.run(run_briefing_async())
