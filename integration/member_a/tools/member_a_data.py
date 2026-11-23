# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""Data orchestration behind the exact public run(request, config, feedback) API."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from integration.member_a.schemas.member_a import ContractError, envelope, validate_data_bundle, validate_request
from integration.member_a.tools import agent_llm
from integration.member_a.tools.company_registry import resolve_company
from integration.member_a.tools.document_tools import load_management_materials, load_seekingalpha_materials, search_evidence
from integration.member_a.tools.member_a_common import Store, coverage, error_record, now_iso, stable_id
from integration.member_a.tools.sec_tools import fetch_financial_facts, list_regulatory_documents
from integration.member_a.tools.yahoo_tools import fetch_market_prices

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "data_agent.md"


def _function(name, properties, required, description):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False}}}


COMPANY_ARG = {"type": "string", "enum": ["MSFT", "CSCO", "AAPL"]}
TIME_ARG = {"type": "string", "format": "date-time"}
TOOLS = [
    _function("resolve_company", {"ticker": COMPANY_ARG}, ["ticker"], "Resolve registered company"),
    _function("fetch_market_prices", {"company_id": COMPANY_ARG, "start_date": {"type": "string", "format": "date"},
                                      "cutoff_at": TIME_ARG}, ["company_id", "start_date", "cutoff_at"], "Get Yahoo daily OHLCV"),
    _function("list_regulatory_documents", {"company_id": COMPANY_ARG, "cutoff_at": TIME_ARG,
              "document_types": {"type": "array", "items": {"type": "string", "enum": ["10-K", "10-Q", "10-K/A", "10-Q/A", "8-K"]}}},
              ["company_id", "cutoff_at", "document_types"], "List and save eligible SEC filings"),
    _function("fetch_financial_facts", {"company_id": COMPANY_ARG, "cutoff_at": TIME_ARG,
              "document_ids": {"type": "array", "items": {"type": "string"}}},
              ["company_id", "document_ids", "cutoff_at"], "Get facts from the listed SEC accessions"),
    _function("load_management_materials", {"company_id": COMPANY_ARG, "cutoff_at": TIME_ARG,
              "manifest_path": {"type": ["string", "null"]}}, ["company_id", "cutoff_at", "manifest_path"], "Import supplied management material manifest"),
    _function("load_seekingalpha_materials", {"company_id": COMPANY_ARG, "cutoff_at": TIME_ARG,
              "manifest_path": {"type": ["string", "null"]}}, ["company_id", "cutoff_at", "manifest_path"], "Import legally obtained Seeking Alpha material manifest"),
    _function("search_evidence", {"bundle_id": {"type": "string"}, "query": {"type": "string"},
              "filters": {"type": "object"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20}},
              ["bundle_id", "query", "filters", "top_k"], "Search acquired evidence"),
    _function("validate_data_bundle", {}, [], "Validate acquired bundle contract")
]


def _merge(bundle, result):
    for key, id_key in [("sources", "source_id"), ("metrics", "metric_id"), ("evidence", "evidence_id")]:
        if key in result:
            existing = {item[id_key]: item for item in bundle[key]}
            existing.update({item[id_key]: item for item in result[key]})
            bundle[key] = list(existing.values())
    for key in ["normalization_derivations", "price_files", "conflicts", "quality_checks"]:
        for item in result.get(key, []):
            if item not in bundle[key]:
                bundle[key].append(item)
    for item in result.get("coverage", []):
        bundle["coverage"] = [c for c in bundle["coverage"] if (c["category"], c["source"]) != (item["category"], item["source"])]
        bundle["coverage"].append(item)


def _summary(result):
    # Metadata only in tool history; full artifacts remain on disk/in DataBundle.
    out = {}
    for key in ["documents", "coverage", "counts", "unmapped_metrics", "company_id", "name", "provider_symbols", "valid"]:
        if key in result:
            out[key] = result[key]
    for key in ["metrics", "evidence", "sources", "price_files"]:
        if key in result:
            out[key + "_count"] = len(result[key])
    if result.get("sources"):
        out["source_artifacts"] = [{k: s.get(k) for k in ["source_id", "source_type", "raw_path", "sha256", "access_method"]}
                                   for s in result["sources"]]
    if result.get("price_files"):
        out["price_artifacts"] = [{k: p.get(k) for k in ["path", "source_id", "row_count"]} for p in result["price_files"]]
    if isinstance(result, list):
        return result
    return out


class DataSession:
    def __init__(self, request, config, store, bundle):
        self.request, self.config, self.store, self.bundle = request, config, store, bundle
        self.documents = []
        self.errors, self.warnings = [], []
        self.called = set()

    def execute(self, name, arguments, call_id=None):
        start = now_iso()
        spec = next((t["function"]["parameters"] for t in TOOLS if t["function"]["name"] == name), None)
        if spec is None:
            raise ContractError("Unregistered tool")
        if not isinstance(arguments, dict) or set(arguments) - set(spec["properties"]) or set(spec["required"]) - set(arguments):
            raise ContractError("Invalid tool argument keys")
        if arguments.get("company_id", self.request["ticker"]) != self.request["ticker"] or arguments.get("ticker", self.request["ticker"]) != self.request["ticker"]:
            raise ContractError("Tool cannot change the task company")
        if arguments.get("cutoff_at", self.request["cutoff_at"]) != self.request["cutoff_at"]:
            raise ContractError("Tool cannot change the task cutoff")
        if name.startswith("load_"):
            expected = self.config.get("management_manifest_path" if name == "load_management_materials" else "seekingalpha_manifest_path")
            if arguments["manifest_path"] != expected:
                raise ContractError("Tool may only use the configured import manifest")
        try:
            if name == "resolve_company":
                result = resolve_company(**arguments)
            elif name == "fetch_market_prices":
                result = fetch_market_prices(**arguments, store=self.store, config=self.config)
                count = sum(p["row_count"] for p in result["price_files"])
                result["coverage"] = [coverage("market_data", "yahoo_finance", "available" if count else "error", 1, int(count > 0),
                                               None if count else "NO_VALID_BARS")]
            elif name == "list_regulatory_documents":
                result = list_regulatory_documents(**arguments, store=self.store, config=self.config)
                self.documents = result["documents"]
                wanted = self.config.get("target_annual_reports", 3) + self.config.get("target_quarters", 8)
                got = len(self.documents)
                result["coverage"] = [coverage("regulatory_filings", "sec_edgar", "available" if got >= wanted else "partial" if got else "not_found", wanted, got)]
            elif name == "fetch_financial_facts":
                if not arguments["document_ids"]:
                    raise ContractError("Financial facts require previously listed document_ids")
                result = fetch_financial_facts(**arguments, store=self.store, config=self.config,
                                               documents=self.documents, sources=self.bundle["sources"])
                result["coverage"] = [coverage("financial_facts", "sec_companyfacts", "partial" if result["unmapped_metrics"] else "available",
                                               14, 14 - len(result["unmapped_metrics"]),
                                               "UNMAPPED_METRICS" if result["unmapped_metrics"] else None)]
            elif name in {"load_management_materials", "load_seekingalpha_materials"}:
                fn = load_management_materials if name == "load_management_materials" else load_seekingalpha_materials
                result = fn(**arguments, store=self.store, config=self.config)
            elif name == "search_evidence":
                result = {"matches": search_evidence(**arguments, data_bundle=self.bundle)}
            else:
                validate_data_bundle(self.bundle)
                result = {"valid": True}
            _merge(self.bundle, result)
            self.warnings.extend(result.get("warnings", []) + result.get("limitations", []))
            self.called.add(name)
            summary = _summary(result)
            if name == "search_evidence":
                summary = result
            self.store.trace("data", name, arguments, start, "success", summary, call_id)
            return summary
        except Exception as exc:
            # Safe fixed text: provider responses may echo credentials or untrusted instructions.
            record = error_record("TOOL_FAILED", f"{name} failed ({type(exc).__name__}); inspect local configuration and source availability")
            self.errors.append(record)
            category = {"fetch_market_prices": ("market_data", "yahoo_finance"),
                        "list_regulatory_documents": ("regulatory_filings", "sec_edgar"),
                        "fetch_financial_facts": ("financial_facts", "sec_companyfacts"),
                        "load_management_materials": ("management_materials", "management_materials"),
                        "load_seekingalpha_materials": ("third_party_research", "seeking_alpha")}.get(name)
            if category:
                _merge(self.bundle, {"coverage": [coverage(*category, "error", 1, 0, record["code"])]})
            self.store.trace("data", name, arguments, start, "failed", record, call_id)
            return {"error": record}


def _start_date(request, config):
    end = date.fromisoformat(request["as_of"])
    try:
        return end.replace(year=end.year - config.get("lookback_years", 3)).isoformat()
    except ValueError:  # February 29 when the target year is not a leap year.
        return end.replace(year=end.year - config.get("lookback_years", 3), day=28).isoformat()


def _collect_deterministic(session):
    req, cfg = session.request, session.config
    base = dict(company_id=req["ticker"], cutoff_at=req["cutoff_at"])
    tasks = [("resolve_company", {"ticker": req["ticker"]}),
             ("fetch_market_prices", dict(base, start_date=_start_date(req, cfg))),
             ("list_regulatory_documents", dict(base, document_types=["10-K", "10-Q", "10-K/A", "10-Q/A", "8-K"]))]
    for name, args in tasks:
        session.execute(name, args)
    if session.documents:
        session.execute("fetch_financial_facts", dict(base, document_ids=[d["document_id"] for d in session.documents]))
    for name, key in [("load_management_materials", "management_manifest_path"), ("load_seekingalpha_materials", "seekingalpha_manifest_path")]:
        session.execute(name, dict(base, manifest_path=cfg.get(key)))
    session.warnings.append("Explicit deterministic collection: no LLM tool selection was performed; this is not a full agent demonstration.")


def _collect_llm(session, feedback):
    req, cfg = session.request, session.config
    context = dict(request=req, bundle_id=session.bundle["bundle_id"],
                   start_date=_start_date(req, cfg),
                   management_manifest_path=cfg.get("management_manifest_path"),
                   seekingalpha_manifest_path=cfg.get("seekingalpha_manifest_path"), feedback=feedback)
    messages = [{"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
                {"role": "user", "content": "TASK_CONTEXT\n" + json.dumps(context, ensure_ascii=False)}]
    budget, used = cfg.get("max_tool_calls", 12), 0
    for _ in range(budget + 1):
        message, metadata = agent_llm.complete(messages, cfg, tools=TOOLS)
        session.store.trace("data", "llm_completion", {}, now_iso(), "success", metadata)
        calls = message.get("tool_calls", [])
        if not calls:
            return
        if used + len(calls) > budget:
            session.warnings.append("TOOL_BUDGET_EXHAUSTED")
            return
        messages.append({k: message[k] for k in ["role", "content", "tool_calls"] if k in message})
        for call in calls:
            used += 1
            try:
                result = session.execute(call["function"]["name"], json.loads(call["function"]["arguments"]), call["id"])
            except (KeyError, ValueError, TypeError) as exc:
                result = {"error": "INVALID_TOOL_CALL", "type": type(exc).__name__}
                session.errors.append(error_record("INVALID_TOOL_CALL", "Model tool arguments rejected"))
            messages.append(dict(role="tool", tool_call_id=call.get("id", "invalid"), content=json.dumps(result, ensure_ascii=False)))
    session.warnings.append("TOOL_BUDGET_EXHAUSTED")


def run_data(request, config, feedback=None):
    store, session = None, None
    try:
        try:
            validate_request(request)
        except ContractError as exc:
            return envelope(request, "data", "failed", errors=[error_record("INVALID_REQUEST", str(exc))])
        store = Store(request, config)
        latest = store.root / "data_latest.json"
        revision = 1
        if latest.exists():
            previous = json.loads(latest.read_text(encoding="utf-8"))
            if previous["run_id"] != request["run_id"] or previous["payload"]["company"]["company_id"] != request["ticker"] or previous["payload"]["cutoff_at"] != request["cutoff_at"]:
                raise ContractError("run_dir already belongs to a different request; use a separate run_id/run_dir")
            revision = previous["payload"]["revision"] + 1
        bundle = dict(bundle_id=stable_id("bundle", request["run_id"], request["ticker"]), revision=revision,
                      company=resolve_company(request["ticker"]), cutoff_at=request["cutoff_at"],
                      sources=[], metrics=[], normalization_derivations=[], evidence=[], price_files=[],
                      coverage=[coverage(category, source, "not_checked", target, 0, "TOOL_NOT_CALLED")
                                for category, source, target in [("market_data", "yahoo_finance", 1),
                                    ("regulatory_filings", "sec_edgar", config.get("target_annual_reports", 3) + config.get("target_quarters", 8)),
                                    ("financial_facts", "sec_companyfacts", 14),
                                    ("management_materials", "management_materials", config.get("target_management_events", 4)),
                                    ("third_party_research", "seeking_alpha", config.get("target_seekingalpha_materials", 2))]],
                      conflicts=[], quality_checks=[])
        session = DataSession(request, config, store, bundle)
        mode = config.get("collection_mode", "llm")
        if mode == "llm":
            _collect_llm(session, feedback)
        elif mode == "deterministic":
            _collect_deterministic(session)
        else:
            raise ContractError("collection_mode must be llm or deterministic")
        validate_data_bundle(bundle)
        useful = bool(bundle["metrics"] or any(p.get("row_count", 0) for p in bundle["price_files"]) or bundle["evidence"])
        incomplete = any(c["state"] not in {"available", "not_applicable"} for c in bundle["coverage"])
        incomplete = incomplete or any(c.get("state") == "unresolved" for c in bundle["conflicts"])
        incomplete = incomplete or any(c.get("status") == "failed" for c in bundle["quality_checks"])
        status = "failed" if not useful else "partial" if incomplete or session.errors or mode != "llm" else "success"
        result = envelope(request, "data", status, bundle, session.warnings, session.errors,
                          trace_path=store.ref(store.root / "logs" / "data_tools.jsonl"))
        path = store.write_json(store.root / f"data_v{revision}.json", result)
        result["artifacts"] = [path]
        store.write_json(store.root / f"data_v{revision}.json", result)
        store.write_json(latest, result)
        return result
    except Exception as exc:
        code = "INVALID_REQUEST_OR_CONTRACT" if isinstance(exc, (ContractError, KeyError, TypeError)) else "LLM_ERROR" if isinstance(exc, agent_llm.LLMError) else "DATA_FAILED"
        message = str(exc) if isinstance(exc, (ContractError, agent_llm.LLMError)) else f"Data stage failed ({type(exc).__name__})"
        result = envelope(request, "data", "failed", session.bundle if session else {},
                          errors=[error_record(code, message)])
        if store:
            store.write_json(store.root / "data_failure.json", result)
        return result
