"""Bounded function-call loop. Only handlers registered by the caller can run.

Model text, filing quotes, and tool arguments are data. Nothing is executed as
code, a shell command, or an arbitrary URL.
"""
from __future__ import annotations

import json
from pathlib import Path

from integration.member_a.tools.agent_llm import LLMError, complete


def _append_trace(path, record):
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def run_tool_loop(messages, tools, handlers, config, *, required_submit, stage):
    """Call the configured provider until required_submit is accepted or the budget ends.

    handlers map a tool name to fn(arguments: dict) -> dict. An unknown name
    returns UNKNOWN_TOOL and is not dispatched. A failed submit stays in the
    conversation so the model can correct it inside the same budget.
    """
    if required_submit not in handlers:
        raise LLMError("required submit tool is not allowlisted")
    budget = config.get("max_tool_calls", 8)
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 12:
        raise LLMError("max_tool_calls must be an integer from 1 to 12")
    allowed = set(handlers)
    used = 0
    accepted = None
    trace_path = config.get("llm_trace_path")
    for _round in range(budget + 1):
        message, metadata = complete(messages, config, tools=tools)
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise LLMError("tool_calls must be a list")
        _append_trace(trace_path, {
            "stage": stage, "event": "model_message", "provider": metadata.get("provider"),
            "model": metadata.get("model"), "tool_calls": len(calls),
            "submit_accepted": accepted is not None,
        })
        if not calls:
            if accepted is not None:
                return accepted
            raise LLMError(f"model stopped before {required_submit}; template text was not substituted")
        if used + len(calls) > budget:
            raise LLMError("tool-call budget exhausted; template text was not substituted")
        messages.append({key: message[key] for key in ("role", "content", "tool_calls") if key in message})
        for call in calls:
            used += 1
            function = call.get("function") if isinstance(call, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            if name not in allowed:
                result = {"error": "UNKNOWN_TOOL", "allowed": sorted(allowed)}
            else:
                raw = function.get("arguments") if isinstance(function, dict) else "{}"
                try:
                    arguments = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    arguments = None
                if not isinstance(arguments, dict):
                    result = {"error": "INVALID_ARGUMENTS"}
                else:
                    try:
                        result = handlers[name](arguments)
                    except LLMError:
                        raise
                    except Exception as exc:
                        result = {"error": "TOOL_REJECTED", "type": type(exc).__name__}
                    if (name == required_submit and isinstance(result, dict)
                            and result.get("accepted") is True):
                        accepted = result["payload"]
            status = "accepted" if isinstance(result, dict) and result.get("accepted") else "rejected"
            error = result.get("error") if isinstance(result, dict) else "INVALID"
            _append_trace(trace_path, {
                "stage": stage, "event": "tool", "name": name if name in allowed else "UNKNOWN_TOOL",
                "status": status, "error": error,
            })
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", "invalid") if isinstance(call, dict) else "invalid",
                "content": json.dumps(result, ensure_ascii=False),
            })
    raise LLMError("tool-call budget exhausted; template text was not substituted")
