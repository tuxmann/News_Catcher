"""Article text extraction: trafilatura first, readability-lxml fallback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from article_format import deduplicate_article_text, strip_link_only_paragraphs
import trafilatura
from lxml import html
from readability import Document


@dataclass
class ExtractedArticle:
    title: str | None
    author: str | None
    date: str | None
    text: str
    images: list["ExtractedImage"]


@dataclass
class ExtractedImage:
    url: str
    caption: str | None


_SIGNUP_HOOK_PARAGRAPH = "sign up here."


def strip_newsletter_signup_paragraphs(text: str) -> str:
    """
    Drop paragraph blocks that are only the common newsletter CTA
    \"Sign up here.\" (case-insensitive, whitespace-normalized).
    """
    if not text or not text.strip():
        return text
    parts = [p.strip() for p in re.split(r"\n\s*\n+", text.strip()) if p.strip()]
    kept: list[str] = []
    for p in parts:
        norm = " ".join(p.split()).casefold()
        if norm == _SIGNUP_HOOK_PARAGRAPH:
            continue
        kept.append(p)
    return "\n\n".join(kept)


def normalize_paragraph_spacing(text: str) -> str:
    """
    Ensure blocks are separated by a blank line (\\n\\n) for reading.
    Preserves single newlines inside a block (e.g. two adjacent <p>-s that
    came through as \\n only become \\n\\n; lines that were already \\n\\n stay).
    """
    text = text.strip()
    if not text:
        return ""
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if "\n\n" in text:
        return text
    if "\n" in text:
        parts = [p.strip() for p in text.split("\n") if p.strip()]
        return "\n\n".join(parts)
    return text


def _readability_paragraph_text(summary_html: str) -> str:
    """Pull <p> blocks from readability HTML; never collapse the whole doc to one line."""
    tree = html.fromstring(summary_html)
    paras: list[str] = []
    for node in tree.xpath("//p"):
        t = " ".join(node.itertext())
        t = " ".join(t.split())
        if t:
            paras.append(t)
    if paras:
        return "\n\n".join(paras)

    tmp = summary_html
    for br in ("<br />", "<br/>", "<br>"):
        tmp = tmp.replace(br, "\n")
    tree2 = html.fromstring(tmp)
    lines: list[str] = []
    for ln in tree2.text_content().splitlines():
        ln = " ".join(ln.split())
        if ln:
            lines.append(ln)
    if lines:
        return "\n\n".join(lines)

    t = tree.text_content().strip()
    return " ".join(t.split()) if t else ""


def _clean_caption(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.split()).strip()
    return cleaned or None


def _looks_like_image_url(url: str) -> bool:
    u = url.lower()
    return any(ext in u for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"))


def _extract_images(raw_html: str, source_url: str) -> list[ExtractedImage]:
    tree = html.fromstring(raw_html)
    out: list[ExtractedImage] = []
    seen: set[str] = set()

    def add(url: str | None, caption: str | None) -> None:
        if not url:
            return
        absolute_url = urljoin(source_url, url.strip())
        if not absolute_url.startswith(("http://", "https://")):
            return
        if absolute_url in seen:
            return
        seen.add(absolute_url)
        out.append(ExtractedImage(url=absolute_url, caption=_clean_caption(caption)))

    # 1) Figure/images in rendered HTML.
    for fig in tree.xpath("//figure"):
        img = fig.xpath(".//img[1]")
        if not img:
            continue
        node = img[0]
        src = node.get("src") or node.get("data-src") or node.get("data-lazy-src")
        if not src:
            srcset = node.get("srcset") or node.get("data-srcset")
            if srcset:
                src = srcset.split(",")[0].strip().split(" ")[0]
        caption_node = fig.xpath(".//figcaption")
        caption = caption_node[0].text_content() if caption_node else None
        add(src, caption or node.get("alt"))

    # 2) Any standalone image tags with alt text fallback.
    for node in tree.xpath("//img"):
        src = node.get("src") or node.get("data-src") or node.get("data-lazy-src")
        if not src:
            continue
        add(src, node.get("alt"))

    # 3) JSON-LD image objects (common in Reuters).
    for script in tree.xpath("//script[@type='application/ld+json']"):
        text = (script.text or "").strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue

        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                image_value = item.get("image")
                caption = item.get("caption")
                if isinstance(image_value, str) and _looks_like_image_url(image_value):
                    add(image_value, caption)
                elif isinstance(image_value, list):
                    for sub in image_value:
                        if isinstance(sub, str) and _looks_like_image_url(sub):
                            add(sub, caption)
                        elif isinstance(sub, dict):
                            add(sub.get("url"), sub.get("caption") or caption)
                elif isinstance(image_value, dict):
                    add(image_value.get("url"), image_value.get("caption") or caption)
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)

    return out


def extract_article(html_bytes: bytes, source_url: str) -> ExtractedArticle:
    raw = html_bytes.decode("utf-8", errors="replace")
    images = _extract_images(raw, source_url)

    meta = trafilatura.extract_metadata(raw, default_url=source_url)
    text = trafilatura.extract(
        raw,
        url=source_url,
        include_comments=False,
        include_tables=True,
        include_formatting=True,
    )

    title = meta.title if meta else None
    author = meta.author if meta else None
    date = meta.date if meta else None

    if text and text.strip():
        text = normalize_paragraph_spacing(text)
        text = strip_newsletter_signup_paragraphs(text)
        text = strip_link_only_paragraphs(text)
        text = deduplicate_article_text(text, title=title)
        return ExtractedArticle(
            title=title,
            author=author,
            date=date,
            text=text,
            images=images,
        )

    doc = Document(raw)
    summary_html = doc.summary()
    fallback_text = _readability_paragraph_text(summary_html)
    fallback_text = normalize_paragraph_spacing(fallback_text)
    fallback_text = strip_newsletter_signup_paragraphs(fallback_text)
    fallback_text = strip_link_only_paragraphs(fallback_text)
    summary_title = doc.title()
    final_title = summary_title or title
    fallback_text = deduplicate_article_text(fallback_text, title=final_title)

    return ExtractedArticle(
        title=final_title,
        author=author,
        date=date,
        text=fallback_text,
        images=images,
    )
