"""
Model-agnostic LLM client for classification tasks.

Provider is selected via the LLM_PROVIDER env var (default: claude).
The model can be overridden via LLM_MODEL; otherwise a sensible default
is used per provider.

Supported providers and their required env vars:

  Provider    | Env var            | Default model
  ------------|--------------------|---------------------------------
  claude      | ANTHROPIC_API_KEY  | claude-haiku-4-5-20251001
  gemini      | GEMINI_API_KEY     | gemini-2.0-flash
  deepseek    | DEEPSEEK_API_KEY   | deepseek-chat
  openai      | OPENAI_API_KEY     | gpt-4o-mini

Usage:
    from lib.llm import complete

    text = await complete(
        system="You are a classifier. Respond with JSON only.",
        user="Classify this: ...",
        max_tokens=256,
    )
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────────────

_PROVIDER_DEFAULTS: dict[str, dict] = {
    "claude": {
        "model":   "claude-haiku-4-5-20251001",
        "api_key": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "model":   "gemini-2.0-flash",
        "api_key": "GEMINI_API_KEY",
    },
    "deepseek": {
        "model":   "deepseek-chat",
        "api_key": "DEEPSEEK_API_KEY",
    },
    "openai": {
        "model":   "gpt-4o-mini",
        "api_key": "OPENAI_API_KEY",
    },
}

# ── Per-provider call implementations ───────────────────────────────────────

async def _call_claude(
    api_key: str, model: str, system: str, user: str, max_tokens: int
) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        res.raise_for_status()
        return res.json()["content"][0]["text"]


async def _call_openai_compat(
    base_url: str, api_key: str, model: str, system: str, user: str, max_tokens: int
) -> str:
    """Handles OpenAI-compatible APIs (OpenAI, Deepseek, etc.)."""
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            },
        )
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]


async def _call_gemini(
    api_key: str, model: str, system: str, user: str, max_tokens: int
) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models"
        f"/{model}:generateContent?key={api_key}"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.post(
            url,
            headers={"content-type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
        )
        res.raise_for_status()
        return res.json()["candidates"][0]["content"]["parts"][0]["text"]


# ── Public interface ─────────────────────────────────────────────────────────

async def complete(
    system: str,
    user: str,
    max_tokens: int = 512,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """
    Call the configured LLM and return the response text.

    provider and model default to the LLM_PROVIDER / LLM_MODEL env vars,
    falling back to claude / claude-haiku-4-5-20251001.

    Raises on API errors so callers can handle fallback logic.
    """
    provider = (provider or os.getenv("LLM_PROVIDER", "claude")).lower()

    cfg = _PROVIDER_DEFAULTS.get(provider)
    if cfg is None:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. "
            f"Supported: {', '.join(_PROVIDER_DEFAULTS)}"
        )

    resolved_model = model or os.getenv("LLM_MODEL") or cfg["model"]
    api_key = os.getenv(cfg["api_key"], "")

    if not api_key:
        raise EnvironmentError(
            f"Missing API key for provider '{provider}': "
            f"set the {cfg['api_key']} environment variable"
        )

    logger.debug("LLM complete: provider=%s model=%s", provider, resolved_model)

    if provider == "claude":
        return await _call_claude(api_key, resolved_model, system, user, max_tokens)

    if provider == "gemini":
        return await _call_gemini(api_key, resolved_model, system, user, max_tokens)

    if provider == "openai":
        return await _call_openai_compat(
            "https://api.openai.com/v1", api_key, resolved_model, system, user, max_tokens
        )

    if provider == "deepseek":
        return await _call_openai_compat(
            "https://api.deepseek.com/v1", api_key, resolved_model, system, user, max_tokens
        )

    raise ValueError(f"Provider '{provider}' registered but not implemented")
