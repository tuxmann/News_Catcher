"""Telegram bot: URL in, article text out."""

from __future__ import annotations

import asyncio
import html as html_module
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Message,
    MessageEntity,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from article_format import format_paragraphs_for_telegram
from article_cache import load_last_article, purge_expired as purge_article_cache, save_last_article
from article_export import (
    is_save_to_disk_phrase,
    save_article_text_file,
    telegram_audio_filename,
)
from domains_store import (
    add_bad_domain,
    bad_domain_refusal_message,
    host_allowed,
    host_is_bad,
    is_valid_registrable_domain,
    load_bad_domains,
    load_domains,
    normalize_registrable_hint,
    registrable_domain_from_url,
    save_bad_domains,
    save_domains,
)
from extract import extract_article
from fetch import (
    FetchError,
    FetchOk,
    FetchOversizeKnown,
    FetchOversizeUnknown,
    fetch_url,
)
from pronunciation_suggest import suggest_pronunciations
from tts import (
    is_speak_phrase,
    split_mp3_for_telegram,
    synthesize_pronunciation_sample,
    synthesize_to_mp3,
)
from tts_normalize import (
    add_literal_replacement,
    find_literal_replacements,
    remove_literal_replacement,
)
from watchlist import (
    CandidatePost,
    check_site,
    domain_hint_from_user_input,
    first_paragraph,
)
from watchlist_store import (
    DEFAULT_CHECK_INTERVAL_MINUTES,
    WatchedSite,
    get_site,
    load_watchlist,
    remove_site,
    set_interval,
    site_is_due,
    upsert_site,
)


def _fetch_error_reply(exc: FetchError) -> str:
    s = str(exc)
    if s == "HTTP 401":
        return (
            "HTTP 401 — the server refused the request (common with anti-bot, e.g. Reuters). "
            "Use the default browser-like USER_AGENT from .env.example (remove a short bot-only UA) "
            "and restart the bot."
        )
    if exc.blocked_domain or s.startswith("HTTP 402") or s.startswith("HTTP 403"):
        domain = exc.blocked_domain or "this site"
        tried = exc.tried_strategies
        code = "403"
        if s.startswith("HTTP "):
            parts = s.split()
            if len(parts) >= 2 and parts[1].isdigit():
                code = parts[1]
        lines = [
            f"HTTP {code} — {domain} blocked plain HTTP (anti-bot / paywall probe).",
        ]
        if tried:
            lines.append("Tried: " + ", ".join(tried) + ".")
        lines.extend(
            [
                "",
                "Send the article URL again (bypass runs automatically), or:",
                f"/fix_403 {domain}",
                "  — record the domain and retry the last blocked URL",
                "",
                "If it keeps failing:",
                "pip install curl_cffi patchright && patchright install chromium",
            ]
        )
        return "\n".join(lines)
    return s


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
# python-telegram-bot polls via httpx; keep those request lines off INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Keep in sync with CommandHandler registrations in main().
_BOT_COMMAND_NAMES = frozenset(
    {
        "start",
        "list_domains",
        "add_domain",
        "remove_domain",
        "list_bad_domains",
        "remove_bad_domain",
        "override_bad_domain",
        "fix_403",
        "pronounce",
        "add_pronunciation",
        "find_pronunciation",
        "delete_pronunciation",
        "fixaword",
        "speak",
        "nevermind",
        "watch_add",
        "watch_list",
        "watch_remove",
        "watch_interval",
        "watch_check",
    }
)


def _leading_command_name(text: str) -> str | None:
    if not text.startswith("/"):
        return None
    segment = text.split(None, 1)[0]
    if "@" in segment:
        segment = segment.split("@", 1)[0]
    name = segment[1:]
    return name.lower() if name else None


class UnlistedSlashCommandFilter(filters.MessageFilter):
    """Leading /command entity for a command this bot does not implement."""

    __slots__ = ()

    def filter(self, message: Message) -> bool:
        text = message.text
        if not text:
            return False
        ents = message.entities
        if not ents or ents[0].offset != 0 or ents[0].type != MessageEntity.BOT_COMMAND:
            return False
        cmd = _leading_command_name(text)
        return cmd is not None and cmd not in _BOT_COMMAND_NAMES


UNLISTED_SLASH_COMMAND = UnlistedSlashCommandFilter()

PENDING_OVERSIZE: dict[str, "PendingOversize"] = {}
PENDING_DOMAIN_ADD: dict[str, "PendingDomainAdd"] = {}
PENDING_DOMAIN_BAD: dict[str, "PendingDomainBad"] = {}
PENDING_PRONUNCIATION: dict[str, "PendingPronunciation"] = {}
PRONUNCIATION_BATCHES: dict[str, list[tuple[str, int, int]]] = {}
PENDING_WORD_FIX: dict[int, "PendingWordFix"] = {}
PENDING_SPEAK_PHRASE: dict[int, "PendingSpeakPhrase"] = {}
PENDING_RULE_ACTION: dict[str, "PendingRuleAction"] = {}
PENDING_FIND_SESSION: dict[str, "PendingFindSession"] = {}
PENDING_WATCH_POST: dict[str, "PendingWatchPost"] = {}

# Inline actions after an article is delivered (callback_data must be <= 64 bytes).
_CALLBACK_SPEAK = "a:speak"
_CALLBACK_SAVE = "a:save"
_CALLBACK_WORD_FIX = "a:wordfix"
_CALLBACK_WORD_FIX_RETRY = "wf:retry"
_CALLBACK_WORD_FIX_FEEDBACK = "wf:feedback"
_CALLBACK_WORD_FIX_CUSTOM = "wf:custom"
_CALLBACK_SPEAK_PHRASE = "s:phrase"

WORD_FIX_HELP = (
    "How fix-a-word works:\n"
    "1. Reply with the word or phrase that sounds wrong in the audio.\n"
    "2. Ollama suggests spellings — you'll get a short audio clip for each.\n"
    "3. Tap Save on the sample you like (writes to tts_replacements.json).\n"
    "4. Not happy? Tap Retry, Send feedback, or My spelling to try your own.\n\n"
    "Send /nevermind anytime to cancel and wait for the next article URL.\n\n"
    "Example words: Polish, U.S., a.m., Mesa"
)


@dataclass
class PendingOversize:
    user_id: int
    url: str
    expires_monotonic: float


@dataclass
class PendingPronunciation:
    user_id: int
    from_text: str
    to_text: str
    expires_monotonic: float
    chat_id: int
    message_id: int
    batch_id: str


@dataclass
class PendingDomainAdd:
    user_id: int
    url: str
    domain: str
    expires_monotonic: float


@dataclass
class PendingDomainBad:
    user_id: int
    domain: str
    url: str
    expires_monotonic: float


@dataclass
class PendingWordFix:
    """Word-fix session: awaiting word, feedback, or follow-up after samples."""

    user_id: int
    article_title: str | None
    article_text: str | None
    from_text: str | None = None
    tried_spellings: list[str] = field(default_factory=list)
    step: str = "awaiting_word"
    expires_monotonic: float = 0.0


@dataclass
class PendingSpeakPhrase:
    """Waiting for a test sentence after tapping Test phrase."""

    user_id: int
    expires_monotonic: float


@dataclass
class PendingRuleAction:
    """Find-pronunciation match: pronounce or delete an existing rule."""

    user_id: int
    from_text: str
    to_text: str
    expires_monotonic: float


@dataclass
class PendingFindSession:
    """Session after /find_pronunciation: confirm pronounce-all."""

    user_id: int
    matches: list[tuple[str, str]]
    expires_monotonic: float


@dataclass
class PendingWatchPost:
    """Watchlist notify: Read or Speak a new blog post."""

    user_id: int
    url: str
    title: str
    expires_monotonic: float


def _allowed_user(user_id: int) -> bool:
    if not config.ALLOWED_TELEGRAM_USER_IDS:
        return False
    return user_id in config.ALLOWED_TELEGRAM_USER_IDS


def _article_actions_keyboard(*, block_token: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("Speak to me", callback_data=_CALLBACK_SPEAK),
            InlineKeyboardButton("Save to disk", callback_data=_CALLBACK_SAVE),
        ],
    ]
    if block_token:
        rows.append(
            [InlineKeyboardButton("Block website", callback_data=f"b:{block_token}")]
        )
    return InlineKeyboardMarkup(rows)


def _word_fix_followup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Retry", callback_data=_CALLBACK_WORD_FIX_RETRY),
                InlineKeyboardButton("Send feedback", callback_data=_CALLBACK_WORD_FIX_FEEDBACK),
            ],
            [
                InlineKeyboardButton("My spelling", callback_data=_CALLBACK_WORD_FIX_CUSTOM),
            ],
        ]
    )


async def _send_word_fix_followup(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    user_id: int,
    article_title: str | None,
    article_text: str | None,
    from_text: str,
    tried_spellings: list[str],
    message: str = (
        "Tap Save on a sample you like, or use the buttons below for more options.\n"
        "Send /nevermind to cancel and wait for the next article URL."
    ),
) -> None:
    _pending_word_fix_put(
        user_id,
        article_title,
        article_text,
        from_text=from_text,
        tried_spellings=tried_spellings,
        step="after_samples",
    )
    await context.bot.send_message(
        chat_id,
        message,
        reply_markup=_word_fix_followup_keyboard(),
    )


def _after_audio_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Fix a word", callback_data=_CALLBACK_WORD_FIX)]]
    )


def _speak_phrase_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Test phrase", callback_data=_CALLBACK_SPEAK_PHRASE)]]
    )


def _action_chat_id(update: Update) -> int | None:
    if update.effective_chat:
        return update.effective_chat.id
    query = update.callback_query
    if query and query.message:
        return query.message.chat_id
    return None


async def _bot_send_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    chat_id: int | None = None,
) -> None:
    cid = chat_id if chat_id is not None else _action_chat_id(update)
    if cid is None:
        return
    await context.bot.send_message(cid, text)


# Between logical paragraphs packed into one Telegram message (extract uses \n\n already).
PARAGRAPH_GAP = "\n\n"

# Reserve room for "123 of 456\n\n" prefix (Telegram hard limit 4096).
_HEADER_RESERVE = 32
_TELEGRAM_HARD_MAX = 4096


def _split_paragraphs(text: str) -> list[str]:
    """Logical paragraphs (blank-line separated); preserves inner single newlines."""
    text = text.strip()
    if not text:
        return []
    return [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]


def _split_words_only(s: str, max_len: int) -> list[str]:
    """Split on whitespace only; each segment length <= max_len when possible."""
    words = s.split()
    if not words:
        return []
    out: list[str] = []
    cur: list[str] = []
    ln = 0
    for w in words:
        need = len(w) + (1 if cur else 0)
        if ln + need <= max_len:
            cur.append(w)
            ln += need
            continue
        if cur:
            out.append(" ".join(cur))
            cur = []
            ln = 0
        if len(w) <= max_len:
            cur = [w]
            ln = len(w)
        else:
            out.append(w)
    if cur:
        out.append(" ".join(cur))
    return out


def _split_long_block(s: str, max_len: int) -> list[str]:
    """
    Break a block that exceeds max_len: prefer sentence boundaries, then words.
    Never splits inside a word unless a single word exceeds max_len.
    """
    s = s.strip()
    if len(s) <= max_len:
        return [s]
    sentences = re.split(r"(?<=[.!?…])\s+", s)
    if len(sentences) <= 1:
        return _split_words_only(s, max_len)
    out: list[str] = []
    buf = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        trial = sent if not buf else f"{buf} {sent}"
        if len(trial) <= max_len:
            buf = trial
        else:
            if buf:
                out.append(buf)
            if len(sent) <= max_len:
                buf = sent
            else:
                out.extend(_split_words_only(sent, max_len))
                buf = ""
    if buf:
        out.append(buf)
    return out


def _atomic_units_for_chunking(paragraphs: list[str], max_unit_len: int) -> list[str]:
    units: list[str] = []
    for p in paragraphs:
        if len(p) <= max_unit_len:
            units.append(p)
        else:
            units.extend(_split_long_block(p, max_unit_len))
    return units


def _pack_units_into_chunks(units: list[str], max_body_len: int) -> list[str]:
    """Concatenate whole units with PARAGRAPH_GAP; never split a unit."""
    if not units:
        return []
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    gap_len = len(PARAGRAPH_GAP)

    for u in units:
        extra = len(u) + (gap_len if cur else 0)
        if cur_len + extra <= max_body_len:
            cur.append(u)
            cur_len += extra
            continue
        if cur:
            chunks.append(PARAGRAPH_GAP.join(cur))
        cur = [u]
        cur_len = len(u)

    if cur:
        chunks.append(PARAGRAPH_GAP.join(cur))
    return chunks


def _format_article_chunks(text: str, max_message_len: int) -> list[str]:
    """
    Build Telegram messages: each starts with 'i of n\\n\\n' and contains only
    whole paragraphs (or sentence/word splits when one paragraph is too long).
    """
    max_message_len = min(max_message_len, _TELEGRAM_HARD_MAX)
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    def build_with_body_budget(body_budget: int) -> list[str]:
        units = _atomic_units_for_chunking(paragraphs, body_budget)
        bodies = _pack_units_into_chunks(units, body_budget)
        total = len(bodies)
        messages: list[str] = []
        for i, body in enumerate(bodies):
            prefix = f"{i + 1} of {total}\n\n"
            messages.append(prefix + body)
        return messages

    body_budget = max(256, max_message_len - _HEADER_RESERVE)
    messages = build_with_body_budget(body_budget)
    while any(len(m) > max_message_len for m in messages) and body_budget > 256:
        body_budget = max(256, body_budget - 50)
        messages = build_with_body_budget(body_budget)
    return messages


def _extract_first_url(text: str) -> str | None:
    m = URL_RE.search(text)
    return m.group(0).rstrip(").,;") if m else None


def _pending_domain_put(user_id: int, url: str, domain: str) -> tuple[str, InlineKeyboardMarkup]:
    token = secrets.token_hex(8)
    PENDING_DOMAIN_ADD[token] = PendingDomainAdd(
        user_id=user_id,
        url=url,
        domain=domain,
        expires_monotonic=time.monotonic() + config.PENDING_OVERSIZE_TTL,
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Add to list", callback_data=f"y:{token}"),
                InlineKeyboardButton("No", callback_data=f"n:{token}"),
            ]
        ]
    )
    return token, kb


def _pending_put(user_id: int, url: str) -> tuple[str, InlineKeyboardMarkup]:
    token = secrets.token_hex(8)
    PENDING_OVERSIZE[token] = PendingOversize(
        user_id=user_id,
        url=url,
        expires_monotonic=time.monotonic() + config.PENDING_OVERSIZE_TTL,
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Proceed", callback_data=f"p:{token}"),
                InlineKeyboardButton("Cancel", callback_data=f"x:{token}"),
            ]
        ]
    )
    return token, kb


def _purge_expired_pending() -> None:
    now = time.monotonic()
    dead = [k for k, v in PENDING_OVERSIZE.items() if now > v.expires_monotonic]
    for k in dead:
        del PENDING_OVERSIZE[k]
    dead_d = [k for k, v in PENDING_DOMAIN_ADD.items() if now > v.expires_monotonic]
    for k in dead_d:
        del PENDING_DOMAIN_ADD[k]
    dead_b = [k for k, v in PENDING_DOMAIN_BAD.items() if now > v.expires_monotonic]
    for k in dead_b:
        del PENDING_DOMAIN_BAD[k]
    dead_p = [k for k, v in PENDING_PRONUNCIATION.items() if now > v.expires_monotonic]
    for k in dead_p:
        del PENDING_PRONUNCIATION[k]
    expired_users = [uid for uid, v in PENDING_WORD_FIX.items() if now > v.expires_monotonic]
    for uid in expired_users:
        del PENDING_WORD_FIX[uid]
    expired_speak = [uid for uid, v in PENDING_SPEAK_PHRASE.items() if now > v.expires_monotonic]
    for uid in expired_speak:
        del PENDING_SPEAK_PHRASE[uid]
    dead_ra = [k for k, v in PENDING_RULE_ACTION.items() if now > v.expires_monotonic]
    for k in dead_ra:
        del PENDING_RULE_ACTION[k]
    dead_fs = [k for k, v in PENDING_FIND_SESSION.items() if now > v.expires_monotonic]
    for k in dead_fs:
        del PENDING_FIND_SESSION[k]
    dead_w = [k for k, v in PENDING_WATCH_POST.items() if now > v.expires_monotonic]
    for k in dead_w:
        del PENDING_WATCH_POST[k]


def _pending_rule_action_put(user_id: int, from_text: str, to_text: str) -> str:
    token = secrets.token_hex(6)
    PENDING_RULE_ACTION[token] = PendingRuleAction(
        user_id=user_id,
        from_text=from_text,
        to_text=to_text,
        expires_monotonic=time.monotonic() + config.PENDING_OVERSIZE_TTL,
    )
    return token


def _pending_rule_action_get(token: str) -> PendingRuleAction | None:
    p = PENDING_RULE_ACTION.get(token)
    if p is None:
        return None
    if time.monotonic() > p.expires_monotonic:
        del PENDING_RULE_ACTION[token]
        return None
    return p


def _pending_find_session_put(user_id: int, matches: list[tuple[str, str]]) -> str:
    token = secrets.token_hex(6)
    PENDING_FIND_SESSION[token] = PendingFindSession(
        user_id=user_id,
        matches=matches,
        expires_monotonic=time.monotonic() + config.PENDING_OVERSIZE_TTL,
    )
    return token


def _pending_find_session_get(token: str) -> PendingFindSession | None:
    p = PENDING_FIND_SESSION.get(token)
    if p is None:
        return None
    if time.monotonic() > p.expires_monotonic:
        del PENDING_FIND_SESSION[token]
        return None
    return p


def _pending_watch_post_put(user_id: int, url: str, title: str) -> str:
    token = secrets.token_hex(6)
    PENDING_WATCH_POST[token] = PendingWatchPost(
        user_id=user_id,
        url=url,
        title=title,
        expires_monotonic=time.monotonic() + config.PENDING_OVERSIZE_TTL,
    )
    return token


def _pending_watch_post_get(token: str) -> PendingWatchPost | None:
    p = PENDING_WATCH_POST.get(token)
    if p is None:
        return None
    if time.monotonic() > p.expires_monotonic:
        del PENDING_WATCH_POST[token]
        return None
    return p


def _pending_pronunciation_put(
    user_id: int,
    from_text: str,
    to_text: str,
    *,
    chat_id: int,
    message_id: int,
    batch_id: str,
) -> str:
    token = secrets.token_urlsafe(8)
    PENDING_PRONUNCIATION[token] = PendingPronunciation(
        user_id=user_id,
        from_text=from_text,
        to_text=to_text,
        expires_monotonic=time.monotonic() + config.PENDING_OVERSIZE_TTL,
        chat_id=chat_id,
        message_id=message_id,
        batch_id=batch_id,
    )
    return token


def _pending_pronunciation_get(token: str) -> PendingPronunciation | None:
    p = PENDING_PRONUNCIATION.get(token)
    if p is None:
        return None
    if time.monotonic() > p.expires_monotonic:
        del PENDING_PRONUNCIATION[token]
        return None
    return p


def _pending_pronunciation_remove(token: str) -> None:
    PENDING_PRONUNCIATION.pop(token, None)


def _clear_user_interactive_state(user_id: int) -> None:
    """Drop word-fix / speak-phrase / pronunciation sample sessions."""
    PENDING_WORD_FIX.pop(user_id, None)
    PENDING_SPEAK_PHRASE.pop(user_id, None)
    batch_ids = {p.batch_id for p in PENDING_PRONUNCIATION.values() if p.user_id == user_id}
    for token in [t for t, p in PENDING_PRONUNCIATION.items() if p.user_id == user_id]:
        _pending_pronunciation_remove(token)
    for batch_id in batch_ids:
        PRONUNCIATION_BATCHES.pop(batch_id, None)


async def _delete_other_pronunciation_samples(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    batch_id: str,
    keep_token: str,
) -> None:
    entries = PRONUNCIATION_BATCHES.pop(batch_id, [])
    for token, chat_id, message_id in entries:
        if token == keep_token:
            continue
        _pending_pronunciation_remove(token)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramError:
            pass


def _pending_word_fix_put(
    user_id: int,
    article_title: str | None,
    article_text: str | None,
    *,
    from_text: str | None = None,
    tried_spellings: list[str] | None = None,
    step: str = "awaiting_word",
) -> None:
    existing = PENDING_WORD_FIX.get(user_id)
    PENDING_WORD_FIX[user_id] = PendingWordFix(
        user_id=user_id,
        article_title=article_title,
        article_text=article_text,
        from_text=from_text,
        tried_spellings=(
            list(tried_spellings)
            if tried_spellings is not None
            else (list(existing.tried_spellings) if existing else [])
        ),
        step=step,
        expires_monotonic=time.monotonic() + config.PENDING_OVERSIZE_TTL,
    )


def _pending_word_fix_get(user_id: int) -> PendingWordFix | None:
    pending = PENDING_WORD_FIX.get(user_id)
    if pending is None:
        return None
    if time.monotonic() > pending.expires_monotonic:
        del PENDING_WORD_FIX[user_id]
        return None
    return pending


def _pending_word_fix_clear(user_id: int) -> None:
    PENDING_WORD_FIX.pop(user_id, None)


def _pending_word_fix_pop(user_id: int) -> PendingWordFix | None:
    pending = _pending_word_fix_get(user_id)
    if pending is not None:
        del PENDING_WORD_FIX[user_id]
    return pending


def _pending_speak_phrase_put(user_id: int) -> None:
    PENDING_SPEAK_PHRASE[user_id] = PendingSpeakPhrase(
        user_id=user_id,
        expires_monotonic=time.monotonic() + config.PENDING_OVERSIZE_TTL,
    )


def _pending_speak_phrase_get(user_id: int) -> PendingSpeakPhrase | None:
    pending = PENDING_SPEAK_PHRASE.get(user_id)
    if pending is None:
        return None
    if time.monotonic() > pending.expires_monotonic:
        del PENDING_SPEAK_PHRASE[user_id]
        return None
    return pending


def _pending_speak_phrase_clear(user_id: int) -> None:
    PENDING_SPEAK_PHRASE.pop(user_id, None)


def _article_context_snippet(title: str | None, text: str, word: str) -> str:
    """Short context for Ollama disambiguation."""
    parts: list[str] = []
    if title:
        parts.append(f"Title: {title}")
    needle = word.casefold()
    for para in re.split(r"\n\s*\n+", text):
        if needle in para.casefold():
            parts.append(para.strip()[:500])
            break
    else:
        parts.append(text.strip()[:400])
    return "\n".join(parts)


def _pending_get(token: str) -> PendingOversize | None:
    p = PENDING_OVERSIZE.get(token)
    if p is None:
        return None
    if time.monotonic() > p.expires_monotonic:
        del PENDING_OVERSIZE[token]
        return None
    return p


def _pending_remove(token: str) -> None:
    PENDING_OVERSIZE.pop(token, None)


def _pending_domain_get(token: str) -> PendingDomainAdd | None:
    p = PENDING_DOMAIN_ADD.get(token)
    if p is None:
        return None
    if time.monotonic() > p.expires_monotonic:
        del PENDING_DOMAIN_ADD[token]
        return None
    return p


def _pending_domain_remove(token: str) -> None:
    PENDING_DOMAIN_ADD.pop(token, None)


def _pending_domain_bad_put(user_id: int, domain: str, url: str) -> tuple[str, InlineKeyboardMarkup]:
    token = secrets.token_hex(8)
    PENDING_DOMAIN_BAD[token] = PendingDomainBad(
        user_id=user_id,
        domain=domain,
        url=url,
        expires_monotonic=time.monotonic() + config.PENDING_OVERSIZE_TTL,
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Block website", callback_data=f"b:{token}"),
                InlineKeyboardButton("Keep on list", callback_data=f"k:{token}"),
            ]
        ]
    )
    return token, kb


def _pending_domain_bad_get(token: str) -> PendingDomainBad | None:
    p = PENDING_DOMAIN_BAD.get(token)
    if p is None:
        return None
    if time.monotonic() > p.expires_monotonic:
        del PENDING_DOMAIN_BAD[token]
        return None
    return p


def _pending_domain_bad_remove(token: str) -> None:
    PENDING_DOMAIN_BAD.pop(token, None)


async def _offer_mark_domain_bad(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    domain: str,
    url: str,
    reason: str,
) -> None:
    if not update.effective_user:
        return
    _, kb = _pending_domain_bad_put(update.effective_user.id, domain, url)
    await _reply_on_chat(
        update,
        context,
        f"{reason}\n\n"
        f"Can't get a usable article from {domain}?\n"
        "Block website adds it to domains_bad.json and removes it from the approved list.",
        reply_markup=kb,
    )


def _format_size(num: int) -> str:
    if num >= 1024 * 1024:
        return f"{num / (1024 * 1024):.2f} MB"
    if num >= 1024:
        return f"{num / 1024:.1f} KB"
    return f"{num} bytes"


async def _process_fetched_html(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    html_bytes: bytes,
    final_url: str,
    content_type: str | None,
    *,
    domain_trial: str | None = None,
) -> None:
    chat_id = update.effective_chat.id
    if content_type and "html" not in content_type.lower() and "xml" not in content_type.lower():
        await context.bot.send_message(
            chat_id,
            "Warning: response may not be HTML; extraction might be poor.",
        )
    try:
        article = extract_article(html_bytes, final_url)
    except Exception as e:
        logger.exception("extract failed")
        await context.bot.send_message(chat_id, f"Could not extract article text: {e}")
        if domain_trial:
            await _offer_mark_domain_bad(
                update,
                context,
                domain=domain_trial,
                url=final_url,
                reason=f"Could not extract article text: {e}",
            )
        return

    if not article.text.strip():
        await context.bot.send_message(
            chat_id,
            "No article body found in the HTML (paywall, JS-only page, or unsupported layout).",
        )
        if domain_trial:
            await _offer_mark_domain_bad(
                update,
                context,
                domain=domain_trial,
                url=final_url,
                reason="No article body found in the HTML.",
            )
        return

    header_parts = [f"URL: {final_url}"]
    if article.title:
        header_parts.append(f"Title: {article.title}")
    if article.author:
        header_parts.append(f"Author: {article.author}")
    if article.date:
        header_parts.append(f"Date: {article.date}")
    await context.bot.send_message(chat_id, "\n".join(header_parts))

    for msg in _format_article_chunks(
        format_paragraphs_for_telegram(article.text),
        config.TELEGRAM_CHUNK_CHARS,
    ):
        try:
            await context.bot.send_message(chat_id, msg, parse_mode="HTML")
        except TelegramError:
            await context.bot.send_message(chat_id, msg)

    if update.effective_user:
        save_last_article(
            config.ARTICLE_CACHE_DIR,
            update.effective_user.id,
            final_url,
            article.title,
            article.text,
        )

    if config.CAPTURE_ARTICLE_IMAGES and article.images:
        for idx, image in enumerate(article.images, start=1):
            # Telegram photo captions are limited to 1024 chars.
            base_caption = image.caption or f"Image {idx}"
            caption = base_caption[:1024]
            try:
                await context.bot.send_photo(chat_id, photo=image.url, caption=caption)
            except TelegramError:
                # If Telegram cannot fetch the image URL, send a fallback text line.
                await context.bot.send_message(chat_id, f"{caption}\n{image.url}")

    if update.effective_user:
        block_token: str | None = None
        if domain_trial:
            block_token, _ = _pending_domain_bad_put(
                update.effective_user.id,
                domain_trial,
                final_url,
            )
        await context.bot.send_message(
            chat_id,
            "What would you like to do next?",
            reply_markup=_article_actions_keyboard(block_token=block_token),
        )


async def _start_word_fix_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
) -> None:
    cached = load_last_article(
        config.ARTICLE_CACHE_DIR,
        user_id,
        ttl_seconds=config.LAST_ARTICLE_TTL_SECONDS,
    )
    _pending_word_fix_put(
        user_id,
        cached.title if cached else None,
        cached.text if cached else None,
    )
    await context.bot.send_message(chat_id, WORD_FIX_HELP)
    await context.bot.send_message(
        chat_id,
        "Reply with the word or short phrase that sounds wrong in the audio.",
    )


async def cmd_fixaword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    await _start_word_fix_prompt(
        context,
        user_id=update.effective_user.id,
        chat_id=chat_id,
    )


async def cmd_speak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Speak a single test phrase with current pronunciation rules."""
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    if not config.TTS_ENABLED:
        await update.message.reply_text("Text-to-speech is disabled on this bot.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /speak <sentence or phrase>\n\n"
            "Example:\n"
            "  /speak The train arrives at 5 a.m.\n"
            "  /speak Mesa police responded at 5 a.m. Sunday.\n\n"
            "Uses your saved pronunciations from tts_replacements.json."
        )
        return

    phrase = " ".join(args).strip()
    await _run_speak_phrase(update, context, phrase)


async def cmd_nevermind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel word-fix / speak-phrase sessions and return to waiting for a URL."""
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    user_id = update.effective_user.id
    had_word_fix = user_id in PENDING_WORD_FIX
    had_speak = user_id in PENDING_SPEAK_PHRASE
    had_samples = any(p.user_id == user_id for p in PENDING_PRONUNCIATION.values())
    _clear_user_interactive_state(user_id)
    if not (had_word_fix or had_speak or had_samples):
        await update.message.reply_text(
            "Nothing to cancel. Send me a news article URL when you're ready."
        )
        return
    await update.message.reply_text(
        "Okay — cancelled. Send me a news article URL when you're ready."
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    await update.message.reply_text(
        "Send me a message containing a news article URL (HTTPS, approved domain). "
        "I will fetch and return readable text.\n\n"
        "If the site is not on your list yet, I will ask whether to add its domain.\n\n"
        "After each article you can tap Speak to me or Save to disk, "
        "or type those phrases.\n"
        "After audio is ready, tap Fix a word or send /fixaword to tune pronunciation.\n\n"
        "Commands: /list_domains, /add_domain, /remove_domain, /list_bad_domains, "
        "/override_bad_domain, /fix_403\n"
        "TTS: /speak <phrase> — hear one sentence with current pronunciations\n"
        "     /fixaword — reply with a word to fix; /pronounce <word> [alt1 …]\n"
        "     /find_pronunciation <word> — find saved rules; pronounce or delete\n"
        "     /delete_pronunciation <from> — remove a rule from tts_replacements.json\n"
        "     /add_pronunciation <from> <to> — add a rule without audio preview\n"
        "     /nevermind — cancel fix-a-word or other in-progress prompts\n"
        "Watch: /watch_add <site> [minutes] — poll a blog for new posts\n"
        "       /watch_list, /watch_remove, /watch_interval, /watch_check"
    )


async def cmd_list_domains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    domains = sorted(load_domains(config.DOMAINS_FILE))
    if not domains:
        await update.message.reply_text("No approved domains configured.")
        return
    await update.message.reply_text("Approved domains:\n" + "\n".join(domains))


def _parse_pin_domain(args: list[str], *, usage_cmd: str) -> tuple[bool, str | None]:
    """
    If ADMIN_PIN is set: args should be [PIN, domain].
    Else: args should be [domain].
    Returns (ok, domain_or_error_message).
    """
    if config.ADMIN_PIN:
        if len(args) < 2:
            return False, f"Usage: {usage_cmd} <PIN> <domain>"
        pin, domain = args[0], args[1]
        if pin != config.ADMIN_PIN:
            return False, "Invalid PIN."
        return True, domain
    if len(args) < 1:
        return False, f"Usage: {usage_cmd} <domain>"
    return True, args[0]


async def cmd_add_domain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    args = context.args or []
    ok, domain_or_err = _parse_pin_domain(args, usage_cmd="/add_domain")
    if not ok:
        await update.message.reply_text(domain_or_err or "Bad request.")
        return
    domain = normalize_registrable_hint(domain_or_err or "")
    if not is_valid_registrable_domain(domain):
        await update.message.reply_text("Invalid domain.")
        return
    current = load_domains(config.DOMAINS_FILE)
    current.add(domain)
    save_domains(config.DOMAINS_FILE, current)
    await update.message.reply_text(f"Added domain: {domain}")


async def cmd_fix_403(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record a 403-blocked domain and retry the last failed URL (or show usage)."""
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return

    args = context.args or []
    pending = context.user_data.get("last_403") or {}
    url: str | None = None
    domain: str | None = None

    if args:
        arg = args[0].strip()
        if arg.lower().startswith("http://") or arg.lower().startswith("https://"):
            url = arg
            domain = registrable_domain_from_url(url)
        else:
            domain = normalize_registrable_hint(arg)
    else:
        url = pending.get("url")
        domain = pending.get("domain")

    if not domain or not is_valid_registrable_domain(domain):
        await update.message.reply_text(
            "Usage: /fix_403 <domain>\n"
            "Example: /fix_403 politico.com\n\n"
            "Or send /fix_403 right after a blocked article URL (retries that link).\n"
            "You can also pass the full URL:\n"
            "/fix_403 https://www.politico.com/news/..."
        )
        return

    from fallback_domains_store import record_fallback_domain

    record_fallback_domain(domain)
    await update.message.reply_text(
        f"Recorded {domain} for 403 bypass. "
        + ("Fetching article…" if url else "Send the article URL to fetch it.")
    )

    if url:
        await _deliver_user_url_fetch(
            update, context, url, offer_domain_prompt=False
        )


async def cmd_remove_domain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    args = context.args or []
    ok, domain_or_err = _parse_pin_domain(args, usage_cmd="/remove_domain")
    if not ok:
        await update.message.reply_text(domain_or_err or "Bad request.")
        return
    domain = normalize_registrable_hint(domain_or_err or "")
    current = load_domains(config.DOMAINS_FILE)
    if domain not in current:
        await update.message.reply_text("Domain not in list.")
        return
    current.discard(domain)
    save_domains(config.DOMAINS_FILE, current)
    await update.message.reply_text(f"Removed domain: {domain}")


async def cmd_list_bad_domains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    domains = sorted(load_bad_domains(config.DOMAINS_BAD_FILE))
    if not domains:
        await update.message.reply_text("No bad domains recorded.")
        return
    await update.message.reply_text("Bad domains (extraction failed):\n" + "\n".join(domains))


async def cmd_remove_bad_domain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return

    args = list(context.args or [])
    pending = context.user_data.get("last_bad_domain") or {}
    domain: str | None = None

    # Tapping /remove_bad_domain in a refusal message sends no args — use the
    # domain from the URL that was just blocked.
    if not args and pending.get("domain"):
        domain = normalize_registrable_hint(str(pending["domain"]))
    else:
        ok, domain_or_err = _parse_pin_domain(args, usage_cmd="/remove_bad_domain")
        if not ok:
            hint = ""
            if pending.get("domain"):
                hint = (
                    f"\n\nOr send /remove_bad_domain right after a blocked article "
                    f"(removes {pending['domain']})."
                )
            await update.message.reply_text((domain_or_err or "Bad request.") + hint)
            return
        domain = normalize_registrable_hint(domain_or_err or "")

    if not domain or not is_valid_registrable_domain(domain):
        await update.message.reply_text(
            "Usage: /remove_bad_domain <domain>\n"
            "Example: /remove_bad_domain phys.org\n\n"
            "Or send /remove_bad_domain right after a blocked article URL."
        )
        return

    bad = load_bad_domains(config.DOMAINS_BAD_FILE)
    if domain not in bad:
        await update.message.reply_text("Domain not on the bad list.")
        return
    bad.discard(domain)
    save_bad_domains(config.DOMAINS_BAD_FILE, bad)
    context.user_data.pop("last_bad_domain", None)
    await update.message.reply_text(
        f"Removed {domain} from the bad list. "
        "Use /add_domain if you want it on the approved list again."
    )


async def cmd_override_bad_domain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Temporarily allow a bad-list domain for troubleshooting (does not edit domains_bad.json)."""
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return

    args = context.args or []
    pending = context.user_data.get("last_bad_domain") or {}
    url: str | None = None
    domain: str | None = None

    if args:
        arg = args[0].strip()
        if arg.lower().startswith("http://") or arg.lower().startswith("https://"):
            url = arg
            domain = registrable_domain_from_url(url)
        else:
            domain = normalize_registrable_hint(arg)
            if len(args) > 1:
                maybe_url = args[1].strip()
                if maybe_url.lower().startswith("http://") or maybe_url.lower().startswith(
                    "https://"
                ):
                    url = maybe_url
    else:
        url = pending.get("url")
        domain = pending.get("domain")

    if not domain or not is_valid_registrable_domain(domain):
        await update.message.reply_text(
            "Usage: /override_bad_domain <domain>\n"
            "Example: /override_bad_domain phys.org\n\n"
            "Or send /override_bad_domain right after a blocked article URL "
            "(retries that link without removing it from the bad list).\n"
            "You can also pass the full URL:\n"
            "/override_bad_domain https://phys.org/news/..."
        )
        return

    overrides: set[str] = context.user_data.setdefault("bad_domain_overrides", set())
    overrides.add(domain)

    await update.message.reply_text(
        f"Temporary override enabled for {domain} (this session only; "
        "domains_bad.json is unchanged). "
        + ("Fetching article…" if url else "Send the article URL to fetch it.")
    )

    if url:
        await _deliver_user_url_fetch(
            update,
            context,
            url,
            offer_domain_prompt=False,
            ignore_bad_domain=True,
        )


async def _reply_on_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    em = update.effective_message
    chat = update.effective_chat
    if not chat:
        return
    if em:
        await em.reply_text(text, reply_markup=reply_markup)
    else:
        await context.bot.send_message(chat.id, text, reply_markup=reply_markup)


async def _deliver_user_url_fetch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    *,
    offer_domain_prompt: bool,
    domain_trial: str | None = None,
    ignore_bad_domain: bool = False,
) -> None:
    _purge_expired_pending()
    client: httpx.AsyncClient = context.application.bot_data["http_client"]
    domains = set(load_domains(config.DOMAINS_FILE))
    bad_domains = load_bad_domains(config.DOMAINS_BAD_FILE)
    overrides: set[str] = context.user_data.get("bad_domain_overrides") or set()
    chat = update.effective_chat
    if not chat:
        return

    trial_domain = domain_trial or registrable_domain_from_url(url)
    host = (urlparse(url).hostname or "").lower()
    overridden = bool(
        ignore_bad_domain
        or (host and host_allowed(host, overrides))
        or (trial_domain and trial_domain in overrides)
    )
    if host and host_is_bad(host, bad_domains) and not overridden:
        label = trial_domain or host
        if update.effective_user:
            context.user_data["last_bad_domain"] = {
                "url": url,
                "domain": label if is_valid_registrable_domain(label) else host,
            }
        await _reply_on_chat(
            update,
            context,
            bad_domain_refusal_message(label, for_bot=True),
        )
        return

    if overridden and trial_domain and is_valid_registrable_domain(trial_domain):
        domains.add(trial_domain)

    await context.bot.send_chat_action(chat_id=chat.id, action="typing")
    try:
        result = await fetch_url(
            client,
            url,
            config.FETCH_SOFT_MAX_BYTES,
            domains,
            allow_http=config.ALLOW_HTTP,
            max_redirects=config.MAX_REDIRECTS,
            user_agent=config.USER_AGENT,
        )
    except FetchError as e:
        if e.blocked_domain and update.effective_user:
            context.user_data["last_403"] = {
                "url": getattr(e, "blocked_url", None) or url,
                "domain": e.blocked_domain,
            }
        if offer_domain_prompt and str(e) == "Domain is not on the approved list.":
            reject = getattr(e, "rejected_url", None) or url
            domain = registrable_domain_from_url(reject)
            if domain and is_valid_registrable_domain(domain):
                current = load_domains(config.DOMAINS_FILE)
                bad = load_bad_domains(config.DOMAINS_BAD_FILE)
                if domain in bad and domain not in overrides:
                    if update.effective_user:
                        context.user_data["last_bad_domain"] = {
                            "url": url,
                            "domain": domain,
                        }
                    await _reply_on_chat(
                        update,
                        context,
                        bad_domain_refusal_message(domain, for_bot=True),
                    )
                    return
                if domain not in current and update.effective_user:
                    _, kb = _pending_domain_put(
                        update.effective_user.id,
                        url,
                        domain,
                    )
                    await _reply_on_chat(
                        update,
                        context,
                        f"The domain {domain} is not on your approved list.\n\n"
                        "Add it so links to this site work?",
                        reply_markup=kb,
                    )
                    return
        err_text = _fetch_error_reply(e)
        await _reply_on_chat(update, context, err_text)
        if domain_trial:
            await _offer_mark_domain_bad(
                update,
                context,
                domain=domain_trial,
                url=url,
                reason=err_text,
            )
        return
    except (httpx.HTTPError, OSError) as e:
        logger.exception("fetch failed")
        await _reply_on_chat(update, context, f"Network error: {e}")
        return

    if isinstance(result, FetchOversizeKnown):
        soft = result.soft_limit
        msg = (
            f"Response size is about {_format_size(result.content_length)} "
            f"(soft limit {_format_size(soft)}). Proceed up to "
            f"{_format_size(config.FETCH_HARD_MAX_BYTES)}?"
        )
        if not update.effective_user:
            return
        _, kb = _pending_put(update.effective_user.id, result.final_url)
        await _reply_on_chat(update, context, msg, reply_markup=kb)
        return

    if isinstance(result, FetchOversizeUnknown):
        msg = (
            f"Download exceeded soft limit ({_format_size(result.soft_limit)}) "
            f"after {_format_size(result.bytes_read)} (total size unknown). "
            f"Proceed and refetch up to {_format_size(config.FETCH_HARD_MAX_BYTES)}?"
        )
        if not update.effective_user:
            return
        _, kb = _pending_put(update.effective_user.id, result.final_url)
        await _reply_on_chat(update, context, msg, reply_markup=kb)
        return

    if isinstance(result, FetchOk):
        await _process_fetched_html(
            update,
            context,
            result.content,
            result.final_url,
            result.content_type,
            domain_trial=domain_trial,
        )


async def ignore_unlisted_slash_command(
    _update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Consume updates for other bots' commands in the same chat; do not reply."""
    return


async def _run_save_to_disk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    user_id = update.effective_user.id
    chat_id = _action_chat_id(update)
    if chat_id is None:
        return

    purge_article_cache(config.ARTICLE_CACHE_DIR, config.LAST_ARTICLE_TTL_SECONDS)
    cached = load_last_article(
        config.ARTICLE_CACHE_DIR,
        user_id,
        ttl_seconds=config.LAST_ARTICLE_TTL_SECONDS,
    )
    if cached is None:
        await _bot_send_text(
            update,
            context,
            "No recent article. Send a news URL first, then ask me to save it.",
            chat_id=chat_id,
        )
        return

    try:
        path = save_article_text_file(cached, config.TEST_ARTICLES_DIR)
    except OSError as e:
        logger.exception("save to disk failed")
        await _bot_send_text(update, context, f"Could not save file: {e}", chat_id=chat_id)
        return

    await _bot_send_text(update, context, f"Saved to:\n{path}", chat_id=chat_id)


def _schedule_speak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run TTS in the background so fetches and other commands are not blocked."""
    task = asyncio.create_task(
        _run_speak(update, context),
        name="speak-article",
    )
    tasks: set[asyncio.Task] = context.application.bot_data.setdefault("speak_tasks", set())
    tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("Background speak task failed: %s", exc, exc_info=exc)

    task.add_done_callback(_done)


async def _run_speak_phrase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    phrase: str,
) -> None:
    """Synthesize and send a short test clip (no intro/outro branding)."""
    if not update.effective_user:
        return
    phrase = phrase.strip()
    if not phrase:
        return

    chat_id = _action_chat_id(update)
    user_id = update.effective_user.id
    if chat_id is None:
        return

    if not config.TTS_ENABLED:
        await _bot_send_text(
            update, context, "Text-to-speech is disabled on this bot.", chat_id=chat_id
        )
        return

    await _bot_send_text(update, context, "Generating test audio…", chat_id=chat_id)
    await context.bot.send_chat_action(chat_id=chat_id, action="upload_audio")

    config.AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.AUDIO_OUTPUT_DIR / f"speak_test_{user_id}_{int(time.time())}.mp3"

    try:
        await asyncio.to_thread(
            synthesize_to_mp3,
            phrase,
            out_path,
            title=None,
            source_domain=None,
            chunk_chars=1000,
            lead_silence_ms=0,
        )
    except Exception as e:
        logger.exception("speak phrase TTS failed")
        await _bot_send_text(update, context, f"Could not generate audio: {e}", chat_id=chat_id)
        return

    title = phrase[:64]
    try:
        with out_path.open("rb") as audio_file:
            await context.bot.send_audio(
                chat_id,
                audio=audio_file,
                title=title,
                performer="News Catcher",
            )
    except TelegramError as e:
        await _bot_send_text(update, context, f"Could not send audio: {e}", chat_id=chat_id)
    finally:
        out_path.unlink(missing_ok=True)


async def _run_speak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    user_id = update.effective_user.id
    chat_id = _action_chat_id(update)
    if chat_id is None:
        return

    if not config.TTS_ENABLED:
        await _bot_send_text(
            update, context, "Text-to-speech is disabled on this bot.", chat_id=chat_id
        )
        return

    purge_article_cache(config.ARTICLE_CACHE_DIR, config.LAST_ARTICLE_TTL_SECONDS)
    cached = load_last_article(
        config.ARTICLE_CACHE_DIR,
        user_id,
        ttl_seconds=config.LAST_ARTICLE_TTL_SECONDS,
    )
    if cached is None:
        await _bot_send_text(
            update,
            context,
            "No recent article. Send a news URL first, then ask me to speak.",
            chat_id=chat_id,
        )
        return

    await _bot_send_text(
        update,
        context,
        "🎵Generating audio… this may take a few minutes. "
        "You can fetch another article while you wait.",
        chat_id=chat_id,
    )
    await context.bot.send_chat_action(chat_id=chat_id, action="upload_audio")

    source_domain = registrable_domain_from_url(cached.url)
    config.AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_name = telegram_audio_filename(source_domain, cached.title)
    out_path = config.AUDIO_OUTPUT_DIR / f"{user_id}_{int(time.time())}_{out_name}"

    try:
        await asyncio.to_thread(
            synthesize_to_mp3,
            cached.text,
            out_path,
            title=cached.title,
            source_domain=source_domain,
        )
    except Exception as e:
        logger.exception("TTS failed")
        await _bot_send_text(update, context, f"Could not generate audio: {e}", chat_id=chat_id)
        return

    parts = split_mp3_for_telegram(out_path, config.TELEGRAM_AUDIO_MAX_BYTES)
    title = (cached.title or "Article")[:64]
    total = len(parts)
    for i, part in enumerate(parts, start=1):
        part_title = f"{title} ({i}/{total})" if total > 1 else title
        filename = telegram_audio_filename(
            source_domain, cached.title, part=i, total=total
        )
        try:
            with part.open("rb") as audio_file:
                await context.bot.send_audio(
                    chat_id,
                    audio=InputFile(audio_file, filename=filename),
                    title=part_title,
                    performer="News Catcher",
                )
        except TelegramError as e:
            await _bot_send_text(update, context, f"Could not send audio: {e}", chat_id=chat_id)
            return

    await context.bot.send_message(
        chat_id,
        "🎵Anything sound wrong? Tap Fix a word to tune pronunciation.",
        reply_markup=_after_audio_keyboard(),
    )


async def _handle_save_to_disk_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _run_save_to_disk(update, context)


async def _resolve_pronunciation_alternatives(
    from_text: str,
    alternatives: list[str],
    *,
    article_context: str | None = None,
    user_feedback: str | None = None,
    avoid_spellings: list[str] | None = None,
) -> tuple[list[str], str | None, str | None]:
    """Use explicit alts, or ask Ollama. Returns (alts, warning, error)."""
    alts = [a.strip() for a in alternatives if a.strip()]
    if alts:
        return alts, None, None
    if not config.PRONUNCIATION_SUGGEST_ENABLED:
        return [], None, "Pronunciation suggestions are disabled (PRONUNCIATION_SUGGEST_ENABLED=0)."
    result = await asyncio.to_thread(
        suggest_pronunciations,
        from_text,
        article_context=article_context,
        user_feedback=user_feedback,
        avoid_spellings=avoid_spellings,
    )
    return result.suggestions, result.warning, result.error


async def _send_pronunciation_samples(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    user_id: int,
    from_text: str,
    alternatives: list[str],
) -> None:
    if not alternatives:
        await context.bot.send_message(
            chat_id,
            f"No pronunciation suggestions for {from_text!r}. "
            "Try /pronounce with spellings, or check that Ollama is running.",
        )
        return

    await context.bot.send_message(
        chat_id,
        f"Generating {len(alternatives)} sample(s) for {from_text!r}…",
    )
    config.AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    batch_id = secrets.token_hex(8)
    PRONUNCIATION_BATCHES[batch_id] = []

    for alt in alternatives:
        out_path = config.AUDIO_OUTPUT_DIR / f"pronounce_{user_id}_{secrets.token_hex(4)}.mp3"
        try:
            await asyncio.to_thread(
                synthesize_pronunciation_sample,
                alt,
                out_path,
            )
        except Exception as e:
            logger.exception("pronunciation sample failed")
            await context.bot.send_message(
                chat_id,
                f"Could not synthesize {alt!r}: {e}",
            )
            continue

        label = f"Save: {from_text!r} → {alt!r}"
        if len(label) > 60:
            label = f"Save → {alt!r}"
        try:
            with out_path.open("rb") as audio_file:
                sent = await context.bot.send_audio(
                    chat_id,
                    audio=audio_file,
                    title=f"Pronounce: {alt}",
                    caption=(
                        f"Hear how {alt!r} sounds.\n"
                        f"Tap Save to write {from_text!r} → {alt!r} "
                        f"in tts_replacements.json (whole word).\n"
                        f"Use Save · ignore case for case-insensitive matching."
                    ),
                )
        except TelegramError as e:
            await context.bot.send_message(chat_id, f"Could not send sample for {alt!r}: {e}")
            continue
        finally:
            out_path.unlink(missing_ok=True)

        token = _pending_pronunciation_put(
            user_id,
            from_text,
            alt,
            chat_id=chat_id,
            message_id=sent.message_id,
            batch_id=batch_id,
        )
        PRONUNCIATION_BATCHES[batch_id].append((token, chat_id, sent.message_id))
        save_row = [
            InlineKeyboardButton(label, callback_data=f"r:{token}"),
            InlineKeyboardButton("Save · ignore case", callback_data=f"ri:{token}"),
        ]
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=sent.message_id,
                reply_markup=InlineKeyboardMarkup([save_row]),
            )
        except TelegramError:
            pass


async def _run_word_fix_for_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    from_text: str,
    article_context: str | None,
    article_title: str | None = None,
    article_text: str | None = None,
    user_feedback: str | None = None,
    avoid_spellings: list[str] | None = None,
    show_followup: bool = True,
) -> None:
    chat_id = _action_chat_id(update)
    user_id = update.effective_user.id if update.effective_user else None
    if chat_id is None or user_id is None:
        return

    if not config.TTS_ENABLED:
        await _bot_send_text(
            update, context, "Text-to-speech is disabled on this bot.", chat_id=chat_id
        )
        return

    if user_feedback:
        await _bot_send_text(
            update,
            context,
            "Sending your feedback to Ollama and generating new samples…",
            chat_id=chat_id,
        )
    elif not avoid_spellings:
        await _bot_send_text(
            update,
            context,
            f"Asking Ollama for pronunciation ideas for {from_text!r}…",
            chat_id=chat_id,
        )
    else:
        await _bot_send_text(
            update,
            context,
            f"Retrying with new spelling ideas for {from_text!r}…",
            chat_id=chat_id,
        )

    alternatives, ollama_warning, ollama_error = await _resolve_pronunciation_alternatives(
        from_text,
        [],
        article_context=article_context,
        user_feedback=user_feedback,
        avoid_spellings=avoid_spellings,
    )
    if ollama_warning:
        await _bot_send_text(update, context, ollama_warning, chat_id=chat_id)
    if ollama_error:
        await _bot_send_text(update, context, ollama_error, chat_id=chat_id)
        if show_followup:
            await _send_word_fix_followup(
                context,
                chat_id=chat_id,
                user_id=user_id,
                article_title=article_title,
                article_text=article_text,
                from_text=from_text,
                tried_spellings=list(avoid_spellings or []),
                message=(
                    f"Ollama had no ideas for {from_text!r}. "
                    "Tap My spelling to try your own, or Retry / Send feedback."
                ),
            )
        return
    if not alternatives:
        if show_followup:
            await _send_word_fix_followup(
                context,
                chat_id=chat_id,
                user_id=user_id,
                article_title=article_title,
                article_text=article_text,
                from_text=from_text,
                tried_spellings=list(avoid_spellings or []),
                message=(
                    f"No new Ollama suggestions for {from_text!r}. "
                    "Tap My spelling to try your own."
                ),
            )
        return

    tried = list(avoid_spellings or [])
    for alt in alternatives:
        if alt not in tried:
            tried.append(alt)

    await _send_pronunciation_samples(
        context,
        chat_id=chat_id,
        user_id=user_id,
        from_text=from_text,
        alternatives=alternatives,
    )

    if show_followup:
        await _send_word_fix_followup(
            context,
            chat_id=chat_id,
            user_id=user_id,
            article_title=article_title,
            article_text=article_text,
            from_text=from_text,
            tried_spellings=tried,
        )


async def cmd_add_pronunciation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /add_pronunciation <from> <to>\n"
            "Example: /add_pronunciation U.S. United States\n"
            "Example: /add_pronunciation Polish Poleish"
        )
        return
    from_text = args[0]
    to_text = " ".join(args[1:])
    try:
        added = add_literal_replacement(from_text, to_text)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return
    path = config.TTS_REPLACEMENTS_FILE.resolve()
    if added:
        await update.message.reply_text(
            f"Added pronunciation rule:\n  {from_text!r} → {to_text!r}\n\nSaved to {path}",
            reply_markup=_speak_phrase_keyboard(),
        )
    else:
        await update.message.reply_text(
            f"Updated existing rule:\n  {from_text!r} → {to_text!r}\n\nSaved to {path}",
            reply_markup=_speak_phrase_keyboard(),
        )


async def cmd_find_pronunciation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Search tts_replacements.json for similar rules; offer pronounce / delete."""
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /find_pronunciation <word or fragment>\n\n"
            "Example: /find_pronunciation US\n"
            "Shows the closest saved rules. You can hear them or delete them."
        )
        return

    query = " ".join(args).strip()
    matches = find_literal_replacements(query, limit=8)
    if not matches:
        await update.message.reply_text(
            f"No saved rules close to {query!r}.\n"
            "Try /pronounce to create one, or /add_pronunciation <from> <to>."
        )
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return

    lines = [f"Closest rules for {query!r}:"]
    rows: list[list[InlineKeyboardButton]] = []
    match_pairs: list[tuple[str, str]] = []
    for i, rule in enumerate(matches, start=1):
        lines.append(f"{i}. {rule.from_text!r} → {rule.to_text!r}")
        match_pairs.append((rule.from_text, rule.to_text))
        token = _pending_rule_action_put(user_id, rule.from_text, rule.to_text)
        rows.append(
            [
                InlineKeyboardButton(
                    f"Pronounce {i}", callback_data=f"rp:{token}"
                ),
                InlineKeyboardButton(
                    f"Delete {i}", callback_data=f"rd:{token}"
                ),
            ]
        )

    session = _pending_find_session_put(user_id, match_pairs)
    rows.append(
        [
            InlineKeyboardButton("Yes — pronounce any", callback_data=f"ra:{session}"),
            InlineKeyboardButton("No", callback_data=f"rn:{session}"),
        ]
    )
    await update.message.reply_text(
        "\n".join(lines) + "\n\nPronounce any of these?",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def cmd_delete_pronunciation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /delete_pronunciation <from>\n"
            "Example: /delete_pronunciation a.m.\n\n"
            "Or use /find_pronunciation <word> and tap Delete."
        )
        return
    from_text = " ".join(args).strip()
    # Exact delete first; if missing, offer closest matches.
    try:
        removed = remove_literal_replacement(from_text)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return
    if removed:
        await update.message.reply_text(
            f"Deleted rule for {from_text!r} from {config.TTS_REPLACEMENTS_FILE.resolve()}"
        )
        return

    matches = find_literal_replacements(from_text, limit=5)
    if not matches:
        await update.message.reply_text(f"No rule found for {from_text!r}.")
        return
    lines = [
        f"No exact rule for {from_text!r}. Closest matches — tap Delete, "
        "or retry with the exact `from` text:"
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for i, rule in enumerate(matches, start=1):
        lines.append(f"{i}. {rule.from_text!r} → {rule.to_text!r}")
        token = _pending_rule_action_put(
            update.effective_user.id, rule.from_text, rule.to_text
        )
        rows.append(
            [InlineKeyboardButton(f"Delete {i}", callback_data=f"rd:{token}")]
        )
    await update.message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(rows)
    )


async def on_rule_action_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    if not _allowed_user(update.effective_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return

    data = query.data
    if data.startswith("ra:") or data.startswith("rn:"):
        session = _pending_find_session_get(data[3:])
        if session is None:
            await query.answer("Session expired. Run /find_pronunciation again.", show_alert=True)
            return
        if session.user_id != update.effective_user.id:
            await query.answer("Not your request.", show_alert=True)
            return
        await query.answer()
        chat_id = _action_chat_id(update)
        if chat_id is None:
            return
        if data.startswith("rn:"):
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except TelegramError:
                pass
            await context.bot.send_message(
                chat_id,
                "Okay — use Delete on a row above if you want to remove a rule.",
            )
            return
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        await context.bot.send_message(chat_id, "Generating samples for matched rules…")
        for from_text, to_text in session.matches:
            await _send_pronunciation_samples(
                context,
                chat_id=chat_id,
                user_id=session.user_id,
                from_text=from_text,
                alternatives=[to_text],
            )
        return

    if not (data.startswith("rp:") or data.startswith("rd:")):
        return
    action = data[:2]
    token = data[3:]
    pending = _pending_rule_action_get(token)
    if pending is None:
        await query.answer("Expired. Run /find_pronunciation again.", show_alert=True)
        return
    if pending.user_id != update.effective_user.id:
        await query.answer("Not your request.", show_alert=True)
        return

    await query.answer()
    chat_id = _action_chat_id(update)
    if chat_id is None:
        return

    if action == "rd":
        PENDING_RULE_ACTION.pop(token, None)
        try:
            removed = remove_literal_replacement(pending.from_text)
        except ValueError as e:
            await context.bot.send_message(chat_id, str(e))
            return
        if removed:
            await context.bot.send_message(
                chat_id,
                f"Deleted {pending.from_text!r} → {pending.to_text!r}",
            )
        else:
            await context.bot.send_message(
                chat_id, f"Rule {pending.from_text!r} was already gone."
            )
        return

    # pronounce one
    await context.bot.send_message(
        chat_id,
        f"Playing saved pronunciation:\n  {pending.from_text!r} → {pending.to_text!r}",
    )
    await _send_pronunciation_samples(
        context,
        chat_id=chat_id,
        user_id=pending.user_id,
        from_text=pending.from_text,
        alternatives=[pending.to_text],
    )


async def cmd_pronounce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate short audio samples for spelling alternatives; save via inline button."""
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    if not config.TTS_ENABLED:
        await update.message.reply_text("Text-to-speech is disabled on this bot.")
        return

    args = context.args or []
    if len(args) < 1:
        await update.message.reply_text(
            "Usage: /pronounce <word-in-article> [<how-to-say-it> …]\n\n"
            "With one word, Ollama suggests spellings to try.\n"
            "Example:\n"
            "  /pronounce Polish\n"
            "  /pronounce Polish Poleish Pole-ish\n\n"
            "You get a short audio clip per spelling. Tap Save on the one you want.\n\n"
            "Or tap Fix a word after audio is sent, or use /add_pronunciation to skip audio."
        )
        return

    from_text = args[0]
    alternatives = args[1:]
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return

    article_context: str | None = None
    if len(alternatives) == 0:
        cached = load_last_article(
            config.ARTICLE_CACHE_DIR,
            update.effective_user.id,
            ttl_seconds=config.LAST_ARTICLE_TTL_SECONDS,
        )
        if cached:
            article_context = _article_context_snippet(cached.title, cached.text, from_text)

    if not alternatives and config.PRONUNCIATION_SUGGEST_ENABLED:
        await update.message.reply_text(
            f"Asking Ollama for pronunciation ideas for {from_text!r}…"
        )

    resolved, ollama_warning, ollama_error = await _resolve_pronunciation_alternatives(
        from_text,
        alternatives,
        article_context=article_context,
    )
    if ollama_warning:
        await update.message.reply_text(ollama_warning)
    if ollama_error:
        await update.message.reply_text(ollama_error)
        return
    if not resolved:
        await update.message.reply_text(
            f"No suggestions for {from_text!r}. "
            f"Add spellings: /pronounce {from_text} your-spelling"
        )
        return

    await _send_pronunciation_samples(
        context,
        chat_id=chat_id,
        user_id=update.effective_user.id,
        from_text=from_text,
        alternatives=resolved,
    )


async def on_pronunciation_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    data = query.data
    if data.startswith("ri:"):
        ignore_case = True
        token = data[3:]
    elif data.startswith("r:"):
        ignore_case = False
        token = data[2:]
    else:
        return
    pending = _pending_pronunciation_get(token)
    if pending is None:
        await query.answer("This sample expired. Run /pronounce again.", show_alert=True)
        return
    if pending.user_id != update.effective_user.id:
        await query.answer("Not your request.", show_alert=True)
        return

    await query.answer()
    _pending_pronunciation_remove(token)
    await _delete_other_pronunciation_samples(
        context, batch_id=pending.batch_id, keep_token=token
    )
    try:
        added = add_literal_replacement(
            pending.from_text,
            pending.to_text,
            whole_word=True,
            ignore_case=ignore_case,
        )
    except ValueError as e:
        await query.edit_message_caption(caption=f"Failed to save: {e}")
        return

    path = config.TTS_REPLACEMENTS_FILE.resolve()
    verb = "Added" if added else "Updated"
    case_note = "ignore case" if ignore_case else "case sensitive"
    await query.edit_message_caption(
        caption=(
            f"{verb} in tts_replacements.json:\n"
            f"  {pending.from_text!r} → {pending.to_text!r}\n"
            f"  whole word · {case_note}\n"
            f"File: {path}\n\n"
            "Tap Test phrase or send /speak <sentence> to hear it spoken."
        ),
        reply_markup=_speak_phrase_keyboard(),
    )


async def on_speak_phrase_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    if not _allowed_user(update.effective_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return
    if query.data != _CALLBACK_SPEAK_PHRASE:
        return

    await query.answer()
    chat_id = _action_chat_id(update)
    if chat_id is None:
        return

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    _pending_speak_phrase_put(update.effective_user.id)
    await context.bot.send_message(
        chat_id,
        "Reply with a sentence to test, or send:\n"
        "/speak The train arrives at 5 a.m.",
    )


async def on_word_fix_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    if not _allowed_user(update.effective_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return

    data = query.data
    if data not in (
        _CALLBACK_WORD_FIX_RETRY,
        _CALLBACK_WORD_FIX_FEEDBACK,
        _CALLBACK_WORD_FIX_CUSTOM,
    ):
        return

    user_id = update.effective_user.id
    pending = _pending_word_fix_get(user_id)
    if pending is None or not pending.from_text:
        await query.answer("Session expired. Send /fixaword to start again.", show_alert=True)
        return

    await query.answer()
    chat_id = _action_chat_id(update)
    if chat_id is None:
        return

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    if data == _CALLBACK_WORD_FIX_FEEDBACK:
        _pending_word_fix_put(
            user_id,
            pending.article_title,
            pending.article_text,
            from_text=pending.from_text,
            tried_spellings=pending.tried_spellings,
            step="awaiting_feedback",
        )
        await context.bot.send_message(
            chat_id,
            f"Tell Ollama what sounded wrong with the samples for {pending.from_text!r}, "
            "and what kind of spelling to try.\n\n"
            "Example: \"Too robotic — try something that sounds like the country, not the adjective.\"",
        )
        return

    if data == _CALLBACK_WORD_FIX_CUSTOM:
        _pending_word_fix_put(
            user_id,
            pending.article_title,
            pending.article_text,
            from_text=pending.from_text,
            tried_spellings=pending.tried_spellings,
            step="awaiting_custom_spelling",
        )
        await context.bot.send_message(
            chat_id,
            f"Reply with how you want {pending.from_text!r} to sound when read aloud.\n\n"
            "Examples:\n"
            "  a.m. → ay em\n"
            "  U.S. → United States\n"
            "  Polish → Poleish",
        )
        return

    article_context = _article_context_snippet(
        pending.article_title,
        pending.article_text or "",
        pending.from_text,
    )
    await _run_word_fix_for_text(
        update,
        context,
        from_text=pending.from_text,
        article_context=article_context,
        article_title=pending.article_title,
        article_text=pending.article_text,
        avoid_spellings=pending.tried_spellings,
    )


async def on_article_action_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    if not _allowed_user(update.effective_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return

    data = query.data
    if data not in (_CALLBACK_SPEAK, _CALLBACK_SAVE, _CALLBACK_WORD_FIX):
        await query.answer()
        return

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    if data == _CALLBACK_SPEAK:
        _schedule_speak(update, context)
    elif data == _CALLBACK_SAVE:
        await _run_save_to_disk(update, context)
    elif data == _CALLBACK_WORD_FIX:
        user_id = update.effective_user.id
        chat_id = _action_chat_id(update)
        if chat_id is None:
            return
        await _start_word_fix_prompt(context, user_id=user_id, chat_id=chat_id)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.text:
        return
    _purge_expired_pending()
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    text = update.message.text.strip()
    if is_speak_phrase(text):
        _schedule_speak(update, context)
        return
    if is_save_to_disk_phrase(text):
        await _handle_save_to_disk_request(update, context)
        return

    user_id = update.effective_user.id
    pending_speak = _pending_speak_phrase_get(user_id)
    if pending_speak is not None:
        if text.startswith("/") and not text.lower().startswith("/speak"):
            await update.message.reply_text(
                "Waiting for a test sentence. Reply with plain text or /speak <phrase>."
            )
            return
        phrase = text
        if text.lower().startswith("/speak"):
            parts = text.split(None, 1)
            phrase = parts[1].strip() if len(parts) > 1 else ""
        if not phrase:
            await update.message.reply_text(
                "Send a sentence to test, e.g.\n/speak The meeting starts at 5 a.m."
            )
            return
        _pending_speak_phrase_clear(user_id)
        await _run_speak_phrase(update, context, phrase)
        return

    pending_word = _pending_word_fix_get(user_id)
    if pending_word is not None:
        if pending_word.step == "awaiting_feedback":
            if not text or text.startswith("/"):
                await update.message.reply_text(
                    "Send plain-text feedback for Ollama (what sounded wrong, what to try)."
                )
                return
            if not pending_word.from_text:
                _pending_word_fix_clear(user_id)
                return
            article_context = _article_context_snippet(
                pending_word.article_title,
                pending_word.article_text or "",
                pending_word.from_text,
            )
            await _run_word_fix_for_text(
                update,
                context,
                from_text=pending_word.from_text,
                article_context=article_context,
                article_title=pending_word.article_title,
                article_text=pending_word.article_text,
                user_feedback=text,
                avoid_spellings=pending_word.tried_spellings,
            )
            return

        if pending_word.step == "awaiting_custom_spelling":
            spelling = text.strip()
            if not spelling or spelling.startswith("/"):
                await update.message.reply_text(
                    "Send your spelling as plain text (e.g. ay em for a.m.)."
                )
                return
            if not pending_word.from_text:
                _pending_word_fix_clear(user_id)
                return
            tried = list(pending_word.tried_spellings)
            if spelling not in tried:
                tried.append(spelling)
            await _send_pronunciation_samples(
                context,
                chat_id=update.effective_chat.id,
                user_id=user_id,
                from_text=pending_word.from_text,
                alternatives=[spelling],
            )
            await _send_word_fix_followup(
                context,
                chat_id=update.effective_chat.id,
                user_id=user_id,
                article_title=pending_word.article_title,
                article_text=pending_word.article_text,
                from_text=pending_word.from_text,
                tried_spellings=tried,
                message=f"Your spelling for {pending_word.from_text!r}. Tap Save if it sounds right.",
            )
            return

        if pending_word.step == "after_samples":
            await update.message.reply_text(
                "Tap Retry, Send feedback, or My spelling on the message above, "
                "or send /nevermind to cancel and wait for the next URL."
            )
            return

        if pending_word.step == "awaiting_word":
            word = text.strip()
            if not word or word.startswith("/"):
                await update.message.reply_text(
                    "Send the word or phrase to fix (plain text, not a command)."
                )
                return
            article_context = _article_context_snippet(
                pending_word.article_title,
                pending_word.article_text or "",
                word,
            )
            await _run_word_fix_for_text(
                update,
                context,
                from_text=word,
                article_context=article_context,
                article_title=pending_word.article_title,
                article_text=pending_word.article_text,
            )
            return

    url = _extract_first_url(text)
    if not url:
        return

    _pending_word_fix_clear(user_id)
    _pending_speak_phrase_clear(user_id)
    await _deliver_user_url_fetch(update, context, url, offer_domain_prompt=True)


def _watch_interval_default() -> int:
    return max(
        5,
        int(
            getattr(
                config,
                "WATCHLIST_DEFAULT_INTERVAL_MINUTES",
                DEFAULT_CHECK_INTERVAL_MINUTES,
            )
        ),
    )


async def cmd_watch_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    if not config.WATCHLIST_ENABLED:
        await update.message.reply_text("Watchlist is disabled (WATCHLIST_ENABLED=0).")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /watch_add <website> [check_interval_minutes]\n\n"
            "Example:\n"
            "  /watch_add hackaday.com\n"
            "  /watch_add marktechpost.com 120\n\n"
            "I will poll the site's RSS/Atom feed (or WordPress API) and notify you "
            "when new posts appear. Default interval: "
            f"{_watch_interval_default()} minutes."
        )
        return

    domain = domain_hint_from_user_input(args[0])
    if not domain or not is_valid_registrable_domain(domain):
        await update.message.reply_text(
            "Could not parse a domain. Try: /watch_add example.com"
        )
        return
    domain = normalize_registrable_hint(domain)

    interval = _watch_interval_default()
    if len(args) >= 2:
        try:
            interval = int(args[1])
        except ValueError:
            await update.message.reply_text("Interval must be minutes as an integer.")
            return

    existing = get_site(config.WATCHLIST_FILE, domain)
    site = WatchedSite(
        domain=domain,
        check_interval_minutes=interval,
        feed_url=existing.feed_url if existing else None,
        last_checked_at=existing.last_checked_at if existing else 0.0,
        posts=list(existing.posts) if existing else [],
    )
    upsert_site(config.WATCHLIST_FILE, site)

    # Ensure fetch/read works for this host.
    approved = load_domains(config.DOMAINS_FILE)
    if domain not in approved:
        approved.add(domain)
        save_domains(config.DOMAINS_FILE, approved)

    await update.message.reply_text(
        f"Watching {domain} every {site.check_interval_minutes} minutes.\n"
        f"Also added to approved domains if needed.\n"
        f"Saved to {config.WATCHLIST_FILE.resolve()}\n\n"
        "First check seeds recent posts without notifications. "
        "Use /watch_check to poll now."
    )


async def cmd_watch_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    sites = load_watchlist(config.WATCHLIST_FILE)
    if not sites:
        await update.message.reply_text(
            "No watched sites. Add one with /watch_add example.com"
        )
        return
    lines = ["Watched blogs:"]
    for site in sites:
        feed = site.feed_url or "(auto-discover)"
        lines.append(
            f"• {site.domain} — every {site.check_interval_minutes} min, "
            f"{len(site.posts)} posts remembered\n  feed: {feed}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_watch_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /watch_remove <website>")
        return
    domain = domain_hint_from_user_input(args[0])
    if not domain:
        await update.message.reply_text("Could not parse domain.")
        return
    domain = normalize_registrable_hint(domain)
    if remove_site(config.WATCHLIST_FILE, domain):
        await update.message.reply_text(f"Stopped watching {domain}.")
    else:
        await update.message.reply_text(f"{domain} is not on the watchlist.")


async def cmd_watch_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /watch_interval <website> <minutes>\n"
            "Example: /watch_interval hackaday.com 45"
        )
        return
    domain = domain_hint_from_user_input(args[0])
    if not domain:
        await update.message.reply_text("Could not parse domain.")
        return
    domain = normalize_registrable_hint(domain)
    try:
        minutes = int(args[1])
    except ValueError:
        await update.message.reply_text("Minutes must be an integer.")
        return
    site = set_interval(config.WATCHLIST_FILE, domain, minutes)
    if site is None:
        await update.message.reply_text(f"{domain} is not on the watchlist.")
        return
    await update.message.reply_text(
        f"{domain} will be checked every {site.check_interval_minutes} minutes."
    )


async def cmd_watch_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return
    if not config.WATCHLIST_ENABLED:
        await update.message.reply_text("Watchlist is disabled.")
        return
    await update.message.reply_text("Checking watched sites now…")
    await _run_watchlist_checks(context.application, force=True)


async def _notify_watch_post(
    app: Application,
    *,
    user_id: int,
    site_domain: str,
    post: CandidatePost,
) -> None:
    token = _pending_watch_post_put(user_id, post.url, post.title)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Read", callback_data=f"wr:{token}"),
                InlineKeyboardButton("Speak", callback_data=f"ws:{token}"),
            ]
        ]
    )
    title_html = f"<b>{html_module.escape(post.title)}</b>"
    body = first_paragraph(post.summary) or "(No summary available.)"
    text = (
        f"{title_html}\n\n{html_module.escape(body)}\n\n"
        f"{html_module.escape(site_domain)}\n{html_module.escape(post.url)}"
    )
    try:
        await app.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except TelegramError as e:
        logger.warning("Watch notify failed for user %s: %s", user_id, e)


async def _run_watchlist_checks(app: Application, *, force: bool = False) -> None:
    if not config.WATCHLIST_ENABLED:
        return
    sites = load_watchlist(config.WATCHLIST_FILE)
    if not sites:
        return
    client: httpx.AsyncClient | None = app.bot_data.get("http_client")
    if client is None:
        return

    for site in sites:
        if not force and not site_is_due(site):
            continue
        result = await check_site(client, site)
        upsert_site(config.WATCHLIST_FILE, result.site)
        if result.error and force:
            for uid in sorted(config.ALLOWED_TELEGRAM_USER_IDS):
                try:
                    await app.bot.send_message(
                        chat_id=uid,
                        text=f"Watch check for {site.domain}: {result.error}",
                    )
                except TelegramError:
                    pass
        for post in result.new_posts:
            for uid in sorted(config.ALLOWED_TELEGRAM_USER_IDS):
                await _notify_watch_post(
                    app, user_id=uid, site_domain=result.site.domain, post=post
                )


async def _watchlist_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_watchlist_checks(context.application, force=False)


async def _watchlist_loop(app: Application) -> None:
    await asyncio.sleep(min(45, config.WATCHLIST_TICK_SECONDS))
    while True:
        try:
            await _run_watchlist_checks(app, force=False)
        except Exception:
            logger.exception("watchlist loop failed")
        await asyncio.sleep(max(15, config.WATCHLIST_TICK_SECONDS))


async def _fetch_article_into_cache(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    *,
    user_id: int,
) -> str | None:
    """Fetch and cache an article; return error message or None on success."""
    client: httpx.AsyncClient = context.application.bot_data["http_client"]
    domains = load_domains(config.DOMAINS_FILE)
    try:
        result = await fetch_url(
            client,
            url,
            config.FETCH_SOFT_MAX_BYTES,
            domains,
            allow_http=config.ALLOW_HTTP,
            max_redirects=config.MAX_REDIRECTS,
            user_agent=config.USER_AGENT,
        )
    except FetchError as e:
        return _fetch_error_reply(e)
    except (httpx.HTTPError, OSError) as e:
        return f"Network error: {e}"

    if not isinstance(result, FetchOk):
        return "Article is large — paste the URL to fetch with oversize confirmation."

    try:
        article = extract_article(result.content, result.final_url)
    except Exception as e:
        return f"Could not extract article text: {e}"
    if not article.text.strip():
        return "No article body found."
    save_last_article(
        config.ARTICLE_CACHE_DIR,
        user_id,
        result.final_url,
        article.title,
        article.text,
    )
    return None


async def on_watch_post_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    if not _allowed_user(update.effective_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return
    data = query.data
    if not (data.startswith("wr:") or data.startswith("ws:")):
        return
    pending = _pending_watch_post_get(data[3:])
    if pending is None:
        await query.answer("This post expired. Wait for the next watch notify.", show_alert=True)
        return
    if pending.user_id != update.effective_user.id:
        await query.answer("Not your request.", show_alert=True)
        return

    await query.answer()
    chat_id = _action_chat_id(update)
    if chat_id is None:
        return

    if data.startswith("wr:"):
        await context.bot.send_message(chat_id, f"Reading: {pending.title}")
        await _deliver_user_url_fetch(
            update, context, pending.url, offer_domain_prompt=True
        )
        return

    # Speak
    await context.bot.send_message(chat_id, f"Fetching for audio: {pending.title}")
    err = await _fetch_article_into_cache(
        update, context, pending.url, user_id=pending.user_id
    )
    if err:
        await context.bot.send_message(chat_id, err)
        return
    _schedule_speak(update, context)


async def on_domain_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    _purge_expired_pending()
    data = query.data
    if len(data) < 3 or data[1] != ":":
        await query.answer()
        return
    action, token = data[0], data[2:]
    if action not in ("y", "n"):
        return

    if action == "n":
        pending = _pending_domain_get(token)
        if pending is None:
            await query.answer()
            await query.edit_message_text("This confirmation expired or is invalid.")
            return
        if pending.user_id != update.effective_user.id:
            await query.answer("Not your request.", show_alert=True)
            return
        await query.answer()
        _pending_domain_remove(token)
        await query.edit_message_text(
            "Okay, domain not added. You can still use /add_domain later."
        )
        return

    # action == "y"
    pending = _pending_domain_get(token)
    if pending is None:
        await query.answer()
        await query.edit_message_text("This confirmation expired or is invalid.")
        return
    if pending.user_id != update.effective_user.id:
        await query.answer("Not your request.", show_alert=True)
        return

    await query.answer()
    _pending_domain_remove(token)

    domain = normalize_registrable_hint(pending.domain)
    if not is_valid_registrable_domain(domain):
        await query.edit_message_text("That domain label is invalid; use /add_domain manually.")
        return

    current = load_domains(config.DOMAINS_FILE)
    current.add(domain)
    save_domains(config.DOMAINS_FILE, current)
    await query.edit_message_text(f"Added {domain}. Fetching…")
    await _deliver_user_url_fetch(
        update,
        context,
        pending.url,
        offer_domain_prompt=False,
        domain_trial=domain,
    )


async def on_domain_bad_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    _purge_expired_pending()
    data = query.data
    if len(data) < 3 or data[1] != ":":
        await query.answer()
        return
    action, token = data[0], data[2:]
    if action not in ("b", "k"):
        return

    pending = _pending_domain_bad_get(token)
    if pending is None:
        await query.answer("This confirmation expired.", show_alert=True)
        return
    if pending.user_id != update.effective_user.id:
        await query.answer("Not your request.", show_alert=True)
        return

    await query.answer()
    _pending_domain_bad_remove(token)

    if action == "k":
        await query.edit_message_text(
            f"Kept {pending.domain} on the approved list. "
            "You can retry the URL or use /fix_403 if blocked."
        )
        return

    domain = normalize_registrable_hint(pending.domain)
    if not is_valid_registrable_domain(domain):
        await query.edit_message_text("Invalid domain; not changed.")
        return

    add_bad_domain(config.DOMAINS_BAD_FILE, config.DOMAINS_FILE, domain)
    await query.edit_message_text(
        f"Blocked {domain} and removed it from the approved list.\n"
        f"Saved to {config.DOMAINS_BAD_FILE.resolve()}"
    )


async def on_oversize_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    _purge_expired_pending()
    data = query.data
    if len(data) < 3 or data[1] != ":":
        await query.answer()
        return
    action, token = data[0], data[2:]
    pending = _pending_get(token)
    if pending is None:
        await query.answer()
        await query.edit_message_text("This confirmation expired or is invalid.")
        return
    if pending.user_id != update.effective_user.id:
        await query.answer("Not your request.", show_alert=True)
        return

    await query.answer()
    _pending_remove(token)

    if action == "x":
        await query.edit_message_text("Cancelled (oversized download).")
        return

    if action != "p":
        return

    client: httpx.AsyncClient = context.application.bot_data["http_client"]
    domains = load_domains(config.DOMAINS_FILE)
    await query.edit_message_text("Downloading with raised limit…")
    try:
        result = await fetch_url(
            client,
            pending.url,
            config.FETCH_HARD_MAX_BYTES,
            domains,
            allow_http=config.ALLOW_HTTP,
            max_redirects=config.MAX_REDIRECTS,
            user_agent=config.USER_AGENT,
        )
    except FetchError as e:
        await context.bot.send_message(query.message.chat_id, _fetch_error_reply(e))
        return
    except (httpx.HTTPError, OSError) as e:
        logger.exception("fetch failed")
        await context.bot.send_message(query.message.chat_id, f"Network error: {e}")
        return

    if isinstance(result, FetchOversizeKnown):
        await context.bot.send_message(
            query.message.chat_id,
            f"Still too large: about {_format_size(result.content_length)} "
            f"(hard max {_format_size(config.FETCH_HARD_MAX_BYTES)}).",
        )
        return
    if isinstance(result, FetchOversizeUnknown):
        await context.bot.send_message(
            query.message.chat_id,
            "Response still exceeds the hard size limit.",
        )
        return
    if isinstance(result, FetchOk):
        fake_update = update
        await _process_fetched_html(
            fake_update,
            context,
            result.content,
            result.final_url,
            result.content_type,
        )


async def _post_init(app: Application) -> None:
    config.ARTICLE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    config.WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.ensure_test_articles_dir()
    purge_article_cache(config.ARTICLE_CACHE_DIR, config.LAST_ARTICLE_TTL_SECONDS)
    app.bot_data["http_client"] = httpx.AsyncClient(
        timeout=httpx.Timeout(config.FETCH_TIMEOUT_SECONDS),
        follow_redirects=False,
    )
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "Help and how to use the bot"),
                BotCommand("speak", "Test a phrase with current pronunciations"),
                BotCommand("fixaword", "Fix a mispronounced word in the last audio"),
                BotCommand("pronounce", "Preview pronunciation samples for a word"),
                BotCommand("find_pronunciation", "Find saved rules; pronounce or delete"),
                BotCommand("delete_pronunciation", "Delete a pronunciation rule"),
                BotCommand("add_pronunciation", "Add a pronunciation rule (no audio)"),
                BotCommand("nevermind", "Cancel in-progress prompts; wait for URL"),
                BotCommand("watch_add", "Watch a blog for new posts"),
                BotCommand("watch_list", "List watched blogs"),
                BotCommand("watch_remove", "Stop watching a blog"),
                BotCommand("watch_interval", "Set how often to check a blog"),
                BotCommand("watch_check", "Check watched blogs now"),
                BotCommand("list_domains", "List approved news domains"),
                BotCommand("add_domain", "Add an approved domain"),
                BotCommand("remove_domain", "Remove an approved domain"),
                BotCommand("list_bad_domains", "List domains marked as bad"),
                BotCommand("remove_bad_domain", "Remove a domain from the bad list"),
                BotCommand(
                    "override_bad_domain",
                    "Temporarily allow a bad domain (troubleshooting)",
                ),
                BotCommand("fix_403", "Tips for sites that block the bot"),
            ]
        )
    except TelegramError as e:
        logger.warning("set_my_commands failed: %s", e)

    if config.WATCHLIST_ENABLED:
        if app.job_queue is not None:
            app.job_queue.run_repeating(
                _watchlist_job,
                interval=max(15, config.WATCHLIST_TICK_SECONDS),
                first=45,
                name="watchlist",
            )
        else:
            logger.warning(
                "JobQueue unavailable; install python-telegram-bot[job-queue]. "
                "Starting asyncio watchlist loop instead."
            )
            app.bot_data["watchlist_task"] = asyncio.create_task(
                _watchlist_loop(app), name="watchlist-loop"
            )

    text = "News Catcher bot is online and ready."
    for uid in sorted(config.ALLOWED_TELEGRAM_USER_IDS):
        try:
            await app.bot.send_message(chat_id=uid, text=text)
        except TelegramError as e:
            logger.warning("Startup notify failed for user %s: %s", uid, e)


async def _post_shutdown(app: Application) -> None:
    speak_tasks: set[asyncio.Task] = app.bot_data.get("speak_tasks", set())
    for task in list(speak_tasks):
        task.cancel()
    if speak_tasks:
        await asyncio.gather(*speak_tasks, return_exceptions=True)

    watch_task: asyncio.Task | None = app.bot_data.get("watchlist_task")
    if watch_task is not None:
        watch_task.cancel()
        await asyncio.gather(watch_task, return_exceptions=True)

    client: httpx.AsyncClient = app.bot_data.get("http_client")
    if client:
        await client.aclose()
    from fetch_playwright import close_playwright

    await close_playwright()


def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in the environment or .env file.")
    if not config.ALLOWED_TELEGRAM_USER_IDS:
        raise SystemExit("Set ALLOWED_TELEGRAM_USER_IDS (comma-separated Telegram user IDs).")

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list_domains", cmd_list_domains))
    app.add_handler(CommandHandler("add_domain", cmd_add_domain))
    app.add_handler(CommandHandler("remove_domain", cmd_remove_domain))
    app.add_handler(CommandHandler("list_bad_domains", cmd_list_bad_domains))
    app.add_handler(CommandHandler("remove_bad_domain", cmd_remove_bad_domain))
    app.add_handler(CommandHandler("override_bad_domain", cmd_override_bad_domain))
    app.add_handler(CommandHandler("fix_403", cmd_fix_403))
    app.add_handler(CommandHandler("pronounce", cmd_pronounce))
    app.add_handler(CommandHandler("add_pronunciation", cmd_add_pronunciation))
    app.add_handler(CommandHandler("find_pronunciation", cmd_find_pronunciation))
    app.add_handler(CommandHandler("delete_pronunciation", cmd_delete_pronunciation))
    app.add_handler(CommandHandler("fixaword", cmd_fixaword))
    app.add_handler(CommandHandler("speak", cmd_speak))
    app.add_handler(CommandHandler("nevermind", cmd_nevermind))
    app.add_handler(CommandHandler("watch_add", cmd_watch_add))
    app.add_handler(CommandHandler("watch_list", cmd_watch_list))
    app.add_handler(CommandHandler("watch_remove", cmd_watch_remove))
    app.add_handler(CommandHandler("watch_interval", cmd_watch_interval))
    app.add_handler(CommandHandler("watch_check", cmd_watch_check))
    app.add_handler(CallbackQueryHandler(on_pronunciation_callback, pattern=r"^ri?:"))
    app.add_handler(CallbackQueryHandler(on_rule_action_callback, pattern=r"^r[pdna]:"))
    app.add_handler(CallbackQueryHandler(on_speak_phrase_callback, pattern=r"^s:"))
    app.add_handler(CallbackQueryHandler(on_word_fix_callback, pattern=r"^wf:"))
    app.add_handler(CallbackQueryHandler(on_watch_post_callback, pattern=r"^w[rs]:"))
    app.add_handler(CallbackQueryHandler(on_domain_add_callback, pattern=r"^[yn]:"))
    app.add_handler(CallbackQueryHandler(on_domain_bad_callback, pattern=r"^[bk]:"))
    app.add_handler(CallbackQueryHandler(on_oversize_callback, pattern=r"^[px]:"))
    app.add_handler(CallbackQueryHandler(on_article_action_callback, pattern=r"^a:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(UNLISTED_SLASH_COMMAND, ignore_unlisted_slash_command))

    logger.info("Starting bot…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
