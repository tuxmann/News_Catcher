# News Catcher

<p align="center">
  <img src="docs/NewsCatcher_logo.png" alt="News Catcher — Telegram bot for news articles, text-to-speech, and audio briefings" width="420">
</p>

**News Catcher** is a self-hosted [Telegram](https://telegram.org) bot that turns news URLs into clean article text in your chat. It supports a domain allowlist, anti-bot fetching (including Playwright for Cloudflare sites), **listen-aloud** via [KittenTTS](https://github.com/KittenML/KittenTTS), saving articles for local experiments, and an optional **overnight audio briefing** pipeline (RSS + local LLM → Google Drive).

Designed for personal use: you control which publishers are allowed, who can use the bot, and where audio is stored.

### Find this project on GitHub

Search terms that describe this repo: **telegram news bot**, **article extractor**, **read later telegram**, **news text to speech**, **kittentts**, **trafilatura**, **readability**, **playwright scraping**, **self-hosted news reader**, **audio news briefing**, **python telegram bot**.

If you maintain a fork or publish this repo, add [Topics](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics) such as:

`telegram-bot` `telegram` `news` `news-aggregator` `article-extraction` `web-scraping` `playwright` `text-to-speech` `tts` `kittentts` `python` `python3` `self-hosted` `readability` `trafilatura` `rss` `ollama` `google-drive` `personal-assistant`

## Features

- URL in -> article text out through Telegram
- Extracts article images and sends each with caption (when available)
- Domain allowlist (approved domains only)
- Optional admin PIN for domain management commands
- Oversize download prompt (**Proceed / Cancel**) using soft and hard byte limits
- Paragraph-aware chunking for Telegram with `N of M` chunk headers
- Clear error messages for common anti-bot blocks (`401`, `403`)
- Patchright (stealth Playwright) fallback for Cloudflare-protected domains (default: `economist.com`, `marktechpost.com`)
- Startup online notification to allowed Telegram users
- **Speak last article**: after a URL, send `newscatcher, speak to me` for TTS audio (KittenTTS)
- **Overnight briefing** (optional): `python -m briefing` → Google Drive (`briefing.yaml`, see `.env.example`)

## Example Target Domains

Seeded in `domains.json` as examples (replace or extend for your own outlets):

- `economist.com`
- `reuters.com`
- `washingtonpost.com`
- `theintercept.com`
- `nypost.com`

You can add/remove domains at runtime with bot commands.

## Project Structure

- `News_bot.py` - Telegram handlers, command flow, chunking, and messaging
- `fetch.py` - HTTP fetching, redirects, limits, anti-SSRF checks, Playwright fallback trigger
- `fetch_playwright.py` - Headless Chromium fetch for challenge-protected pages
- `extract.py` - Article extraction (`trafilatura` + readability fallback)
- `domains_store.py` - Load/save domain allowlist and host matching
- `config.py` - Environment/config loading
- `domains.json` - Approved domains list
- `.env.example` - Example environment configuration
- `article_cache.py` - Last article per user (for TTS)
- `tts.py` - KittenTTS + ffmpeg MP3 generation
- `briefing/` - Overnight multi-source briefing → Google Drive
- `docs/NewsCatcher_logo.png` - Project logo (shown above)
- `docs/future_ideas.md` - Roadmap and design notes
- `tests/` - Unit tests for extraction, chunking, and domain checks

## Requirements

- Python 3.10+ (3.12 recommended)
- Linux/macOS/WSL (works on Linux Mint; Playwright may warn and use Ubuntu fallback build)
- Telegram bot token from BotFather
- Your Telegram numeric user ID

Python dependencies are listed in `requirements.txt`.

## Setup

1. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Install Patchright browser runtime (required for Economist/MarkTechPost/Cloudflare fallback):

```bash
patchright install chromium
```

4. Create your `.env` from `.env.example` and set values:

```bash
cp .env.example .env
```

Minimum required values:

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_TELEGRAM_USER_IDS` (comma-separated numeric IDs)

## Run the Bot

```bash
python News_bot.py
```

On startup, the bot sends an "online and ready" message to each allowed user ID.

## Bot Usage

Send any message containing a URL from an approved domain.

The bot will:

1. Validate URL and domain
2. Fetch article HTML
3. Extract readable text
4. Return text in Telegram-safe chunks
5. Send extracted article images with captions

Each chunk starts with:

```text
1 of X
```

### Speak last article (TTS)

1. Send a news URL and wait for the text reply.
2. Send: `newscatcher, speak to me` (case-insensitive).
3. The bot generates an MP3 with [KittenTTS](https://github.com/KittenML/KittenTTS) and sends it as Telegram audio.

### After each article

The bot shows inline buttons: **Speak to me** and **Save to disk**. You can tap those or type `newscatcher, speak to me` / `newscatcher, save to disk`.

**Save to disk** writes the last article body to `test_articles/` as a `.txt` file named from the **first six words** of the title (underscores between words). Override directory with `TEST_ARTICLES_DIR` in `.env`.

### Test article → audio (local experiments)

Run the interactive script (lists the 9 newest files in `TEST_ARTICLES_DIR`, then prompts for article and voice(s)):

```bash
python test_article_to_audio.py
```

TTS options (model, speed, etc.) are at the top of [`test_article_to_audio.py`](test_article_to_audio.py). Output MP3s are named like `article_stem_Jasper.mp3` next to the `.txt`.

List voices only: set `LIST_VOICES_ONLY = True` in that file and run again.

Requires `ffmpeg` on PATH, `espeak-ng` (or `espeak`), `TTS_ENABLED=1`, and **KittenTTS 0.8.1** from `requirements.txt` (not the older PyPI `kittentts` 0.1.x package).

```bash
pip install -r requirements.txt
```

Default model: `KittenML/kitten-tts-mini-0.8`. Voices: `Bella`, `Jasper`, `Luna`, `Bruno`, `Rosie`, `Hugo`, `Kiki`, `Leo` (set `TTS_VOICE` in `.env`).

#### TTS pronunciation normalization

KittenTTS cannot disambiguate homographs (e.g. **Polish** the country vs. nail polish) and often pauses on dotted abbreviations like **U.S.** because its internal chunker splits on periods. News Catcher applies your rules **before** synthesis.

1. Edit [`tts_replacements.json`](tts_replacements.json) in the project root (or point elsewhere with `TTS_REPLACEMENTS_FILE` in `.env`).
2. Keep `TTS_NORMALIZE_ENABLED=1` (default). Set to `0` to pass article text through unchanged.
3. Restart the bot or re-run `test_article_to_audio.py` after changing the JSON (rules are cached per process).

**File format:**

```json
{
  "replacements": [
    {"from": "U.S.", "to": "United States"},
    {"from": "U.K.", "to": "United Kingdom"}
  ],
  "regex": [
    {
      "pattern": "\\bPolish\\b(?=\\s+government)",
      "replace": "Poleish",
      "flags": "i"
    }
  ]
}
```

- **`replacements`** — simple find-and-replace strings, applied in file order (longer `from` strings are applied first automatically so `U.S.A.` wins over `U.S.`).
- **`regex`** — optional Python regex rules; use for context (country sense of *Polish* only before *government*, *army*, etc.). Optional `"flags": "i"` for case-insensitive.

Shipped defaults expand common abbreviations (`U.S.`, `U.K.`, `E.U.`, `U.N.`) and rewrite *Polish* before news-y following words to **Poleish** (a spelling KittenTTS reads as the country). Add your own entries for `Turkey`, `Jordan`, `Georgia`, outlet-specific names, etc.

**Try a phrase locally:**

```python
from tts_normalize import normalize_for_tts
print(normalize_for_tts("The Polish government and the U.S. envoy met."))
```

Normalization runs on Telegram **speak**, overnight **briefing** audio, and `test_article_to_audio.py` (all use `synthesize_to_mp3` in `tts.py`).

### Commands

- `/start` - intro/help
- `/list_domains` - show approved domains
- `/add_domain <domain>` or `/add_domain <PIN> <domain>` (if `ADMIN_PIN` is set)
- `/remove_domain <domain>` or `/remove_domain <PIN> <domain>` (if `ADMIN_PIN` is set)

## Security Model

- Only users in `ALLOWED_TELEGRAM_USER_IDS` can use the bot
- Optional `ADMIN_PIN` for sensitive domain changes
- HTTP fetch restricted to approved domains
- Redirects are revalidated against approved domains
- DNS/IP checks block private/loopback/link-local destinations

## Download Limits and Confirmation

- `FETCH_SOFT_MAX_BYTES`: normal max before asking
- `FETCH_HARD_MAX_BYTES`: absolute ceiling even after approval

If soft limit is exceeded, the bot prompts:

- **Proceed** - refetch with raised cap (up to hard max)
- **Cancel** - abort fetch

## Cloudflare / Anti-Bot Handling

Some sites (notably Economist) may return `HTTP 403` to plain HTTP clients.

On `HTTP 403`, the bot tries fallbacks in order:

1. **WordPress REST API** for domains in `WORDPRESS_API_DOMAINS` (default: `marktechpost.com`) — no browser required.
2. **Headless Chromium** (patchright) for domains in `PLAYWRIGHT_FALLBACK_DOMAINS`.

Config:

- `WORDPRESS_API_DOMAINS` (default when omitted: `marktechpost.com`)
- `PLAYWRIGHT_FALLBACK_DOMAINS` (default when omitted: `economist.com`, `marktechpost.com`)
- `PLAYWRIGHT_TIMEOUT_MS` (default: `60000`)
- `PLAYWRIGHT_CHANNEL` (optional; defaults to `chrome` when `google-chrome` is installed)

To disable fallback, set:

```env
PLAYWRIGHT_FALLBACK_DOMAINS=
```

## Overnight briefing (Phase 2)

1. Copy `briefing.yaml.example` → `briefing.yaml` and set RSS feeds.
2. Create a Google Cloud service account with Drive API access; share your target Drive folder with the service account email.
3. Set in `.env`: `GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, `OLLAMA_HOST`, `OLLAMA_MODEL`.
4. Run locally or via cron:

```bash
python -m briefing
```

Uploads `briefing-YYYY-MM-DD.mp3` (and metadata JSON) to your Drive folder and deletes files older than `BRIEFING_RETENTION_DAYS` (default 7).

Example cron (3 AM daily):

```cron
0 3 * * * cd /path/to/News_Catcher && .venv/bin/python -m briefing >> logs/briefing.log 2>&1
```

## Testing

Run tests:

```bash
PYTHONPATH=. python -m unittest discover -s tests -v
```

Includes tests for:

- extraction output
- paragraph/chunk formatting
- domain allowlist matching

## Troubleshooting

### `HTTP 401` (commonly Reuters)

- Use a browser-like `USER_AGENT` (default in `.env.example`)
- Do not append a custom bot token/name to the UA string

### `HTTP 403` (commonly Economist, MarkTechPost)

- Install Patchright browser:
  - `patchright install chromium`
- Ensure the domain is in `PLAYWRIGHT_FALLBACK_DOMAINS` (marktechpost.com is included by default)

### Patchright warning on Linux Mint

You may see:

`BEWARE: your OS is not officially supported ... downloading fallback build`

This is expected on Mint and usually works fine.

## Important Security Note

If a bot token is ever exposed, revoke and rotate it immediately in BotFather.

## License and contributing

Licensed under the [GNU General Public License v3.0](LICENSE) (GPL-3.0).

This project is intended to be forked and adapted for your own Telegram user IDs and domain list. Start from [Setup](#setup), copy `.env.example` to `.env`, and adjust `domains.json` for the news sites you read.

Issues and pull requests are welcome if you improve extraction, TTS quality, or briefing workflows for the community.
