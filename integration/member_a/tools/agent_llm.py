# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""Member-A HKU transport. Existing llm_client.py and B/C modules stay unchanged."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse


class LLMError(RuntimeError):
    pass


def complete(messages, config, *, tools=None, json_output=False):
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    key = os.getenv("HKU_GENAI_API_KEY", "").strip()
    if not key:
        raise LLMError("HKU_GENAI_API_KEY is not configured; no model response was fabricated")
    base = os.getenv("HKU_GENAI_BASE_URL", "https://api.hku.hk/foundry/student").rstrip("/")
    if urlparse(base).scheme != "https":
        raise LLMError("LLM endpoint must use HTTPS")
    model = os.getenv("HKU_GENAI_MODEL") or os.getenv("HKU_GENAI_DEPLOYMENT") or "DeepSeek-V3.2"
    body = dict(messages=messages, temperature=0, max_tokens=config.get("max_tokens", 4096))
    if "/foundry/" in base:
        url = base + "/models/chat/completions"
        body["model"] = model
    else:
        version = os.getenv("HKU_GENAI_API_VERSION", "2025-04-01-preview")
        url = f"{base}/deployments/{quote(model, safe='')}/chat/completions?api-version={quote(version, safe='')}"
    if tools:
        body.update(tools=tools, tool_choice="auto")
    if json_output:
        body["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(url, data=json.dumps(body, allow_nan=False).encode(),
                                     headers={"Content-Type": "application/json", "api-key": key}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=config.get("llm_timeout_seconds", 90)) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not expose response bodies, headers, or echoed credentials.
        raise LLMError(f"HKU model HTTP {exc.code}; inspect provider configuration") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise LLMError("HKU model request failed or response was not JSON") from None
    try:
        message = data["choices"][0]["message"]
        if not isinstance(message, dict):
            raise TypeError()
    except (KeyError, IndexError, TypeError):
        raise LLMError("HKU response has no assistant message") from None
    return message, dict(model=model, usage=data.get("usage", {}))
