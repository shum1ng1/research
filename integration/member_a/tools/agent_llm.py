# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""LLM transport used by Data, Reviewer, and the live Analysis/Writer tool loop.

Providers:
- deepseek: official API, https://api.deepseek.com/chat/completions
  Authorization: Bearer DEEPSEEK_API_KEY. Default model deepseek-flash.
  Docs: https://api-docs.deepseek.com/api/create-chat-completion/
  Thinking is disabled so tool_choice stays valid. Web tools are never sent.
- hku: existing student gateway. Header api-key. This is not the official DeepSeek API.

No response is invented when the key is missing or the HTTP call fails.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse


class LLMError(RuntimeError):
    pass


OFFICIAL_DEEPSEEK_HOST = "api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def provider_name(config) -> str:
    raw = ""
    if isinstance(config, dict):
        raw = config.get("llm_provider") or (config.get("llm") or {}).get("provider") or ""
    raw = str(raw or os.getenv("LLM_PROVIDER") or "").strip().lower()
    if not raw:
        return "hku"
    if raw not in {"deepseek", "hku"}:
        raise LLMError("llm_provider must be deepseek or hku; no response was fabricated")
    return raw


def _model_id(value: str, fallback: str) -> str:
    candidate = (value or "").strip() or fallback
    if not _MODEL_RE.fullmatch(candidate):
        raise LLMError("model id contains unsupported characters")
    return candidate


def _deepseek_url() -> str:
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
    parsed = urlparse(base)
    if (parsed.scheme != "https" or parsed.hostname != OFFICIAL_DEEPSEEK_HOST
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise LLMError("DEEPSEEK_BASE_URL must be https://api.deepseek.com")
    return base + "/chat/completions"


def _message(data):
    try:
        message = data["choices"][0]["message"]
        if not isinstance(message, dict):
            raise TypeError()
    except (KeyError, IndexError, TypeError):
        raise LLMError("model response has no assistant message") from None
    return message


def _post(url, headers, body, timeout, attempts):
    payload = json.dumps(body, allow_nan=False).encode()
    last = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last = exc.code
            if exc.code not in _RETRYABLE_HTTP or attempt == attempts - 1:
                raise LLMError(f"model HTTP {exc.code}; inspect provider configuration") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            if attempt == attempts - 1:
                raise LLMError("model request failed or response was not JSON") from None
            last = "transport"
    raise LLMError(f"model request failed ({last})")


def complete(messages, config, *, tools=None, json_output=False):
    """Return (assistant_message, metadata). Raises LLMError instead of inventing text."""
    _load_env()
    config = config or {}
    provider = provider_name(config)
    timeout = float(config.get("llm_timeout_seconds", 90))
    attempts = int(config.get("llm_http_attempts", 3))
    if attempts < 1 or attempts > 5:
        raise LLMError("llm_http_attempts must be from 1 to 5")
    if provider == "deepseek":
        key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise LLMError("DEEPSEEK_API_KEY is not configured; no model response was fabricated")
        model = _model_id(os.getenv("DEEPSEEK_MODEL", ""), DEFAULT_DEEPSEEK_MODEL)
        url = _deepseek_url()
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": config.get("max_tokens", 4096),
            # Official docs: deepseek-flash ignores temperature in non-thinking mode.
            # Thinking is disabled because thinking mode rejects required tool_choice
            # and we never attach deepseek_web_search or deepseek_read_url.
            "thinking": {"type": "disabled"},
        }
    else:
        key = os.getenv("HKU_GENAI_API_KEY", "").strip()
        if not key:
            raise LLMError("HKU_GENAI_API_KEY is not configured; no model response was fabricated")
        base = os.getenv("HKU_GENAI_BASE_URL", "https://api.hku.hk/foundry/student").rstrip("/")
        if urlparse(base).scheme != "https":
            raise LLMError("LLM endpoint must use HTTPS")
        model = _model_id(
            os.getenv("HKU_GENAI_MODEL", "") or os.getenv("HKU_GENAI_DEPLOYMENT", ""),
            "DeepSeek-V3.2",
        )
        body = {"messages": messages, "temperature": 0, "max_tokens": config.get("max_tokens", 4096)}
        if "/foundry/" in base:
            url = base + "/models/chat/completions"
            body["model"] = model
        else:
            version = os.getenv("HKU_GENAI_API_VERSION", "2025-04-01-preview")
            url = (f"{base}/deployments/{quote(model, safe='')}/chat/completions"
                   f"?api-version={quote(version, safe='')}")
        headers = {"Content-Type": "application/json", "api-key": key}
    if tools:
        body.update(tools=tools, tool_choice="auto")
    if json_output:
        body["response_format"] = {"type": "json_object"}
    data = _post(url, headers, body, timeout, attempts)
    message = _message(data)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return message, {"provider": provider, "model": model, "usage": usage}
