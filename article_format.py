"""Clean article text for storage/TTS and format for Telegram HTML."""

from __future__ import annotations

import html as html_module
import re

# **bold**, __bold__, ## bold ##
_EMPHASIS_RE = re.compile(
    r"\*\*(.+?)\*\*|__(.+?)__|##\s*(.+?)\s*##",
    re.DOTALL,
)

# Markdown heading (# …) or bullet (- / +) at line start — not ** emphasis.
_HEADING_PREFIX_RE = re.compile(r"^\s*#{1,6}\s+")
_BULLET_PREFIX_RE = re.compile(r"^\s*[-+]\s+")

# Paragraph that is only * / _ markers (empty emphasis leftovers from HTML).
_STRAY_EMPHASIS_MARKERS_RE = re.compile(r"^[\s*_]+$")

# Paragraph that is only a URL or a single markdown link.
_LINK_ONLY_RE = re.compile(
    r"^(?:"
    r"https?://[^\s]+"
    r"|\[[^\]]*\]\(\s*https?://[^\s)]+\s*\)"
    r"|(?:read\s+more|continue\s+reading|see\s+also|related\s+story)"
    r"[\s:—-]*https?://[^\s]+"
    r")\s*\.?\s*$",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def _normalize_compare(text: str) -> str:
    """Lowercase alnum-ish key for duplicate detection."""
    t = re.sub(r"[^\w\s]", " ", text.casefold())
    return " ".join(t.split())


def strip_formatting_prefixes(text: str) -> str:
    """Remove leading # headings and -/+ list markers from each line."""
    if not text:
        return text
    lines: list[str] = []
    for line in text.split("\n"):
        line = _HEADING_PREFIX_RE.sub("", line)
        line = _BULLET_PREFIX_RE.sub("", line)
        lines.append(line)
    return "\n".join(lines)


def _deduplicate_sentences(text: str, seen: set[str]) -> str:
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    if len(parts) <= 1:
        key = _normalize_compare(text)
        if key and key in seen:
            return ""
        if key:
            seen.add(key)
        return text.strip()
    kept: list[str] = []
    for sent in parts:
        sent = sent.strip()
        if not sent:
            continue
        key = _normalize_compare(sent)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        kept.append(sent)
    return " ".join(kept)


def deduplicate_article_text(text: str, *, title: str | None = None) -> str:
    """
    Drop repeated paragraphs/sentences and a leading paragraph that repeats the title.
    """
    if not text or not text.strip():
        return text

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text.strip()) if p.strip()]
    if title and title.strip():
        norm_title = _normalize_compare(title)
        while paragraphs:
            first_key = _normalize_compare(paragraphs[0])
            if first_key == norm_title or (
                norm_title and len(norm_title) > 20 and first_key.startswith(norm_title[:40])
            ):
                paragraphs.pop(0)
                continue
            first_sents = _SENTENCE_SPLIT_RE.split(paragraphs[0])
            if first_sents and _normalize_compare(first_sents[0]) == norm_title:
                rest = _deduplicate_sentences(
                    " ".join(first_sents[1:]).strip(), set()
                )
                if rest:
                    paragraphs[0] = rest
                else:
                    paragraphs.pop(0)
                continue
            break

    seen_paras: set[str] = set()
    seen_sents: set[str] = set()
    kept: list[str] = []
    for para in paragraphs:
        para_key = _normalize_compare(para)
        if para_key and para_key in seen_paras:
            continue
        cleaned = _deduplicate_sentences(para, seen_sents)
        if not cleaned.strip():
            continue
        if para_key:
            seen_paras.add(para_key)
        kept.append(cleaned.strip())

    return "\n\n".join(kept)


def body_starts_with_title(body: str, title: str) -> bool:
    """True when narration should not prepend the title again."""
    if not body.strip() or not title.strip():
        return False
    norm_title = _normalize_compare(title)
    first_para = body.strip().split("\n\n")[0]
    if _normalize_compare(first_para) == norm_title:
        return True
    sents = _SENTENCE_SPLIT_RE.split(first_para)
    if sents and _normalize_compare(sents[0]) == norm_title:
        return True
    return norm_title in _normalize_compare(first_para[: len(title) + 40])


def strip_link_only_paragraphs(text: str) -> str:
    """Drop paragraph blocks that are only an off-site link."""
    if not text or not text.strip():
        return text
    parts = [p.strip() for p in re.split(r"\n\s*\n+", text.strip()) if p.strip()]
    kept = [p for p in parts if not _paragraph_is_link_only(p)]
    return "\n\n".join(kept)


def strip_stray_emphasis_markers(text: str) -> str:
    """Drop paragraphs (and trailing lines) that are only * / _ leftovers."""
    if not text or not text.strip():
        return text
    parts = [p.strip() for p in re.split(r"\n\s*\n+", text.strip()) if p.strip()]
    kept: list[str] = []
    for para in parts:
        if _STRAY_EMPHASIS_MARKERS_RE.fullmatch(para):
            continue
        lines = para.split("\n")
        while lines and _STRAY_EMPHASIS_MARKERS_RE.fullmatch(lines[-1].strip()):
            lines.pop()
        cleaned = "\n".join(lines).strip()
        if cleaned:
            kept.append(cleaned)
    return "\n\n".join(kept)


def _paragraph_is_link_only(paragraph: str) -> bool:
    p = " ".join(paragraph.split())
    return bool(_LINK_ONLY_RE.match(p))


def strip_emphasis_markers(text: str) -> str:
    """Remove markdown-style emphasis markers; keep inner text (for TTS)."""
    text = strip_formatting_prefixes(text)

    def _inner(m: re.Match[str]) -> str:
        return m.group(1) or m.group(2) or m.group(3) or ""

    return _EMPHASIS_RE.sub(_inner, text)


def emphasis_to_telegram_html(text: str) -> str:
    """Convert **/__/## emphasis to Telegram HTML <b> tags."""
    if not text:
        return text

    parts: list[str] = []
    last = 0
    for m in _EMPHASIS_RE.finditer(text):
        if m.start() > last:
            parts.append(html_module.escape(text[last : m.start()]))
        inner = m.group(1) or m.group(2) or m.group(3) or ""
        parts.append(f"<b>{html_module.escape(inner)}</b>")
        last = m.end()
    if last < len(text):
        parts.append(html_module.escape(text[last:]))
    return "".join(parts)


def format_paragraphs_for_telegram(text: str) -> str:
    """Strip line prefixes, apply emphasis HTML per paragraph."""
    if not text or not text.strip():
        return text
    parts = [p.strip() for p in re.split(r"\n\s*\n+", text.strip()) if p.strip()]
    return "\n\n".join(
        emphasis_to_telegram_html(strip_formatting_prefixes(p)) for p in parts
    )
