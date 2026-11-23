"""Synthetic MSFT/CSCO fixture. No Yahoo, SEC, or model calls.

The Data and Reviewer functions still run. Market transport and the HKU client
are replaced with labeled stand-ins for this process only.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from integration.member_a.tools.company_registry import resolve_company
from integration.member_a.tools.member_a_common import now_iso
from integration.member_a.tools.sec_tools import INSTANT, TAGS
from integration.member_a.tools.yahoo_tools import COLUMNS, clean_bars

NOTICE = "SYNTHETIC TEST FIXTURE; NOT REAL COMPANY FINANCIAL DATA"
_PATCH_PRICE = "integration.member_a.tools.member_a_data.fetch_market_prices"
_PATCH_LLM = "integration.member_a.tools.agent_llm.complete"


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _periods(as_of: str):
    day = date.fromisoformat(as_of)
    quarter_end = day - timedelta(days=40)
    quarter_start = quarter_end - timedelta(days=89)
    prior_end = quarter_end - timedelta(days=365)
    prior_start = prior_end - timedelta(days=89)
    annual_end = prior_start - timedelta(days=1)
    annual_start = annual_end - timedelta(days=364)
    return [
        ("10-K", annual_start, annual_end, annual_end + timedelta(days=20)),
        ("10-Q", prior_start, prior_end, prior_end + timedelta(days=15)),
        ("10-Q", quarter_start, quarter_end, quarter_end + timedelta(days=15)),
    ]


def _amounts(name: str, form: str, end_index: int):
    """Decimal-friendly synthetic amounts. end_index 2 is the latest quarter."""
    special = {
        "revenue": {0: 400, 1: 100, 2: 110},
        "net_income_attributable_to_parent": {0: 40, 1: 10, 2: 11},
        "operating_cash_flow": {0: 80, 1: 40, 2: 50},
        "capex_cash_outflow": {0: 20, 1: 15, 2: 20},
        "eps_diluted": {0: 8, 1: 2, 2: 2.5},
    }
    if name in special:
        return special[name][end_index]
    return 10 + end_index


def prepare_fixture(project: Path, request: dict, config: dict) -> list[str]:
    """Write local SEC/management files and point config at them. Returns accessions."""
    project = Path(project).resolve()
    ticker = request["ticker"]
    company = resolve_company(ticker)
    cik = company["provider_symbols"]["sec_cik"]
    base = project / config["run_dir"] / "fixture"
    base.mkdir(parents=True, exist_ok=True)
    periods = _periods(request["as_of"])
    accns = [f"{cik}-26-00000{i + 1}" for i in range(len(periods))]
    rows = []
    for index, (form, _start, end, filed) in enumerate(periods):
        if datetime.combine(filed, time(12, 0), timezone.utc) > datetime.fromisoformat(request["cutoff_at"]):
            raise RuntimeError("synthetic filing is after cutoff; choose a later as_of")
        rows.append({
            "accessionNumber": accns[index],
            "form": form,
            "reportDate": end.isoformat(),
            "filingDate": filed.isoformat(),
            "acceptanceDateTime": filed.isoformat() + "T12:00:00Z",
            "primaryDocument": f"synthetic{index}.htm",
        })
    columns = {key: [row[key] for row in rows] for key in rows[0]}
    submissions = {
        "cik": int(cik),
        "name": company["name"],
        "fixture_notice": NOTICE,
        "filings": {"recent": columns, "files": []},
    }
    concepts = {}
    for name, tags in TAGS.items():
        unit = "shares" if "shares" in name else "USD/shares" if name == "eps_diluted" else "USD"
        facts = []
        for index, (form, start, end, _filed) in enumerate(periods):
            fact = {
                "val": _amounts(name, form, index),
                "end": end.isoformat(),
                "accn": accns[index],
                "fy": end.year,
                "fp": "FY" if form == "10-K" else "Q1",
                "form": form,
                "filed": rows[index]["filingDate"],
            }
            if name not in INSTANT:
                fact["start"] = start.isoformat()
            facts.append(fact)
        concepts[tags[0]] = {"label": name, "units": {unit: facts}}
    companyfacts = {"cik": int(cik), "fixture_notice": NOTICE, "facts": {"us-gaap": concepts}}
    files = []

    def save(url: str, filename: str, content: bytes) -> None:
        path = base / filename
        path.write_bytes(content)
        files.append({
            "source_url": url,
            "local_path": str(path.relative_to(project)),
            "sha256": hashlib.sha256(content).hexdigest(),
        })

    save(f"https://data.sec.gov/submissions/CIK{cik}.json", "submissions.json", json.dumps(submissions).encode())
    save(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", "companyfacts.json", json.dumps(companyfacts).encode())
    for index, accession in enumerate(accns):
        table = "".join(
            f"<tr><td>{name}</td><td>{_amounts(name, periods[index][0], index)}</td></tr>" for name in TAGS
        )
        html = f"<html><h1>{NOTICE}</h1><h2>{ticker}</h2><table>{table}</table></html>"
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/synthetic{index}.htm"
        save(url, f"filing{index}.html", html.encode())
    manifest = base / "sec_import.json"
    _write(manifest, {"fixture_notice": NOTICE, "company_id": ticker, "files": files})
    config["sec_import_manifest_path"] = str(manifest.relative_to(project))
    config["fetch_older_submissions"] = False
    host = "www.microsoft.com" if ticker == "MSFT" else "investor.cisco.com"
    texts = {
        "management_materials": NOTICE + "\nManagement expects continuing uncertainty. Synthetic commentary only.\n",
        "third_party_research": NOTICE + "\nThird party commentary. Synthetic commentary only.\n",
    }
    for kind, key, text in (
        ("management_materials", "management_manifest_path", texts["management_materials"]),
        ("third_party_research", "seekingalpha_manifest_path", texts["third_party_research"]),
    ):
        text_path = base / f"{kind}.txt"
        text_path.write_text(text, encoding="utf-8")
        source_type = "IR" if kind == "management_materials" else "SEEKING_ALPHA"
        document_host = host if kind == "management_materials" else "seekingalpha.com"
        doc = {
            "company_id": ticker,
            "category": kind,
            "source_type": source_type,
            "document_type": "earnings_transcript" if kind == "management_materials" else "research_article",
            "title": NOTICE,
            "source_url": f"https://{document_host}/synthetic-test-not-a-real-document",
            "language": "en",
            "available_at": rows[-1]["acceptanceDateTime"],
            "source_timezone": "America/New_York",
            "local_path": str(text_path.relative_to(project)),
            "access_method": "synthetic_fixture",
            "author": "TEST FIXTURE",
        }
        path = base / f"{kind}_manifest.json"
        _write(path, {"fixture_notice": NOTICE, "documents": [doc]})
        config[key] = str(path.relative_to(project))
    config["fixture_notice"] = NOTICE
    config["llm_transport"] = "synthetic_fixture"
    config["review_transport"] = "synthetic_fixture"
    return accns


def _sessions(cutoff_at: str, count: int = 3):
    cutoff = datetime.fromisoformat(cutoff_at)
    found = []
    day = cutoff.date() - timedelta(days=1)
    while len(found) < count:
        close = datetime.combine(day, time(21, 0), timezone.utc)
        if close <= cutoff:
            found.append((day.isoformat(), close))
        day -= timedelta(days=1)
        if (cutoff.date() - day).days > 40:
            break
    found.sort()
    closes = [100.0, 105.0, 110.0]
    return [(session, closes[index], close) for index, (session, close) in enumerate(found)]


def fake_prices(company_id, start_date, cutoff_at, *, store, config):
    del start_date
    company = resolve_company(company_id)
    bars = _sessions(cutoff_at)
    records = [
        {"session_date": session, "open": close, "high": close + 1, "low": close - 1,
         "close": close, "adj_close": close, "volume": 1000}
        for session, close, _stamp in bars
    ]
    schedule = {session: stamp.isoformat() for session, _close, stamp in bars}
    raw = json.dumps({"fixture_notice": NOTICE, "records": records, "exchange_schedule": schedule})
    source = store.source(
        company_id, NOTICE, "YAHOO_FINANCE", "market_snapshot",
        f"https://finance.yahoo.com/quote/{company_id}/history/", raw,
        available_at=now_iso(), role="transport_snapshot", access_method="synthetic_fixture",
    )
    cleaned, checks = clean_bars(records, company, cutoff_at, schedule)
    path = store.root / "prices.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows([dict(row, source_id=source["source_id"]) for row in cleaned])
    return {
        "sources": [source],
        "price_files": [{
            "path": store.ref(path),
            "source_id": source["source_id"],
            "row_count": len(cleaned),
            "columns": COLUMNS,
            "currency": "USD",
            "volume_unit": "shares",
            "adjustment_method": "yahoo_auto_adjust_false",
        }],
        "quality_checks": checks,
        "limitations": [NOTICE + " Prices are invented and were not downloaded from Yahoo."],
    }


def _call(name, arguments, identifier):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def data_model(request, config, accns):
    base = {"company_id": request["ticker"], "cutoff_at": request["cutoff_at"]}
    sequence = [
        [
            _call("resolve_company", {"ticker": request["ticker"]}, "c1"),
            _call("fetch_market_prices", dict(base, start_date="2000-01-01"), "c2"),
            _call("list_regulatory_documents", dict(base, document_types=["10-K", "10-Q", "8-K"]), "c3"),
            _call("load_management_materials", dict(base, manifest_path=config["management_manifest_path"]), "c4"),
            _call("load_seekingalpha_materials", dict(base, manifest_path=config["seekingalpha_manifest_path"]), "c5"),
        ],
        [_call("fetch_financial_facts", dict(base, document_ids=list(accns)), "c6")],
        [_call("validate_data_bundle", {}, "c7")],
        [],
    ]
    index = {"n": 0}

    def complete(*args, **kwargs):
        del args, kwargs
        calls = sequence[index["n"]] if index["n"] < len(sequence) else []
        index["n"] += 1
        return (
            {"role": "assistant", "content": "SYNTHETIC FIXTURE TOOL CALLS; NOT A MODEL", "tool_calls": calls},
            {"model": "synthetic_fixture", "usage": {}},
        )
    return complete


def review_model(support="supported"):
    index = {"n": 0}

    def complete(*args, **kwargs):
        del kwargs
        context = json.loads(args[0][1]["content"].split("\n", 1)[1])
        if index["n"] == 0:
            calls = [_call("verify_numeric_claims", {}, "r1")]
        else:
            judgments = [{
                "report_claim_id": claim["report_claim_id"],
                "support": support,
                "rationale": "SYNTHETIC TEST JUDGMENT; NOT A LIVE MODEL REVIEW",
                "evidence_ids": [item["evidence_id"] for item in claim["attached_evidence"]],
            } for claim in context["claims"]]
            calls = [_call("check_claim_support", {"judgments": judgments}, "r2")]
        index["n"] += 1
        return (
            {"role": "assistant", "content": None, "tool_calls": calls},
            {"model": "synthetic_fixture", "usage": {}},
        )
    return complete


def patched_data(request, config, feedback=None):
    from integration.member_a.agents.data_agent import run as run_data
    accns = config["synthetic_accns"]
    with patch(_PATCH_PRICE, side_effect=fake_prices), patch(_PATCH_LLM, side_effect=data_model(request, config, accns)):
        return run_data(request, config, feedback)


def patched_review(request, config, data_bundle, analysis_bundle, report_draft, support="supported"):
    from integration.member_a.agents.reviewer_agent import run as run_review
    with patch(_PATCH_LLM, side_effect=review_model(support)):
        return run_review(request, config, data_bundle, analysis_bundle, report_draft)
