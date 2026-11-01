"""HKU Student GenAI — Azure DeepSeek (Foundry) client.

Default base: https://api.hku.hk/foundry/student
Auth: api-key header (same Student GenAI subscription key)
Chat: POST /models/chat/completions  (OpenAI-style body; no api-version)

Preferred model: DeepSeek-V3.2
Also via env: DeepSeek-V4-Flash, DeepSeek-V4-Pro

Optional Azure OpenAI path (legacy): set
  HKU_GENAI_BASE_URL=https://api.hku.hk/openai/student
  HKU_GENAI_DEPLOYMENT=gpt-4.1-mini
  HKU_GENAI_API_VERSION=2025-04-01-preview
Then the client uses /deployments/{name}/chat/completions?api-version=...
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE = "https://api.hku.hk/foundry/student"
DEFAULT_MODEL = "DeepSeek-V3.2"
FOUNDRY_PATH = "/models/chat/completions"

# Known DeepSeek deployment / model ids on the HKU Foundry gateway
KNOWN_DEEPSEEK_MODELS = (
    "DeepSeek-V3.2",
    "DeepSeek-V4-Flash",
    "DeepSeek-V4-Pro",
)


class HKULLMError(RuntimeError):
    """Raised when the HKU GenAI gateway returns an error or key is missing."""


def _model_name() -> str:
    """Prefer HKU_GENAI_MODEL, then HKU_GENAI_DEPLOYMENT, then default."""
    return (
        os.getenv("HKU_GENAI_MODEL", "").strip()
        or os.getenv("HKU_GENAI_DEPLOYMENT", "").strip()
        or DEFAULT_MODEL
    )


def _config() -> dict[str, str]:
    key = os.getenv("HKU_GENAI_API_KEY", "").strip()
    base = os.getenv("HKU_GENAI_BASE_URL", DEFAULT_BASE).rstrip("/")
    return {
        "api_key": key,
        "base_url": base,
        "model": _model_name(),
        "api_version": os.getenv("HKU_GENAI_API_VERSION", "").strip(),
    }


def _is_foundry(base_url: str) -> bool:
    return "/foundry/" in base_url or base_url.endswith("/foundry/student")


def has_api_key() -> bool:
    return bool(os.getenv("HKU_GENAI_API_KEY", "").strip())


def chat_completions(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.0,
    response_format: dict[str, Any] | None = None,
    max_tokens: int = 2048,
    timeout: float = 90.0,
) -> str:
    """Call HKU GenAI chat completions; return assistant message content.

    Default path: Foundry DeepSeek POST {base}/models/chat/completions
    with body {"model", "messages", ...}.

    Never invents a fake response — raises if key missing or HTTP fails.
    """
    cfg = _config()
    if not cfg["api_key"]:
        raise HKULLMError(
            "HKU_GENAI_API_KEY is not set. Copy .env.example → .env and paste "
            "your Primary key from https://developer.hku.hk/profile"
        )

    if _is_foundry(cfg["base_url"]):
        url = f"{cfg['base_url']}{FOUNDRY_PATH}"
        payload: dict[str, Any] = {
            "model": cfg["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    else:
        # Legacy Azure OpenAI student gateway
        api_version = cfg["api_version"] or "2025-04-01-preview"
        url = (
            f"{cfg['base_url']}/deployments/{cfg['model']}/chat/completions"
            f"?api-version={api_version}"
        )
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    if response_format is not None:
        payload["response_format"] = response_format

    headers = {
        "api-key": cfg["api_key"],
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)

    if resp.status_code >= 400:
        # Never echo api-key; response body is safe to surface truncated
        raise HKULLMError(
            f"HKU GenAI HTTP {resp.status_code}: {resp.text[:500]}"
        )

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise HKULLMError(
            f"Unexpected response shape: {json.dumps(data)[:500]}"
        ) from exc
