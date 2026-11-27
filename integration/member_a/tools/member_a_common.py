# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""Source storage, time boundaries and redacted traces for member A."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from integration.member_a.schemas.member_a import ContractError, aware_datetime


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix, *values):
    text = json.dumps(values, sort_keys=True, ensure_ascii=False)
    return prefix + "_" + hashlib.sha256(text.encode()).hexdigest()[:20]


def public_time(source):
    precision = source.get("available_at_precision")
    if precision == "date":
        day = date.fromisoformat(source["public_date"])
        return datetime.combine(day, time(23, 59, 59), ZoneInfo(source["source_timezone"]))
    if source.get("available_at"):
        return aware_datetime(source["available_at"])
    return None


def known_by(source, cutoff):
    when = public_time(source)
    return when is not None and when <= aware_datetime(cutoff)


def redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if re.search(r"api.?key|authorization|password|secret|token", k, re.I)
                    and k not in {"max_tokens", "prompt_tokens", "completion_tokens", "total_tokens"}
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def error_record(code, message, retryable=False):
    return dict(code=code, message=message, retryable=retryable)


class Store:
    def __init__(self, request, config):
        if not isinstance(config, dict):
            raise ContractError("config must be an object")
        for key, default, lower, upper in [("max_tool_calls", 12, 1, 100), ("lookback_years", 3, 1, 30),
                                          ("target_annual_reports", 3, 1, 20), ("target_quarters", 8, 1, 80),
                                          ("target_management_events", 4, 1, 40), ("target_seekingalpha_materials", 2, 1, 40)]:
            value = config.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise ContractError(f"config.{key} must be an integer from {lower} to {upper}")
        self.project = Path(config.get("project_root", Path.cwd())).resolve()
        path = Path(config.get("run_dir", f"runs/{request['run_id']}"))
        self.root = self.resolve(path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_id = request["run_id"]

    def resolve(self, path):
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.project / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(self.project):
            raise ContractError("artifact/import paths must remain inside project_root")
        return candidate

    def ref(self, path):
        return str(self.resolve(path).relative_to(self.project))

    def write_json(self, path, value):
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        fd, temp = tempfile.mkstemp(dir=target.parent, prefix=".json-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text + "\n")
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return self.ref(target)

    def source(self, company_id, title, source_type, document_type, url, raw,
               *, available_at=None, public_date=None, precision="second", language="en",
               source_timezone="America/New_York", access_method="http", version="1",
               extension="json", **extra):
        if not isinstance(raw, bytes):
            raw = raw.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        sid = stable_id(company_id + "_S", url, version, digest)
        target = self.root / "raw" / f"{sid}.{extension}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(raw)
        elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ContractError("retained raw artifact was altered; preserve evidence and use a clean run directory")
        result = dict(source_id=sid, company_id=company_id, title=title,
                      source_type=source_type, document_type=document_type, source_url=url,
                      language=language, available_at=available_at,
                      available_at_precision=precision, public_date=public_date,
                      source_timezone=source_timezone, retrieved_at=now_iso(),
                      access_method=access_method, raw_path=self.ref(target), sha256=digest,
                      document_version=version, **extra)
        return result

    def trace(self, agent, tool_name, arguments, started_at, status, result=None, call_id=None):
        path = self.root / "logs" / f"{agent}_tools.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        event = dict(run_id=self.run_id, agent=agent,
                     call_id=call_id or stable_id("call", started_at, tool_name),
                     tool_name=tool_name, arguments=redact(arguments), started_at=started_at,
                     ended_at=now_iso(), status=status, result=redact(result or {}))
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        return self.ref(path)

    def verify_source(self, source):
        path = self.resolve(source["raw_path"])
        if not path.is_file():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]


def coverage(category, source, state, requested, obtained, reason=None, **extra):
    return dict(category=category, source=source, state=state,
                requested_count=requested, obtained_count=obtained, reason_code=reason,
                substitute_source_ids=[], course_requirement_resolution="pending", **extra)
