# News Catcher

<p align="center">
  <img src="docs/NewsCatcher_logo.png" alt="News Catcher — Telegram bot for news articles, text-to-speech, and audio briefings" width="420">
</p>

**News Catcher** is a self-hosted [Telegram](https://telegram.org) bot that turns news URLs into clean article text in your chat. It supports a domain allowlist, anti-bot fetching (including Playwright for Cloudflare sites), **listen-aloud** via [KittenTTS](https://github.com/KittenML/KittenTTS), saving articles for local experiments, **deep research** (Google News → local [Ollama](https://ollama.com) article + TTS), and an optional **overnight audio briefing** pipeline (RSS + local LLM → Google Drive).

A **desktop GUI** (`gui_app.py`) offers the same fetch, research, speak, and pronunciation tools without Telegram.

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
- **Deep research**: conversational `/research` — topic, article count (10/25/50), length preset → Ollama writes a neutral news article → follow-up Q&A → TTS with Deep research outro
- **Blog watchlist**: poll RSS/WordPress for new posts; do-not-disturb window (default 12am–4am local); Read or Speak digests in Telegram
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
- `gui_app.py` / `gui_service.py` - Desktop GUI (fetch, deep research, speak, Test & Fix)
- `research.py` / `google_news.py` - Deep research: Google News ingest, URL decode, Ollama synthesis
- `fetch.py` - HTTP fetching, redirects, limits, anti-SSRF checks, Playwright fallback trigger
- `fetch_playwright.py` - Headless Chromium fetch for challenge-protected pages
- `extract.py` - Article extraction (`trafilatura` + readability fallback)
- `domains_store.py` - Load/save domain allowlist and host matching
- `config.py` - Environment/config loading
- `domains.json` - Approved domains list
- `data/watchlist.json` - Watched blogs (created at runtime)
- `watchlist.py` / `watchlist_store.py` - Blog watchlist: RSS/WordPress polling, DND quiet hours
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
- Telegram bot token from BotFather (Telegram mode only)
- Your Telegram numeric user ID (Telegram mode only)
- [Ollama](https://ollama.com) for deep research and optional overnight briefing (`OLLAMA_HOST`, `OLLAMA_MODEL`)

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

## Run the desktop GUI

```bash
python gui_app.py
```

Same fetch, deep research, speak, and Test & Fix pronunciation tools as Telegram, without a bot token.

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
3. Save `tts_replacements.json` — the bot reloads it automatically on the next speak (no restart needed).

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

**Try spellings in Telegram** (writes to `tts_replacements.json`):

```text
/speak
```

The bot asks for a test sentence, sends audio, then starts **fix-a-word** so you can tune what sounded wrong (same flow as `/fixaword` after article audio).

```text
/pronounce Polish
/pronounce Polish Poleish Pole-ish
```

With one word, Ollama suggests spellings. You get a short audio clip per spelling; tap **Save** on the one you want. Without audio:

```text
/add_pronunciation Polish Poleish
```

After a full **article** speak, tap **Fix a word** or send `/fixaword`.

Article **speak** runs in the background — you can fetch the next URL while audio is generating.

### Deep research

Deep research collects recent headlines from **Google News** (topic search or a **Full Coverage** link), downloads articles from your `domains.json` allowlist, and asks **Ollama** to write a **neutral, just-the-facts news article or essay** in flowing prose (not bullet lists). Sources are listed at the bottom of the text reply.

**Telegram (conversational)**

```text
/research
```

The bot asks:

1. **Topic** — phrase or Google News Full Coverage URL (`news.google.com/stories/…`)
2. **Article count** — 10, 25, or 50 source articles
3. **Length** — Under 500 words, 500–1200 words, Over 1200 words, or 5000 word essay

You can skip step 1 with `/research Apple smart glasses launch` (starts at article count). Pasting a Full Coverage URL in chat also starts research at article count.

Progress updates appear while articles are downloaded and Ollama writes. Then use **Speak to me** or `newscatcher, speak to me`. Research audio ends with: *That's the end from News Catcher's Deep research.*

**Follow-up questions** — After a research article, ask plain-text questions in chat. Answers use the article and sources; speculation is allowed if labeled, but facts are not invented. Send `/nevermind` or a new URL to exit Q&A mode.

**Desktop GUI**

```bash
python gui_app.py
```

Enter a topic or Full Coverage URL, tap **Research**, then **speak**. (GUI uses `RESEARCH_MAX_ARTICLES` and `RESEARCH_TARGET_WORDS` from `.env`; Telegram lets you pick per run.)

**Requirements**

- Ollama running locally (`ollama serve`; set `OLLAMA_HOST` and `OLLAMA_MODEL` in `.env`)
- `googlenewsdecoder` (in `requirements.txt`) — resolves Google News redirect URLs to publisher links
- **patchright** for Full Coverage pages: `patchright install chromium`
- Outlets you care about must be in `domains.json`

**Config** (`.env`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `RESEARCH_MAX_ARTICLES` | `10` | Default headline cap (GUI; Telegram asks each run) |
| `RESEARCH_TARGET_WORDS` | `1000` | Default target length (GUI; Telegram asks each run) |
| `RESEARCH_OLLAMA_TIMEOUT` | `300` | Seconds to wait for Ollama |

### Blog watchlist

Poll watched blogs for new posts and notify in Telegram.

```text
/watch_add          → asks for site, then check interval (15/30/60/120 min)
/watch_remove       → asks which site to stop watching
/watch_interval     → asks site, then new interval
/watch_list         → list watched sites and DND window
/watch_check        → poll all sites now (ignores quiet hours)
```

One-liners still work: `/watch_add hackaday.com 30`.

**Do not disturb** — By default, automatic polls and digests are skipped from **12am–4am** local time. Manual `/watch_check` still runs. Configure with `WATCHLIST_DND_*` in `.env`.

When new posts arrive, tap **Read** or **Speak** on the digest (one or all posts).

### Commands

Many commands work **conversationally**: send the command alone and the bot asks for the rest. Send `/nevermind` to cancel an in-progress prompt.

| Command | With no arguments | With arguments |
|---------|-------------------|----------------|
| `/start` | Help | — |
| `/research` | Topic → count → length | `/research <topic>` skips topic step |
| `/speak` | Asks phrase → audio → fix-a-word | `/speak <phrase>` |
| `/pronounce` | Asks word | `/pronounce <word> [alt …]` |
| `/add_pronunciation` | Asks from → to | `/add_pronunciation <from> <to>` |
| `/find_pronunciation` | Asks search term | `/find_pronunciation <word>` |
| `/delete_pronunciation` | Asks rule to delete | `/delete_pronunciation <from>` |
| `/fixaword` | Fix-a-word on last audio | — |
| `/eliminate_phrase` | Website → phrase to strip | — |
| `/watch_add` | Site → interval | `/watch_add <site> [minutes]` |
| `/watch_remove` | Asks site | `/watch_remove <site>` |
| `/watch_interval` | Site → interval | `/watch_interval <site> <minutes>` |
| `/watch_list` | List watchlist | — |
| `/watch_check` | Poll now | — |
| `/add_domain` | Asks domain (PIN first if set) | `/add_domain [PIN] <domain>` |
| `/remove_domain` | Asks domain (PIN first if set) | `/remove_domain [PIN] <domain>` |
| `/fix_403` | Asks domain (or retries last blocked URL) | `/fix_403 <domain or URL>` |
| `/remove_bad_domain` | Asks domain | `/remove_bad_domain [PIN] <domain>` |
| `/override_bad_domain` | Asks domain | `/override_bad_domain <domain or URL>` |
| `/list_domains` | Show allowlist | — |
| `/list_bad_domains` | Show bad domains | — |
| `/list_eliminate_phrases` | List phrase filters | `[site]` to filter |
| `/remove_eliminate_phrase` | — | `<site> <phrase>` |
| `/nevermind` | Cancel prompts / research Q&A | — |

**Telegram upload timeouts** — Large MP3 uploads use `TELEGRAM_WRITE_TIMEOUT` (default 300s). Raise in `.env` if sends still time out on slow links.

## Security Model

- Only users in `ALLOWED_TELEGRAM_USER_IDS` can use the bot
- Optional `ADMIN_PIN` for sensitive domain changes
- HTTP fetch restricted to approved domains
- `ASK_ADD_DOMAIN=1` (default) prompts to add unknown hosts to `domains.json`; set `0` to add them automatically
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

On anti-bot HTTP responses (**401**, **402**, **403**, configurable), the bot tries fallbacks in order (for any **allowlisted** domain when `AUTO_403_FALLBACKS=1`, default):

1. **WordPress REST API** — domains in `WORDPRESS_API_DOMAINS` (default: `marktechpost.com`).
2. **TLS impersonation** (`curl_cffi`) — works for Cloudflare (Politico) and Le Monde (402).
3. **Headless Chromium** (patchright).

If a new site returns 403, send the URL again; bypass runs automatically. If it still fails, use **`/fix_403 politico.com`** (or `/fix_403` right after the failed URL to retry).

Config:

- `ANTIBOT_FALLBACK_STATUSES` (default: `401,402,403`)
- `AUTO_403_FALLBACKS` (default: `1`) — try curl_cffi + browser for all allowlisted domains
- `WORDPRESS_API_DOMAINS` (default when omitted: `marktechpost.com`)
- `PLAYWRIGHT_FALLBACK_DOMAINS` (used when `AUTO_403_FALLBACKS=0`)
- `PLAYWRIGHT_TIMEOUT_MS` (default: `60000`)
- `PLAYWRIGHT_CHANNEL` (optional; defaults to `chrome` when `google-chrome` is installed)
- `CURL_CFFI_IMPERSONATE` (default: `chrome131`)

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

Reuters uses **DataDome**; a 401 often means anti-bot, not a bad user-agent alone.

- Ensure `ANTIBOT_FALLBACK_STATUSES` includes `401` (default: `401,402,403`)
- Use a browser-like `USER_AGENT` from `.env.example` (do not append a bot name)
- Install bypass tools: `pip install curl_cffi patchright && patchright install chromium`
- Send the URL again — bypass runs automatically after 401

Reuters may still block some server/datacenter IPs; residential connections work more often.

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
