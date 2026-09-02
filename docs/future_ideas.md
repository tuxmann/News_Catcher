# Future ideas

Ideas to build on top of the News Catcher Telegram bot (URL in → article text out). Both are feasible with the existing fetch/extract stack; neither is implemented yet.

---

## Vision

1. **Speak last story** — After sending a news URL, say `newscatcher, speak to me` and get an audio version of that article in Telegram (preferred) or email.
2. **Premium audio briefing** — Like Google News audio briefing, but ~60 minutes instead of 10–15, with **deep dives** on a topic and **subject** filters (e.g. Economy under Business). Runs **overnight** as a batch job; local LLM + TTS produce audio, then upload to a **dedicated Google Drive folder** for listening on your phone at work. **Not** delivered via Telegram. Old files are **deleted after 1 week**.

---

## What exists today

| Capability | Status |
|------------|--------|
| Fetch + extract article from URL | Done (`fetch.py`, `extract.py`, `News_bot.py`) |
| Persist last article per user | Done (`article_cache.py`) |
| Handle non-URL messages (speak trigger) | Done (`newscatcher, speak to me`) |
| TTS / audio | Done (`tts.py` + Telegram `send_audio`) |
| Multi-source briefing / LLM synthesis | Done (`briefing/` → Google Drive) |
| Deep research (Google News → Ollama article) | Done (`research.py`, Telegram `/research`, GUI) |
| Blog watchlist (RSS/WordPress digests) | Done (`watchlist.py`, Telegram `/watch_*`) |
| Conversational Telegram commands | Done (`News_bot.py`) |

---

## Deep research (implemented)

On-demand topic research: Google News RSS or Full Coverage → fetch allowlisted articles → Ollama writes neutral prose (article/essay, not bullet lists) → optional TTS with a News Catcher Deep research outro → follow-up Q&A in Telegram.

Telegram `/research` is conversational (topic, article count, length). GUI uses env defaults.

See [README.md](../README.md#deep-research) for usage.

---

## Phase 1: Speak last story

### Flow

1. User sends a news URL → bot fetches and posts text (unchanged).
2. User sends **`newscatcher, speak to me`** (case-insensitive; comma optional).
3. Bot loads the **last successfully extracted article** for that user, runs TTS, sends audio in Telegram.

### Implementation notes

- **Last-article cache** (`article_cache.py`): After a successful extract, store `user_id`, `url`, `title`, `text`, `timestamp`. Prefer disk (JSON/SQLite) so bot restarts do not lose state. TTL e.g. 24–72 hours. Error if empty: *"No recent article. Send a URL first."*
- **Speak trigger** in `News_bot.py`: Detect phrase before the URL-only early return. Example pattern: `^newscatcher\s*,?\s*speak\s+to\s+me\s*$`. `/speak` tests a phrase and starts fix-a-word.
- **TTS** (`tts.py`): KittenTTS — chunk long text, synthesize per chunk, concat with ffmpeg into one MP3. Prepend title. Run in `asyncio.to_thread()`; `send_chat_action(upload_audio)` while working.
- **Delivery**: `bot.send_audio` with title/performer. Telegram limit ~50 MB per file. Email via SMTP only if needed for huge files or preference.

### Expectations

- CPU TTS on a full article can take **minutes** — show "Generating audio…".
- A long article can be **20+ minutes** of audio; still usually fits Telegram limits.
- Very long pieces may need **Part 1/N** audio messages.

---

## Phase 2: Premium hour-long briefing

Separate subsystem (e.g. `briefing/`), reusing `fetch.py`, `extract.py`, `domains_store.py`. **Decoupled from Telegram delivery** — the bot may still accept topic/deep-dive requests during the day, but the finished audio lives on Drive.

### Goals

- **~60 minutes** of narrated news (≈ 9,000–10,000 words at ~150 wpm).
- **Deep dive** — e.g. "ballroom security money": search recent coverage on allowed domains, fetch several articles, local LLM merges facts and viewpoints into one narrative.
- **Subject** — e.g. Economy under Business: filter by feed/taxonomy/section, not a one-off search phrase.
- **Overnight batch** — cron/systemd timer (e.g. 2–6 AM): ingest → LLM → TTS → upload. Long CPU work stays off the interactive bot.
- **Google Drive delivery** — upload MP3 (and optional metadata JSON) to a **fixed folder** (folder ID in config). Listen via Drive app on phone at work.
- **Retention** — delete Drive files (and local temp copies) **older than 7 days** in that folder after each run or via a small cleanup step in the same job.

### Building blocks

| Piece | Role |
|-------|------|
| Source ingest | RSS/Atom per outlet; respect `domains.json` allowlist |
| Briefing planner | Allocates time per story / deep-dive segment |
| Local LLM | Outline → segment scripts → optional fact-check against source snippets (Ollama, llama.cpp, etc.) |
| TTS | Same KittenTTS pipeline as Phase 1, heavier chunking |
| **Drive upload** | Google Drive API (`google-api-python-client` + OAuth or service account); target folder ID from env |
| **Retention** | List folder → remove files older than 7 days |
| **Scheduler** | `cron` / systemd timer for overnight pipeline; optional config file or Telegram queue for next-run deep dives |

### Deep dive vs subject

- **Deep dive**: Query-driven — user names a topic; collect 5–15 related articles; synthesize one cohesive story.
- **Subject**: Category-driven — ongoing filter (Business → Economy) on feeds or tags.

### Risks

- Publisher ToS / copyright for aggregation and narration (personal allowlist use is lower risk than public redistribution).
- Quality depends on prompts and fitting source text in the LLM context window.
- **Compute**: ~1 hour of audio + multi-article LLM on CPU can take hours unless GPU or shorter prototype runs first.

### Google Drive setup (when building)

- Create a folder in Drive (e.g. `News Catcher Briefings`) and note its **folder ID**.
- Google Cloud project + Drive API enabled; credentials via **service account** (share folder with service account email) or **OAuth** desktop flow for personal use.
- Env/config: `GOOGLE_DRIVE_FOLDER_ID`, credentials path, `BRIEFING_RETENTION_DAYS=7`.
- Filename convention: `briefing-YYYY-MM-DD.mp3` (and optional `.json` with title, segments, sources).

### Suggested ramp

1. Prototype a **15-minute** briefing + Drive upload + 7-day cleanup.
2. Add deep-dive topics (config file or queue from Telegram).
3. Scale script length toward **60 minutes**.

---

## Open decisions

- Phase 1 only: Telegram vs email fallback for large single-article audio.
- Last-article cache TTL and storage path.
- KittenTTS model variant (mini vs nano, voice choice).
- Local LLM backend and hardware (CPU vs GPU).
- Phase 2 ingest: RSS-only vs search across allowed sites.
- Phase 2: Google auth method (service account vs OAuth).
- How to queue deep dives / subjects for the overnight run (static YAML vs Telegram → queue file).

---

## Suggested build order

1. ~~Document ideas (this file).~~
2. ~~`article_cache.py` + save hook in `_process_fetched_html`.~~
3. ~~Speak phrase handler + `tts.py` (KittenTTS + ffmpeg).~~
4. ~~Config: `TTS_ENABLED`, `TTS_MODEL`, `LAST_ARTICLE_TTL`, `AUDIO_OUTPUT_DIR`.~~
5. ~~Tests: cache round-trip, phrase matching (mock TTS in CI).~~
6. ~~`briefing/` package: overnight ingest → LLM → script → TTS → Drive upload → 7-day cleanup.~~

Run overnight briefing: `python -m briefing` (see `briefing.yaml.example` and `.env.example`).
