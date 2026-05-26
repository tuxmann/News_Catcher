"""Load settings from environment."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off", ""):
        return default
    return default


PROJECT_ROOT = Path(__file__).resolve().parent

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_TELEGRAM_USER_IDS: frozenset[int] = frozenset(
    int(x.strip())
    for x in os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "").split(",")
    if x.strip().isdigit()
)
ADMIN_PIN = os.environ.get("ADMIN_PIN", "").strip()
DOMAINS_FILE = Path(os.environ.get("DOMAINS_FILE", "domains.json"))
if not DOMAINS_FILE.is_absolute():
    DOMAINS_FILE = PROJECT_ROOT / DOMAINS_FILE

FETCH_SOFT_MAX_BYTES = _int("FETCH_SOFT_MAX_BYTES", 5 * 1024 * 1024)
FETCH_HARD_MAX_BYTES = _int("FETCH_HARD_MAX_BYTES", 20 * 1024 * 1024)
FETCH_TIMEOUT_SECONDS = _int("FETCH_TIMEOUT_SECONDS", 20)
MAX_REDIRECTS = _int("MAX_REDIRECTS", 5)
ALLOW_HTTP = _bool("ALLOW_HTTP", False)
# Default matches a normal desktop Chrome build. Short/custom UAs often get HTTP 401 from
# anti-bot layers (e.g. Reuters/DataDome). Do not append a bot product name to the UA string.
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
).strip()

# Max length per Telegram body message (including "N of M" header); hard cap 4096.
TELEGRAM_CHUNK_CHARS = 4000

# Capture and send extracted article images/captions in Telegram responses.
CAPTURE_ARTICLE_IMAGES = _bool("CAPTURE_ARTICLE_IMAGES", False)

# Pending oversize confirmation TTL (seconds)
PENDING_OVERSIZE_TTL = 300

# If plain HTTP gets 403, retry with headless Chromium for these registrable domains
# (Cloudflare "challenge" pages). Unset = economist.com,marktechpost.com; empty = off.
_pw_raw = os.environ.get("PLAYWRIGHT_FALLBACK_DOMAINS")
if _pw_raw is None:
    _pw_domains = ["economist.com", "marktechpost.com"]
else:
    # Strip optional surrounding quotes (dotenv may leave them when value contains commas).
    _pw_raw = _pw_raw.strip().strip('"').strip("'")
    _pw_domains = [x.strip().lower() for x in _pw_raw.split(",") if x.strip()]
PLAYWRIGHT_FALLBACK_DOMAINS: frozenset[str] = frozenset(_pw_domains)
PLAYWRIGHT_TIMEOUT_MS = _int("PLAYWRIGHT_TIMEOUT_MS", 60_000)
# Optional system Chrome channel for patchright/playwright (e.g. "chrome"). Empty = bundled Chromium.
# When unset, use system Google Chrome if installed (better Cloudflare pass rate).
_pw_channel_raw = os.environ.get("PLAYWRIGHT_CHANNEL")
if _pw_channel_raw is None:
    PLAYWRIGHT_CHANNEL = "chrome" if shutil.which("google-chrome") else ""
else:
    PLAYWRIGHT_CHANNEL = _pw_channel_raw.strip()

# On HTTP 403, try WordPress REST API before headless browser (no Cloudflare on many hosts).
_wp_api_raw = os.environ.get("WORDPRESS_API_DOMAINS")
if _wp_api_raw is None:
    _wp_api_domains = ["marktechpost.com"]
else:
    _wp_api_raw = _wp_api_raw.strip().strip('"').strip("'")
    _wp_api_domains = [x.strip().lower() for x in _wp_api_raw.split(",") if x.strip()]
WORDPRESS_API_DOMAINS: frozenset[str] = frozenset(_wp_api_domains)

# Phase 1: speak last article (TTS + Telegram audio)
TTS_ENABLED = _bool("TTS_ENABLED", True)
TTS_MODEL = os.environ.get("TTS_MODEL", "KittenML/kitten-tts-mini-0.8").strip()
TTS_VOICE = os.environ.get("TTS_VOICE", "Jasper").strip()
TTS_CHUNK_CHARS = _int("TTS_CHUNK_CHARS", 3500)
TTS_NORMALIZE_ENABLED = _bool("TTS_NORMALIZE_ENABLED", True)
TTS_REPLACEMENTS_FILE = Path(os.environ.get("TTS_REPLACEMENTS_FILE", "tts_replacements.json"))
if not TTS_REPLACEMENTS_FILE.is_absolute():
    TTS_REPLACEMENTS_FILE = PROJECT_ROOT / TTS_REPLACEMENTS_FILE
LAST_ARTICLE_TTL_SECONDS = _int("LAST_ARTICLE_TTL_SECONDS", 72 * 3600)
ARTICLE_CACHE_DIR = Path(os.environ.get("ARTICLE_CACHE_DIR", "data/article_cache"))
if not ARTICLE_CACHE_DIR.is_absolute():
    ARTICLE_CACHE_DIR = PROJECT_ROOT / ARTICLE_CACHE_DIR
TEST_ARTICLES_DIR = Path(os.environ.get("TEST_ARTICLES_DIR", "test_articles"))
if not TEST_ARTICLES_DIR.is_absolute():
    TEST_ARTICLES_DIR = PROJECT_ROOT / TEST_ARTICLES_DIR


def ensure_test_articles_dir() -> Path:
    """Create TEST_ARTICLES_DIR if missing; return the path."""
    TEST_ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    return TEST_ARTICLES_DIR
AUDIO_OUTPUT_DIR = Path(os.environ.get("AUDIO_OUTPUT_DIR", "data/audio"))
if not AUDIO_OUTPUT_DIR.is_absolute():
    AUDIO_OUTPUT_DIR = PROJECT_ROOT / AUDIO_OUTPUT_DIR
# Telegram send_audio limit (bytes); split into parts if larger
TELEGRAM_AUDIO_MAX_BYTES = _int("TELEGRAM_AUDIO_MAX_BYTES", 48 * 1024 * 1024)

# Phase 2: overnight briefing → Google Drive
BRIEFING_ENABLED = _bool("BRIEFING_ENABLED", False)
BRIEFING_CONFIG_FILE = Path(os.environ.get("BRIEFING_CONFIG_FILE", "briefing.yaml"))
if not BRIEFING_CONFIG_FILE.is_absolute():
    BRIEFING_CONFIG_FILE = PROJECT_ROOT / BRIEFING_CONFIG_FILE
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
BRIEFING_RETENTION_DAYS = _int("BRIEFING_RETENTION_DAYS", 7)
BRIEFING_OUTPUT_DIR = Path(os.environ.get("BRIEFING_OUTPUT_DIR", "data/briefings"))
if not BRIEFING_OUTPUT_DIR.is_absolute():
    BRIEFING_OUTPUT_DIR = PROJECT_ROOT / BRIEFING_OUTPUT_DIR
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2").strip()
BRIEFING_TARGET_MINUTES = _int("BRIEFING_TARGET_MINUTES", 60)
BRIEFING_WORDS_PER_MINUTE = _int("BRIEFING_WORDS_PER_MINUTE", 150)
