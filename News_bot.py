"""Telegram bot: URL in, article text out."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from dataclasses import dataclass
import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity, Update
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
from article_cache import load_last_article, purge_expired as purge_article_cache, save_last_article
from article_export import is_save_to_disk_phrase, save_article_text_file
from domains_store import (
    is_valid_registrable_domain,
    load_domains,
    normalize_registrable_hint,
    registrable_domain_from_url,
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
from tts import is_speak_phrase, split_mp3_for_telegram, synthesize_to_mp3


def _fetch_error_reply(exc: FetchError) -> str:
    s = str(exc)
    if s == "HTTP 401":
        return (
            "HTTP 401 — the server refused the request (common with anti-bot, e.g. Reuters). "
            "Use the default browser-like USER_AGENT from .env.example (remove a short bot-only UA) "
            "and restart the bot."
        )
    if s == "HTTP 403":
        return (
            "HTTP 403 — the site blocked the request. MarkTechPost uses the WordPress API "
            "fallback automatically; other Cloudflare sites need patchright "
            "(patchright install chromium) and PLAYWRIGHT_FALLBACK_DOMAINS — see .env.example."
        )
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
_BOT_COMMAND_NAMES = frozenset({"start", "list_domains", "add_domain", "remove_domain"})


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

# Inline actions after an article is delivered (callback_data must be <= 64 bytes).
_CALLBACK_SPEAK = "a:speak"
_CALLBACK_SAVE = "a:save"


@dataclass
class PendingOversize:
    user_id: int
    url: str
    expires_monotonic: float


@dataclass
class PendingDomainAdd:
    user_id: int
    url: str
    domain: str
    expires_monotonic: float


def _allowed_user(user_id: int) -> bool:
    if not config.ALLOWED_TELEGRAM_USER_IDS:
        return False
    return user_id in config.ALLOWED_TELEGRAM_USER_IDS


def _article_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Speak to me", callback_data=_CALLBACK_SPEAK),
                InlineKeyboardButton("Save to disk", callback_data=_CALLBACK_SAVE),
            ]
        ]
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
        return

    if not article.text.strip():
        await context.bot.send_message(
            chat_id,
            "No article body found in the HTML (paywall, JS-only page, or unsupported layout).",
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

    for msg in _format_article_chunks(article.text, config.TELEGRAM_CHUNK_CHARS):
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
        await context.bot.send_message(
            chat_id,
            "What would you like to do next?",
            reply_markup=_article_actions_keyboard(),
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
        "or type those phrases.\n\n"
        "Commands: /list_domains, /add_domain, /remove_domain"
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
) -> None:
    _purge_expired_pending()
    client: httpx.AsyncClient = context.application.bot_data["http_client"]
    domains = load_domains(config.DOMAINS_FILE)
    chat = update.effective_chat
    if not chat:
        return
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
        if offer_domain_prompt and str(e) == "Domain is not on the approved list.":
            reject = getattr(e, "rejected_url", None) or url
            domain = registrable_domain_from_url(reject)
            if domain and is_valid_registrable_domain(domain):
                current = load_domains(config.DOMAINS_FILE)
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
        await _reply_on_chat(update, context, _fetch_error_reply(e))
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
        update, context, "Generating audio… this may take a few minutes.", chat_id=chat_id
    )
    await context.bot.send_chat_action(chat_id=chat_id, action="upload_audio")

    config.AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.AUDIO_OUTPUT_DIR / f"{user_id}_{int(time.time())}.mp3"

    try:
        await asyncio.to_thread(
            synthesize_to_mp3,
            cached.text,
            out_path,
            title=cached.title,
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
        try:
            with part.open("rb") as audio_file:
                await context.bot.send_audio(
                    chat_id,
                    audio=audio_file,
                    title=part_title,
                    performer="News Catcher",
                )
        except TelegramError as e:
            await _bot_send_text(update, context, f"Could not send audio: {e}", chat_id=chat_id)
            return


async def _handle_save_to_disk_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _run_save_to_disk(update, context)


async def _handle_speak_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_speak(update, context)


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
    if data not in (_CALLBACK_SPEAK, _CALLBACK_SAVE):
        await query.answer()
        return

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    if data == _CALLBACK_SPEAK:
        await _run_speak(update, context)
    else:
        await _run_save_to_disk(update, context)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.text:
        return
    _purge_expired_pending()
    if not _allowed_user(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    text = update.message.text.strip()
    if is_speak_phrase(text):
        await _handle_speak_request(update, context)
        return
    if is_save_to_disk_phrase(text):
        await _handle_save_to_disk_request(update, context)
        return

    url = _extract_first_url(text)
    if not url:
        return

    await _deliver_user_url_fetch(update, context, url, offer_domain_prompt=True)


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
    await _deliver_user_url_fetch(update, context, pending.url, offer_domain_prompt=False)


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
    config.ensure_test_articles_dir()
    purge_article_cache(config.ARTICLE_CACHE_DIR, config.LAST_ARTICLE_TTL_SECONDS)
    app.bot_data["http_client"] = httpx.AsyncClient(
        timeout=httpx.Timeout(config.FETCH_TIMEOUT_SECONDS),
        follow_redirects=False,
    )
    text = "News Catcher bot is online and ready."
    for uid in sorted(config.ALLOWED_TELEGRAM_USER_IDS):
        try:
            await app.bot.send_message(chat_id=uid, text=text)
        except TelegramError as e:
            logger.warning("Startup notify failed for user %s: %s", uid, e)


async def _post_shutdown(app: Application) -> None:
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
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list_domains", cmd_list_domains))
    app.add_handler(CommandHandler("add_domain", cmd_add_domain))
    app.add_handler(CommandHandler("remove_domain", cmd_remove_domain))
    app.add_handler(CallbackQueryHandler(on_domain_add_callback, pattern=r"^[yn]:"))
    app.add_handler(CallbackQueryHandler(on_oversize_callback, pattern=r"^[px]:"))
    app.add_handler(CallbackQueryHandler(on_article_action_callback, pattern=r"^a:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(UNLISTED_SLASH_COMMAND, ignore_unlisted_slash_command))

    logger.info("Starting bot…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
