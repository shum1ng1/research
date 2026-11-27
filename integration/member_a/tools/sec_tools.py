# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""SEC adapters with accession-aware, point-in-time fact selection."""
from __future__ import annotations

import json
import hashlib
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from integration.member_a.schemas.member_a import aware_datetime
from integration.member_a.tools.company_registry import resolve_company
from integration.member_a.tools.member_a_common import known_by, now_iso, stable_id
from integration.member_a.tools.normalization import json_number, subtract_cumulative
from integration.member_a.tools.document_tools import extract_text

TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income_attributable_to_parent": ["NetIncomeLoss"],
    "research_and_development": ["ResearchAndDevelopmentExpense"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex_cash_outflow": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
    "total_assets": ["Assets"], "total_liabilities": ["Liabilities"],
    "equity_attributable_to_parent": ["StockholdersEquity"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "weighted_average_diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "shares_outstanding": ["CommonStockSharesOutstanding"],
}
INSTANT = {"cash_and_equivalents", "total_assets", "total_liabilities",
           "equity_attributable_to_parent", "shares_outstanding"}


class SECClient:
    access_method = "http"
    def __init__(self, config):
        self.user_agent = config.get("sec_user_agent") or os.getenv("SEC_USER_AGENT", "")
        if "@" not in self.user_agent:
            raise ValueError("Set SEC_USER_AGENT to your project name and real contact email")
        self.timeout = config.get("request_timeout_seconds", 30)
        self.last_request = 0.0
        self.attempts = config.get("http_attempts", 3)

    def get(self, url):
        if urlparse(url).scheme != "https" or urlparse(url).hostname not in {"data.sec.gov", "www.sec.gov"}:
            raise ValueError("SEC URL is not allowlisted")
        for attempt in range(self.attempts):
            elapsed = time.monotonic() - self.last_request
            if elapsed < 0.2:
                time.sleep(0.2 - elapsed)
            self.last_request = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent,
                                                      "Accept-Encoding": "identity"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    if urlparse(response.url).hostname not in {"data.sec.gov", "www.sec.gov"}:
                        raise ValueError("Unexpected SEC redirect")
                    raw = response.read(30_000_001)
                    if len(raw) > 30_000_000:
                        raise ValueError("SEC document exceeds size limit")
                    return raw
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.attempts - 1:
                    raise RuntimeError(f"SEC HTTP {exc.code}") from None
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError("SEC request exhausted")


class ImportedSECClient:
    """Exact URL-to-file replay of manually downloaded official SEC artifacts."""
    access_method = "manual_import"

    def __init__(self, manifest_path, store, company_id):
        manifest = json.loads(store.resolve(manifest_path).read_text(encoding="utf-8"))
        if manifest.get("company_id") != company_id:
            raise ValueError("SEC import company mismatch")
        self.store = store
        self.records = {r["source_url"]: r for r in manifest["files"]}

    def get(self, url):
        record = self.records[url]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"data.sec.gov", "www.sec.gov"}:
            raise ValueError("SEC import URL is not official")
        raw = self.store.resolve(record["local_path"]).read_bytes()
        if len(raw) > 30_000_000 or hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("SEC import size/hash mismatch")
        return raw


def _client(config, store, company_id):
    path = config.get("sec_import_manifest_path")
    if path and not config.get("allow_manual_import", True):
        raise ValueError("SEC manual import is disabled by configuration")
    return ImportedSECClient(path, store, company_id) if path else SECClient(config)


def _rows(columns):
    if not columns:
        return []
    n = len(columns.get("accessionNumber", []))
    return [{key: values[i] for key, values in columns.items() if isinstance(values, list) and i < len(values)}
            for i in range(n)]


def _publication(row):
    accepted = row.get("acceptanceDateTime")
    if accepted:
        # SEC submissions API emits timezone-qualified acceptance times.
        try:
            return dict(available_at=aware_datetime(accepted).isoformat(), precision="second",
                        public_date=row["filingDate"])
        except ValueError:
            pass
    return dict(available_at=None, precision="date", public_date=row["filingDate"])


def list_regulatory_documents(company_id, cutoff_at, document_types, *, store, config, client=None):
    company = resolve_company(company_id)
    client = client or _client(config, store, company_id)
    method = getattr(client, "access_method", "http")
    cik = company["provider_symbols"]["sec_cik"]
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    raw = client.get(url)
    payload = json.loads(raw)
    if str(payload.get("cik", "")).lstrip("0") != cik.lstrip("0"):
        raise ValueError("SEC CIK does not match registered company")
    sources = [store.source(company_id, "SEC submission index snapshot", "SEC_SUBMISSIONS",
                            "transport_snapshot", url, raw, available_at=now_iso(), role="transport_snapshot", access_method=method)]
    rows = _rows(payload.get("filings", {}).get("recent", {}))
    # Older submission files are documented in the index; no guessed filenames.
    if config.get("fetch_older_submissions", True):
        cutoff_day = aware_datetime(cutoff_at).date().isoformat()
        for part in payload.get("filings", {}).get("files", []):
            if part.get("filingFrom", "9999") > cutoff_day:
                continue
            part_name = part.get("name", "")
            if "/" in part_name or not part_name.endswith(".json"):
                continue
            if len(rows) >= config.get("max_submission_rows", 8000):
                break
            older_url = f"https://data.sec.gov/submissions/{part_name}"
            older_raw = client.get(older_url)
            rows.extend(_rows(json.loads(older_raw)))
            sources.append(store.source(company_id, "SEC older index snapshot", "SEC_SUBMISSIONS",
                                         "transport_snapshot", older_url, older_raw,
                                         available_at=now_iso(), role="transport_snapshot", access_method=method))
    eligible = []
    for row in rows:
        if row.get("form") not in document_types or not row.get("primaryDocument"):
            continue
        pub = _publication(row)
        if known_by(dict(available_at=pub["available_at"], available_at_precision=pub["precision"],
                         public_date=pub["public_date"], source_timezone="America/New_York"), cutoff_at):
            eligible.append(row)
    eligible.sort(key=lambda r: (r.get("filingDate", ""), r["accessionNumber"]), reverse=True)
    counts = {}
    documents, evidence = [], []
    limits = {"10-K": config.get("target_annual_reports", 3), "10-K/A": config.get("target_annual_reports", 3),
              "10-Q": config.get("target_quarters", 8), "10-Q/A": config.get("target_quarters", 8),
              "8-K": config.get("target_management_events", 4)}
    seen = set()
    for row in eligible:
        accession, form = row["accessionNumber"], row["form"]
        if accession in seen or counts.get(form, 0) >= limits.get(form, 4):
            continue
        seen.add(accession)
        primary = row["primaryDocument"]
        if "/" in primary or ".." in primary:
            continue
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary}"
        filing_raw = client.get(filing_url)
        pub = _publication(row)
        source = store.source(company_id, f"{company_id} {form} {row.get('reportDate')}",
                              "SEC_FILING", form, filing_url, filing_raw,
                              **pub, extension="html", version=accession,
                              accession=accession, period_end=row.get("reportDate"), role="analytical", access_method=method)
        text = extract_text(filing_raw, ".html")
        text_path = store.root / "processed" / f"{source['source_id']}.txt"
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        source.update(extracted_text_path=store.ref(text_path), extracted_text_sha256=hashlib.sha256(text.encode()).hexdigest())
        for start in range(0, len(text), 1600):
            end = min(start + 1900, len(text))
            evidence.append(dict(evidence_id=stable_id(company_id + "_E", source["source_id"], start, end),
                                 source_id=source["source_id"], quote=text[start:end], language="en",
                                 locator=dict(format="text_offsets", start=start, end=end, text_path=store.ref(text_path))))
        sources.append(source)
        documents.append(dict(document_id=accession, form=form, report_date=row.get("reportDate"),
                              source_id=source["source_id"], source_url=filing_url))
        counts[form] = counts.get(form, 0) + 1
    return dict(sources=sources, evidence=evidence, documents=documents, registered_name=payload.get("name"), counts=counts)


def _period(fact, name):
    if name in INSTANT:
        return "instant"
    if not fact.get("start"):
        return None
    days = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days + 1
    if 60 <= days <= 110:
        return "quarter"
    if 150 <= days <= 220:
        return "half_year"
    if 240 <= days <= 310:
        return "nine_months"
    if 330 <= days <= 390:
        return "annual"
    return None


def fetch_financial_facts(company_id, document_ids, cutoff_at, *, store, config, documents, sources, client=None):
    client = client or _client(config, store, company_id)
    company = resolve_company(company_id)
    cik = company["provider_symbols"]["sec_cik"]
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    raw = client.get(url)
    data = json.loads(raw)
    if str(data.get("cik", "")).lstrip("0") != cik.lstrip("0"):
        raise ValueError("Company Facts CIK mismatch")
    snapshot = store.source(company_id, "SEC Company Facts snapshot", "SEC_COMPANYFACTS",
                             "transport_snapshot", url, raw, available_at=now_iso(), role="transport_snapshot", access_method=getattr(client, "access_method", "http"))
    all_sources = [snapshot]
    by_source = {s["source_id"]: s for s in sources}
    by_accn = {d["document_id"]: d for d in documents if d["document_id"] in document_ids}
    if set(document_ids) - set(by_accn):
        raise ValueError("document_ids must come from list_regulatory_documents")
    facts = data.get("facts", {}).get("us-gaap", {})
    selected = {}
    conflicts = []
    for name, tags in TAGS.items():
        units_expected = "shares" if "shares" in name else "USD/shares" if name == "eps_diluted" else "USD"
        for priority, tag in enumerate(tags):
            concept = facts.get(tag)
            if not concept:
                continue
            for index, fact in enumerate(concept.get("units", {}).get(units_expected, [])):
                accn = fact.get("accn")
                # .date() is the calendar date of the timezone-aware cutoff, not a sliced string.
                cutoff_day = aware_datetime(cutoff_at).date().isoformat()
                if accn not in by_accn or fact.get("end", "9999") > cutoff_day:
                    continue
                document = by_accn[accn]
                filing_source = by_source[document["source_id"]]
                if not known_by(filing_source, cutoff_at):
                    continue
                period_type = _period(fact, name)
                if period_type is None:
                    continue
                key = (name, fact.get("start") if name not in INSTANT else None, fact["end"])
                # Latest eligible filing version wins; tag preference only breaks ties.
                rank = (filing_source["public_date"], accn, -priority)
                candidate = (rank, fact, tag, index, period_type, filing_source)
                old = selected.get(key)
                if old and old[0] == rank and old[1]["val"] != fact["val"]:
                    conflicts.append(dict(metric_name=name, period=key[1:], state="unresolved",
                                          values=[old[1]["val"], fact["val"]], reason="SAME_CONTEXT_VALUES_DIFFER"))
                if old is None or rank > old[0]:
                    selected[key] = candidate
    metrics, evidence = [], []
    cutoff_day = aware_datetime(cutoff_at).date()
    minimum_year = cutoff_day.year - config.get("lookback_years", 3) - 1
    for (name, start, end), (_, fact, tag, index, period_type, filing) in sorted(selected.items(), key=lambda x: str(x[0])):
        if date.fromisoformat(end).year < minimum_year:
            continue
        extracted = dict(taxonomy="us-gaap", concept=tag, unit=("shares" if "shares" in name else "USD/shares" if name == "eps_diluted" else "USD"),
                         fact=fact, original_filing_url=filing["source_url"],
                         original_filing_source_id=filing["source_id"],
                         snapshot_source_id=snapshot["source_id"],
                         snapshot_pointer=f"/facts/us-gaap/{tag}/units")
        quote = json.dumps(extracted, ensure_ascii=False, sort_keys=True)
        source = store.source(company_id, f"{name} {start or 'instant'} to {end}", "SEC_XBRL_FACT",
                              "xbrl_fact", filing["source_url"], quote,
                              available_at=filing["available_at"], public_date=filing["public_date"],
                              precision=filing["available_at_precision"], version=filing["document_version"],
                              parent_source_id=filing["source_id"],
                              snapshot_source_id=snapshot["source_id"], role="analytical")
        all_sources.append(source)
        eid = stable_id(company_id + "_E", source["source_id"], "fact")
        evidence.append(dict(evidence_id=eid, source_id=source["source_id"], quote=quote,
                             locator={"format": "json", "pointer": "/fact"}, language="en"))
        metric = dict(metric_id=stable_id(company_id + "_M", name, start, end, fact["accn"]),
                      company_id=company_id, name=name, value=json_number(fact["val"]),
                      unit="shares" if "shares" in name else "currency_per_share" if name == "eps_diluted" else "currency",
                      currency=None if "shares" in name else "USD", original_value=fact["val"],
                      original_unit=extracted["unit"], scale_multiplier=1,
                      period_start=start, period_end=end, period_type=period_type,
                      value_type="instant" if period_type == "instant" else "duration",
                      fiscal_year=fact.get("fy"), fiscal_quarter=fact.get("fp"),
                      fiscal_label_note="SEC fy/fp describe filing context; use actual start/end for metric period",
                      accounting_standard="US_GAAP", reporting_basis="GAAP", scope="consolidated",
                      source_id=source["source_id"], evidence_id=eid, source_field="us-gaap:" + tag,
                      origin="reported", missing_reason=None)
        if name == "capex_cash_outflow":
            metric["definition"] = "Cash payments to acquire property, plant and equipment; excludes lease additions"
        metrics.append(metric)
    derivations = []
    # Add missing standalone quarters only; do not difference EPS/stock counts/margins.
    existing = {(m["name"], m["period_start"], m["period_end"]) for m in metrics if m["period_type"] == "quarter"}
    for later in list(metrics):
        if later["period_type"] not in {"half_year", "nine_months", "annual"}:
            continue
        options = [m for m in metrics if m["name"] == later["name"] and m["period_start"] == later["period_start"]
                   and m["period_end"] < later["period_end"]]
        if not options:
            continue
        earlier = max(options, key=lambda m: m["period_end"])
        try:
            quarter, derivation = subtract_cumulative(later, earlier)
        except ValueError:
            continue
        key = (quarter["name"], quarter["period_start"], quarter["period_end"])
        if key not in existing:
            metrics.append(quarter)
            derivations.append(derivation)
            existing.add(key)
    return dict(sources=all_sources, metrics=metrics, evidence=evidence,
                normalization_derivations=derivations, conflicts=conflicts,
                unmapped_metrics=[name for name in TAGS if not any(m["name"] == name for m in metrics)])
