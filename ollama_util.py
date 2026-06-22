"""Resolve Ollama host/model for local LLM calls."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def list_ollama_models(host: str, *, timeout_seconds: float = 10.0) -> list[str]:
    """Return installed model names from Ollama /api/tags (empty if unreachable)."""
    import httpx

    url = f"{host.rstrip('/')}/api/tags"
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Could not list Ollama models at %s: %s", host, e)
        return []

    models: list[str] = []
    for item in data.get("models", []):
        if isinstance(item, dict):
            name = item.get("name") or item.get("model")
            if isinstance(name, str) and name.strip():
                models.append(name.strip())
    return models


def resolve_ollama_model(
    host: str,
    requested: str,
    *,
    timeout_seconds: float = 10.0,
) -> tuple[str | None, str | None]:
    """
    Pick a model name that Ollama actually has.

    Returns (model_name, user_message). model_name is None when no model can be used.
    user_message explains fallback or failure (for Telegram).
    """
    requested = requested.strip()
    if not requested:
        return None, "OLLAMA_MODEL is not set."

    models = list_ollama_models(host, timeout_seconds=timeout_seconds)
    if not models:
        return None, (
            f"Cannot reach Ollama at {host}. "
            "Is it running? Try: ollama serve"
        )

    if requested in models:
        return requested, None

    req_base = requested.split(":")[0]
    for name in models:
        if name.split(":")[0] == req_base:
            msg = f"Model {requested!r} is not installed; using {name!r} instead."
            logger.info(msg)
            return name, msg

    preferred = (
        "llama3.1:8b",
        "llama3.1",
        "mistral:7b",
        "qwen3:8b",
        "gemma4:e4b",
    )
    for pref in preferred:
        for name in models:
            if name == pref or name.startswith(f"{pref}:"):
                msg = f"Model {requested!r} is not installed; using {name!r} instead."
                logger.info(msg)
                return name, msg

    chat_models = [m for m in models if "embed" not in m.casefold()]
    if chat_models:
        name = chat_models[0]
        msg = f"Model {requested!r} is not installed; using {name!r} instead."
        logger.info(msg)
        return name, msg

    return None, (
        f"Model {requested!r} is not installed. "
        f"Available: {', '.join(models[:5])}"
        + ("…" if len(models) > 5 else "")
        + f". Run: ollama pull {requested}"
    )
